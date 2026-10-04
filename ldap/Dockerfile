# The directory: OpenLDAP, standalone or a read-only replica of another.
#
# The retail database signs people in against this (pg_hba's `ldap` method),
# and the auth service reads it to turn its groups into Postgres roles. It
# runs in one of two modes, chosen by LDAP_MODE at start:
#
#   standalone   people and groups live here, loaded from a file on first
#                start and edited through the auth service's web interface
#   replica      a copy of another directory -- AD, OpenLDAP, anything that
#                answers an LDAP search -- made on an interval, read-only,
#                with every password checked by passing the bind through to
#                the primary (slapd's remoteauth overlay)
#
# Alpine because its OpenLDAP is packaged a module at a time: the overlays
# this needs -- remoteauth for the replica, ppolicy for lockout, Argon2 for
# hashing -- are each a package, and nothing else comes with them. Python is
# Alpine's own, with its ldap3 and cryptography, so there is no pip and no
# compiler in the image.
#
# No port is published in compose. Only the retail database and the auth
# service talk to this, on the stack's own network.
FROM alpine:3.22

RUN apk add --no-cache \
        openldap \
        openldap-back-mdb \
        openldap-clients \
        openldap-overlay-memberof \
        openldap-overlay-refint \
        openldap-overlay-ppolicy \
        openldap-overlay-remoteauth \
        openldap-passwd-argon2 \
        openldap-passwd-sha2 \
        python3 \
        py3-ldap3 \
        py3-cryptography \
 && mkdir -p /var/lib/openldap/openldap-data /var/lib/openldap/run /etc/nl2sql/ldap-tls /seed \
 && chown -R ldap:ldap /var/lib/openldap /etc/nl2sql/ldap-tls

WORKDIR /opt/nl2sql
COPY ldap/nl2sql_ldap/ ./nl2sql_ldap/
ENV PYTHONPATH=/opt/nl2sql \
    PYTHONUNBUFFERED=1

# Started as root, and only for a moment: the entry point gives the `ldap`
# user the directories it writes, then becomes that user before it does
# anything else, so slapd and everything it starts run as `ldap`. Root on the
# directory is this user on the local socket (the peer credential), so there
# is no root password. Not `USER ldap`: a named volume another container
# mounts first -- the certificate's, when the retail database starts before
# the directory, as it does under compose -- is created owned by root, and
# only root can hand it over. A `docker exec` and the health check run as
# root too, and the entry point gives root up for them the same way.

# 389 for LDAP with StartTLS, 636 for LDAPS. Unprivileged ports would need
# every client configured; Docker lets a container's user bind these.
EXPOSE 389 636

HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=5 \
    CMD python3 -m nl2sql_ldap health || exit 1

LABEL org.opencontainers.image.title="nl2sql directory" \
      org.opencontainers.image.description="OpenLDAP for the nl2sql stack: standalone, or a read-only replica of another directory" \
      org.opencontainers.image.version="6.0.1"

ENTRYPOINT ["python3", "-m", "nl2sql_ldap"]
