"""The two safety rules that must never be weakened: blocked tools and the child's environment.

1. BLOCKED_TOOLS: upstream tools this server never shows and never forwards.
2. build_child_env: the upstream process gets an ALLOWLIST of environment variables, so
   nothing from the user's shell (extra secrets, CI switches, test URLs) can leak into it or
   change how it behaves.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Final

from app_store_connect.config import Config
from app_store_connect.paths import ensure_private_dir, state_dir

# Found in the security audit of upstream v0.22.0.
BLOCKED_TOOLS: Final[dict[str, str]] = {
    "toolsets": (
        "it lets the model switch on extra tool groups (web login, admin, signing) "
        "without asking you"
    ),
    "web_login": "it opens an Apple ID sign-in, and the API key already covers what is needed",
    "asc_api_request": "it can send a raw request to any App Store Connect endpoint",
}

# The only upstream tool groups we switch on. Web, admin and signing groups stay off.
TOOLSETS: Final = "core,release,testflight,metadata,reviews,reports,screenshots"

# Variables copied from our own environment, unchanged, when they exist.
PASSTHROUGH_NAMES: Final = ("PATH", "HOME", "LANG", "TMPDIR")
PASSTHROUGH_PREFIXES: Final = ("LC_",)


def run_dir() -> Path:
    """Folder the upstream process runs in (private, and holds no pin files)."""
    return ensure_private_dir(state_dir() / "run")


def _private_subdir(name: str) -> Path:
    """Return a private subfolder of the state dir (for upstream's own config and state)."""
    return ensure_private_dir(state_dir() / name)


def blocked_message(name: str) -> str:
    """Explain why a blocked tool is refused."""
    return (
        f"The tool '{name}' is blocked by app-store-connect because {BLOCKED_TOOLS[name]}. "
        "It is never forwarded, whatever the user or model asks."
    )


def build_child_env(config: Config, parent: Mapping[str, str]) -> dict[str, str]:
    """Return the complete environment for the upstream process (nothing else is added).

    XDG_CONFIG_HOME and XDG_STATE_HOME point at private folders so the user's own upstream
    config file and its state are never read.
    """
    env = {
        name: value
        for name, value in parent.items()
        if name in PASSTHROUGH_NAMES or name.startswith(PASSTHROUGH_PREFIXES)
    }
    env["XDG_CONFIG_HOME"] = str(_private_subdir("upstream-config"))
    env["XDG_STATE_HOME"] = str(_private_subdir("upstream-state"))
    env["ASC_KEY_ID"] = config.key_id
    env["ASC_ISSUER_ID"] = config.issuer_id
    env["ASC_KEY_KIND"] = config.key_kind
    env["ASC_PRIVATE_KEY_PATH"] = str(config.private_key_path)
    if config.vendor_number:
        env["ASC_VENDOR_NUMBER"] = config.vendor_number
    if config.allow_writes:
        env["ASC_MCP_ALLOW_WRITES"] = "1"
    if config.root is not None:
        env["ASC_MCP_ROOT"] = str(config.root)
    env["ASC_MCP_TOOLSETS"] = TOOLSETS
    return env
