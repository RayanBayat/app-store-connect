"""App Store Connect in Claude, safely: an MCP server for Apple's App Store Connect API."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("app-store-connect")
except PackageNotFoundError:  # running from a source tree that is not installed
    __version__ = "0.0.0"
