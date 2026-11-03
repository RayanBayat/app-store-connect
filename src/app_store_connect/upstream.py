"""Download, verify and install the upstream server binary (idoo/app-store-connect-mcp).

Trust chain, in plain words:

1. `checksums.txt` is signed by the upstream release workflow using Sigstore "keyless"
   signing. We verify that signature against ONE exact identity (that workflow, at the tag of
   the version we want) so a signature from any other repository or tag is rejected.
2. The tarball we downloaded must match the sha256 written in the verified `checksums.txt`.
3. We extract only the one binary file, never anything else from the archive.
4. We remember the binary's sha256 and re-check it every time we start.

If any step fails we raise `UpstreamError`. A binary that was not fully verified is never
returned to the caller.
"""

from __future__ import annotations

import hashlib
import json
import logging
import platform
import re
import sys
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Final, Protocol

import httpx
from sigstore.errors import Error as SigstoreError
from sigstore.models import Bundle
from sigstore.verify import Verifier
from sigstore.verify.policy import Identity, VerificationPolicy

from app_store_connect.paths import data_dir, ensure_private_dir

logger = logging.getLogger(__name__)

# The single source of truth for the upstream version we trust. Bump it only by following
# MAINTENANCE.md (security review first).
PINNED_VERSION: Final = "0.22.0"
UPSTREAM_REPO: Final = "idoo/app-store-connect-mcp"
BINARY_NAME: Final = "app-store-connect-mcp"
OIDC_ISSUER: Final = "https://token.actions.githubusercontent.com"
RECORD_NAME: Final = "verified.json"
VERSION_PATTERN: Final = re.compile(r"\d+\.\d+\.\d+")

MAX_CHECKSUMS_BYTES: Final = 1 * 1024 * 1024
MAX_BUNDLE_BYTES: Final = 1 * 1024 * 1024
MAX_TARBALL_BYTES: Final = 100 * 1024 * 1024
MAX_BINARY_BYTES: Final = 200 * 1024 * 1024
HTTP_TIMEOUT: Final = httpx.Timeout(60.0, connect=15.0)
CHUNK_SIZE: Final = 64 * 1024

_OS_NAMES: Final = {"linux": "linux", "darwin": "darwin"}
_ARCH_NAMES: Final = {"x86_64": "amd64", "amd64": "amd64", "arm64": "arm64", "aarch64": "arm64"}


class UpstreamError(Exception):
    """Something went wrong getting a verified upstream binary. The text is for humans."""


class ArtifactVerifier(Protocol):
    """The one method of sigstore's `Verifier` that we use (lets tests pass a fake)."""

    def verify_artifact(
        self, input_: bytes, bundle: Bundle, policy: VerificationPolicy
    ) -> None:  # pragma: no cover
        """Raise `sigstore.errors.VerificationError` if the signature does not check out."""


def check_version(version: str) -> str:
    """Return `version` if it looks like X.Y.Z, else raise (it ends up in URLs and paths)."""
    if not VERSION_PATTERN.fullmatch(version):
        raise UpstreamError(f"Upstream version {version!r} is not in the form X.Y.Z.")
    return version


def asset_name(version: str, system: str | None = None, machine: str | None = None) -> str:
    """Return the release tarball name for this platform, e.g. ..._0.22.0_linux_amd64.tar.gz."""
    raw_os = (system or sys.platform).lower()
    raw_arch = (machine or platform.machine()).lower()
    os_name = _OS_NAMES.get("linux" if raw_os.startswith("linux") else raw_os)
    arch = _ARCH_NAMES.get(raw_arch)
    if os_name is None or arch is None:
        raise UpstreamError(
            f"Unsupported platform {raw_os}/{raw_arch}. Upstream publishes Linux and macOS "
            "builds for amd64 and arm64."
        )
    return f"{BINARY_NAME}_{check_version(version)}_{os_name}_{arch}.tar.gz"


