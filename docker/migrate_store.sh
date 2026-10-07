#!/usr/bin/env bash
# One runtime store's data, moved from the server it had before 6.3 into the
# database it has now (V6-40).
#
# Until 6.3 the feedback, corrections and completions stores were each a
# Postgres server of their own, on a volume of their own; since, they are
# databases in one server (the `stores` service). What people typed into
# them -- verdicts, corrections, completions -- is moved across once, by
# `launch.sh`, which runs this in the `storesmigrate` service (the stores'
# own image, as its postgres user) with the old volume mounted read-only at
# /legacy:
#
#   1. nothing to do when the old volume holds no database, or the new one
#      says it was already moved (a comment on the database);
#   2. refused when the new database already has tables: a store in use is
#      not merged into, and saying so is better than guessing;
#   3. the old server started on a copy -- the volume stays as it was --
#      listening on a socket in the copy and nowhere else;
#   4. its database dumped, and restored into the new one as the store's
#      owner, without the old owner or grants: the roles are the new
#      server's. Row-level security policies and the extensions are left
#      out -- the review service makes its policies again on every start,
#      and the one-shot made pgvector already -- because restoring them
#      would name roles and objects the owner does not own;
#   5. the new database marked as moved, and the old server stopped.
#
# The old volume is kept: launch.sh says how to remove it once you have
# looked. Snippets are not moved; their store is rebuilt from
# context_questions/sql_snippets.md on every start that finds it behind.
#
# Environment:
#   NL2SQL_DB            the store's database, the same name before and after
#   NL2SQL_OWNER         its owner in the new server, and its superuser in
#                        the old one (each old store's POSTGRES_USER)
#   NL2SQL_VOLUME        the old volume's name, for the marker and messages
#   NL2SQL_LEGACY        where the old volume is mounted (/legacy)
#   NL2SQL_TARGET        the new server's socket directory
#   NL2SQL_WORK          where the copy is made (/tmp)
set -euo pipefail

say() { echo "migrate_store: $*"; }
die() { echo "migrate_store: $*" >&2; exit 1; }

db="${NL2SQL_DB:?NL2SQL_DB is not set}"
owner="${NL2SQL_OWNER:?NL2SQL_OWNER is not set}"
volume="${NL2SQL_VOLUME:?NL2SQL_VOLUME is not set}"
legacy="${NL2SQL_LEGACY:-/legacy}/pgdata"
target="${NL2SQL_TARGET:-/run/nl2sql/sockets/stores}"
marker="nl2sql: migrated from $volume"

new() {  # new DATABASE SQL -- one value from the new server, as its superuser
    psql -h "$target" -U postgres -d "$1" -X -A -t -q -v ON_ERROR_STOP=1 -c "$2"
}

if [ ! -f "$legacy/PG_VERSION" ]; then
    say "$volume holds no database, so there is nothing to move"
    exit 0
fi
case "$(new postgres "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = '$db'")" in
    "nl2sql: migrated from"*)
        say "$db was moved from $volume already"
        exit 0 ;;
esac
tables="$(new "$db" "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")"
if [ "$tables" != 0 ]; then
    die "$db in the runtime stores already has $tables tables, so $volume is not merged into it. Move what you need by hand, then: docker volume rm $volume"
fi

work="$(mktemp -d "${NL2SQL_WORK:-/tmp}/legacy.XXXXXX")"
cp -a "$legacy/." "$work/"
chmod 700 "$work"
rm -f "$work/postmaster.pid"
stop() {
    pg_ctl -D "$work" -m fast -w -s stop >/dev/null 2>&1 || true
    rm -rf "$work"
}
trap stop EXIT
# Quietly (client_min_messages, the server's own default): an old store made
# by postgres:18 and opened here, in the stores' image, is told on every
# connection that its collation version differs -- which matters to its
# indexes, and the restore builds every index anew under this server's.
# Postgres says so before it reads a client's own options, so PGOPTIONS
# cannot quiet it; the server's default can.
pg_ctl -D "$work" -w -s -l "$work/server.log" \
    -o "-c listen_addresses='' -c unix_socket_directories=$work -c logging_collector=off -c client_min_messages=error" start

pg_dump -h "$work" -U "$owner" -d "$db" -Fc --no-owner --no-privileges -f "$work/dump"
pg_restore -l "$work/dump" | grep -v -E ' (POLICY|EXTENSION) ' > "$work/list" || true
pg_restore -h "$target" -U postgres -d "$db" --role="$owner" --no-owner --no-privileges \
    --exit-on-error -L "$work/list" "$work/dump"
new "$db" "COMMENT ON DATABASE \"$db\" IS '$marker'" >/dev/null
moved="$(new "$db" "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")"
say "moved $db from $volume into the runtime stores ($moved tables); $volume is kept"
