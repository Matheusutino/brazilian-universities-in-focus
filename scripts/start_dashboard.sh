#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
log_directory="$project_root/data/logs"
log_path="${DASHBOARD_LOG:-$log_directory/dashboard.log}"
pid_path="${DASHBOARD_PID_FILE:-$log_directory/dashboard.pid}"
address="${DASHBOARD_ADDRESS:-127.0.0.1}"
port="${DASHBOARD_PORT:-8501}"

if command -v uv >/dev/null 2>&1; then
  uv_binary="$(command -v uv)"
elif [[ -x /home/utinomatheus/.local/bin/uv ]]; then
  uv_binary="/home/utinomatheus/.local/bin/uv"
else
  echo "uv was not found in PATH or at /home/utinomatheus/.local/bin/uv" >&2
  exit 1
fi

if [[ -f "$pid_path" ]]; then
  previous_pid="$(<"$pid_path")"
  if [[ "$previous_pid" =~ ^[0-9]+$ ]] && kill -0 "$previous_pid" 2>/dev/null; then
    echo "Dashboard is already running with PID $previous_pid" >&2
    exit 1
  fi
fi

mkdir -p "$log_directory"
cd "$project_root"
printf 'Dashboard requested at %s\n' "$(date --iso-8601=seconds)" >"$log_path"
nohup "$uv_binary" run streamlit run src/university_news/dashboard/app.py \
  --server.address "$address" \
  --server.port "$port" \
  >>"$log_path" 2>&1 </dev/null &
pid="$!"
echo "$pid" >"$pid_path"

echo "Dashboard started."
echo "PID: $pid"
echo "URL: http://$address:$port"
echo "Log: $log_path"
