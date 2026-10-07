"""slapd's configuration, written from the settings on every start.

Rendered rather than kept in the volume, so a changed environment -- a mode,
a primary, a lockout policy -- is the configuration on the next start, with
no copy in a volume to fall out of step with it. The data is the only thing
that persists.

What differs between the two modes is who may write, and where a password
is checked:

* **standalone** -- the auth service's account writes people and groups for
  the web interface; passwords are checked here, hashed with Argon2, under a
  lockout policy.
* **replica** -- nobody writes over the network at all. The access rules give
  every account read access and no more, and the copy from the primary is
  made over the local socket by this container's own user, which is the
  directory's root. A bind to a person is passed through to the primary
  (`remoteauth`), using the primary's DN the copy stored in `seeAlso`.

In both, root is this container's own user on the local socket and nothing
else: there is no root password to leak.
"""

from __future__ import annotations

import os

from .layout import Layout
from .settings import DATA_DIR, RUN_DIR, DirectorySettings

#: The local socket, as a URL: the path percent-encoded, as ldapi:// needs.
SOCKET = f"{RUN_DIR}/ldapi"
SOCKET_URL = "ldapi://" + SOCKET.replace("/", "%2F")

#: What remoteauth calls the primary. Any name: it only connects a person's
#: entry (which names no domain) to the server below.
UPSTREAM_DOMAIN = "upstream"


def peercred_dn(uid: int | None = None, gid: int | None = None) -> str:
    """The DN slapd gives a local-socket connection from this user."""
    uid = os.getuid() if uid is None else uid
    gid = os.getgid() if gid is None else gid
    return f"gidNumber={gid}+uidNumber={uid},cn=peercred,cn=external,cn=auth"


def _modules(settings: DirectorySettings) -> list[str]:
    modules = ["back_mdb", "memberof", "refint", "argon2", "pw-sha2"]
    modules.append("remoteauth" if settings.replica else "ppolicy")
    return [f"moduleload {name}.so" for name in modules]


def _access(settings: DirectorySettings, layout: Layout) -> list[str]:
    """Who may read and write what. First match wins, as in slapd."""
    service = f'dn.exact="{layout.service_account}"'
    writer = "write" if not settings.replica else "read"
    rules = [
        # A password is never readable by anyone. In a standalone directory
        # the auth service may set one (=w, write without read), and so may
        # its owner -- through the Password Modify operation, which checks
        # the old one first, and checking is the auth privilege (=xw).
        # Everyone may use one to bind with.
        "access to attrs=userPassword",
        *([f"  by {service} =w", "  by self =xw"] if not settings.replica else []),
        "  by anonymous auth",
        "  by * none",
        # The lockout bookkeeping, which only the password policy defines:
        # the auth service may clear a lockout in a standalone directory.
        *(
            [
                "access to attrs=pwdAccountLockedTime,pwdFailureTime",
                f"  by {service} write",
                "  by * none",
            ]
            if not settings.replica
            else []
        ),
        # People and groups: written by the auth service in a standalone
        # directory, read by any account in either.
        f'access to dn.subtree="{layout.people}"',
        f"  by {service} {writer}",
        "  by users read",
        "  by * none",
        f'access to dn.subtree="{layout.groups}"',
        f"  by {service} {writer}",
        "  by users read",
        "  by * none",
        # Everything else -- the services branch, the policy, the base -- is
        # readable by any account that has signed in, and written by no one.
        "access to *",
        "  by users read",
        "  by * none",
    ]
    return rules


def _overlays(settings: DirectorySettings, layout: Layout) -> list[str]:
    lines = [
        "overlay memberof",
        "memberof-refint TRUE",
        "overlay refint",
        "refint_attributes member",
    ]
    if settings.replica:
        upstream = settings.upstream
        assert upstream is not None
        tls = [f"tls_reqcert={'demand' if upstream.verify else 'never'}"]
        if upstream.starttls:
            tls.insert(0, "starttls=yes")
        if upstream.cacert:
            tls.append(f"tls_cacert={upstream.cacert}")
        lines += [
            "overlay remoteauth",
            "remoteauth_dn_attribute seeAlso",
            # Must be set even though no entry carries it: without one the
            # overlay refuses every bind before looking at the mapping.
            "remoteauth_domain_attribute associatedDomain",
            f"remoteauth_default_domain {UPSTREAM_DOMAIN}",
            f"remoteauth_mapping {UPSTREAM_DOMAIN} {upstream.uri}",
            f"remoteauth_default_realm {upstream.uri}",
            "remoteauth_retry_count 1",
            "remoteauth_store off",
            "remoteauth_tls " + " ".join(tls),
        ]
    else:
        lines += [
            "overlay ppolicy",
            f'ppolicy_default "{layout.password_policy}"',
            "ppolicy_hash_cleartext",
            "ppolicy_use_lockout",
        ]
    return lines


def render(settings: DirectorySettings, *, root: str | None = None) -> str:
    """The whole of slapd.conf for these settings."""
    layout = Layout(settings.base_dn)
    root = root or peercred_dn()
    lines = [
        "# Written by nl2sql_ldap on every start -- edit the environment, not this.",
        f"# Mode: {settings.mode}.",
        "include /etc/openldap/schema/core.schema",
        "include /etc/openldap/schema/cosine.schema",
        "include /etc/openldap/schema/inetorgperson.schema",
        "modulepath /usr/lib/openldap",
        *_modules(settings),
        f"pidfile {RUN_DIR}/slapd.pid",
        f"argsfile {RUN_DIR}/slapd.args",
        f"loglevel {settings.log_level}",
        # Argon2 for a password set through the Password Modify operation or
        # (with the policy's hash_cleartext) written in the clear.
        "password-hash {ARGON2}",
        f"TLSCertificateFile {settings.tls_cert_file}",
        f"TLSCertificateKeyFile {settings.tls_key_file}",
        "TLSProtocolMin 3.3",
        # A simple bind -- a password -- needs an encrypted connection. The
        # local socket counts (its strength is 71); plain ldap:// does not.
        *(["security simple_bind=64"] if settings.require_tls else []),
        "sizelimit 2000",
        "database config",
        f'access to * by dn.exact="{root}" manage by * none',
        "database mdb",
        "maxsize 1073741824",
        f'suffix "{settings.base_dn}"',
        f'rootdn "{root}"',
        f"directory {DATA_DIR}",
        "index objectClass eq",
        "index uid eq,pres",
        "index cn eq,sub",
        "index member eq",
        "index memberOf eq",
        "index seeAlso eq",
        *_overlays(settings, layout),
        *_access(settings, layout),
    ]
    return "\n".join(lines) + "\n"


def listen_urls() -> str:
    """What slapd listens on: plain (for StartTLS), LDAPS, and the socket."""
    return f"ldap:/// ldaps:/// {SOCKET_URL}"
