#!/usr/bin/env bash
# Build the two native pieces this app uses.
#
#   bridge/libbswbridge.dylib          our C ABI wrapper around Studio's plugin
#   vendor/.../h2d_network_bridge     signed-control bridge (upstream bambu-headless)
#   vendor/.../h2d_host_identity.dylib executable-path shim (upstream, AGPL)
#
# Requires Xcode Command Line Tools and nlohmann-json (brew install nlohmann-json).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"

echo "==> building bridge/libbswbridge.dylib"
clang++ -std=c++17 -O2 -shared -fPIC -o bridge/libbswbridge.dylib bridge/bsw_bridge.cpp

echo "==> building the signed-control bridge and host shim"
"$PYTHON" - <<'PY'
import sys
sys.path.insert(0, "vendor/bambu-headless")
import send_h2d_print as core
print(core.ensure_bridge())
PY

echo "==> done"
