#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "${1:-}" != "" && "${1:-}" != "--live" ]]; then
  echo 'Usage: bash scripts/run_demo.sh [--live]'; exit 2
fi
if [[ ! -x .venv/bin/python || ! -d web/node_modules ]]; then
  echo 'First run uv sync --locked and (cd web && npm ci).'; exit 2
fi
mkdir -p .repoweave/demos
demo_root=$(mktemp -d "$PWD/.repoweave/demos/demo-XXXXXXXX")
chmod 700 "$demo_root"
.venv/bin/python scripts/dashboard_backend.py prepare --root "$demo_root"
args=(--root "$demo_root")
if [[ "${1:-}" == '--live' ]]; then args+=(--live); fi
pids=()
cleanup() {
  trap - EXIT INT TERM
  for pid in "${pids[@]}"; do kill -TERM "$pid" 2>/dev/null || true; done
  for pid in "${pids[@]}"; do wait "$pid" 2>/dev/null || true; done
}
trap cleanup EXIT
trap 'cleanup; exit 0' INT TERM
.venv/bin/python scripts/dashboard_backend.py api "${args[@]}" & pids+=("$!")
.venv/bin/python scripts/dashboard_backend.py worker "${args[@]}" & pids+=("$!")
(cd web && exec node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5173 --strictPort) & pids+=("$!")
echo "Dashboard: http://127.0.0.1:5173"
echo "Persisted demo: $demo_root"
echo 'Capture the demo fixture in the browser. API keys stay on the backend.'
echo 'Ctrl-C stops the services and preserves all run artifacts.'
# macOS ships Bash 3.2, without wait -n.
while true; do
  for pid in "${pids[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then wait "$pid"; exit 1; fi
  done
  sleep 1
done
