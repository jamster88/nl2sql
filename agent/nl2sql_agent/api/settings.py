"""Settings for the REST server, kept apart from the agent's own.

`config.py` answers "how should the pipeline behave"; this answers "how
should it be reachable". They are separate dataclasses because they are
separately owned: the pipeline settings are the same whether a question
arrives from the CLI or from a browser, while every one of these exists
only because there is a socket.

Same environment discipline as `config.py`, and for the same reason --
Compose forwards a variable the host has not set as an empty string, so
empty has to read as absent or every forwarded knob would override its own
default with nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields

from nl2sql_identity import DEFAULT_PUBLIC_KEY_FILE, SESSION_COOKIE, USERS

from nl2sql_common.env import env as _env
from nl2sql_common.env import env_bool as _env_bool
from nl2sql_common.env import env_float as _env_float
from nl2sql_common.env import env_int as _env_int
from nl2sql_common.env import env_str as _env_str
from nl2sql_common.env import env_tuple as _env_tuple
from nl2sql_common.env import env_url as _env_url
from nl2sql_common.env import secret as _secret

#: Where the dummy certificate is written inside the container. A directory
#: rather than the working tree: it is a volume in compose, so a regenerated
#: certificate survives a container restart and a client that pinned the
#: fingerprint keeps working.
DEFAULT_TLS_DIR = "/etc/nl2sql/tls"
DEFAULT_CERT_FILE = f"{DEFAULT_TLS_DIR}/server.crt"
DEFAULT_KEY_FILE = f"{DEFAULT_TLS_DIR}/server.key"

#: 8443, not 443: the server runs unprivileged, and the port a reverse proxy
#: or a GUI's dev server talks to is configuration either way.
DEFAULT_PORT = 8443

#: Names the generated certificate is valid for. `nl2sql-api` is the compose
#: service, which is how another container reaches it; the rest are how the
#: machine running Docker does.
DEFAULT_HOSTNAMES = ("localhost", "nl2sql-api", "api", "127.0.0.1", "::1")


@dataclass
class ApiSettings:
    """Everything about the socket, none of it about the pipeline."""

    # --- Binding ---------------------------------------------------------
    host: str = "0.0.0.0"
    port: int = DEFAULT_PORT
    # Set when the API is served under a path prefix by a reverse proxy, so
    # the OpenAPI document and the Location headers stay correct.
    root_path: str = ""

    # --- TLS -------------------------------------------------------------
    # On by default. Turning it off is for one situation only: something
    # else already terminates TLS in front of this process.
    tls_enabled: bool = True
    tls_cert_file: str = DEFAULT_CERT_FILE
    tls_key_file: str = DEFAULT_KEY_FILE
    # Write a dummy certificate when none is present. This is what makes the
    # container start with TLS out of the box and no ceremony.
    tls_generate: bool = True
    # The switch that takes the dummy certificates away. With this false the
    # server refuses to start behind a self-signed certificate at all --
    # neither generating one nor loading one it finds -- so a deployment that
    # is supposed to have a real chain fails loudly instead of quietly
    # serving the throwaway one from development.
    tls_allow_self_signed: bool = True
    tls_hostnames: tuple[str, ...] = DEFAULT_HOSTNAMES
    tls_days: int = 365

    # --- Who may call ----------------------------------------------------
    # A static token for scripts and other services: with it set, every /v1
    # route also accepts `Authorization: Bearer ...` or `X-API-Key: ...`.
    token: str | None = None
    # Who the token is, and what it may do (V6-62): its questions and jobs
    # are recorded under `token:<name>`, and it holds these roles and no
    # others. Asking takes nl2sql_users; reloading the caches takes
    # nl2sql_admins, which a token holds only when it is named here.
    token_name: str = "api-token"
    token_roles: tuple[str, ...] = (USERS,)
    # Browser origins allowed to call this API directly. None by default:
    # every page of this stack reaches the API through its own nginx, on its
    # own origin, so a cross-origin browser call is something to allow by
    # name (API_CORS_ORIGINS), never by default.
    cors_origins: tuple[str, ...] = ()
    # Forwarding a caller-chosen database principal means letting an HTTP
    # client pick the role rows are read as. Off unless someone decides
    # otherwise: the default identity is the agent's own read-only role.
    # Never for a signed-in person, whose principal is themselves.
    allow_principal: bool = False
    # Who sees a failure in its own words -- a driver's error, a host, a
    # port, the readiness detail (V6-32). An administrator does; anyone else
    # sees which part failed. True shows everyone, for a development server.
    debug_detail: bool = False
    # Sign-in (the auth service). On -- the default, here as well as in
    # compose, so a server started any other way is not open by accident --
    # a person's session (the cookie a GUI sends, or the bearer the desktop
    # client holds) is what every /v1 route needs, their question runs as
    # their own database role, and they see only their own questions. The
    # token above still works, for the machines that have no person to sign
    # in. Off (AUTH_ENABLED=false) is the open server of 5.x, and the banner
    # says so.
    auth_enabled: bool = True
    auth_public_key_file: str = DEFAULT_PUBLIC_KEY_FILE
    auth_cookie_name: str = SESSION_COOKIE

    # --- Work ------------------------------------------------------------
    # Questions in flight at once. Two, not one, so a browser polling a
    # second tab is not blocked behind a 60-second run -- and not many more,
    # because they all queue on the same Ollama host anyway.
    max_concurrency: int = 2
    # Finished jobs are kept this long so a GUI that reconnects can still
    # collect the answer it asked for.
    job_ttl_seconds: int = 3600
    max_jobs: int = 200
    # Questions waiting behind those running. Past this a new one is refused
    # with 429 and Retry-After rather than queued: a queue that only grows
    # is a promise the model host cannot keep.
    max_queued: int = 20
    # What one signed-in person may have waiting or running at once, so one
    # person's loop cannot fill the queue for everyone else.
    max_per_person: int = 3
    # A question still waiting after this long is failed rather than run for
    # a caller who has very likely gone.
    queue_ttl_seconds: int = 600
    # The ceiling on `POST /v1/questions?wait=`. A question takes about a
    # minute; this is the point past which a caller should be using the job
    # or the event stream instead of holding a socket open.
    max_wait_seconds: float = 900.0
    # How long an idle event stream is held open before the server closes it
    # and invites the client to reconnect from the last sequence number.
    event_stream_timeout_seconds: float = 300.0
    # A comment line on the stream this often, so proxies and load balancers
    # that drop idle connections see traffic.
    keepalive_seconds: float = 15.0

    # --- Feedback --------------------------------------------------------
    # The staging database a verdict is written to, as the INSERT-only
    # `nl2sql_feedback_writer` role. Unset means the feedback routes answer
    # 503 with a reason -- they still exist, so a generated client does not
    # change shape depending on whether the server it met had feedback on.
    #
    # This is the only write credential this process holds, and the role it
    # names can see nothing a curator has already judged. See
    # `nl2sql_review.store.ensure_writer_role` for what fences it.
    feedback_db_url: str | None = None

    # --- Presentation ----------------------------------------------------
    docs_enabled: bool = True
    log_level: str = "info"

    #: Set when the values came from the environment, purely so the banner
    #: can say so. Not part of the contract.
    source: str = field(default="defaults", compare=False)

    @classmethod
    def from_env(cls) -> "ApiSettings":
        return cls(
            host=_env_str("API_HOST", "0.0.0.0"),
            port=_env_int("API_PORT", DEFAULT_PORT),
            root_path=_env_str("API_ROOT_PATH", ""),
            tls_enabled=_env_bool("API_TLS_ENABLED", True),
            tls_cert_file=_env_str("API_TLS_CERT_FILE", DEFAULT_CERT_FILE),
            tls_key_file=_env_str("API_TLS_KEY_FILE", DEFAULT_KEY_FILE),
            tls_generate=_env_bool("API_TLS_GENERATE", True),
            tls_allow_self_signed=_env_bool("API_TLS_ALLOW_SELF_SIGNED", True),
            tls_hostnames=_env_tuple("API_TLS_HOSTNAMES", DEFAULT_HOSTNAMES),
            tls_days=_env_int("API_TLS_DAYS", 365),
            token=_secret("API_TOKEN"),
            token_name=_env_str("API_TOKEN_NAME", "api-token"),
            token_roles=_env_tuple("API_TOKEN_ROLES", (USERS,)),
            cors_origins=_env_tuple("API_CORS_ORIGINS", ()),
            allow_principal=_env_bool("API_ALLOW_PRINCIPAL", False),
            debug_detail=_env_bool("API_DEBUG_DETAIL", False),
            auth_enabled=_env_bool("AUTH_ENABLED", True),
            auth_public_key_file=_env_str("AUTH_PUBLIC_KEY_FILE", DEFAULT_PUBLIC_KEY_FILE),
            auth_cookie_name=_env_str("AUTH_COOKIE_NAME", SESSION_COOKIE),
            max_concurrency=_env_int("API_MAX_CONCURRENCY", 2),
            job_ttl_seconds=_env_int("API_JOB_TTL_SECONDS", 3600),
            max_jobs=_env_int("API_MAX_JOBS", 200),
            max_queued=_env_int("API_MAX_QUEUED", 20),
            max_per_person=_env_int("API_MAX_PER_PERSON", 3),
            queue_ttl_seconds=_env_int("API_QUEUE_TTL_SECONDS", 600),
            max_wait_seconds=_env_float("API_MAX_WAIT_SECONDS", 900.0),
            event_stream_timeout_seconds=_env_float("API_EVENT_STREAM_TIMEOUT_SECONDS", 300.0),
            keepalive_seconds=_env_float("API_KEEPALIVE_SECONDS", 15.0),
            feedback_db_url=_env_url("API_FEEDBACK_DB_URL"),
            docs_enabled=_env_bool("API_DOCS_ENABLED", True),
            log_level=_env_str("API_LOG_LEVEL", "info"),
            source="environment",
        )

    # --- derived ---------------------------------------------------------

    @property
    def scheme(self) -> str:
        return "https" if self.tls_enabled else "http"

    @property
    def authenticated(self) -> bool:
        return bool(self.token)

    def public_url(self, host: str | None = None) -> str:
        shown = host or ("localhost" if self.host in ("0.0.0.0", "::", "") else self.host)
        return f"{self.scheme}://{shown}:{self.port}{self.root_path}"

    def warnings(self) -> list[str]:
        """Configurations that will work and probably should not.

        Returned rather than logged so the banner, the readiness endpoint and
        the tests all see exactly the same list.
        """
        notes: list[str] = []
        if not self.tls_enabled:
            notes.append(
                "TLS is disabled (API_TLS_ENABLED=false): questions, SQL and rows "
                "cross the network in clear text. Only do this behind something "
                "that terminates TLS itself."
            )
        if not self.token and not self.auth_enabled:
            notes.append(
                "This server is OPEN: sign-in is off (AUTH_ENABLED=false) and no "
                "API_TOKEN is set, so every caller that can reach the port can ask "
                "questions."
            )
        if self.token and "*" in self.cors_origins:
            notes.append(
                "API_CORS_ORIGINS is '*' while a token is required; browsers refuse "
                "to send credentials to a wildcard origin. List the GUI's origin."
            )
        if self.feedback_db_url and not self.token and not self.auth_enabled:
            notes.append(
                "API_FEEDBACK_DB_URL is set while no API_TOKEN is, so anyone who can "
                "reach the port can write rows into the feedback staging database."
            )
        if self.allow_principal:
            notes.append(
                "API_ALLOW_PRINCIPAL is on: callers choose the database role their "
                "rows are read as. Only safe when something in front of this has "
                "already authenticated them."
            )
        return notes


#: The dataclass fields, so a test can prove none has been added without an
#: environment variable to set it with.
SETTING_FIELDS = tuple(f.name for f in fields(ApiSettings) if f.name != "source")
