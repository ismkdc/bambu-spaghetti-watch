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

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
else
  PYTHON="${PYTHON:-python3}"
fi

if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "error: $PYTHON is older than 3.10; create .venv with Python 3.12 first" >&2
  exit 1
fi

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
