# The tracking store behind MLflow: experiments, runs, every question's trace
# and the verdicts on it.
#
# Stock Postgres, as the staging database is, and for its reason -- what it
# holds accumulates from use rather than from a build, so there is nothing to
# bake in; MLflow creates its schema on first start. It is built here only so
# that it is published with the rest of the release, under the release's tag:
# a release's images are one set.
FROM postgres:18

LABEL org.opencontainers.image.title="nl2sql MLflow store" \
      org.opencontainers.image.description="Postgres holding MLflow's experiments, runs, traces and verdicts for the nl2sql agent" \
      org.opencontainers.image.version="6.1.0"
