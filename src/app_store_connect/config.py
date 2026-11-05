"""Read and validate the settings the user passes as environment variables.

Why this is strict: the server holds a key that can change a real App Store listing.
We would rather refuse to start with a clear message than run with a half-safe setup
(for example a private key that other users on the machine can read).

Nothing in this module ever prints the key contents, the issuer ID, or more than the
last 4 characters of the key ID.
"""

from __future__ import annotations

import os
import re
import shlex
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

DEFAULT_REVIEW_DAYS: Final = 30
MIN_REVIEW_DAYS: Final = 1
MAX_REVIEW_DAYS: Final = 365
KEY_KINDS: Final = ("team", "individual")
TRUE_WORDS: Final = ("1", "true", "yes")
# Any permission bit for "group" or "others" (rwx for both) is too open.
OPEN_BITS: Final = 0o077
GROUP_OTHER_WRITE: Final = 0o022
# A real .p8 key is about 250 bytes; anything near this size is not a key.
MAX_KEY_BYTES: Final = 8 * 1024


class KeyFileError(Exception):
    """The key file is unsafe to read. The text never contains key material."""


class ConfigError(Exception):
    """The environment is missing something or is unsafe. The text is meant for a human."""


@dataclass(frozen=True, slots=True)
class Config:
    """Validated settings. Built once at start-up, never changed afterwards."""

    # repr=False: these must never show up if a Config (or anything holding one) is printed.
    # The key path is hidden too, because Apple's file name contains the key ID.
    key_id: str = field(repr=False)
    issuer_id: str = field(repr=False)
    private_key_path: Path = field(repr=False)
    key_kind: str = "team"
    vendor_number: str | None = None
    allow_writes: bool = False
    root: Path | None = None
    review_max_days: int = DEFAULT_REVIEW_DAYS

    @property
    def masked_key_id(self) -> str:
        """The key ID with all but the last 4 characters hidden, safe to print."""
        return mask_key_id(self.key_id)


def mask_key_id(key_id: str) -> str:
    """Return `key_id` as '******ABCD' so logs never show the whole value."""
    return "*" * max(len(key_id) - 4, 0) + key_id[-4:]


# Apple names downloaded keys AuthKey_<KEY ID>.p8, so the file name contains the key ID.
_KEY_FILE_NAME: Final = re.compile(r"(AuthKey_)([A-Za-z0-9]+)([A-Za-z0-9]{4})(\.p8)")


def masked_path(path: Path) -> str:
    """Return `path` for messages, hiding the key ID in an AuthKey_<ID>.p8 file name.

    Without this, the masked key ID shown elsewhere would be undone by the file name.
    """
    match = _KEY_FILE_NAME.fullmatch(path.name)
    if match is None:
        return str(path)
    hidden = "*" * len(match[2])
    return str(path.with_name(f"{match[1]}{hidden}{match[3]}{match[4]}"))


def chmod_target(path: Path) -> str:
    """Return a shell argument for fix-it hints that still matches the real file.

    A pattern like AuthKey_*ABCD.p8 lets people copy-paste the chmod command without
    the message spelling out the whole key ID.
    """
    match = _KEY_FILE_NAME.fullmatch(path.name)
    if match is None:
        return shlex.quote(str(path))
    return f"{shlex.quote(str(path.parent))}/{match[1]}*{match[3]}{match[4]}"


def parse_review_days(raw: str | None) -> int:
    """Return the allowed review age in days; anything invalid falls back to 30.

    Falling back (instead of failing) keeps a typo from locking the user out, and the
    default is the strictest value we recommend anyway.
    """
    try:
        value = int(raw or "")
    except ValueError:
        return DEFAULT_REVIEW_DAYS
    if MIN_REVIEW_DAYS <= value <= MAX_REVIEW_DAYS:
        return value
    return DEFAULT_REVIEW_DAYS


def is_truthy(raw: str | None) -> bool:
    """True only for the explicit words 1, true or yes (any case)."""
    return (raw or "").strip().lower() in TRUE_WORDS


def key_file_problem(info: os.stat_result, path: Path) -> str | None:
    """Return what is wrong with an already-opened key file's metadata, or None if it is fine.

    Used on the open file descriptor (fstat), so the answer is about the very file we read.
    """
    if not stat.S_ISREG(info.st_mode):
        return f"The key file {masked_path(path)} is not a regular file."
    if info.st_uid != os.getuid():
        return f"The key file {masked_path(path)} is not owned by you, so it cannot be trusted."
    if stat.S_IMODE(info.st_mode) & OPEN_BITS:
        return (
            f"The key file {masked_path(path)} can be read or changed by other users. "
            f"Fix it with: chmod 600 {chmod_target(path)}"
        )
    if info.st_size >= MAX_KEY_BYTES:
        return f"The key file {masked_path(path)} is larger than 8 KiB, so it is not a .p8 key."
    return None


