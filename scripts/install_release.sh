#!/usr/bin/env bash
# Download, verify and install a release of app-store-connect-mcp.
#
# Usage: install_release.sh <version> [--extract-only DIR]
#
# Why so strict: the binary runs with App Store Connect credentials, so we
# verify both the checksum and the GitHub build attestation before using it,
# and abort (never install) if either check fails.
set -euo pipefail

REPO="idoo/app-store-connect-mcp"
BIN_NAME="app-store-connect-mcp"

usage() {
  echo "Usage: install_release.sh <version> [--extract-only DIR]"
  echo "  <version>  release without the leading v, e.g. 0.22.0"
  echo "  --extract-only DIR  verify and copy the binary to DIR, do not install"
  echo "  env ASC_MCP_INSTALL_DIR  install target (default: \$HOME/.local/bin)"
}

die() {
  echo "error: $*" >&2
  exit 1
}

# Map `uname -s` output to the os name used in release asset names.
detect_os() {
  case "$(uname -s)" in
    Linux) echo linux ;;
    Darwin) echo darwin ;;
    *) die "unsupported operating system: $(uname -s)" ;;
  esac
}

# Map `uname -m` output to the arch name used in release asset names.
detect_arch() {
  case "${1:-$(uname -m)}" in
    x86_64 | amd64) echo amd64 ;;
    aarch64 | arm64) echo arm64 ;;
    *) die "unsupported architecture: ${1:-$(uname -m)}" ;;
  esac
}

asset_name() {
  echo "${BIN_NAME}_${1}_${2}_${3}.tar.gz"
}

# Print the sha256 of a file on either Linux or macOS.
file_sha256() {
  if command -v sha256sum > /dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

# Verify the downloaded asset against checksums.txt (only the asset's line).
verify_checksum() {
  local dir="$1"
  if command -v sha256sum > /dev/null 2>&1; then
    (cd "$dir" && sha256sum -c --ignore-missing checksums.txt) || die "checksum verification failed"
  else
    (cd "$dir" && shasum -a 256 -c --ignore-missing checksums.txt) || die "checksum verification failed"
  fi
}

main() {
  local version="" extract_only=""
  while [ $# -gt 0 ]; do
    case "$1" in
      -h | --help) usage; exit 0 ;;
      --extract-only)
        [ $# -ge 2 ] || die "--extract-only needs a directory"
        extract_only="$2"; shift 2 ;;
      -*) usage >&2; die "unknown option: $1" ;;
      *) [ -z "$version" ] || die "only one version allowed"; version="${1#v}"; shift ;;
    esac
  done
  [ -n "$version" ] || { usage >&2; die "version is required"; }

  command -v gh > /dev/null 2>&1 || die "the GitHub CLI (gh) is required: https://cli.github.com"

  local os arch asset tmp
  os="$(detect_os)"
  arch="$(detect_arch)"
  asset="$(asset_name "$version" "$os" "$arch")"
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT

  echo "Downloading $asset (v$version)..."
  gh release download "v$version" -R "$REPO" -p "$asset" -p checksums.txt -D "$tmp" \
    || die "download failed (does release v$version exist for $os/$arch?)"

  verify_checksum "$tmp"

  echo "Verifying build attestation..."
  gh attestation verify "$tmp/$asset" --repo "$REPO" \
    --signer-workflow "$REPO/.github/workflows/release.yml" \
    --source-ref "refs/tags/v$version" \
    || die "attestation verification FAILED; refusing to install"

  tar -xzf "$tmp/$asset" -C "$tmp" "$BIN_NAME" || die "binary $BIN_NAME not found in archive"

  if [ -n "$extract_only" ]; then
    mkdir -p "$extract_only"
    install -m 755 "$tmp/$BIN_NAME" "$extract_only/$BIN_NAME"
    echo "Extracted verified binary to $extract_only/$BIN_NAME"
    return 0
  fi

  local dest="${ASC_MCP_INSTALL_DIR:-$HOME/.local/bin}"
  mkdir -p "$dest"
  install -m 755 "$tmp/$BIN_NAME" "$dest/$BIN_NAME"
  "$dest/$BIN_NAME" version
  echo "sha256: $(file_sha256 "$dest/$BIN_NAME")"
}

# Allow tests to `source` this file for its helper functions without running main.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
