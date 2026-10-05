"""What the directory is told by its environment.

Two modes, and the difference between them is who owns the users:

* **standalone** (the default) -- this directory is the source of truth.
  Its people and groups are loaded from a file on first start, or created
  and edited through the auth service's web interface, and passwords are
  checked here.
* **replica** -- another directory is. Active Directory, OpenLDAP, 389-DS,
  anything that answers an LDAP search: its people and groups are copied
  here on an interval (`replica.py`), passwords are checked by passing the
  bind through to it (slapd's `remoteauth` overlay), and nothing here can
  be edited -- every change comes down from the primary.

Same environment discipline as the rest of the repository: Compose forwards
a variable the host has not set as an empty string, so empty reads as unset.
A password can also come from a file (`*_FILE`), which is how Docker secrets
arrive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from nl2sql_common.env import (
    env as _env,
    env_str as _env_str,
    env_int as _env_int,
    env_bool as _env_bool,
    env_tuple as _env_tuple,
    secret,
)

STANDALONE = "standalone"
REPLICA = "replica"
MODES = (STANDALONE, REPLICA)

DEFAULT_BASE_DN = "dc=nl2sql,dc=local"

#: Where slapd keeps its database: a volume in compose.
DATA_DIR = "/var/lib/openldap/openldap-data"
#: The local socket (`ldapi`) and the pid file. Root-equivalent access to
#: the directory is a connection on this socket from inside the container.
RUN_DIR = "/var/lib/openldap/run"
#: The configuration written on every start, so the environment is always
#: the whole truth about how the directory runs.
CONFIG_FILE = "/var/lib/openldap/run/slapd.conf"
#: The directory's own certificate: a volume in compose, mounted read-only
#: by the retail database and the auth service to verify it.
TLS_DIR = "/etc/nl2sql/ldap-tls"

#: The names the generated certificate covers: the compose service and its
#: container, which is how the retail database and the auth service reach it.
DEFAULT_TLS_HOSTNAMES = ("nl2sql-ldap", "ldap", "localhost")

#: What each upstream flavour is searched with, unless the variables say
#: otherwise. Active Directory's user filter leaves out disabled accounts
#: (bit 2 of userAccountControl): a person switched off there stops being
#: able to sign in here on the next sync.
FLAVOURS = {
    "generic": {
        "user_filter": "(objectClass=person)",
        "group_filter": "(|(objectClass=groupOfNames)(objectClass=groupOfUniqueNames)(objectClass=posixGroup))",
        "login_attribute": "uid",
        "member_attribute": "member",
    },
    "openldap": {
        "user_filter": "(objectClass=inetOrgPerson)",
        "group_filter": "(|(objectClass=groupOfNames)(objectClass=groupOfUniqueNames))",
        "login_attribute": "uid",
        "member_attribute": "member",
    },
    "ad": {
        "user_filter": (
            "(&(objectCategory=person)(objectClass=user)"
            "(!(userAccountControl:1.2.840.113556.1.4.803:=2)))"
        ),
        "group_filter": "(objectClass=group)",
        "login_attribute": "sAMAccountName",
        "member_attribute": "member",
    },
}

#: The four groups the stack's services grant access by. Created in a
#: standalone directory; mirrored from the primary in a replica, where the
#: primary's own group names can be mapped onto them (LDAP_REPLICA_GROUPS).
DEFAULT_GROUPS = ("nl2sql-users", "nl2sql-reviewers", "nl2sql-curators", "nl2sql-admins")


class SettingsError(ValueError):
    """A configuration the directory refuses to start with, and why."""


def group_map(raw: str | None, default: tuple[str, ...]) -> dict[str, str]:
    """`upstream=local` pairs, or the default names mapped to themselves.

    `CN=NL2SQL Reviewers=nl2sql-reviewers` splits on the *last* `=`, since an
    upstream group can be named by its DN, which is full of them.
    """
    if raw is None:
        return {name: name for name in default}
    mapping: dict[str, str] = {}
    for part in raw.split(";"):
        part = part.strip()
        if not part:
            continue
        upstream, sep, local = part.rpartition("=")
        if not sep or not upstream.strip() or not local.strip():
            # A bare name mirrors under its own name.
            upstream, local = part, part
        mapping[upstream.strip()] = local.strip()
    return mapping


@dataclass
class UpstreamSettings:
    """The primary a replica copies from."""

    uri: str
    bind_dn: str
    bind_password: str
    base_dn: str
    flavour: str = "generic"
    user_base: str = ""
    group_base: str = ""
    user_filter: str = FLAVOURS["generic"]["user_filter"]
    group_filter: str = FLAVOURS["generic"]["group_filter"]
    login_attribute: str = "uid"
    member_attribute: str = "member"
    #: upstream group name (cn, or its DN) -> the local group it becomes.
    groups: dict[str, str] = field(default_factory=lambda: {g: g for g in DEFAULT_GROUPS})
    starttls: bool = False
    cacert: str | None = None
    verify: bool = True
    #: A plain ldap:// primary without StartTLS is refused unless this says
    #: so: every password passed through to it would cross in clear, and the
    #: directory refuses clear-text binds to itself for the same reason.
    allow_cleartext: bool = False
    interval_seconds: int = 60
    #: Copy only people who are in one of the mirrored groups. An Active
    #: Directory has every employee in it; the stack needs the ones who may
    #: use it, and each one copied is one more role in the retail database.
    only_group_members: bool = True
    page_size: int = 500
    timeout_seconds: int = 10

    @classmethod
    def from_env(cls) -> "UpstreamSettings":
        flavour = _env_str("LDAP_UPSTREAM_FLAVOUR", "generic").lower()
        if flavour not in FLAVOURS:
            raise SettingsError(
                f"LDAP_UPSTREAM_FLAVOUR={flavour} is not one of {', '.join(sorted(FLAVOURS))}"
            )
        preset = FLAVOURS[flavour]
        missing = [
            name
            for name in ("LDAP_UPSTREAM_URI", "LDAP_UPSTREAM_BIND_DN", "LDAP_UPSTREAM_BASE_DN")
            if _env(name) is None
        ]
        password = secret("LDAP_UPSTREAM_BIND_PASSWORD")
        if password is None:
            missing.append("LDAP_UPSTREAM_BIND_PASSWORD")
        if missing:
            raise SettingsError(
                "LDAP_MODE=replica needs the primary to copy from: set " + ", ".join(missing)
            )
        base = _env_str("LDAP_UPSTREAM_BASE_DN", "")
        return cls(
            uri=_env_str("LDAP_UPSTREAM_URI", ""),
            bind_dn=_env_str("LDAP_UPSTREAM_BIND_DN", ""),
            bind_password=password or "",
            base_dn=base,
            flavour=flavour,
            user_base=_env_str("LDAP_UPSTREAM_USER_BASE", base),
            group_base=_env_str("LDAP_UPSTREAM_GROUP_BASE", base),
            user_filter=_env_str("LDAP_UPSTREAM_USER_FILTER", preset["user_filter"]),
            group_filter=_env_str("LDAP_UPSTREAM_GROUP_FILTER", preset["group_filter"]),
            login_attribute=_env_str("LDAP_UPSTREAM_LOGIN_ATTRIBUTE", preset["login_attribute"]),
            member_attribute=_env_str("LDAP_UPSTREAM_MEMBER_ATTRIBUTE", preset["member_attribute"]),
            groups=group_map(_env("LDAP_REPLICA_GROUPS"), DEFAULT_GROUPS),
            starttls=_env_bool("LDAP_UPSTREAM_STARTTLS", False),
            cacert=_env("LDAP_UPSTREAM_CACERT"),
            verify=_env_bool("LDAP_UPSTREAM_TLS_VERIFY", True),
            allow_cleartext=_env_bool("LDAP_UPSTREAM_ALLOW_CLEARTEXT", False),
            interval_seconds=_env_int("LDAP_REPLICA_INTERVAL", 60),
            only_group_members=_env_bool("LDAP_REPLICA_ONLY_GROUP_MEMBERS", True),
            page_size=_env_int("LDAP_UPSTREAM_PAGE_SIZE", 500),
            timeout_seconds=_env_int("LDAP_UPSTREAM_TIMEOUT", 10),
        )

    @property
    def secure(self) -> bool:
        """Whether a password crosses to the primary encrypted."""
        return self.uri.lower().startswith("ldaps://") or self.starttls


@dataclass
class DirectorySettings:
    """Everything the directory container runs with."""

    mode: str = STANDALONE
    base_dn: str = DEFAULT_BASE_DN
    organisation: str = "nl2sql"
    #: The auth service's own account (cn=nl2sql-auth,ou=services): it reads
    #: people and groups for the role sync, and in a standalone directory
    #: writes them for the web interface.
    service_password: str | None = None
    #: The first person, created on a standalone directory's first start in
    #: every group, so somebody can sign in to the web interface at all.
    admin_user: str = "admin"
    admin_password: str | None = None
    admin_name: str = "Directory administrator"
    #: People (and groups) loaded on a standalone directory's first start:
    #: CSV or LDIF, by extension (`records.py`).
    seed_file: str | None = None
    groups: tuple[str, ...] = DEFAULT_GROUPS
    tls_cert_file: str = f"{TLS_DIR}/ldap.crt"
    tls_key_file: str = f"{TLS_DIR}/ldap.key"
    tls_generate: bool = True
    tls_hostnames: tuple[str, ...] = DEFAULT_TLS_HOSTNAMES
    tls_days: int = 825
    #: Refuse a password over a connection that is not encrypted. The local
    #: socket counts as encrypted; plain `ldap://` without StartTLS does not.
    require_tls: bool = True
    #: Lock an account after this many wrong passwords in a row (0: never),
    #: for this long. Standalone only: a replica's primary keeps its own count
    #: of the binds passed through to it.
    lockout_failures: int = 5
    lockout_seconds: int = 900
    min_password_length: int = 12
    log_level: str = "stats"
    upstream: UpstreamSettings | None = None

    @classmethod
    def from_env(cls) -> "DirectorySettings":
        mode = _env_str("LDAP_MODE", STANDALONE).lower()
        if mode not in MODES:
            raise SettingsError(f"LDAP_MODE={mode} is not one of {', '.join(MODES)}")
        return cls(
            mode=mode,
            base_dn=_env_str("LDAP_BASE_DN", DEFAULT_BASE_DN),
            organisation=_env_str("LDAP_ORGANISATION", "nl2sql"),
            service_password=secret("LDAP_SERVICE_PASSWORD"),
            admin_user=_env_str("LDAP_ADMIN_USER", "admin").lower(),
            admin_password=secret("LDAP_ADMIN_PASSWORD"),
            admin_name=_env_str("LDAP_ADMIN_NAME", "Directory administrator"),
            seed_file=_env("LDAP_SEED_FILE"),
            groups=_env_tuple("LDAP_GROUPS", DEFAULT_GROUPS),
            tls_cert_file=_env_str("LDAP_TLS_CERT_FILE", f"{TLS_DIR}/ldap.crt"),
            tls_key_file=_env_str("LDAP_TLS_KEY_FILE", f"{TLS_DIR}/ldap.key"),
            tls_generate=_env_bool("LDAP_TLS_GENERATE", True),
            tls_hostnames=_env_tuple("LDAP_TLS_HOSTNAMES", DEFAULT_TLS_HOSTNAMES),
            tls_days=_env_int("LDAP_TLS_DAYS", 825),
            require_tls=_env_bool("LDAP_REQUIRE_TLS", True),
            lockout_failures=_env_int("LDAP_LOCKOUT_FAILURES", 5),
            lockout_seconds=_env_int("LDAP_LOCKOUT_SECONDS", 900),
            min_password_length=_env_int("LDAP_MIN_PASSWORD_LENGTH", 12),
            log_level=_env_str("LDAP_LOG_LEVEL", "stats"),
            upstream=UpstreamSettings.from_env() if mode == REPLICA else None,
        )

    @property
    def replica(self) -> bool:
        return self.mode == REPLICA

    def problems(self) -> list[str]:
        """Reasons not to start at all, in the words the log should show."""
        found: list[str] = []
        if not self.service_password:
            found.append(
                "LDAP_SERVICE_PASSWORD is not set: the auth service would have no account "
                "to read the directory with (setup.sh writes one into .env)"
            )
        if not self.replica and not self.admin_password:
            found.append(
                "LDAP_ADMIN_PASSWORD is not set: a standalone directory needs a first "
                "person who can sign in (setup.sh writes one into .env)"
            )
        if self.upstream is not None and not self.upstream.secure and not self.upstream.allow_cleartext:
            found.append(
                f"LDAP_UPSTREAM_URI is {self.upstream.uri} without StartTLS: the bind account's "
                "password and every person's would cross to the primary in clear text. Use "
                "ldaps:// or LDAP_UPSTREAM_STARTTLS=true -- or, for a primary that cannot do "
                "either, say so with LDAP_UPSTREAM_ALLOW_CLEARTEXT=true"
            )
        return found

    def warnings(self) -> list[str]:
        """Configurations that will start and probably should not."""
        notes: list[str] = []
        if self.upstream is not None and not self.upstream.secure and self.upstream.allow_cleartext:
            notes.append(
                f"LDAP_UPSTREAM_URI is {self.upstream.uri} without StartTLS, allowed by "
                "LDAP_UPSTREAM_ALLOW_CLEARTEXT=true: every password passed through to the "
                "primary crosses the network in clear text. Use ldaps:// or "
                "LDAP_UPSTREAM_STARTTLS=true."
            )
        if self.upstream is not None and not self.upstream.verify:
            notes.append(
                "LDAP_UPSTREAM_TLS_VERIFY=false: the primary's certificate is not checked, "
                "so anything that answers at its address is believed."
            )
        if not self.require_tls:
            notes.append(
                "LDAP_REQUIRE_TLS=false: passwords are accepted over unencrypted connections."
            )
        if not self.replica and self.lockout_failures == 0:
            notes.append("LDAP_LOCKOUT_FAILURES=0: wrong passwords can be tried without limit.")
        return notes
