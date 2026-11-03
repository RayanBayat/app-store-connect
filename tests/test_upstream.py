"""Tests for upstream.py: naming, checksums, safe extraction, verification failure paths."""

from __future__ import annotations

import hashlib
import io
import platform
import stat
import sys
import tarfile
from pathlib import Path
from typing import cast

import httpx
import pytest
from sigstore.errors import VerificationError
from sigstore.models import Bundle
from sigstore.verify.policy import VerificationPolicy

from app_store_connect import upstream
from app_store_connect.upstream import UpstreamError, ensure_upstream

VERSION = "0.22.0"
BINARY_BYTES = b"#!/bin/sh\necho fake upstream\n"
ASSET = upstream.asset_name(VERSION, "linux", "x86_64")


def make_tarball(members: list[tuple[str, bytes, bytes]] | None = None) -> bytes:
    """Build a .tar.gz. Each member is (name, content, tar type; b'0' file, b'2' symlink)."""
    entries = members or [("LICENSE", b"MIT", b"0"), (upstream.BINARY_NAME, BINARY_BYTES, b"0")]
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content, kind in entries:
            info = tarfile.TarInfo(name)
            info.type = kind
            info.mode = 0o755
            if kind == b"2":
                info.linkname = "/etc/passwd"
                archive.addfile(info)
            else:
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


