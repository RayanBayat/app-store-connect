"""Where this tool keeps its files, following the XDG convention.

Everything lives in private (0700) folders because it includes downloaded binaries and
review records that other users on the machine have no business touching.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Final

APP_NAME: Final = "app-store-connect"
PRIVATE_DIR_MODE: Final = 0o700


def _base(env_name: str, fallback: tuple[str, ...], env: Mapping[str, str] | None) -> Path:
    """Return $env_name if set, else ~/<fallback>."""
    source = os.environ if env is None else env
    value = source.get(env_name)
    if value:
        return Path(value)
    return Path.home().joinpath(*fallback)


def state_dir(env: Mapping[str, str] | None = None) -> Path:
    """Folder for review records and run files (under XDG_STATE_HOME, else ~/.local/state)."""
    return _base("XDG_STATE_HOME", (".local", "state"), env) / APP_NAME


def ensure_private_dir(path: Path) -> Path:
    """Create `path` (and missing parents) and force mode 0700 on it and on each parent below it.

    Only folders at or below the app folder are tightened. We never chmod the user's own
    ~/.local or $XDG_* folders, so we walk up until we leave the app folder.
    """
    path.mkdir(parents=True, exist_ok=True)
    current = path
    while True:
        current.chmod(PRIVATE_DIR_MODE)
        if current.name == APP_NAME or current == current.parent:
            return path
        current = current.parent
