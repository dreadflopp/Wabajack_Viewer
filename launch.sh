#!/usr/bin/env sh
set -eu
viewer_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$viewer_dir/wabbajack_viewer.py" "$@"
