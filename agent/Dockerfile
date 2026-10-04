FROM python:3.12-slim

WORKDIR /app

COPY agent/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY agent/nl2sql_agent/ ./nl2sql_agent/
# Sign-in's shared half: it verifies the session the auth service signs, so
# the API and the console can tell who is asking. Verify only -- the signing
# key is the auth service's alone.
COPY auth/nl2sql_identity/ ./nl2sql_identity/

# Where the REST server keeps its TLS certificate (API_TLS_CERT_FILE and
# API_TLS_KEY_FILE default into it). A volume in compose, so a certificate
# generated on first start survives a restart and a client that pinned its
# fingerprint keeps working.
RUN mkdir -p /etc/nl2sql/tls

# The API's port. Nothing listens unless the container is started with
# `python -m nl2sql_agent.api`; the default entrypoint is still the CLI.
EXPOSE 8443

ARG AGENT_VERSION=6.0.1
ENV AGENT_VERSION=${AGENT_VERSION}

LABEL org.opencontainers.image.title="nl2sql RAG agent" \
      org.opencontainers.image.description="Natural-language-to-SQL agent (LangChain/LangGraph) with pgvector knowledge-base retrieval and a TLS REST API" \
      org.opencontainers.image.version="${AGENT_VERSION}"

ENTRYPOINT ["python", "-m", "nl2sql_agent"]
