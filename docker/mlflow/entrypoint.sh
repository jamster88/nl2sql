#!/bin/sh
# MLflow's tracking server, as the account `mlflow` rather than root (V6-31).
#
# Root only long enough to give that account the artifact volume: a named
# volume Docker created is root's, and so is everything a 6.1 container
# wrote into it. Only what is not the account's already is changed, so a
# start with nothing to hand over touches nothing. Then the command compose
# gives -- `mlflow server ...` -- as the account, with its groups and nothing
# else. Started as anyone else (`docker run --user`), it runs as given.
#
# The tracking store's URL is made here (6.3, V6-38), from MLFLOW_DB_USER,
# MLFLOW_DB_HOST and MLFLOW_DB_NAME and the password in the secret file
# MLFLOW_DB_PASSWORD_FILE names, and handed to the server as
# MLFLOW_BACKEND_STORE_URI, which it reads for --backend-store-uri. On the
# command line, as it was until 6.3, the password was in `ps` and in
# `docker inspect` for anyone who could run either. A URL given outright is
# used as it is.
set -eu

if [ -z "${MLFLOW_BACKEND_STORE_URI:-}" ] && [ -n "${MLFLOW_DB_PASSWORD_FILE:-}" ]; then
    # Encoded for a URL, which a generated password never needs and one
    # chosen by hand might.
    password="$(python -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.stdin.read().strip(), safe=""))' \
        < "$MLFLOW_DB_PASSWORD_FILE")"
    MLFLOW_BACKEND_STORE_URI="postgresql://${MLFLOW_DB_USER:-mlflow}:${password}@${MLFLOW_DB_HOST:-nl2sql-mlflowdb}:5432/${MLFLOW_DB_NAME:-mlflow}"
    export MLFLOW_BACKEND_STORE_URI
fi

artifacts="${MLFLOW_ARTIFACTS_DIR:-/mlflow/artifacts}"
if [ "$(id -u)" = 0 ]; then
    mkdir -p "$artifacts"
    find "$artifacts" ! -user mlflow -exec chown mlflow:mlflow {} + 2>/dev/null || true
    exec setpriv --reuid=mlflow --regid=mlflow --init-groups --inh-caps=-all -- "$@"
fi
exec "$@"
