#!/usr/bin/env bash
# The retail database's start: its certificate, its transport rules and its
# passwords, then Postgres. The image's ENTRYPOINT since v1_2.
#
# Before v1_2 the image carried the superuser's password, `nl2sql`, inside
# its data, and accepted it from any address in clear text. Now nothing is
# baked in, and every start -- of a fresh volume or of one an older image
# made -- does three things before the server listens on the network:
#
#   1. A certificate. Its own, written on first start into
#      $POSTGRES_TLS_DIR (the pgtls volume under compose) for the names in
#      POSTGRES_TLS_HOSTNAMES, self-signed, and kept from then on. One
#      mounted there by someone else is used as it is and never replaced.
#      Postgres serves TLS with it (ssl=on).
#
#   2. The transport rules, a marked block at the top of pg_hba.conf:
#         hostnossl all all all reject    nothing over the network in clear
#         host all postgres all reject    the superuser only on the local socket
#      The first goes with POSTGRES_REQUIRE_TLS=false, for a client that
#      cannot do TLS; the second never does. launch.sh's sign-in block
#      (docker/ldap_hba.sh) is written above this one, and is hostssl too.
#
#   3. The passwords, from the environment, through a server listening on
#      no TCP port at all: the superuser's is removed -- it is reached with
#      `docker compose exec`, as root's peer, never with a password --
#      POSTGRES_PASSWORD becomes the owner's and POSTGRES_READER_PASSWORD
#      the agent reader's. Unset leaves a password as it was. Read inside
#      psql with \getenv, so no password is ever on a command line.
#
# Anything other than `postgres` as the command (a shell, `postgres
# --version`) is passed straight to the stock entrypoint.
#
# For the tests: NL2SQL_STOCK_ENTRYPOINT is the stock script to source, and
# NL2SQL_GOSU the program that drops to the postgres user.
#
# `set -u` is left off, as the stock script leaves it off: its functions,
# sourced below, read variables that may be unset.
set -Eeo pipefail

stock="${NL2SQL_STOCK_ENTRYPOINT:-/usr/local/bin/docker-entrypoint.sh}"
tls_dir="${POSTGRES_TLS_DIR:-/etc/nl2sql/pg-tls}"
cert="$tls_dir/server.crt"
key="$tls_dir/server.key"
marker="$tls_dir/generated-for"
hostnames="${POSTGRES_TLS_HOSTNAMES:-nl2sql-postgres,postgres,localhost,127.0.0.1,::1}"
begin="# BEGIN nl2sql transport"
end="# END nl2sql transport"

say() { echo "nl2sql-postgres: $*"; }
die() { echo "nl2sql-postgres: $*" >&2; exit 1; }

if [ "${1:-}" != postgres ]; then
    exec "$stock" "$@"
fi

# The stock script's functions -- its environment, its directories, its
# socket-only server -- without running its main.
# shellcheck source=/dev/null
source "$stock"
docker_setup_env

if [ "$(id -u)" = 0 ]; then
    docker_create_db_directories
    # A volume mounted here is root's on first use; the certificate is the
    # postgres user's to write and keep.
    mkdir -p "$tls_dir"
    chown postgres:postgres "$tls_dir"
    exec "${NL2SQL_GOSU:-gosu}" postgres "$BASH_SOURCE" "$@"
fi

if [ -z "$DATABASE_ALREADY_EXISTS" ]; then
    # Not this image's data: an empty volume over PGDATA. The stock image
    # knows how to make a database from nothing; this one ships one.
    say "no database at $PGDATA, so the stock image initialises one"
    exec "$stock" "$@"
fi

# --- 1. the certificate ------------------------------------------------------

alt_names() {  # alt_names "a,b,1.2.3.4" -> "DNS:a,DNS:b,IP:1.2.3.4"
    local name out=""
    local IFS=,
    for name in $1; do
        if [[ "$name" == *:* || "$name" =~ ^[0-9]+(\.[0-9]+){3}$ ]]; then
            out="${out:+$out,}IP:$name"
        else
            out="${out:+$out,}DNS:$name"
        fi
    done
    printf '%s' "$out"
}

