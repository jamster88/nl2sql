"""Settings for the SQL console, kept apart from the agent's and the API's.

Two kinds of setting, and the split is the point of the module.

**The agent's own**, read through `config.Settings` and never given a name of
their own here: the database the agent reads (`DATABASE_URL`), the schema it
introspects, its statement timeout, its row cap, its plan-cost ceiling and
the sample rows its prompt carries. A console that ran under different
limits would answer a different question from the one being troubleshot --
a query that times out for the agent and not here is not reproduced, it is
hidden. `AGENT_SETTINGS` lists them, and compose passes the agent's values
for exactly those.

**The console's own**, `CONSOLE_*`, which exist only because there is a
socket: where it listens, its TLS, its token, and how many rows it will send
a browser. That last one is deliberately not `MAX_ROWS`: the agent reads 50
rows and a person looking for why an answer was wrong usually needs to see
the 51st. What the agent would have made of the extra rows is reported
beside them rather than hidden by them.

Same environment discipline as `config.py`, for the same reason: compose
forwards a variable the host has not set as an empty string, so empty has to
read as absent.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields

from nl2sql_identity import CURATORS, DEFAULT_PUBLIC_KEY_FILE, REVIEWERS, SESSION_COOKIE

from ..api.settings import DEFAULT_CERT_FILE, DEFAULT_KEY_FILE, _env_tuple
from ..config import _env, _env_bool, _env_int, _env_str

#: 8445: the API is 8443 and the review service 8444, and this is the third
#: of the same kind of process.
DEFAULT_PORT = 8445

#: The name the certificate must cover for the console interface's proxy to
#: verify this process. Compose adds it to API_TLS_HOSTNAMES; named here so
#: the banner can say which name is missing rather than "handshake failed".
SERVICE_HOSTNAME = "nl2sql-console"

#: Rows sent to the browser for one query. Enough to see past the agent's
#: own cap of 50, not so many that a careless `SELECT *` over the sales fact
#: becomes a page that will not render. The rest are never fetched: the
#: query is read through a server-side cursor.
DEFAULT_MAX_ROWS = 1000

#: The longest statement accepted, in characters. The agent's own queries
#: are a few hundred; this is room for a hand-written one with every CTE it
#: needs, and a bound on what one request can make the parser read.
MAX_SQL_LENGTH = 20000

#: The agent settings the console reads, by environment variable. Compose
#: passes each of these with the agent's own value, and a test holds the
#: code to reading exactly these and nothing else of the agent's.
AGENT_SETTINGS: tuple[str, ...] = (
    "DATABASE_URL",
    "DB_SCHEMA",
    "MAX_ROWS",
    "SAMPLE_ROWS",
    "STATEMENT_TIMEOUT_MS",
    "MAX_PLAN_COST",
)


@dataclass
class ConsoleSettings:
    """Everything about the console's socket, none of it about the database."""

    # --- Binding ---------------------------------------------------------
    host: str = "0.0.0.0"
    port: int = DEFAULT_PORT
    root_path: str = ""

    # --- TLS -------------------------------------------------------------
    # The certificate the agent API generates, presented rather than copied:
    # this process never writes one. The same volume, mounted read-only.
    tls_enabled: bool = True
    tls_cert_file: str = DEFAULT_CERT_FILE
    tls_key_file: str = DEFAULT_KEY_FILE

    # --- Who may call ----------------------------------------------------
    # Unset means no authentication. That is a working configuration on one
    # machine and a warning everywhere else: anyone who can reach the port
    # can run SQL as the agent's role.
    token: str | None = None
    # Empty by default, unlike the API. The interface is same-origin behind
    # its own nginx, and a SQL runner that any page in the browser could
    # call is not something to offer without being asked.
    cors_origins: tuple[str, ...] = ()
    # Sign-in (the auth service). On, a person's session is what every /v1
    # route needs, they must hold one of `allowed_roles`, and their query runs
    # as their own database role -- the role the agent runs their questions
    # as, which keeps this the database as the agent sees it, for them.
    # CONSOLE_TOKEN still works, for scripts, as the agent's reader.
    auth_enabled: bool = False
    auth_public_key_file: str = DEFAULT_PUBLIC_KEY_FILE
    auth_cookie_name: str = SESSION_COOKIE
    allowed_roles: tuple[str, ...] = (REVIEWERS, CURATORS)

    # --- What a query may return ----------------------------------------
    max_rows: int = DEFAULT_MAX_ROWS

    # --- Presentation ----------------------------------------------------
    docs_enabled: bool = True
    log_level: str = "info"

    #: Set when the values came from the environment, purely so the banner
    #: can say so. Not part of the contract.
    source: str = field(default="defaults", compare=False)

    @classmethod
    def from_env(cls) -> "ConsoleSettings":
        return cls(
            host=_env_str("CONSOLE_HOST", "0.0.0.0"),
            port=_env_int("CONSOLE_PORT", DEFAULT_PORT),
            root_path=_env_str("CONSOLE_ROOT_PATH", ""),
            tls_enabled=_env_bool("CONSOLE_TLS_ENABLED", True),
            tls_cert_file=_env_str("CONSOLE_TLS_CERT_FILE", DEFAULT_CERT_FILE),
            tls_key_file=_env_str("CONSOLE_TLS_KEY_FILE", DEFAULT_KEY_FILE),
            token=_env("CONSOLE_TOKEN"),
            cors_origins=_env_tuple("CONSOLE_CORS_ORIGINS", ()),
            auth_enabled=_env_bool("AUTH_ENABLED", False),
            auth_public_key_file=_env_str("AUTH_PUBLIC_KEY_FILE", DEFAULT_PUBLIC_KEY_FILE),
            auth_cookie_name=_env_str("AUTH_COOKIE_NAME", SESSION_COOKIE),
            allowed_roles=_env_tuple("CONSOLE_ALLOWED_ROLES", (REVIEWERS, CURATORS)),
            max_rows=_env_int("CONSOLE_MAX_ROWS", DEFAULT_MAX_ROWS),
            docs_enabled=_env_bool("CONSOLE_DOCS_ENABLED", True),
            log_level=_env_str("CONSOLE_LOG_LEVEL", "info"),
            source="environment",
        )

    # --- derived ---------------------------------------------------------

    @property
    def scheme(self) -> str:
        return "https" if self.tls_enabled else "http"

    def public_url(self) -> str:
        shown = "localhost" if self.host in ("0.0.0.0", "::", "") else self.host
        return f"{self.scheme}://{shown}:{self.port}{self.root_path}"

    def warnings(self) -> list[str]:
        """Configurations that will work and probably should not.

        Returned rather than logged so the banner, `/v1/meta` and the tests
        all see exactly the same list.
        """
        notes: list[str] = []
        if not self.tls_enabled:
            notes.append(
                "TLS is disabled (CONSOLE_TLS_ENABLED=false): SQL and rows cross the "
                "network in clear text. Only do this behind something that "
                "terminates TLS itself."
            )
        if not self.token and not self.auth_enabled:
            notes.append(
                "No CONSOLE_TOKEN is set and sign-in is off (AUTH_ENABLED=false), so anyone "
                "who can reach the port can run SQL as the agent's database role."
            )
        if self.token and "*" in self.cors_origins:
            notes.append(
                "CONSOLE_CORS_ORIGINS is '*' while a token is required; browsers refuse "
                "to send credentials to a wildcard origin. List the origin instead."
            )
        return notes


#: The dataclass fields, so a test can prove none has been added without an
#: environment variable to set it with.
SETTING_FIELDS = tuple(f.name for f in fields(ConsoleSettings) if f.name != "source")
