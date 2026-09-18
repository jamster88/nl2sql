FROM python:3.12-slim

WORKDIR /app

COPY agent/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY agent/nl2sql_agent/ ./nl2sql_agent/

ARG AGENT_VERSION=3.0.0
ENV AGENT_VERSION=${AGENT_VERSION}

LABEL org.opencontainers.image.title="nl2sql RAG agent" \
      org.opencontainers.image.description="Natural-language-to-SQL agent (LangChain/LangGraph) with pgvector knowledge-base retrieval" \
      org.opencontainers.image.version="${AGENT_VERSION}"

ENTRYPOINT ["python", "-m", "nl2sql_agent"]
