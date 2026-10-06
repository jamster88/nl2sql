# The review service: FastAPI over the staging database, plus the ability to
# rewrite the golden question document and the SQL snippet document, and
# reload the stores built from them.
#
# This image carries `rag/` and its dependencies, which the agent image does
# not. That is the whole reason it is a separate image rather than another
# entrypoint on the agent's: promotion calls `ragproc.loaders`, the code of
# steps 5, 6 and 7, because it already handles the upsert, the delete of
# pairs no longer in the document, the BM25 rebuild and incremental
# re-embedding -- and a second implementation would be a second set of rules
# to keep in agreement with the first.
#
# What it deliberately does not carry is the agent: no langchain, no
# langgraph, no model client. This process never answers a question.
#
# Started as root, and root only long enough to see who owns the documents'
# directory, mounted from the checkout: it then runs as that person, so a
# promoted pair in the working tree is theirs (V6-28, `server.main`). The
# account below is who it runs as when the directory is root's -- the copy in
# this image, with nothing mounted -- and the group it reads its TLS key with
# either way. No USER line for that reason: the owner is the host's, unknown
# until the container starts.

FROM python:3.12-slim@sha256:02108f5d322dd89f1c9e552442c25acb0543dfdbc455693a5599624f20d9155d

WORKDIR /app

# Its own requirements rather than the agent's, so the image that can write
# the golden question set does not also ship the machinery for answering
# questions.
# Every dependency at the version, and with the hash, its lock file names
# (V6-24): a version republished under the same number, or a file swapped on
# the index, is refused rather than installed. `requirements.txt` beside it is
# what a person edits; `uv pip compile --generate-hashes --universal` writes
# the lock from it.
COPY review/requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# The shared package -- sign-in's guard and session format, settings, the
# error envelope -- installed as a package rather than copied (V6-66), so
# `pip show nl2sql-common` here says which version this image carries.
COPY common/ /tmp/nl2sql-common/
RUN pip install --no-cache-dir --no-deps /tmp/nl2sql-common && rm -rf /tmp/nl2sql-common

COPY review/nl2sql_review/ ./nl2sql_review/

RUN groupadd --system --gid 10001 nl2sql \
 && useradd --system --uid 10001 --gid nl2sql --home-dir /home/nl2sql --create-home --shell /usr/sbin/nologin nl2sql

# The loaders, and the package they live in. A promotion and a snippet write
# call `ragproc.loaders` in this process (V6-27), from /app/rag, which is
# REVIEW_RAG_DIR's default; the scripts are for the stack's start, when the
# snippet store is behind its document (launch.sh runs 07 in a one-off
# container of this image).
COPY rag/ragproc/ ./rag/ragproc/
COPY rag/05_load_golden_pairs.py rag/06_embed_golden_pairs.py rag/07_load_snippets.py ./rag/

# The golden question document. Bind-mounted over in compose so a promotion
# writes the working tree's copy rather than a copy inside a container that
# disappears with it -- the file in the image is the fallback, and a
# promotion against it is a promotion nobody will ever see in git.
COPY context_questions/translated_questions.md context_questions/sql_snippets.md ./context_questions/

# Where its own certificate is expected to appear, mounted read-only from the
# volume the stack's pki service issues it into. This service presents it
# and never generates one; see nl2sql_review/settings.py.
RUN mkdir -p /etc/nl2sql/tls

EXPOSE 8444

ARG REVIEW_VERSION=6.3.0
ENV REVIEW_VERSION=${REVIEW_VERSION}

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-m", "nl2sql_common.health", "REVIEW"]

LABEL org.opencontainers.image.title="nl2sql feedback review service" \
      org.opencontainers.image.description="Reviews captured feedback and promotes it into the golden question set" \
      org.opencontainers.image.version="${REVIEW_VERSION}"

ENTRYPOINT ["python", "-m", "nl2sql_review"]