def read_key_file(path: Path) -> bytes:
    """Open the key once, check the open file, and read it from that same descriptor.

    O_NONBLOCK keeps a FIFO swapped in for the key from hanging the server; the fstat then
    refuses it because it is not a regular file. Nothing is checked on the path and read
    later, so the file cannot be swapped between the check and the read.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as exc:
        raise KeyFileError(f"The key file {masked_path(path)} could not be opened.") from exc
    with os.fdopen(fd, "rb") as handle:
        problem = key_file_problem(os.fstat(handle.fileno()), path)
        if problem is not None:
            raise KeyFileError(problem)
        return handle.read(MAX_KEY_BYTES)


def check_key_file(path: Path) -> list[str]:
    """Return problems with the key file and its folder (empty list means fine)."""
    if not path.is_file():
        return [
            f"The key file {masked_path(path)} does not exist or is not a regular file. "
            "Check ASC_PRIVATE_KEY_PATH points to your AuthKey_XXXXXXXXXX.p8 file."
        ]
    problems: list[str] = []
    if problem := key_file_problem(path.stat(), path):
        problems.append(problem)
    folder = path.resolve().parent
    if stat.S_IMODE(folder.stat().st_mode) & OPEN_BITS:
        problems.append(
            f"The folder {folder} that holds the key can be opened by other users. "
            f"Fix it with: chmod 700 {folder} (or move the key to a private folder)."
        )
    return problems


def check_required(env: Mapping[str, str]) -> list[str]:
    """Return one message per missing required variable."""
    problems: list[str] = []
    for name, hint in (
        ("ASC_KEY_ID", "the Key ID shown next to your key in App Store Connect"),
        ("ASC_ISSUER_ID", "the Issuer ID shown at the top of the API keys page"),
        ("ASC_PRIVATE_KEY_PATH", "the path to your AuthKey_XXXXXXXXXX.p8 file"),
    ):
        if not env.get(name, "").strip():
            problems.append(f"{name} is not set. Set it to {hint}.")
    return problems


def load_config(env: Mapping[str, str]) -> Config:
    """Build a `Config` from `env`, or raise `ConfigError` listing every problem found."""
    problems = check_required(env)
    if env.get("ASC_PRIVATE_KEY"):
        problems.append(
            "ASC_PRIVATE_KEY is set, but this server never accepts the key contents because "
            "they would end up in your client's config file. Remove it and use "
            "ASC_PRIVATE_KEY_PATH (the path to the .p8 file) instead."
        )
    key_kind = env.get("ASC_KEY_KIND", "team").strip().lower() or "team"
    if key_kind not in KEY_KINDS:
        problems.append(f"ASC_KEY_KIND must be 'team' or 'individual', not {key_kind!r}.")
    raw_path = env.get("ASC_PRIVATE_KEY_PATH", "").strip()
    key_path = Path(raw_path).expanduser() if raw_path else None
    if key_path is not None:
        problems.extend(check_key_file(key_path))
    root = _load_root(env, problems)
    if problems or key_path is None:
        raise ConfigError(_format_problems(problems))
    return Config(
        key_id=env["ASC_KEY_ID"].strip(),
        issuer_id=env["ASC_ISSUER_ID"].strip(),
        private_key_path=key_path,
        key_kind=key_kind,
        vendor_number=env.get("ASC_VENDOR_NUMBER", "").strip() or None,
        allow_writes=is_truthy(env.get("ASC_ALLOW_WRITES")),
        root=root,
        review_max_days=parse_review_days(env.get("ASC_REVIEW_MAX_DAYS")),
    )


def _load_root(env: Mapping[str, str], problems: list[str]) -> Path | None:
    """Return ASC_ROOT as a path, adding a problem when it is not a folder."""
    raw = env.get("ASC_ROOT", "").strip()
    if not raw:
        return None
    root = Path(raw).expanduser()
    if not root.is_dir():
        problems.append(f"ASC_ROOT={raw} is not an existing folder.")
        return root
    real = root.resolve()
    if real in (Path(real.anchor), Path.home().resolve()):
        problems.append(
            f"ASC_ROOT={raw} is too broad: it is / or your home folder. Use a folder that holds "
            "only the screenshots."
        )
    elif real.stat().st_mode & GROUP_OTHER_WRITE:
        problems.append(
            f"ASC_ROOT={raw} is writable by other users, so files in it cannot be trusted. "
            f"Fix it with: chmod go-w {shlex.quote(str(real))}"
        )
    return root


def _format_problems(problems: list[str]) -> str:
    """Join problems into one message, one bullet each."""
    lines = ["app-store-connect cannot start:"]
    lines.extend(f"  - {problem}" for problem in problems)
    return "\n".join(lines)
