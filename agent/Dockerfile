FROM python:3.12-slim@sha256:02108f5d322dd89f1c9e552442c25acb0543dfdbc455693a5599624f20d9155d

WORKDIR /app

# Every dependency at the version, and with the hash, its lock file names
# (V6-24): a version republished under the same number, or a file swapped on
# the index, is refused rather than installed. `requirements.txt` beside it is
# what a person edits; `uv pip compile --generate-hashes --universal` writes
# the lock from it.
COPY agent/requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# The shared package -- sign-in's guard and session format, settings, the
# error envelope -- installed as a package rather than copied (V6-66), so
# `pip show nl2sql-common` here says which version this image carries.
COPY common/ /tmp/nl2sql-common/
RUN pip install --no-cache-dir --no-deps /tmp/nl2sql-common && rm -rf /tmp/nl2sql-common

COPY agent/nl2sql_agent/ ./nl2sql_agent/

# The account everything here runs as (V6-31): the API, the console and the
# CLI write nothing but what they are asked to, and read their own key, which
# the stack's pki service gives this uid. 10001 by number, so a volume, a
# compose `user:` and the pki service can all name it without this image's
# /etc/passwd. pki itself runs from this image as root (compose says so), the
# one service here that writes other services' files.
RUN groupadd --system --gid 10001 nl2sql \
    && useradd --system --uid 10001 --gid nl2sql --home-dir /home/nl2sql --create-home --shell /usr/sbin/nologin nl2sql

# Where the REST server keeps its TLS certificate (API_TLS_CERT_FILE and
# API_TLS_KEY_FILE default into it). Mounted read-only from the volume the
# pki service issues into; the account's own, so a server started without
# one can still generate a development certificate there.
RUN mkdir -p /etc/nl2sql/tls && chown nl2sql:nl2sql /etc/nl2sql/tls

# The API's port. Nothing listens unless the container is started with
# `python -m nl2sql_agent.api`; the default entrypoint is still the CLI.
EXPOSE 8443

ARG AGENT_VERSION=7.0.0
ENV AGENT_VERSION=${AGENT_VERSION}

USER 10001:10001

LABEL org.opencontainers.image.title="nl2sql RAG agent" \
      org.opencontainers.image.description="Natural-language-to-SQL agent (LangChain/LangGraph) with pgvector knowledge-base retrieval and a TLS REST API" \
      org.opencontainers.image.version="${AGENT_VERSION}"

ENTRYPOINT ["python", "-m", "nl2sql_agent"]