write_certificate() {
    local first="${hostnames%%,*}"
    mkdir -p "$tls_dir"
    # A CA as well as a server, like the directory's: a client given this
    # file as its root certificate (sslrootcert) has to be allowed to treat
    # it as one.
    (umask 077 && openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes \
        -keyout "$key" -out "$cert" -days "${POSTGRES_TLS_DAYS:-3650}" \
        -subj "/CN=$first/O=nl2sql (development)" \
        -addext "subjectAltName=$(alt_names "$hostnames")" \
        -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
        -addext "keyUsage=critical,digitalSignature,keyCertSign" \
        -addext "extendedKeyUsage=serverAuth" 2>/dev/null)
    chmod 600 "$key"
    chmod 644 "$cert"
    printf '%s\n' "$hostnames" > "$marker"
}

if [ ! -f "$cert" ] || [ ! -f "$key" ]; then
    if [ -f "$cert" ] && [ ! -f "$marker" ]; then
        die "there is a certificate at $cert and no key at $key beside it"
    fi
    write_certificate
    say "wrote a certificate for $hostnames at $cert"
elif [ -f "$marker" ] && [ "$(cat "$marker")" != "$hostnames" ]; then
    write_certificate
    say "reissued the certificate for $hostnames: POSTGRES_TLS_HOSTNAMES changed"
elif [ -f "$marker" ] && ! openssl x509 -checkend 2592000 -noout -in "$cert" >/dev/null 2>&1; then
    write_certificate
    say "reissued the certificate, which had less than 30 days left"
else
    say "using the certificate at $cert"
fi

# --- 2. the transport rules --------------------------------------------------

hba="$PGDATA/pg_hba.conf"
work="$(mktemp "$hba.XXXXXX")"
{
    echo "$begin -- written on start by the image (nl2sql-entrypoint.sh)"
    case "${POSTGRES_REQUIRE_TLS:-true}" in
        false|0|no|off) echo "# POSTGRES_REQUIRE_TLS=false: passwords may cross the network in clear" ;;
        *) echo "hostnossl all all all reject" ;;
    esac
    echo "host all postgres all reject"
    echo "$end"
    awk -v b="$begin" -v e="$end" '
        index($0, b) == 1 { skip = 1 }
        !skip { print }
        index($0, e) == 1 { skip = 0 }
    ' "$hba"
} > "$work"
# Through the file rather than over it, so it keeps its owner and mode.
cat "$work" > "$hba"
rm -f "$work"

# --- 3. the passwords ---------------------------------------------------------

# Empty is unset: compose passes a variable nobody set as "", and an empty
# password in Postgres clears the one there was.
[ -n "${POSTGRES_PASSWORD:-}" ] || unset POSTGRES_PASSWORD
[ -n "${POSTGRES_READER_PASSWORD:-}" ] || unset POSTGRES_READER_PASSWORD

docker_temp_server_start "$@"
psql -X -q -v ON_ERROR_STOP=1 --no-psqlrc --username=postgres --dbname=postgres \
    -v owner="$POSTGRES_USER" -v reader="${POSTGRES_READER_USER:-nl2sql_reader}" <<'SQL'
SET client_min_messages = warning;
ALTER ROLE postgres PASSWORD NULL;
\getenv owner_password POSTGRES_PASSWORD
\if :{?owner_password}
ALTER ROLE :"owner" PASSWORD :'owner_password';
\endif
\getenv reader_password POSTGRES_READER_PASSWORD
SELECT :{?reader_password} AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'reader') AS set_reader \gset
\if :set_reader
ALTER ROLE :"reader" PASSWORD :'reader_password';
\endif
SQL
docker_temp_server_stop
set_from_env=""
[ -n "${POSTGRES_PASSWORD:-}" ] && set_from_env="$POSTGRES_USER"
[ -n "${POSTGRES_READER_PASSWORD:-}" ] && set_from_env="${set_from_env:+$set_from_env and }${POSTGRES_READER_USER:-nl2sql_reader}"
say "the superuser has no password; passwords from the environment: ${set_from_env:-none}"

unset "${!POSTGRES_@}"
exec "$@" -c ssl=on -c "ssl_cert_file=$cert" -c "ssl_key_file=$key"
