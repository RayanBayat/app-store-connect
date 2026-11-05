"""App Store Connect in Claude, safely: an MCP server for Apple's App Store Connect API."""

from importlib.metadata import PackageNotFoundError, version
from typing import Final

# The one place the name is written: the package name on PyPI, the MCP server name, the folder
# name for saved files and the command name all use it.
APP_NAME: Final = "app-store-connect"

try:
    __version__ = version(APP_NAME)
except PackageNotFoundError:  # running from a source tree that is not installed
    __version__ = "0.0.0"
