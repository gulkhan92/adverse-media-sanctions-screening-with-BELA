#!/usr/bin/env bash
# Local development PostgreSQL 16+ cluster with pgvector (Homebrew). Usage: dev_db.sh start|stop|status|psql
set -euo pipefail
PGBIN="${PGBIN:-/opt/homebrew/opt/postgresql@17/bin}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PGDATA="${PGDATA:-$ROOT/.pgdata}"
PORT="${ATLAS_DB_PORT:-54329}"
DB="${ATLAS_DB_NAME:-atlas}"

case "${1:-}" in
  start)
    if [ ! -d "$PGDATA" ]; then
      "$PGBIN/initdb" -D "$PGDATA" -U atlas --auth=trust -E UTF8 >/dev/null
      printf "port=%s\nlisten_addresses='127.0.0.1'\nshared_buffers=1GB\nmaintenance_work_mem=1GB\nwork_mem=64MB\n" "$PORT" >> "$PGDATA/postgresql.conf"
    fi
    "$PGBIN/pg_ctl" -D "$PGDATA" -l "$PGDATA/server.log" -w start
    "$PGBIN/createdb" -h 127.0.0.1 -p "$PORT" -U atlas "$DB" 2>/dev/null || true
    ;;
  stop) "$PGBIN/pg_ctl" -D "$PGDATA" stop ;;
  status) "$PGBIN/pg_ctl" -D "$PGDATA" status ;;
  psql) shift; "$PGBIN/psql" -h 127.0.0.1 -p "$PORT" -U atlas "$DB" "$@" ;;
  *) echo "usage: $0 start|stop|status|psql" >&2; exit 2 ;;
esac
