#!/bin/sh
# MLflow's tracking server, as the account `mlflow` rather than root (V6-31).
#
# Root only long enough to give that account the artifact volume: a named
# volume Docker created is root's, and so is everything a 6.1 container
# wrote into it. Only what is not the account's already is changed, so a
# start with nothing to hand over touches nothing. Then the command compose
# gives -- `mlflow server ...` -- as the account, with its groups and nothing
# else. Started as anyone else (`docker run --user`), it runs as given.
set -eu

artifacts="${MLFLOW_ARTIFACTS_DIR:-/mlflow/artifacts}"
if [ "$(id -u)" = 0 ]; then
    mkdir -p "$artifacts"
    find "$artifacts" ! -user mlflow -exec chown mlflow:mlflow {} + 2>/dev/null || true
    exec setpriv --reuid=mlflow --regid=mlflow --init-groups --inh-caps=-all -- "$@"
fi
exec "$@"
