# The auth service: sign-in, the session, the role sync and the directory's
# web interface.
#
# Its own image because of what it holds: the key every session is signed
# with, a Postgres login that can create and drop the people in the retail
# database, and the directory account that edits them. None of that belongs
# in the agent's image, which faces the browser, or in the review service's,
# which can rewrite the golden set.
#
# It carries three packages and nothing of the agent's:
#   nl2sql_auth      this service
#   nl2sql_identity  the session format, shared with every service that
#                    verifies one (the agent and review images copy it too)
#   nl2sql_ldap      the directory's own operations, shared with the
#                    directory image, so the web interface edits people
#                    with the same code the directory is seeded with
#
# Root, as the review service and the console are, for the same reason: it
# presents the agent API's certificate, whose key that API writes 0600.
FROM python:3.12-slim

WORKDIR /app

COPY auth/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY auth/nl2sql_auth/ ./nl2sql_auth/
COPY auth/nl2sql_identity/ ./nl2sql_identity/
COPY ldap/nl2sql_ldap/ ./nl2sql_ldap/

# Where the volumes are mounted: the API's certificate (read-only), the
# directory's certificate (read-only), the signing key (this service's
# alone) and its public half (read-only everywhere else).
RUN mkdir -p /etc/nl2sql/tls /etc/nl2sql/ldap-tls /etc/nl2sql/auth /var/lib/nl2sql-auth \
 && chmod 700 /var/lib/nl2sql-auth

EXPOSE 8446

ARG AUTH_VERSION=6.0.1
ENV AUTH_VERSION=${AUTH_VERSION} \
    PYTHONUNBUFFERED=1

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request,ssl;\
ctx=ssl._create_unverified_context();\
scheme='https' if os.getenv('AUTH_TLS_ENABLED','true').lower() not in ('0','false','no') else 'http';\
urllib.request.urlopen(f\"{scheme}://127.0.0.1:{os.getenv('AUTH_PORT','8446')}/healthz\", context=ctx, timeout=4)" || exit 1

LABEL org.opencontainers.image.title="nl2sql auth service" \
      org.opencontainers.image.description="Sign-in against the retail database and its directory, the session, the role sync and the directory's web interface" \
      org.opencontainers.image.version="${AUTH_VERSION}"

ENTRYPOINT ["python", "-m", "nl2sql_auth"]
