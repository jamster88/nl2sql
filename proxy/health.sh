#!/bin/sh
# The proxy's health check, and one that verifies (V6-37).
#
# The six images it replaced each asked their own port with
# `wget --no-check-certificate`, which passes for a page serving an expired
# certificate or another page's. This asks over the page's own listener,
# under the name `localhost` -- which every page's certificate covers -- and
# verifies the answer against the stack's CA, the file beside the page's
# certificate (PROXY_HEALTH_CACERT names another). Plain HTTP, when the page
# was told to serve it, has nothing to verify.
set -eu

port="${PROXY_PORT:?PROXY_PORT is not set}"
case "${PROXY_TLS_ENABLED:-true}" in
    false|0|no|off)
        exec curl -fsS -o /dev/null --max-time 4 "http://127.0.0.1:$port/_proxy/health" ;;
esac
ca="${PROXY_HEALTH_CACERT:-$(dirname "${PROXY_TLS_CERT_FILE:-/etc/nl2sql/tls/server.crt}")/ca.crt}"
exec curl -fsS -o /dev/null --max-time 4 --cacert "$ca" --resolve "localhost:$port:127.0.0.1" \
    "https://localhost:$port/_proxy/health"
