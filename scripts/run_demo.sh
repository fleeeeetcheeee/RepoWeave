#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
live=false
resume_root=''
while [[ $# -gt 0 ]]; do
  case "$1" in
    --live) live=true ;;
    --resume)
      if [[ $# -lt 2 || -z "$2" ]]; then
        echo '--resume requires an existing demo directory'; exit 2
      fi
      resume_root="$2"; shift ;;
    *) echo 'Usage: bash scripts/run_demo.sh [--live] [--resume DEMO_DIRECTORY]'; exit 2 ;;
  esac
  shift
done
if [[ ! -x .venv/bin/python || ! -d web/node_modules ]]; then
  echo 'First run uv sync --locked and (cd web && npm ci).'; exit 2
fi
if [[ -n "$resume_root" ]]; then
  if [[ ! -f "$resume_root/state/runs.sqlite" || ! -f "$resume_root/owned-fixture/module_0.py" ]]; then
    echo 'Resume requires a saved demo database and its owned fixture'; exit 2
  fi
  demo_root=$(cd "$resume_root" && pwd)
else
  mkdir -p .repoweave/demos
  demo_root=$(mktemp -d "$PWD/.repoweave/demos/demo-XXXXXXXX")
  chmod 700 "$demo_root"
  .venv/bin/python scripts/dashboard_backend.py prepare --root "$demo_root"
fi
args=(--root "$demo_root")
if [[ "$live" == true ]]; then args+=(--live); fi
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
if [[ -n "$resume_root" ]]; then
  echo 'Open Demo to present a saved run, or start another experiment. API keys stay on the backend.'
else
  echo 'Capture the demo fixture in the browser. API keys stay on the backend.'
fi
echo 'Ctrl-C stops the services and preserves all run artifacts.'
# macOS ships Bash 3.2, without wait -n.
while true; do
  for pid in "${pids[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then wait "$pid"; exit 1; fi
  done
  sleep 1
done
