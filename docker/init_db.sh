#!/usr/bin/env bash
# Build-time database bootstrap.
#
# Initializes a Postgres cluster, applies ddl.sql, bulk-loads the generated
# CSVs, then shuts down cleanly -- so the populated cluster is committed into
# the image layer rather than created on first run.
set -euo pipefail

DDL_FILE=${DDL_FILE:-/opt/nl2sql/ddl.sql}
LOAD_SQL=${LOAD_SQL:-/csv/_load.sql}

initdb -D "$PGDATA" \
  --username=postgres \
  --auth-local=trust \
  --auth-host=scram-sha-256 \
  --encoding=UTF8

# The stock image's postgresql.conf.sample already sets listen_addresses='*';
# this is the matching host rule the runtime entrypoint adds when it does the
# initialization itself.
printf 'host all all all scram-sha-256\n' >> "$PGDATA/pg_hba.conf"

# Durability settings are relaxed only for this throwaway build-time server;
# they are command-line overrides, so the shipped postgresql.conf keeps the
# normal defaults.
pg_ctl -D "$PGDATA" -w \
  -o "-c listen_addresses='' -c fsync=off -c full_page_writes=off -c synchronous_commit=off" \
  start

super() { psql -v ON_ERROR_STOP=1 --username=postgres "$@"; }

super --dbname=postgres --command="ALTER ROLE postgres PASSWORD '${DB_PASSWORD}'"
super --dbname=postgres --command="CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASSWORD}'"
super --dbname=postgres --command="CREATE DATABASE ${DB_NAME} OWNER ${DB_USER}"
super --dbname="${DB_NAME}" --command="ALTER SCHEMA public OWNER TO ${DB_USER}"

psql -v ON_ERROR_STOP=1 --username="${DB_USER}" --dbname="${DB_NAME}" --file="$DDL_FILE"

# Server-side COPY (reads /csv directly) rather than \copy: same container,
# no client round-trip.
super --dbname="${DB_NAME}" --file="$LOAD_SQL"
super --dbname="${DB_NAME}" --command="VACUUM ANALYZE"

pg_ctl -D "$PGDATA" -m fast -w stop