class FakeVerifier:
    """Records what it was asked to verify; raises when told to."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[bytes, VerificationPolicy]] = []

    def verify_artifact(self, input_: bytes, bundle: Bundle, policy: VerificationPolicy) -> None:
        self.calls.append((input_, policy))
        if self.fail:
            raise VerificationError("bad signature")


@pytest.fixture(autouse=True)
def fake_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(upstream, "load_bundle", lambda _data: cast(Bundle, object()))


def release_client(
    tarball: bytes, *, checksum_override: str | None = None
) -> tuple[httpx.Client, list[str]]:
    """A client serving a fake release; also returns the list of requested paths."""
    digest = checksum_override or hashlib.sha256(tarball).hexdigest()
    files = {
        "checksums.txt": f"{digest}  {ASSET}\n".encode(),
        "checksums.txt.sigstore.json": b"{}",
        ASSET: tarball,
    }
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        name = request.url.path.rsplit("/", 1)[-1]
        if name in files and f"/v{VERSION}/" in request.url.path:
            return httpx.Response(200, content=files[name])
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


@pytest.fixture(autouse=True)
def linux_amd64(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("linux", "x86_64", "app-store-connect-mcp_0.22.0_linux_amd64.tar.gz"),
        ("linux", "aarch64", "app-store-connect-mcp_0.22.0_linux_arm64.tar.gz"),
        ("darwin", "arm64", "app-store-connect-mcp_0.22.0_darwin_arm64.tar.gz"),
        ("darwin", "x86_64", "app-store-connect-mcp_0.22.0_darwin_amd64.tar.gz"),
    ],
)
def test_asset_name(system: str, machine: str, expected: str) -> None:
    assert upstream.asset_name("0.22.0", system, machine) == expected


@pytest.mark.parametrize(("system", "machine"), [("win32", "x86_64"), ("linux", "riscv64")])
def test_asset_name_unsupported_platform(system: str, machine: str) -> None:
    with pytest.raises(UpstreamError, match="Unsupported platform"):
        upstream.asset_name("0.22.0", system, machine)


@pytest.mark.parametrize("bad", ["../0.22.0", "0.22", "v0.22.0", "0.22.0/../x", ""])
def test_version_must_be_plain_x_y_z(bad: str) -> None:
    with pytest.raises(UpstreamError, match=r"X\.Y\.Z"):
        upstream.check_version(bad)


def test_expected_identity_is_exact() -> None:
    assert upstream.expected_identity("0.22.0") == (
        "https://github.com/idoo/app-store-connect-mcp/.github/workflows/release.yml"
        "@refs/tags/v0.22.0"
    )


def test_parse_checksums_skips_junk() -> None:
    good = "a" * 64
    text = f"{good}  file.tar.gz\nnot a checksum line\n{'b' * 63}  short\n{good} *star.zip\n"
    assert upstream.parse_checksums(text) == {"file.tar.gz": good, "star.zip": good}


def test_ensure_upstream_installs_and_records_sha(isolated_xdg: Path) -> None:
    client, _ = release_client(make_tarball())
    verifier = FakeVerifier()
    path = ensure_upstream(VERSION, client=client, verifier=verifier)
    expected_dir = isolated_xdg / "data" / "app-store-connect" / "upstream" / VERSION
    assert path == expected_dir / "app-store-connect-mcp"
    assert path.read_bytes() == BINARY_BYTES
    assert stat.S_IMODE(path.stat().st_mode) == 0o755
    assert stat.S_IMODE(expected_dir.stat().st_mode) == 0o700
    assert upstream._recorded_sha(VERSION) == hashlib.sha256(BINARY_BYTES).hexdigest()
    assert len(verifier.calls) == 1


def test_verifier_gets_exact_identity_and_issuer() -> None:
    client, _ = release_client(make_tarball())
    verifier = FakeVerifier()
    ensure_upstream(VERSION, client=client, verifier=verifier)
    policy = verifier.calls[0][1]
    assert policy._identity == upstream.expected_identity(VERSION)  # type: ignore[attr-defined]
    assert policy._issuer._value == upstream.OIDC_ISSUER  # type: ignore[attr-defined]


def test_second_start_reuses_binary_without_network() -> None:
    client, _ = release_client(make_tarball())
    first = ensure_upstream(VERSION, client=client, verifier=FakeVerifier())
    offline = httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(500)))
    assert ensure_upstream(VERSION, client=offline, verifier=FakeVerifier(fail=True)) == first


def test_tampered_binary_is_reinstalled() -> None:
    client, seen = release_client(make_tarball())
    path = ensure_upstream(VERSION, client=client, verifier=FakeVerifier())
    path.write_bytes(b"evil")
    again = ensure_upstream(VERSION, client=client, verifier=FakeVerifier())
    assert again.read_bytes() == BINARY_BYTES
    assert len(seen) == 6  # three downloads, twice


def test_signature_failure_never_returns_binary(isolated_xdg: Path) -> None:
    client, _ = release_client(make_tarball())
    with pytest.raises(UpstreamError, match="Signature check"):
        ensure_upstream(VERSION, client=client, verifier=FakeVerifier(fail=True))
    assert not upstream.binary_path(VERSION).exists()
    assert upstream.installed_binary(VERSION) is None


def test_checksum_mismatch_refused() -> None:
    client, _ = release_client(make_tarball(), checksum_override="0" * 64)
    with pytest.raises(UpstreamError, match=r"does not match checksums\.txt"):
        ensure_upstream(VERSION, client=client, verifier=FakeVerifier())
    assert not upstream.binary_path(VERSION).exists()


def test_missing_checksum_entry_refused() -> None:
    tarball = make_tarball()
    files = {
        "checksums.txt": f"{'a' * 64}  other.tar.gz\n".encode(),
        "checksums.txt.sigstore.json": b"{}",
        ASSET: tarball,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=files[request.url.path.rsplit("/", 1)[-1]])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(UpstreamError, match="no entry"):
        ensure_upstream(VERSION, client=client, verifier=FakeVerifier())


def test_download_error_is_clear() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(404)))
    with pytest.raises(UpstreamError, match="Could not download"):
        ensure_upstream(VERSION, client=client, verifier=FakeVerifier())


def test_download_size_limit(tmp_path: Path) -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, content=b"x" * 100))
    )
    with pytest.raises(UpstreamError, match="larger than"):
        upstream.download(client, "https://example.com/f", tmp_path / "f", 10)


def test_download_refuses_non_https(tmp_path: Path) -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, content=b"x"))
    )
    with pytest.raises(UpstreamError, match="non-https"):
        upstream.download(client, "http://example.com/f", tmp_path / "f", 10)


@pytest.mark.parametrize(
    "members",
    [
        [("../evil", b"x", b"0"), (upstream.BINARY_NAME, BINARY_BYTES, b"0")],
        [("/abs/evil", b"x", b"0"), (upstream.BINARY_NAME, BINARY_BYTES, b"0")],
        [(upstream.BINARY_NAME, b"", b"2")],
        [("link", b"", b"2"), (upstream.BINARY_NAME, BINARY_BYTES, b"0")],
        [("LICENSE", b"MIT", b"0")],
        [(upstream.BINARY_NAME, b"a", b"0"), ("sub/" + upstream.BINARY_NAME, b"b", b"0")],
    ],
    ids=["traversal", "absolute", "binary-is-symlink", "other-symlink", "no-binary", "duplicate"],
)
def test_extraction_refuses_unsafe_archives(
    tmp_path: Path, members: list[tuple[str, bytes, bytes]]
) -> None:
    tarball = tmp_path / "a.tar.gz"
    tarball.write_bytes(make_tarball(members))
    work = tmp_path / "out"
    work.mkdir()
    with pytest.raises(UpstreamError):
        upstream.extract_binary(tarball, work)
    assert list(work.iterdir()) == []


def test_extraction_extracts_only_the_binary(tmp_path: Path) -> None:
    tarball = tmp_path / "a.tar.gz"
    tarball.write_bytes(make_tarball())
    work = tmp_path / "out"
    work.mkdir()
    result = upstream.extract_binary(tarball, work)
    assert result.read_bytes() == BINARY_BYTES
    assert [p.name for p in work.iterdir()] == [upstream.BINARY_NAME]


def test_corrupt_tarball_is_a_clear_error(tmp_path: Path) -> None:
    tarball = tmp_path / "a.tar.gz"
    tarball.write_bytes(b"not a tarball")
    with pytest.raises(UpstreamError, match="Could not extract"):
        upstream.extract_binary(tarball, tmp_path)


def test_bad_bundle_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()  # drop the fake loader to exercise the real parser
    with pytest.raises(UpstreamError, match="could not be read"):
        upstream.load_bundle(b"{}")