def release_url(version: str, filename: str) -> str:
    """Return the download URL of one asset of the release for `version`."""
    return f"https://github.com/{UPSTREAM_REPO}/releases/download/v{version}/{filename}"


def expected_identity(version: str) -> str:
    """The exact signer identity: upstream's release workflow at the tag for `version`."""
    return f"https://github.com/{UPSTREAM_REPO}/.github/workflows/release.yml@refs/tags/v{version}"


def parse_checksums(text: str) -> dict[str, str]:
    """Parse `sha256  filename` lines into {filename: sha256}; malformed lines are skipped."""
    sums: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            sums[parts[1].lstrip("*")] = parts[0]
    return sums


def sha256_of(path: Path) -> str:
    """Return the hex sha256 of a file, reading it in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def install_dir(version: str, env: dict[str, str] | None = None) -> Path:
    """Return where `version` is installed: <data dir>/upstream/<version>."""
    return data_dir(env) / "upstream" / check_version(version)


def binary_path(version: str) -> Path:
    """Return the full path of the installed binary for `version`."""
    return install_dir(version) / BINARY_NAME


def download(client: httpx.Client, url: str, dest: Path, max_bytes: int) -> None:
    """Download `url` to `dest`, following redirects, refusing anything over `max_bytes`."""
    try:
        with client.stream("GET", url, follow_redirects=True) as response:
            response.raise_for_status()
            if response.url.scheme != "https":
                raise UpstreamError(f"Download of {url} ended on a non-https address.")
            total = 0
            with dest.open("wb") as out:
                for chunk in response.iter_bytes(CHUNK_SIZE):
                    total += len(chunk)
                    if total > max_bytes:
                        raise UpstreamError(f"Download of {url} is larger than {max_bytes} bytes.")
                    out.write(chunk)
    except httpx.HTTPError as exc:
        raise UpstreamError(f"Could not download {url}: {exc}") from exc


def load_bundle(data: bytes) -> Bundle:
    """Parse a Sigstore bundle (the `.sigstore.json` file)."""
    try:
        return Bundle.from_json(data)
    except Exception as exc:  # sigstore raises several unrelated types for bad bundles
        raise UpstreamError(f"The Sigstore bundle could not be read: {exc}") from exc


def verify_checksums_signature(
    checksums: bytes, bundle_data: bytes, version: str, verifier: ArtifactVerifier | None = None
) -> None:
    """Verify that `checksums` was signed by upstream's release workflow for `version`.

    Uses sigstore-python's `Verifier.verify_artifact` with an `Identity` policy that pins both
    the signer identity and the OIDC issuer, so nothing else is accepted.
    """
    policy = Identity(identity=expected_identity(version), issuer=OIDC_ISSUER)
    try:
        active = verifier or Verifier.production()
        active.verify_artifact(checksums, load_bundle(bundle_data), policy)
    except SigstoreError as exc:
        raise UpstreamError(
            f"Signature check of checksums.txt FAILED for v{version}: {exc}. "
            "The download was NOT installed."
        ) from exc


def _is_unsafe_member(member: tarfile.TarInfo) -> bool:
    """True for absolute paths, '..' parts, links and device files."""
    path = PurePosixPath(member.name)
    if path.is_absolute() or ".." in path.parts:
        return True
    return not (member.isfile() or member.isdir())


def extract_binary(tarball: Path, work_dir: Path) -> Path:
    """Extract only the `app-store-connect-mcp` file from `tarball` into `work_dir`.

    The whole archive is refused if any member is unsafe (absolute path, `..`, link, device),
    and extraction uses tarfile's strict `data` filter as a second line of defence.
    """
    try:
        with tarfile.open(tarball, mode="r:gz") as archive:
            members = archive.getmembers()
            if any(_is_unsafe_member(m) for m in members):
                raise UpstreamError("The upstream archive contains an unsafe entry; refusing it.")
            wanted = [m for m in members if PurePosixPath(m.name).parts[-1:] == (BINARY_NAME,)]
            if len(wanted) != 1 or not wanted[0].isfile():
                raise UpstreamError(f"The archive must contain exactly one file {BINARY_NAME}.")
            if wanted[0].size > MAX_BINARY_BYTES:
                raise UpstreamError(f"{BINARY_NAME} in the archive is unexpectedly large.")
            archive.extract(wanted[0], path=work_dir, filter="data")
            return work_dir / wanted[0].name
    except (tarfile.TarError, OSError) as exc:
        raise UpstreamError(f"Could not extract the upstream archive: {exc}") from exc


def _record_path(version: str) -> Path:
    return install_dir(version) / RECORD_NAME


def _recorded_sha(version: str) -> str | None:
    """Return the sha256 recorded when this version was verified, or None."""
    try:
        data = json.loads(_record_path(version).read_text(encoding="utf-8"))
        sha = data["sha256"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return sha if isinstance(sha, str) else None


def installed_binary(version: str) -> Path | None:
    """Return the installed binary if its sha256 still matches the verified record."""
    binary = binary_path(version)
    recorded = _recorded_sha(version)
    if recorded is None or not binary.is_file():
        return None
    if sha256_of(binary) != recorded:
        logger.warning("installed upstream binary no longer matches its record; reinstalling")
        return None
    return binary


def _write_record(version: str, sha: str, asset: str) -> None:
    record = {"sha256": sha, "asset": asset, "verified_at": datetime.now(UTC).isoformat()}
    target = _record_path(version)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(target)


def _download_and_verify(
    client: httpx.Client, version: str, work: Path, verifier: ArtifactVerifier | None
) -> tuple[Path, str]:
    """Download the three release files into `work`; return (extracted binary, asset name)."""
    asset = asset_name(version)
    sums_path, bundle_path, tar_path = work / "checksums.txt", work / "bundle.json", work / asset
    download(client, release_url(version, "checksums.txt"), sums_path, MAX_CHECKSUMS_BYTES)
    download(
        client, release_url(version, "checksums.txt.sigstore.json"), bundle_path, MAX_BUNDLE_BYTES
    )
    download(client, release_url(version, asset), tar_path, MAX_TARBALL_BYTES)
    checksums = sums_path.read_bytes()
    verify_checksums_signature(checksums, bundle_path.read_bytes(), version, verifier)
    expected = parse_checksums(checksums.decode("utf-8", errors="replace")).get(asset)
    if expected is None:
        raise UpstreamError(f"checksums.txt has no entry for {asset}.")
    if sha256_of(tar_path) != expected:
        raise UpstreamError(f"The downloaded {asset} does not match checksums.txt. Not installed.")
    return extract_binary(tar_path, work / "extracted"), asset


def ensure_upstream(
    version: str = PINNED_VERSION,
    *,
    client: httpx.Client | None = None,
    verifier: ArtifactVerifier | None = None,
) -> Path:
    """Return the path of a verified upstream binary for `version`, installing it if needed.

    Reuses the installed binary only when its sha256 matches the hash recorded at the time it
    was verified. Otherwise downloads and fully verifies a fresh copy.
    """
    check_version(version)
    existing = installed_binary(version)
    if existing is not None:
        return existing
    own_client = client is None
    http = client or httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True)
    try:
        target_dir = ensure_private_dir(install_dir(version))
        with tempfile.TemporaryDirectory(prefix="asc-download-") as tmp:
            work = Path(tmp)
            (work / "extracted").mkdir()
            extracted, asset = _download_and_verify(http, version, work, verifier)
            _record_path(version).unlink(missing_ok=True)  # no stale record for a new binary
            staged = target_dir / f"{BINARY_NAME}.tmp"
            staged.write_bytes(extracted.read_bytes())
            staged.chmod(0o755)
            staged.replace(target_dir / BINARY_NAME)
    finally:
        if own_client:
            http.close()
    binary = target_dir / BINARY_NAME
    _write_record(version, sha256_of(binary), asset)
    return binary
