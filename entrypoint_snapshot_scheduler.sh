#!/bin/bash

set -euo pipefail

INSTALL_DIR="/app"

cd "$INSTALL_DIR" || { echo "Failed to cd to $INSTALL_DIR" >&2; exit 1; }

exec uv run cli_snapshot_scheduler.py
