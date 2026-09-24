# The review service: FastAPI over the staging database, plus the ability to
# rewrite the golden question document and reload the stores built from it.
#
# This image carries `rag/` and its dependencies, which the agent image does
# not. That is the whole reason it is a separate image rather than another
# entrypoint on the agent's: promotion runs `05_load_golden_pairs.py` and
# `06_embed_golden_pairs.py` as scripts, because they already handle the
# upsert, the delete of pairs no longer in the document, the BM25 rebuild and
# incremental re-embedding -- and a second implementation would be a second
# set of rules to keep in agreement with the first.
#
# What it deliberately does not carry is the agent: no langchain, no
# langgraph, no model client. This process never answers a question.

FROM python:3.12-slim

WORKDIR /app

# Its own requirements file rather than the agent's, so the image that can
# write the golden question set does not also ship the machinery for
# answering questions.
COPY review/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY review/nl2sql_review/ ./nl2sql_review/

# The loaders, and the package they import. Promotion runs these as scripts
# from /app/rag, which is REVIEW_RAG_DIR's default.
COPY rag/ragproc/ ./rag/ragproc/
COPY rag/05_load_golden_pairs.py rag/06_embed_golden_pairs.py ./rag/

# The golden question document. Bind-mounted over in compose so a promotion
# writes the working tree's copy rather than a copy inside a container that
# disappears with it -- the file in the image is the fallback, and a
# promotion against it is a promotion nobody will ever see in git.
COPY context_questions/translated_questions.md ./context_questions/

# Where the agent API's certificate is expected to appear, mounted read-only
# from the volume that API writes it into. This service presents that
# certificate and never generates one; see nl2sql_review/settings.py.
RUN mkdir -p /etc/nl2sql/tls

EXPOSE 8444

ARG REVIEW_VERSION=4.3.0
ENV REVIEW_VERSION=${REVIEW_VERSION}

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request,ssl;\
ctx=ssl._create_unverified_context();\
scheme='https' if os.getenv('REVIEW_TLS_ENABLED','true').lower() not in ('0','false','no') else 'http';\
urllib.request.urlopen(f\"{scheme}://127.0.0.1:{os.getenv('REVIEW_PORT','8444')}/healthz\", context=ctx, timeout=4)" || exit 1

LABEL org.opencontainers.image.title="nl2sql feedback review service" \
      org.opencontainers.image.description="Reviews captured feedback and promotes it into the golden question set" \
      org.opencontainers.image.version="${REVIEW_VERSION}"

ENTRYPOINT ["python", "-m", "nl2sql_review"]
