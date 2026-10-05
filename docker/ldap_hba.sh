#!/bin/sh
# Sign-in's rules in the retail database's pg_hba.conf, written in place.
#
# Run inside the retail container, as postgres, on every start -- launch.sh
# pipes it in (`docker compose exec -T -u postgres postgres sh -s`) -- so
# the published image needs no change and a volume made before sign-in
# existed gets the rules too. It writes one marked block at the top of the
# file and leaves every other line as it was:
#
#   # BEGIN nl2sql sign-in
#   hostssl all <reader>,<sync> all scram-sha-256
#   hostssl <db> +nl2sql_ldap all ldap ldapserver=... ldaptls=1 ldapprefix="uid=" ldapsuffix=",ou=people,<base>"
#   # END nl2sql sign-in
#
# `hostssl`, both (6.1). pg_hba's `ldap` method is clear-text password
# authentication: the client sends the person's directory password to the
# server, which binds to the directory with it. Over a plain connection that
# is their password for every page, readable by anyone on the path. The
# image has served TLS since v1_2 (docker/entrypoint.sh), so a person's
# password is accepted only over an encrypted connection, and a client that
# tries without one finds no rule that admits it.
#
# The first line is there because pg_hba's `+role` matches indirect members,
# and two service roles are: the sync holds ADMIN on nl2sql_ldap, and the
# agent's reader may SET ROLE to every person. Without it their passwords
# would be sent to the directory, which has never heard of them. The rules
# are first-match, so the block goes before the file's own.
#
# With NL2SQL_SIGNIN=off the block is taken out, which is what turning
# sign-in off has to do: without it, people could still sign in to the
# database with directory passwords while every service had stopped asking.
#
# The new file is checked before it is used: Postgres parses pg_hba.conf on
# reload, keeps the old rules if the new ones are broken -- and refuses to
# start at all on the next restart. So a file that does not parse is put
# back as it was, and the start fails here, where it says why.
#
# Environment:
#   NL2SQL_SIGNIN          on | off
#   NL2SQL_DB              the retail database
#   NL2SQL_SERVICE_ROLES   reader,sync -- the roles kept on passwords
#   NL2SQL_LDAP_HOST       the directory, as the database reaches it
#   NL2SQL_LDAP_PORT       389
#   NL2SQL_LDAP_TLS        starttls | ldaps | none
#   NL2SQL_LDAP_BASE_DN    the directory's base, people under ou=people
#   HBA_FILE               only for the tests; $PGDATA/pg_hba.conf otherwise
set -eu

die() { echo "ldap_hba: $*" >&2; exit 1; }

hba="${HBA_FILE:-${PGDATA:?PGDATA is not set}/pg_hba.conf}"
begin="# BEGIN nl2sql sign-in"
end="# END nl2sql sign-in"
signin="${NL2SQL_SIGNIN:-off}"
db="${NL2SQL_DB:?NL2SQL_DB is not set}"

[ -f "$hba" ] || die "there is no $hba to write the rules into"

if [ "$signin" != on ] && [ "$signin" != off ]; then
    die "NL2SQL_SIGNIN must be on or off, not $signin"
fi

if [ "$signin" = on ]; then
    roles="${NL2SQL_SERVICE_ROLES:?NL2SQL_SERVICE_ROLES is not set}"
    host="${NL2SQL_LDAP_HOST:?NL2SQL_LDAP_HOST is not set}"
    port="${NL2SQL_LDAP_PORT:-389}"
    base="${NL2SQL_LDAP_BASE_DN:?NL2SQL_LDAP_BASE_DN is not set}"
    case "${NL2SQL_LDAP_TLS:-starttls}" in
        starttls) tls="ldaptls=1" ;;
        ldaps) tls="ldapscheme=ldaps" ;;
        none) tls="" ;;
        *) die "NL2SQL_LDAP_TLS must be starttls, ldaps or none, not $NL2SQL_LDAP_TLS" ;;
    esac
    # Written inside double quotes in the rule; a quote in it would end them.
    case "$base" in
        *\"*) die "NL2SQL_LDAP_BASE_DN cannot contain a double quote: $base" ;;
    esac
fi

work="$(mktemp "$hba.XXXXXX")"
{
    if [ "$signin" = on ]; then
        echo "$begin -- written on start by docker/ldap_hba.sh; change .env, not these lines"
        echo "hostssl all $roles all scram-sha-256"
        echo "hostssl $db +nl2sql_ldap all ldap ldapserver=$host ldapport=$port $tls ldapprefix=\"uid=\" ldapsuffix=\",ou=people,$base\""
        echo "$end"
    fi
    awk -v b="$begin" -v e="$end" '
        index($0, b) == 1 { skip = 1 }
        !skip { print }
        index($0, e) == 1 { skip = 0 }
    ' "$hba"
} > "$work"

if [ "$(cat "$work")" = "$(cat "$hba")" ]; then
    rm -f "$work"
    echo "sign-in rules unchanged ($signin)"
    exit 0
fi

# Rewritten through the file rather than moved over it, so it keeps its
# owner and mode, which Postgres checks.
backup="$hba.nl2sql-backup"
cat "$hba" > "$backup"
cat "$work" > "$hba"
rm -f "$work"

errors="$(psql -X -q -At -d "$db" -c "SELECT count(*) FROM pg_hba_file_rules WHERE error IS NOT NULL")"
if [ "$errors" != "0" ]; then
    cat "$backup" > "$hba"
    die "the new rules did not parse ($errors errors), so the old file is back: see pg_hba_file_rules"
fi
psql -X -q -At -d "$db" -c "SELECT pg_reload_conf()" > /dev/null
echo "sign-in rules written ($signin)"
