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

import os
from dataclasses import dataclass, field, fields
from pathlib import Path

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

#: The certificate the agent API generates, as this service sees it. The
#: same volume, mounted read-only: this process presents that certificate
#: and never writes one.
DEFAULT_TLS_DIR = "/etc/nl2sql/tls"
DEFAULT_CERT_FILE = f"{DEFAULT_TLS_DIR}/server.crt"
DEFAULT_KEY_FILE = f"{DEFAULT_TLS_DIR}/server.key"

#: The name the certificate must cover for another container to verify this
#: one. Compose adds it to API_TLS_HOSTNAMES; named here so the readiness
#: check can say which name is missing rather than "handshake failed".
SERVICE_HOSTNAME = "nl2sql-review"


def _env(name: str) -> str | None:
    raw = os.getenv(name)
    return raw if raw not in (None, "") else None


def _env_str(name: str, default: str) -> str:
    return _env(name) or default


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    return int(raw) if raw else default


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    return float(raw) if raw else default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_tuple(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = _env(name)
    if raw is None:
        return default
    return tuple(part.strip() for part in raw.split(",") if part.strip())


@dataclass
class ReviewSettings:
    """Everything the review service needs, and nothing about the pipeline."""

    # --- Binding ---------------------------------------------------------
    host: str = "0.0.0.0"
    port: int = DEFAULT_PORT
    root_path: str = ""

    # --- TLS -------------------------------------------------------------
    # This service presents the certificate the agent API already generated,
    # mounted read-only from the volume that API writes it into. It does not
    # generate one of its own, and that is a deliberate subtraction: a second
    # copy of the certificate code would be 250 lines whose only job is to
    # agree with the first copy, and the two would be discovered to disagree
    # by a client failing to connect.
    #
    # The consequence is a configuration requirement rather than a code one.
    # The certificate has to be valid for this service's name too, so compose
    # adds `nl2sql-review` to API_TLS_HOSTNAMES. `warnings()` says so when the
    # file is not where it should be, and the server refuses to start with TLS
    # claimed and no certificate to present -- which is the failure that would
    # otherwise look like a working HTTPS URL.
    tls_enabled: bool = True
    tls_cert_file: str = DEFAULT_CERT_FILE
    tls_key_file: str = DEFAULT_KEY_FILE

    # --- Who may call ----------------------------------------------------
    # This one is not optional in the way the agent's is. A caller here can
    # write the golden question set; `warnings()` says so loudly when it is
    # unset, and the readiness endpoint repeats it.
    token: str | None = None
    cors_origins: tuple[str, ...] = ("*",)

    # --- The staging database -------------------------------------------
    # As the owner: this service creates the schema and the writer role.
    feedback_db_url: str = "postgresql://feedback:feedback@localhost:5435/nl2sql_feedback"
    #: The password handed to `nl2sql_feedback_writer` when it is recreated
    #: on start. The agent API is given the same one, as its whole URL.
    writer_password: str = "nl2sql_feedback_writer"
    #: Create the schema and the writer role on start. Off for a deployment
    #: where a DBA owns the schema and the service should not touch it.
    manage_schema: bool = True

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
            token=_env("REVIEW_TOKEN"),
            cors_origins=_env_tuple("REVIEW_CORS_ORIGINS", ("*",)),
            feedback_db_url=_env_str(
                "FEEDBACK_DB_URL", "postgresql://feedback:feedback@localhost:5435/nl2sql_feedback"
            ),
            writer_password=_env_str("FEEDBACK_WRITER_PASSWORD", "nl2sql_feedback_writer"),
            manage_schema=_env_bool("REVIEW_MANAGE_SCHEMA", True),
            document=_env_str("REVIEW_DOCUMENT", DEFAULT_DOCUMENT),
            rag_dir=_env_str("REVIEW_RAG_DIR", DEFAULT_RAG_DIR),
            reload_context=_env_bool("REVIEW_RELOAD_CONTEXT", True),
            reload_vectors=_env_bool("REVIEW_RELOAD_VECTORS", True),
            reload_timeout_seconds=_env_float("REVIEW_RELOAD_TIMEOUT_SECONDS", 600.0),
            chunk_db_url=_env_str(
                "CHUNK_DB_URL", "postgresql://ragproc:ragproc@localhost:5433/nl2sql_chunks"
            ),
            vector_db_url=_env_str(
                "VECTOR_DB_URL", "postgresql://ragproc:ragproc@localhost:5434/nl2sql_vectors"
            ),
            ollama_url=_env_str("OLLAMA_URL", "http://localhost:11434"),
            embed_model=_env_str("EMBED_MODEL", "bge-m3"),
            docs_enabled=_env_bool("REVIEW_DOCS_ENABLED", True),
            log_level=_env_str("REVIEW_LOG_LEVEL", "info"),
            source="environment",
        )

    # --- derived ---------------------------------------------------------

    @property
    def scheme(self) -> str:
        return "https" if self.tls_enabled else "http"

    @property
    def authenticated(self) -> bool:
        return bool(self.token)

    @property
    def document_path(self) -> Path:
        return Path(self.document)

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

    def warnings(self) -> list[str]:
        """Configurations that will work and probably should not."""
        notes: list[str] = []
        if not self.token:
            notes.append(
                "No REVIEW_TOKEN is set, so anyone who can reach this port can edit "
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
                "This service presents the certificate the agent API generates, so "
                "the API has to have started at least once and the apitls volume has "
                "to be mounted here."
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
