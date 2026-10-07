"""Settings for the review service.

Same environment discipline as the agent's `api/settings.py`, and for the
same reason: Compose forwards a variable the host has not set as an empty
string, so empty has to read as absent or every forwarded knob overrides its
own default with nothing.

What this service is allowed to touch is most of what is configured here,
because it is the process with the dangerous powers in this system. It can
write the golden question document and reload the stores built from it. The
agent's API -- the process actually exposed to a browser -- holds none of
that; it has an INSERT-only role on one table. Keeping the two apart is the
point of there being two services, so the settings that grant the powers
live here and are never read by the other one.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path

from nl2sql_identity import CURATORS, DEFAULT_PUBLIC_KEY_FILE, REVIEWERS, SESSION_COOKIE
from nl2sql_common.env import (
    env_str as _env_str,
    env_int as _env_int,
    env_float as _env_float,
    env_bool as _env_bool,
    env_tuple as _env_tuple,
    env_url as _env_url,
    secret as _secret,
)

#: The document that *is* the golden set. Everything downstream -- the
#: context store rows, the BM25 statistics, both vector tables -- is built
#: from it, and step 5 deletes rows whose pair is no longer in it. So this
#: file is what promotion writes, and the databases follow.
DEFAULT_DOCUMENT = "/app/context_questions/translated_questions.md"

#: Where `rag/`'s loader scripts live in the image. Promotion runs them as
#: scripts, from this directory, because that is how `rag/README.md`
#: documents them and how their own tests exercise them -- re-implementing
#: the upsert, the delete and the BM25 rebuild here would be a second place
#: for the loading rules to live and drift.
DEFAULT_RAG_DIR = "/app/rag"

DEFAULT_PORT = 8444

#: The retail database a corrected query is validated against, as the
#: read-only role the agent itself uses. Validation *runs* the reviewer's SQL,
#: and running a stranger's SQL is exactly what `nl2sql_reader` exists for:
#: SELECT on the retail tables and nothing else, read-only by default.
DEFAULT_RETAIL_DB_URL = "postgresql://nl2sql_reader:nl2sql_reader@localhost:5432/nl2sql_retail"

#: The two stores a reviewer's fix goes into, one per verdict: a *wrong*
#: answer's correction and a *correct but incomplete* answer's completion.
#: Each is its own Postgres with pgvector -- its records and its RAG side by
#: side -- apart from the golden set and apart from each other.
DEFAULT_CORRECTIONS_DB_URL = "postgresql://corrections:corrections@localhost:5435/nl2sql_corrections"
DEFAULT_COMPLETIONS_DB_URL = "postgresql://completions:completions@localhost:5435/nl2sql_completions"

#: The SQL snippets' source of truth, as the golden question document is the
#: golden pairs': written here, then loaded into the snippet store.
DEFAULT_SNIPPETS_DOCUMENT = "/app/context_questions/sql_snippets.md"

#: The snippet store, as its owner: the loader creates its tables and the
#: read-only role the agent connects as.
DEFAULT_SNIPPETS_DB_URL = "postgresql://snippets:snippets@localhost:5435/nl2sql_snippets"

#: This service's own certificate, which the stack's pki service issues from
#: its development CA (6.1; until then it presented the agent API's). Mounted
#: read-only: this process presents it and never writes one.
DEFAULT_TLS_DIR = "/etc/nl2sql/tls"
DEFAULT_CERT_FILE = f"{DEFAULT_TLS_DIR}/server.crt"
DEFAULT_KEY_FILE = f"{DEFAULT_TLS_DIR}/server.key"

#: The name the certificate must cover for another container to verify this
#: one. Compose gives it to the pki service in REVIEW_TLS_HOSTNAMES; named
#: here so the readiness check can say which name is missing rather than
#: "handshake failed".
SERVICE_HOSTNAME = "nl2sql-review"


@dataclass
class ReviewSettings:
    """Everything the review service needs, and nothing about the pipeline."""

    # --- Binding ---------------------------------------------------------
    host: str = "0.0.0.0"
    port: int = DEFAULT_PORT
    root_path: str = ""

    # --- TLS -------------------------------------------------------------
    # This service presents a certificate of its own, issued by the stack's
    # pki service and mounted read-only (6.1; until then it presented the
    # agent API's, whose key was then in eleven containers). It does not
    # generate one: the issuing is the pki service's, so there is one copy of
    # the certificate code and one CA that every client trusts.
    #
    # The consequence is a configuration requirement rather than a code one.
    # The certificate has to be valid for this service's name, so compose
    # gives the pki service `nl2sql-review` in REVIEW_TLS_HOSTNAMES.
    # `warnings()` says so when the file is not where it should be, and the
    # server refuses to start with TLS claimed and no certificate to present
    # -- which is the failure that would
    # otherwise look like a working HTTPS URL.
    tls_enabled: bool = True
    tls_cert_file: str = DEFAULT_CERT_FILE
    tls_key_file: str = DEFAULT_KEY_FILE

    # --- Who may call ----------------------------------------------------
    # This one is not optional in the way the agent's is. A caller here can
    # write the golden question set; `warnings()` says so loudly when it is
    # unset, and the readiness endpoint repeats it.
    token: str | None = None
    # Who the token is, and what it may do (V6-62). What it does is
    # recorded under `token:<name>` -- never under the X-Reviewer it sends.
    # Empty roles: with sign-in on, the reviewer roles only, and curating by
    # token is something to grant here by name; with it off, both, because
    # the review and curation pages' proxies then send this token for
    # everyone who uses them.
    token_name: str = "review-token"
    token_roles: tuple[str, ...] = ()
    # None by default: the review and curation pages reach this through their
    # own nginx, on their own origin, so a cross-origin browser call is
    # something to allow by name (REVIEW_CORS_ORIGINS).
    cors_origins: tuple[str, ...] = ()
    # Sign-in (the auth service). On, a person's session is what every /v1
    # route needs: the review routes take a reviewer, the curation routes a
    # curator, and reading takes either. What they do is recorded as done by
    # them -- not by whatever name a header claimed -- and the SQL they run
    # to check a fix or a snippet runs as their own database role.
    # REVIEW_TOKEN still works, for scripts, as itself. On by default
    # in the code as well as in compose, so a review service started any
    # other way is not open by accident.
    auth_enabled: bool = True
    auth_public_key_file: str = DEFAULT_PUBLIC_KEY_FILE
    auth_cookie_name: str = SESSION_COOKIE
    reviewer_roles: tuple[str, ...] = (REVIEWERS,)
    curator_roles: tuple[str, ...] = (CURATORS,)

    # --- The staging database -------------------------------------------
    # As the owner: this service creates the schema and the writer role.
    feedback_db_url: str = "postgresql://feedback:feedback@localhost:5435/nl2sql_feedback"
    #: The password handed to `nl2sql_feedback_writer` when it is recreated
    #: on start. The agent API is given the same one, as its whole URL.
    writer_password: str = "nl2sql_feedback_writer"
    #: Create the schema and the writer role on start. Off for a deployment
    #: where a DBA owns the schema and the service should not touch it.
    manage_schema: bool = True

    # --- Corrections and completions --------------------------------------
    # A wrong answer, or one that was right and incomplete, is not promoted
    # into the golden set. The reviewer writes the SQL that should have been
    # generated, it is run against the live retail database, and only a query
    # that runs goes into the store for its verdict.
    retail_db_url: str = DEFAULT_RETAIL_DB_URL
    #: A validation is a query somebody typed, so it gets the agent's own
    #: limits: a statement timeout and a row cap.
    validate_timeout_ms: int = 30000
    validate_max_rows: int = 200
    corrections_db_url: str = DEFAULT_CORRECTIONS_DB_URL
    completions_db_url: str = DEFAULT_COMPLETIONS_DB_URL
    #: Embed each stored fix's question into its store's RAG table. Off, the
    #: record is kept and the vector is written by the next save that can.
    embed_fixes: bool = True

    # --- Promotion -------------------------------------------------------
    document: str = DEFAULT_DOCUMENT
    rag_dir: str = DEFAULT_RAG_DIR
    #: Run `05_load_golden_pairs.py` after writing the document. Without it
    #: the pair is in the source of truth and not yet in the context store,
    #: which is a valid state -- just not a live one.
    reload_context: bool = True
    #: Run `06_embed_golden_pairs.py` after that. Needs an embedding host,
    #: so it is the step most likely to be off in a deployment without one.
    reload_vectors: bool = True
    #: A loader that hangs must not hold the HTTP request forever.
    reload_timeout_seconds: float = 600.0
    chunk_db_url: str = "postgresql://ragproc:ragproc@localhost:5433/nl2sql_chunks"
    vector_db_url: str = "postgresql://ragproc:ragproc@localhost:5434/nl2sql_vectors"
    ollama_url: str = "http://localhost:11434"
    embed_model: str = "bge-m3"

    # --- SQL snippets ----------------------------------------------------
    # Joins, filters, measures and dimensions beside what they mean, written
    # by the curation interface: validated against the retail database, then
    # written into their document, then loaded into their store by
    # `07_load_snippets.py` -- the order a promotion follows.
    snippets_document: str = DEFAULT_SNIPPETS_DOCUMENT
    snippets_db_url: str = DEFAULT_SNIPPETS_DB_URL
    #: The role the loader (re)creates for the agent, and its password: the
    #: agent's SNIPPET_DB_URL names the same two.
    snippets_reader_user: str = "snippets_reader"
    snippets_reader_password: str = "snippets_reader"
    #: Run the loader after writing the document. Off, a snippet is in the
    #: source of truth and not yet in the store the agent reads.
    reload_snippets: bool = True

    # --- Presentation ----------------------------------------------------
    docs_enabled: bool = True
    log_level: str = "info"

    source: str = field(default="defaults", compare=False)

    @classmethod
    def from_env(cls) -> "ReviewSettings":
        return cls(
            host=_env_str("REVIEW_HOST", "0.0.0.0"),
            port=_env_int("REVIEW_PORT", DEFAULT_PORT),
            root_path=_env_str("REVIEW_ROOT_PATH", ""),
            tls_enabled=_env_bool("REVIEW_TLS_ENABLED", True),
            tls_cert_file=_env_str("REVIEW_TLS_CERT_FILE", DEFAULT_CERT_FILE),
            tls_key_file=_env_str("REVIEW_TLS_KEY_FILE", DEFAULT_KEY_FILE),
            token=_secret("REVIEW_TOKEN"),
            token_name=_env_str("REVIEW_TOKEN_NAME", "review-token"),
            token_roles=_env_tuple("REVIEW_TOKEN_ROLES", ()),
            cors_origins=_env_tuple("REVIEW_CORS_ORIGINS", ()),
            auth_enabled=_env_bool("AUTH_ENABLED", True),
            auth_public_key_file=_env_str("AUTH_PUBLIC_KEY_FILE", DEFAULT_PUBLIC_KEY_FILE),
            auth_cookie_name=_env_str("AUTH_COOKIE_NAME", SESSION_COOKIE),
            reviewer_roles=_env_tuple("REVIEW_REVIEWER_ROLES", (REVIEWERS,)),
            curator_roles=_env_tuple("REVIEW_CURATOR_ROLES", (CURATORS,)),
            feedback_db_url=_env_url("FEEDBACK_DB_URL", "postgresql://feedback:feedback@localhost:5435/nl2sql_feedback"),
            writer_password=_secret("FEEDBACK_WRITER_PASSWORD") or "nl2sql_feedback_writer",
            manage_schema=_env_bool("REVIEW_MANAGE_SCHEMA", True),
            retail_db_url=_env_url("RETAIL_DB_URL", DEFAULT_RETAIL_DB_URL),
            validate_timeout_ms=_env_int("REVIEW_VALIDATE_TIMEOUT_MS", 30000),
            validate_max_rows=_env_int("REVIEW_VALIDATE_MAX_ROWS", 200),
            corrections_db_url=_env_url("CORRECTIONS_DB_URL", DEFAULT_CORRECTIONS_DB_URL),
            completions_db_url=_env_url("COMPLETIONS_DB_URL", DEFAULT_COMPLETIONS_DB_URL),
            embed_fixes=_env_bool("REVIEW_EMBED_FIXES", True),
            document=_env_str("REVIEW_DOCUMENT", DEFAULT_DOCUMENT),
            rag_dir=_env_str("REVIEW_RAG_DIR", DEFAULT_RAG_DIR),
            reload_context=_env_bool("REVIEW_RELOAD_CONTEXT", True),
            reload_vectors=_env_bool("REVIEW_RELOAD_VECTORS", True),
            reload_timeout_seconds=_env_float("REVIEW_RELOAD_TIMEOUT_SECONDS", 600.0),
            chunk_db_url=_env_url("CHUNK_DB_URL", "postgresql://ragproc:ragproc@localhost:5433/nl2sql_chunks"),
            vector_db_url=_env_url("VECTOR_DB_URL", "postgresql://ragproc:ragproc@localhost:5434/nl2sql_vectors"),
            ollama_url=_env_str("OLLAMA_URL", "http://localhost:11434"),
            embed_model=_env_str("EMBED_MODEL", "bge-m3"),
            snippets_document=_env_str("REVIEW_SNIPPETS_DOCUMENT", DEFAULT_SNIPPETS_DOCUMENT),
            snippets_db_url=_env_url("SNIPPETS_DB_URL", DEFAULT_SNIPPETS_DB_URL),
            snippets_reader_user=_env_str("SNIPPETS_READER_USER", "snippets_reader"),
            snippets_reader_password=_secret("SNIPPETS_READER_PASSWORD") or "snippets_reader",
            reload_snippets=_env_bool("REVIEW_RELOAD_SNIPPETS", True),
            docs_enabled=_env_bool("REVIEW_DOCS_ENABLED", True),
            log_level=_env_str("REVIEW_LOG_LEVEL", "info"),
            source="environment",
        )

    # --- derived ---------------------------------------------------------

    @property
    def scheme(self) -> str:
        return "https" if self.tls_enabled else "http"

    @property
    def document_path(self) -> Path:
        return Path(self.document)

    @property
    def snippets_document_path(self) -> Path:
        return Path(self.snippets_document)

    @property
    def certificate_present(self) -> bool:
        """Both halves, not just the certificate.

        A certificate without its key is the configuration that starts, looks
        right in a `ls`, and fails at the first handshake.
        """
        return Path(self.tls_cert_file).is_file() and Path(self.tls_key_file).is_file()

    def public_url(self, host: str | None = None) -> str:
        shown = host or ("localhost" if self.host in ("0.0.0.0", "::", "") else self.host)
        return f"{self.scheme}://{shown}:{self.port}{self.root_path}"

    def token_holds(self) -> frozenset[str]:
        """The roles REVIEW_TOKEN holds: REVIEW_TOKEN_ROLES, or the default above."""
        if self.token_roles:
            return frozenset(self.token_roles)
        if self.auth_enabled:
            return frozenset(self.reviewer_roles)
        return frozenset((*self.reviewer_roles, *self.curator_roles))

    def warnings(self) -> list[str]:
        """Configurations that will work and probably should not."""
        notes: list[str] = []
        if not self.token and not self.auth_enabled:
            notes.append(
                "This review service is OPEN: sign-in is off (AUTH_ENABLED=false) and "
                "no REVIEW_TOKEN is set, so anyone who can reach this port can edit "
                "and promote golden questions. This service writes the question set "
                "the agent is measured against; it is not the one to leave open."
            )
        if not self.tls_enabled:
            notes.append(
                "TLS is disabled (REVIEW_TLS_ENABLED=false): the review token and "
                "every submission cross the network in clear text."
            )
        elif not self.certificate_present:
            notes.append(
                f"REVIEW_TLS_ENABLED is on but {self.tls_cert_file} is not readable. "
                "Under compose the pki service issues this service its own certificate "
                "into the reviewtls volume, which has to be mounted here."
            )
        if self.token and "*" in self.cors_origins:
            notes.append(
                "REVIEW_CORS_ORIGINS is '*' while a token is required; browsers refuse "
                "to send credentials to a wildcard origin. List the review GUI's origin."
            )
        if not self.reload_context and self.reload_vectors:
            notes.append(
                "REVIEW_RELOAD_VECTORS is on while REVIEW_RELOAD_CONTEXT is off. The "
                "embedder reads the rows the context loader writes, so it will find "
                "nothing new to embed."
            )
        if not self.embed_fixes:
            notes.append(
                "REVIEW_EMBED_FIXES is off: corrections and completions are stored "
                "without their vectors, so nothing can retrieve them until a save "
                "with embedding on catches them up."
            )
        if not self.reload_snippets:
            notes.append(
                "REVIEW_RELOAD_SNIPPETS is off: a snippet is written to its document "
                "but not loaded, so the agent will not be shown it until someone runs "
                "rag/07_load_snippets.py -- or the stack is next started."
            )
        if not self.reload_context:
            notes.append(
                "REVIEW_RELOAD_CONTEXT is off: a promoted pair is written to the "
                "question document but not loaded, so the agent will not retrieve it "
                "until someone runs rag/05_load_golden_pairs.py."
            )
        return notes


#: The dataclass fields, so a test can prove none has been added without an
#: environment variable to set it with.
SETTING_FIELDS = tuple(f.name for f in fields(ReviewSettings) if f.name != "source")
