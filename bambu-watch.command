#!/bin/bash
# Run the menu bar app from Terminal, which already has Local Network access.
# macOS denies that permission to a plain launchd agent, so signed pause/resume
# over the printer's LAN address only works from here (or after granting the
# permission to the app in System Settings -> Privacy & Security -> Local Network).
set -euo pipefail
cd "$(dirname "$0")"
exec .venv/bin/bambu-watch "$@"
