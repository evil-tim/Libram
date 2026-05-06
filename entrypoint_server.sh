#!/bin/bash

set -euo pipefail

INSTALL_DIR="/app"
HOST="${HOST:-0.0.0.0}"

cd "$INSTALL_DIR" || { echo "Failed to cd to $INSTALL_DIR"; exit 1; }

exec uv run fastapi run server.py --app combined_app --host "$HOST" --port 7778
