# MLflow's tracking server, as this project runs it: where the agent's
# traces, the verdicts given on them and the benchmark's runs are kept
# (agent/nl2sql_agent/tracing.py).
#
# MLflow's own published image, in the variant that carries a Postgres
# driver, pinned to the version of the tracing client the agent image
# installs (agent/requirements.txt) and the benchmark's runs client
# (tests/requirements.txt); a test holds the three together. It is built here
# and published beside the rest of the stack, under the release's tag, so a
# release's images are one set: pulling them never mixes in another release's
# MLflow, and nothing has to be fetched from a second registry.
#
# Nothing is added. How it is served -- the store, the artifact volume, the
# hosts it answers to -- is the `mlflow` service's command in
# docker-compose.yml, where it can change without a rebuild.
FROM ghcr.io/mlflow/mlflow:v3.16.1-full

EXPOSE 5000

LABEL org.opencontainers.image.title="nl2sql MLflow server" \
      org.opencontainers.image.description="MLflow tracking server for the nl2sql agent's traces, the verdicts on them and the benchmark's runs" \
      org.opencontainers.image.version="5.5.1"
