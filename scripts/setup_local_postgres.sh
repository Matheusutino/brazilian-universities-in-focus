#!/usr/bin/env bash
# Run a rootless PostgreSQL 16 for this project as a systemd user service.
#
# Binaries come from PG_BIN, from pg_ctl on PATH, or from the pgserver wheel
# installed under the cluster home. Everything lives in the project's data/
# directory (on /exp). PostgreSQL accepts NFS mounted with "hard" and server
# locking; the Unix socket cannot live on NFS, so it goes to a local directory.
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
pg_home="${UNIVERSITY_NEWS_PG_HOME:-$project_root/data/postgres}"
socket_dir="${UNIVERSITY_NEWS_PG_SOCKET_DIR:-/tmp}"
pg_data="$pg_home/data"
pg_port="${UNIVERSITY_NEWS_PG_PORT:-5432}"
database="university_news"
role="university_news"
unit_name="university-news-postgres.service"
user_unit_root="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
env_file="$project_root/.env"

if [[ -n "${PG_BIN:-}" ]]; then
  pg_bin="$PG_BIN"
elif command -v pg_ctl >/dev/null 2>&1; then
  pg_bin=$(dirname "$(command -v pg_ctl)")
else
  if [[ ! -x "$pg_home/venv/bin/python" ]]; then
    echo "Installing portable PostgreSQL binaries (pgserver) into $pg_home/venv"
    uv venv --quiet --python 3.12 "$pg_home/venv"
    uv pip install --quiet --python "$pg_home/venv/bin/python" pgserver
  fi
  pg_bin=$(find "$pg_home/venv" -path '*pgserver/pginstall/bin' -type d | head -n 1)
fi
if [[ ! -x "$pg_bin/postgres" ]]; then
  echo "PostgreSQL binaries not found in $pg_bin" >&2
  exit 1
fi
echo "Using $("$pg_bin/postgres" --version) from $pg_bin"

if [[ ! -f "$pg_data/PG_VERSION" ]]; then
  mkdir -p "$pg_home"
  "$pg_bin/initdb" --pgdata="$pg_data" --username="$USER" \
    --auth-local=peer --auth-host=scram-sha-256 --encoding=UTF8 --locale=C.UTF-8 \
    >/dev/null
  cat >>"$pg_data/postgresql.conf" <<EOF

# university-news
listen_addresses = '127.0.0.1'
port = $pg_port
unix_socket_directories = '$socket_dir'
EOF
  echo "Initialized cluster in $pg_data"
fi

mkdir -p "$user_unit_root"
cat >"$user_unit_root/$unit_name" <<EOF
[Unit]
Description=University news PostgreSQL (rootless)

[Service]
Type=simple
ExecStart=$pg_bin/postgres -D $pg_data
ExecReload=/bin/kill -HUP \$MAINPID
KillSignal=SIGINT
TimeoutStopSec=120
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now "$unit_name" >/dev/null

for _ in $(seq 1 30); do
  "$pg_bin/pg_isready" --host="$socket_dir" --port="$pg_port" --quiet && break
  sleep 1
done
"$pg_bin/pg_isready" --host="$socket_dir" --port="$pg_port"

psql_admin=("$pg_bin/psql" --host="$socket_dir" --port="$pg_port" --dbname=postgres
  --no-psqlrc --quiet --tuples-only --no-align --set=ON_ERROR_STOP=1)

if grep -Eq '^DATABASE_URL=.+$' "$env_file" 2>/dev/null \
  && ! grep -Eq '^DATABASE_URL=.*change-me' "$env_file"; then
  echo "DATABASE_URL already set in $env_file; leaving credentials unchanged"
else
  password=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
  if [[ -z $("${psql_admin[@]}" -c "SELECT 1 FROM pg_roles WHERE rolname = '$role'") ]]; then
    "${psql_admin[@]}" -c "CREATE ROLE $role LOGIN PASSWORD '$password'"
  else
    "${psql_admin[@]}" -c "ALTER ROLE $role PASSWORD '$password'"
  fi
  url="postgresql://$role:$password@127.0.0.1:$pg_port/$database"
  if grep -Eq '^DATABASE_URL=' "$env_file" 2>/dev/null; then
    sed -i "s|^DATABASE_URL=.*$|DATABASE_URL=$url|" "$env_file"
  else
    printf '\nDATABASE_URL=%s\n' "$url" >>"$env_file"
  fi
  echo "Wrote DATABASE_URL to $env_file"
fi

for name in "$database" "${database}_test"; do
  if [[ -z $("${psql_admin[@]}" -c "SELECT 1 FROM pg_database WHERE datname = '$name'") ]]; then
    "${psql_admin[@]}" -c "CREATE DATABASE $name OWNER $role"
    echo "Created database $name"
  fi
done

cd "$project_root"
uv run university-news-db init
echo "PostgreSQL is running as $unit_name (data: $pg_data)"
