#!/bin/bash

set -euo pipefail

INSTALL_DIR="/app"

cd "$INSTALL_DIR" || { echo "Failed to cd to $INSTALL_DIR"; exit 1; }

exec uv run cli_scheduler.py
