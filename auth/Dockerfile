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
#                    verifies one (installed from common/, as everywhere)
#   nl2sql_ldap      the directory's own operations, shared with the
#                    directory image, so the web interface edits people
#                    with the same code the directory is seeded with
#
# Started as root, and root only long enough to give the account `nl2sql`
# the two directories it writes -- the signing key's and the public key's,
# with whatever an older release wrote there as root -- before it reads
# either (V6-31, `server.main`). No USER line for that reason: a named
# volume Docker or a 6.1 container made is root's, and only root can hand
# it over. Its TLS key is the account's already, from the pki service.
FROM python:3.12-slim@sha256:02108f5d322dd89f1c9e552442c25acb0543dfdbc455693a5599624f20d9155d

WORKDIR /app

# Every dependency at the version, and with the hash, its lock file names
# (V6-24): a version republished under the same number, or a file swapped on
# the index, is refused rather than installed. `requirements.txt` beside it is
# what a person edits; `uv pip compile --generate-hashes --universal` writes
# the lock from it.
COPY auth/requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# The shared package -- sign-in's guard and session format, settings, the
# error envelope -- installed as a package rather than copied (V6-66), so
# `pip show nl2sql-common` here says which version this image carries.
COPY common/ /tmp/nl2sql-common/
RUN pip install --no-cache-dir --no-deps /tmp/nl2sql-common && rm -rf /tmp/nl2sql-common

COPY auth/nl2sql_auth/ ./nl2sql_auth/
COPY ldap/nl2sql_ldap/ ./nl2sql_ldap/

# The account it runs as, uid 10001 as in every Python image of the stack.
RUN groupadd --system --gid 10001 nl2sql \
 && useradd --system --uid 10001 --gid nl2sql --home-dir /home/nl2sql --create-home --shell /usr/sbin/nologin nl2sql

# Where the volumes are mounted: its own certificate (read-only), the
# directory's and the retail database's certificates (read-only), the
# signing key (this service's alone) and its public half (read-only
# everywhere else).
RUN mkdir -p /etc/nl2sql/tls /etc/nl2sql/ldap-tls /etc/nl2sql/pg-tls /etc/nl2sql/auth /var/lib/nl2sql-auth \
 && chown nl2sql:nl2sql /etc/nl2sql/auth /var/lib/nl2sql-auth \
 && chmod 700 /var/lib/nl2sql-auth

EXPOSE 8446

ARG AUTH_VERSION=7.0.0
ENV AUTH_VERSION=${AUTH_VERSION} \
    PYTHONUNBUFFERED=1

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-m", "nl2sql_common.health", "AUTH"]

LABEL org.opencontainers.image.title="nl2sql auth service" \
      org.opencontainers.image.description="Sign-in against the retail database and its directory, the session, the role sync and the directory's web interface" \
      org.opencontainers.image.version="${AUTH_VERSION}"

ENTRYPOINT ["python", "-m", "nl2sql_auth"]
