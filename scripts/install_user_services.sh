#!/usr/bin/env bash
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
uv_path=$(command -v uv)
template_root="$project_root/deploy/systemd"
generated_root="$template_root/generated"
user_unit_root="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

if ! grep -Eq '^DATABASE_URL=.+$' "$project_root/.env"; then
  echo "DATABASE_URL must be configured in $project_root/.env" >&2
  exit 1
fi

cd "$project_root"
"$uv_path" run university-news-db status >/dev/null

mkdir -p "$generated_root" "$user_unit_root"

for template in "$template_root"/*.service.in; do
  output="$generated_root/$(basename "${template%.in}")"
  sed \
    -e "s|__PROJECT_ROOT__|$project_root|g" \
    -e "s|__UV__|$uv_path|g" \
    "$template" > "$output"
  install -m 0644 "$output" "$user_unit_root/$(basename "$output")"
done

for timer in "$template_root"/*.timer; do
  install -m 0644 "$timer" "$user_unit_root/$(basename "$timer")"
done

# One enrichment worker per provider in [enrichment].providers.
mapfile -t providers < <("$uv_path" run university-news-config enrichment providers \
  | python3 -c 'import json, sys; print("\n".join(json.load(sys.stdin)))')
enrichment_units=()
for provider in "${providers[@]}"; do
  enrichment_units+=("university-news-enrichment-worker@$provider.service")
done

systemctl --user daemon-reload
# Providers removed from [enrichment].providers stop running.
for unit in $(systemctl --user list-unit-files 'university-news-enrichment-worker@*' --no-legend | awk '{print $1}'); do
  provider="${unit#university-news-enrichment-worker@}"
  provider="${provider%.service}"
  if [[ ! " ${providers[*]} " =~ " ${provider} " ]]; then
    systemctl --user disable --now "$unit"
  fi
done
systemctl --user enable --now \
  university-news-core-worker.service \
  "${enrichment_units[@]}" \
  university-news-embedding-worker.service \
  university-news-site.service \
  university-news-daily.timer \
  university-news-publish.timer \
  university-news-backup.timer

systemctl --user --no-pager status university-news-daily.timer
