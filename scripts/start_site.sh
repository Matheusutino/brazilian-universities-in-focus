#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if command -v uv >/dev/null 2>&1; then
  uv_binary="$(command -v uv)"
elif [[ -x /home/utinomatheus/.local/bin/uv ]]; then
  uv_binary="/home/utinomatheus/.local/bin/uv"
else
  echo "uv was not found in PATH or at /home/utinomatheus/.local/bin/uv" >&2
  exit 1
fi

log_directory="$project_root/$($uv_binary run university-news-config runtime log_directory)"
log_path="$log_directory/$($uv_binary run university-news-config runtime site_log)"
pid_path="$log_directory/$($uv_binary run university-news-config runtime site_pid)"
address="$($uv_binary run university-news-config site address)"
port="$($uv_binary run university-news-config site port)"

if [[ -f "$pid_path" ]]; then
  previous_pid="$(<"$pid_path")"
  if [[ "$previous_pid" =~ ^[0-9]+$ ]] && kill -0 "$previous_pid" 2>/dev/null; then
    echo "Site is already running with PID $previous_pid" >&2
    exit 1
  fi
fi

mkdir -p "$log_directory"
cd "$project_root"
# Static pages are rebuilt from PostgreSQL here and by the publish service.
"$uv_binary" run university-news-site build
printf 'Site requested at %s\n' "$(date --iso-8601=seconds)" >"$log_path"
nohup "$uv_binary" run university-news-site serve \
  --address "$address" \
  --port "$port" \
  >>"$log_path" 2>&1 </dev/null &
pid="$!"
echo "$pid" >"$pid_path"

echo "Site started."
echo "PID: $pid"
echo "URL: http://$address:$port"
echo "Log: $log_path"
