#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
log_directory="$project_root/data/logs"
log_path="${INDEX_LOG:-$log_directory/lancedb-index-gpu.log}"
pid_path="${INDEX_PID_FILE:-$log_directory/lancedb-index-gpu.pid}"
threads="${INDEX_THREADS:-8}"
batch_size="${INDEX_BATCH_SIZE:-8}"
gpu_layers="${EMBEDDING_GPU_LAYERS:--1}"

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
    echo "Indexing is already running with PID $previous_pid" >&2
    exit 1
  fi
fi

mkdir -p "$log_directory"
cd "$project_root"
printf 'Indexing requested at %s\n' "$(date --iso-8601=seconds)" >"$log_path"
nohup "$uv_binary" run university-news-index \
  --recreate \
  --gpu-layers "$gpu_layers" \
  --threads "$threads" \
  --batch-size "$batch_size" \
  >>"$log_path" 2>&1 </dev/null &
pid="$!"
echo "$pid" >"$pid_path"

echo "GPU indexing started."
echo "PID: $pid"
echo "Log: $log_path"
