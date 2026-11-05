#!/usr/bin/env bash
# Dump the PostgreSQL database into data/backups/ (on /exp) and keep the newest
# BACKUP_KEEP dumps. The live cluster stays on local disk, where PostgreSQL is
# safe; the dumps are what lives on the shared filesystem.
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
backup_root="${BACKUP_DIR:-$project_root/data/backups}"
keep="${BACKUP_KEEP:-14}"

if [[ -z "${DATABASE_URL:-}" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$project_root/.env"
  set +a
fi
if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "DATABASE_URL is not set" >&2
  exit 1
fi

if command -v pg_dump >/dev/null 2>&1; then
  pg_dump_bin=$(command -v pg_dump)
else
  pg_dump_bin=$(find "${UNIVERSITY_NEWS_PG_HOME:-$project_root/data/postgres}" -path '*pginstall/bin/pg_dump' | head -n 1)
fi
if [[ ! -x "${pg_dump_bin:-}" ]]; then
  echo "pg_dump not found" >&2
  exit 1
fi

mkdir -p "$backup_root"
target="$backup_root/university_news-$(date +%Y%m%d-%H%M%S).dump"
"$pg_dump_bin" --format=custom --compress=6 --file="$target.tmp" "$DATABASE_URL"
mv "$target.tmp" "$target"
echo "Wrote $target ($(du -h "$target" | cut -f1))"

ls -1t "$backup_root"/university_news-*.dump | tail -n +"$((keep + 1))" | xargs -r rm -f
