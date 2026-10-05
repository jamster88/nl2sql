#!/usr/bin/env bash
# Build-time database bootstrap.
#
# Initializes a Postgres cluster, applies ddl.sql, bulk-loads the generated
# CSVs, creates the agent's read-only role, then shuts down cleanly -- so the
# populated cluster is committed into the image layer rather than created on
# first run.
set -euo pipefail

DDL_FILE=${DDL_FILE:-/opt/nl2sql/ddl.sql}
LOAD_SQL=${LOAD_SQL:-/csv/_load.sql}
READER_SQL=${READER_SQL:-/opt/nl2sql/reader_role.sql}

initdb -D "$PGDATA" \
  --username=postgres \
  --auth-local=trust \
  --auth-host=scram-sha-256 \
  --encoding=UTF8

# The stock image's postgresql.conf.sample already sets listen_addresses='*';
# this is the host rule the stock entrypoint would add when it does the
# initialization itself -- except that it is `hostssl`: a password here is
# only ever accepted over TLS, which the image's own entrypoint turns on.
printf 'hostssl all all all scram-sha-256\n' >> "$PGDATA/pg_hba.conf"

# Durability settings are relaxed only for this throwaway build-time server;
# they are command-line overrides, so the shipped postgresql.conf keeps the
# normal defaults.
pg_ctl -D "$PGDATA" -w \
  -o "-c listen_addresses='' -c fsync=off -c full_page_writes=off -c synchronous_commit=off" \
  start

super() { psql -v ON_ERROR_STOP=1 --username=postgres "$@"; }

# No password for anyone at build time. The superuser never has one; the
# owner's and the reader's are the environment's, set by the image's
# entrypoint on every start.
super --dbname=postgres --command="CREATE ROLE ${DB_USER} LOGIN"
super --dbname=postgres --command="CREATE DATABASE ${DB_NAME} OWNER ${DB_USER}"
super --dbname="${DB_NAME}" --command="ALTER SCHEMA public OWNER TO ${DB_USER}"

# pg_trgm backs the Literal Matcher's fuzzy search over the literal catalog
# (arch4 section 4.3). It ships with the base image but is not installed by
# default, and only a superuser can install it -- the agent connects as a
# read-only role. The agent falls back to Python difflib when it is absent,
# so this makes matching better, not possible.
super --dbname="${DB_NAME}" --command="CREATE EXTENSION IF NOT EXISTS pg_trgm"

psql -v ON_ERROR_STOP=1 --username="${DB_USER}" --dbname="${DB_NAME}" --file="$DDL_FILE"

# Server-side COPY (reads /csv directly) rather than \copy: same container,
# no client round-trip.
super --dbname="${DB_NAME}" --file="$LOAD_SQL"
super --dbname="${DB_NAME}" --command="VACUUM ANALYZE"

# The agent reads through this role; only the DDL and the COPY above run as
# the owner.
super --dbname="${DB_NAME}" \
  -v reader="${DB_READER}" -v owner="${DB_USER}" \
  --file="$READER_SQL"

pg_ctl -D "$PGDATA" -m fast -w stop
