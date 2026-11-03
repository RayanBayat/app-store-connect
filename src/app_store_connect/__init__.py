"""App Store Connect in Claude, safely: a guarded MCP proxy around the audited upstream server."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("app-store-connect")
except PackageNotFoundError:  # running from a source tree that is not installed
    __version__ = "0.0.0"
