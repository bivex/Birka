#!/usr/bin/env bash
# Stop the running Birka process (and its launcher, if any).
#
# Usage:
#   ./stopper.sh           # graceful SIGTERM, escalate to SIGKILL if needed
#   ./stopper.sh -9        # force-kill immediately
#   ./stopper.sh --all     # also kill restarter.sh launchers
#
# Matches running processes by the project path so it never touches
# unrelated python processes.
set -euo pipefail

PROJECT_ROOT="/Volumes/External/Code/Birka"
FORCE=0
ALL=0

for arg in "$@"; do
  case "$arg" in
    -9|--kill|-f|--force) FORCE=1 ;;
    --all) ALL=1 ;;
    -h|--help)
      sed -n '2,11p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      exit 1
      ;;
  esac
done

# PIDs of the Birka python process (src/main.py under the project root).
mapfile -t PIDS < <(pgrep -f "python3 ${PROJECT_ROOT}/src/main.py" || true)

if [[ ${ALL} -eq 1 ]]; then
  # restarter.sh instances (if any) — killed before the python process so it
  # doesn't immediately respawn it.
  mapfile -t RPIDS < <(pgrep -f "restarter.sh" || true)
  if [[ ${#RPIDS[@]} -gt 0 ]]; then
    echo "[birka] stopping restarter.sh (${RPIDS[*]})"
    kill "${RPIDS[@]}" 2>/dev/null || true
    sleep 1
    for pid in "${RPIDS[@]}"; do
      kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
    done
  fi
fi

if [[ ${#PIDS[@]} -eq 0 ]]; then
  echo "[birka] not running"
  exit 0
fi

if [[ ${FORCE} -eq 1 ]]; then
  echo "[birka] force-killing (${PIDS[*]})"
  kill -9 "${PIDS[@]}" 2>/dev/null || true
  exit 0
fi

echo "[birka] stopping (${PIDS[*]})"
kill "${PIDS[@]}" 2>/dev/null || true

# Wait up to ~5s for a clean exit, escalate to SIGKILL.
for _ in $(seq 1 10); do
  sleep 0.5
  alive=()
  for pid in "${PIDS[@]}"; do
    kill -0 "$pid" 2>/dev/null && alive+=("$pid")
  done
  PIDS=("${alive[@]:-}")
  [[ ${#PIDS[@]} -eq 0 ]] && break
done

if [[ ${#PIDS[@]} -gt 0 ]]; then
  echo "[birka] did not exit, force-killing (${PIDS[*]})"
  kill -9 "${PIDS[@]}" 2>/dev/null || true
fi

echo "[birka] stopped"
