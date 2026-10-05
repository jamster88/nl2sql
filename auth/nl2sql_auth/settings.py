"""Settings for the auth service.

This process holds three powers no other service has, which is why it is a
service of its own: the private key every session is signed with, a
Postgres role that can create and drop the people in the retail database
(`nl2sql_rolesync`), and -- in a standalone directory -- the directory
account that edits people and groups. It never answers a question and never
runs a person's SQL; signing someone in is a connection to the retail
database *as them*, which proves the password without this service ever
being able to check one itself.

Same environment discipline as the other services: Compose forwards a
variable the host has not set as an empty string, so empty reads as unset.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from nl2sql_identity import ADMINS, CURATORS, REVIEWERS, SESSION_COOKIE, USERS

DEFAULT_PORT = 8446
#: The directory's own API: the routes that make and change people,
#: administrators among them. On a port of its own so that it can be left
#: unpublished while the sign-in port is open to the desktop client.
DEFAULT_DIRECTORY_PORT = 8447

#: This service's own key and certificate, which the stack's pki service
#: issues from its development CA (since 6.1; it presented the agent API's
#: until then). The desktop client and every page's proxy trust the CA.
DEFAULT_TLS_DIR = "/etc/nl2sql/tls"

#: The retail database's own certificate, which it writes on first start
#: into a volume this service mounts read-only.
DEFAULT_DB_CACERT = "/etc/nl2sql/pg-tls/server.crt"

#: The connection modes that check the database is the one it claims to be.
VERIFYING_SSLMODES = ("verify-ca", "verify-full")

#: The signing key, in a volume nothing else mounts.
DEFAULT_SIGNING_KEY = "/var/lib/nl2sql-auth/session.key"
#: Its public half, in the volume every verifying service mounts read-only.
DEFAULT_PUBLIC_KEY = "/etc/nl2sql/auth/session.pub"

#: The directory's certificate, which it writes into a volume of its own.
DEFAULT_LDAP_CACERT = "/etc/nl2sql/ldap-tls/ldap.crt"

#: The name the certificate must cover for a GUI's proxy to verify this.
SERVICE_HOSTNAME = "nl2sql-auth"

#: Directory group -> Postgres role. A primary whose groups are named
#: otherwise is mapped onto the four local names in the directory replica
#: (LDAP_REPLICA_GROUPS); this maps the local names onto the roles.
DEFAULT_GROUP_ROLES = {
    "nl2sql-users": USERS,
    "nl2sql-reviewers": REVIEWERS,
    "nl2sql-curators": CURATORS,
    "nl2sql-admins": ADMINS,
}


def _env(name: str) -> str | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    return raw.strip()


def _env_str(name: str, default: str) -> str:
    return _env(name) or default


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    return default if raw is None else int(raw)


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    return default if raw is None else float(raw)


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    return default if raw is None else raw.lower() in {"1", "true", "yes", "on"}


def _secret(name: str) -> str | None:
    """`NAME`, or the contents of the file `NAME_FILE` names (Docker secrets)."""
    path = _env(f"{name}_FILE")
    if path:
        return Path(path).read_text().strip() or None
    return _env(name)


def group_roles(raw: str | None) -> dict[str, str]:
    """`group=role` pairs, comma-separated; unset is the four defaults."""
    if raw is None:
        return dict(DEFAULT_GROUP_ROLES)
    mapping = {}
    for part in raw.split(","):
        group, sep, role = part.strip().rpartition("=")
        if sep and group.strip() and role.strip():
            mapping[group.strip()] = role.strip()
    return mapping


@dataclass
class AuthSettings:
    """Everything the auth service runs with."""

    # --- Binding ---------------------------------------------------------
    host: str = "0.0.0.0"
    port: int = DEFAULT_PORT
    #: The directory API's own port; 0 serves it on `port` with the rest,
    #: as 6.0 did.
    directory_port: int = DEFAULT_DIRECTORY_PORT
    root_path: str = ""
    tls_enabled: bool = True
    tls_cert_file: str = f"{DEFAULT_TLS_DIR}/server.crt"
    tls_key_file: str = f"{DEFAULT_TLS_DIR}/server.key"

    # --- Sessions --------------------------------------------------------
    signing_key_file: str = DEFAULT_SIGNING_KEY
    public_key_file: str = DEFAULT_PUBLIC_KEY
    session_hours: float = 8.0
    cookie_name: str = SESSION_COOKIE
    #: Wrong passwords per name before signing in is refused for
    #: `throttle_seconds`. The directory's own lockout (LDAP
    #: LDAP_LOCKOUT_FAILURES) is the one that also covers a direct psql;
    #: this one answers sooner and says so.
    throttle_failures: int = 5
    #: And per address, ten times as many: behind a proxy or Docker's NAT a
    #: whole office can be one address, and five would let one person's
    #: typing lock everybody else out.
    throttle_address_failures: int = 50
    throttle_seconds: int = 900

    # --- The retail database ----------------------------------------------
    #: Where a person's password is checked: a connection as them.
    db_host: str = "nl2sql-postgres"
    db_port: int = 5432
    db_name: str = "nl2sql_retail"
    #: verify-full: the connection is TLS, the certificate is the database's
    #: own (`db_sslrootcert`) and it names the host. A person's password
    #: crosses this hop, in the clear inside the session -- pg_hba's `ldap`
    #: method needs it to bind to the directory -- so nothing less will do
    #: by default (6.1; `prefer` until then, against a server with no TLS).
    db_sslmode: str = "verify-full"
    db_sslrootcert: str = DEFAULT_DB_CACERT
    db_connect_timeout: int = 5
    #: The sync's own login: CREATEROLE, ADMIN on the nl2sql roles only.
    rolesync_url: str | None = None
    #: The agent's read-only role, which may become each person (SET ROLE).
    reader_role: str = "nl2sql_reader"
    group_roles: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_GROUP_ROLES))
    role_sync_interval: float = 30.0
    #: What a person's own login is held to when they connect directly --
    #: with psql, a BI tool. Questions and the console set their own.
    user_statement_timeout_ms: int = 60000
    user_connection_limit: int = 5

    # --- The directory ------------------------------------------------------
    ldap_url: str = "ldap://nl2sql-ldap:389"
    ldap_starttls: bool = True
    ldap_cacert: str | None = DEFAULT_LDAP_CACERT
    ldap_base_dn: str = "dc=nl2sql,dc=local"
    ldap_mode: str = "standalone"
    ldap_service_password: str | None = None
    ldap_timeout: int = 10
    #: The directory's own policy (LDAP_MIN_PASSWORD_LENGTH), read here so a
    #: form can say it before the directory refuses a password for it.
    min_password_length: int = 12

    # --- Who may do what ------------------------------------------------------
    #: MLflow's interface, through its proxy's auth_request.
    mlflow_roles: tuple[str, ...] = (REVIEWERS, ADMINS)

    # --- Presentation ---------------------------------------------------------
    cors_origins: tuple[str, ...] = ()
    docs_enabled: bool = True
    log_level: str = "info"

    #: Set when the values came from the environment, for the banner.
    source: str = field(default="defaults", compare=False)

    @classmethod
    def from_env(cls) -> "AuthSettings":
        return cls(
            host=_env_str("AUTH_HOST", "0.0.0.0"),
            port=_env_int("AUTH_PORT", DEFAULT_PORT),
            directory_port=_env_int("AUTH_DIRECTORY_PORT", DEFAULT_DIRECTORY_PORT),
            root_path=_env_str("AUTH_ROOT_PATH", ""),
            tls_enabled=_env_bool("AUTH_TLS_ENABLED", True),
            tls_cert_file=_env_str("AUTH_TLS_CERT_FILE", f"{DEFAULT_TLS_DIR}/server.crt"),
            tls_key_file=_env_str("AUTH_TLS_KEY_FILE", f"{DEFAULT_TLS_DIR}/server.key"),
            signing_key_file=_env_str("AUTH_SIGNING_KEY_FILE", DEFAULT_SIGNING_KEY),
            public_key_file=_env_str("AUTH_PUBLIC_KEY_FILE", DEFAULT_PUBLIC_KEY),
            session_hours=_env_float("AUTH_SESSION_HOURS", 8.0),
            cookie_name=_env_str("AUTH_COOKIE_NAME", SESSION_COOKIE),
            throttle_failures=_env_int("AUTH_THROTTLE_FAILURES", 5),
            throttle_address_failures=_env_int("AUTH_THROTTLE_ADDRESS_FAILURES", 50),
            throttle_seconds=_env_int("AUTH_THROTTLE_SECONDS", 900),
            db_host=_env_str("AUTH_DB_HOST", "nl2sql-postgres"),
            db_port=_env_int("AUTH_DB_PORT", 5432),
            db_name=_env_str("AUTH_DB_NAME", "nl2sql_retail"),
            db_sslmode=_env_str("AUTH_DB_SSLMODE", "verify-full"),
            db_sslrootcert=_env_str("AUTH_DB_SSLROOTCERT", DEFAULT_DB_CACERT),
            db_connect_timeout=_env_int("AUTH_DB_CONNECT_TIMEOUT", 5),
            rolesync_url=_secret("AUTH_ROLESYNC_DB_URL"),
            reader_role=_env_str("AUTH_READER_ROLE", "nl2sql_reader"),
            group_roles=group_roles(_env("AUTH_GROUP_ROLES")),
            role_sync_interval=_env_float("AUTH_ROLE_SYNC_INTERVAL", 30.0),
            user_statement_timeout_ms=_env_int("AUTH_USER_STATEMENT_TIMEOUT_MS", 60000),
            user_connection_limit=_env_int("AUTH_USER_CONNECTION_LIMIT", 5),
            ldap_url=_env_str("AUTH_LDAP_URL", "ldap://nl2sql-ldap:389"),
            ldap_starttls=_env_bool("AUTH_LDAP_STARTTLS", True),
            ldap_cacert=_env("AUTH_LDAP_CACERT") or DEFAULT_LDAP_CACERT,
            ldap_base_dn=_env_str("LDAP_BASE_DN", "dc=nl2sql,dc=local"),
            ldap_mode=_env_str("LDAP_MODE", "standalone").lower(),
            ldap_service_password=_secret("LDAP_SERVICE_PASSWORD"),
            ldap_timeout=_env_int("AUTH_LDAP_TIMEOUT", 10),
            min_password_length=_env_int("LDAP_MIN_PASSWORD_LENGTH", 12),
            mlflow_roles=tuple(
                part.strip()
                for part in _env_str("MLFLOW_ALLOWED_ROLES", f"{REVIEWERS},{ADMINS}").split(",")
                if part.strip()
            ),
            cors_origins=tuple(
                part.strip() for part in (_env("AUTH_CORS_ORIGINS") or "").split(",") if part.strip()
            ),
            docs_enabled=_env_bool("AUTH_DOCS_ENABLED", True),
            log_level=_env_str("AUTH_LOG_LEVEL", "info"),
            source="environment",
        )

    # --- derived ---------------------------------------------------------

    @property
    def replica(self) -> bool:
        """A replica's people are the primary's: nothing here edits them."""
        return self.ldap_mode == "replica"

    @property
    def session_seconds(self) -> int:
        return int(self.session_hours * 3600)

    @property
    def db_ssl(self) -> dict[str, str]:
        """The TLS settings every connection to the retail database takes."""
        settings = {"sslmode": self.db_sslmode}
        if self.db_sslmode in VERIFYING_SSLMODES:
            settings["sslrootcert"] = self.db_sslrootcert
        return settings

    @property
    def rolesync_conninfo(self) -> str | None:
        """The role sync's URL, held to the same TLS as a sign-in.

        Its password crosses the same hop. A URL that already says how to
        connect (`sslmode=` in its query) is left as it was.
        """
        if not self.rolesync_url:
            return None
        parts = urlsplit(self.rolesync_url)
        query = dict(parse_qsl(parts.query))
        if "sslmode" in query:
            return self.rolesync_url
        query.update(self.db_ssl)
        return urlunsplit(parts._replace(query=urlencode(query)))

    @property
    def certificate_present(self) -> bool:
        return Path(self.tls_cert_file).is_file() and Path(self.tls_key_file).is_file()

    def public_url(self) -> str:
        scheme = "https" if self.tls_enabled else "http"
        shown = "localhost" if self.host in ("0.0.0.0", "::", "") else self.host
        return f"{scheme}://{shown}:{self.port}{self.root_path}"

    def problems(self) -> list[str]:
        """Reasons the service would only fail at its first use. Said at start."""
        found = []
        if not self.rolesync_url:
            found.append(
                "AUTH_ROLESYNC_DB_URL is not set: nobody in the directory can be made a "
                "role in the retail database, so nobody can sign in"
            )
        if not self.ldap_service_password:
            found.append(
                "LDAP_SERVICE_PASSWORD is not set: the directory cannot be read, so the "
                "role sync has nobody to sync"
            )
        return found

    def warnings(self) -> list[str]:
        notes = []
        if not self.tls_enabled:
            notes.append(
                "TLS is disabled (AUTH_TLS_ENABLED=false): passwords cross the network in "
                "clear text. Only do this behind something that terminates TLS itself."
            )
        if not self.ldap_starttls and not self.ldap_url.lower().startswith("ldaps://"):
            notes.append(
                "AUTH_LDAP_STARTTLS=false with a plain ldap:// URL: the directory will "
                "refuse the service's password, which it only accepts encrypted."
            )
        if self.db_sslmode in ("disable", "allow", "prefer"):
            notes.append(
                f"AUTH_DB_SSLMODE={self.db_sslmode}: a password being checked may reach the "
                "retail database unencrypted -- pg_hba's ldap method sends it in clear "
                "inside whatever the connection is. The database serves TLS and its sign-in "
                "rule is hostssl, so this only works against one configured otherwise. "
                "Use verify-full."
            )
        elif self.db_sslmode == "require":
            notes.append(
                "AUTH_DB_SSLMODE=require: encrypted, but nothing checks that the server "
                "is the retail database, so a password could be handed to whatever answers "
                "at its address. Use verify-full with AUTH_DB_SSLROOTCERT."
            )
        if self.db_sslmode in VERIFYING_SSLMODES and not Path(self.db_sslrootcert).is_file():
            notes.append(
                f"AUTH_DB_SSLMODE={self.db_sslmode} and there is no certificate at "
                f"{self.db_sslrootcert} to verify the retail database against, so every "
                "sign-in will fail. The database writes it into the pgtls volume on its "
                "first start; mount that here."
            )
        return notes


#: The dataclass fields, so a test can prove none lacks a variable.
SETTING_FIELDS = tuple(f.name for f in fields(AuthSettings) if f.name != "source")
