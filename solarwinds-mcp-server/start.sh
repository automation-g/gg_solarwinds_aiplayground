#!/bin/bash
cd "$(dirname "$0")"
exec /home/abigailcuadra/.local/bin/uv run --project . solarwinds-mcp
