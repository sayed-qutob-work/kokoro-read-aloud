#!/usr/bin/env bash
# Regenerate requirements.txt (CPU) and requirements-cuda.txt (NVIDIA) from
# uv.lock, for installs that use pip: Windows, until it gets `setup`.
# Run it after every change to uv.lock, and commit the two files with it.
#
# Every package is pinned to the version and SHA-256 in the lock, so pip
# installs the measured set instead of resolving indirect dependencies
# itself (it picked huggingface-hub 2.x, AUDIT 2026-10-17). The markers on
# each line keep the files valid on Linux and Windows alike.
#
# The index lines are written here, not by `uv export --emit-index-url`.
# That writes only PyPI, because pyproject.toml ties the PyTorch index to
# torch alone, which this format cannot say. And the order matters: in a
# requirements file, --index-url REPLACES the index list, extras read before
# it included, so it must come first. Hash-checking keeps pip on the locked
# files even though the PyTorch index also hosts other packages.

set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
UV="${UV:-$(command -v uv || echo "${XDG_DATA_HOME:-$HOME/.local/share}/kokoro-read-aloud/bin/uv")}"

export_one() {  # $1 = extra, $2 = output file, $3 = PyTorch index, $4 = what
  {
    cat <<HEADER
# $4.
# Every package is pinned to its version and SHA-256 in uv.lock, the set
# measured in docs/AUDIT.md. Install one of the two files, not both:
#
#   env\\Scripts\\python.exe -m pip install -r $2
#
# Generated from uv.lock by tools/export-requirements.sh -- do not edit.
# On Linux, linux/kokoroctl setup installs the same set with uv.
--index-url https://pypi.org/simple
--extra-index-url $3

HEADER
    env -u VIRTUAL_ENV UV_NO_CONFIG=1 "$UV" export --frozen --extra "$1" \
      --no-header --no-annotate --format requirements-txt
  } > "$2.tmp"
  mv -- "$2.tmp" "$2"
  echo "wrote $2 ($(grep -c '^[a-z0-9]' "$2") packages)"
}

export_one cpu requirements.txt https://download.pytorch.org/whl/cpu \
  "CPU install (no NVIDIA GPU)"
export_one cuda requirements-cuda.txt https://download.pytorch.org/whl/cu126 \
  "NVIDIA GPU install (torch built for CUDA 12.6)"
