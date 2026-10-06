"""What the one-shot prepares, read from the environment compose gives it.

The names are the stack's own (`POSTGRES_DB`, `FEEDBACK_DB_USER`, ...), so
`.env` says what they are once and every service reads the same thing; the
passwords come from the secrets compose mounts (`*_FILE`, V6-38).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from nl2sql_common.env import env_bool, env_int, env_str, secret

#: Where each database's socket directory is mounted (docker-compose.yml).
DEFAULT_SOCKETS = "/run/nl2sql/sockets"


@dataclass(frozen=True)
class Store:
    """One of the runtime stores in the shared server: a database, its owner."""

    key: str
    role: str
    database: str
    password: str | None
    #: The review service makes the feedback writer, and the snippet loader
    #: the agent's reader, as these owners -- which takes CREATEROLE. With
    #: it they can make and change only roles they made themselves.
    creates_roles: bool = False
    #: pgvector, created here as the superuser: the owner cannot.
    vectors: bool = False


@dataclass(frozen=True)
class Login:
    """A store that is its own server: its owner, which is its superuser."""

    socket: str
    role: str
    database: str
    password: str | None


@dataclass(frozen=True)
class OpsSettings:
    sockets: Path
    retail_database: str
    retail_owner: str
    reader: str
    reader_password: str | None
    signin: bool
    rolesync: str
    rolesync_password: str | None
    ldap_host: str
    ldap_port: int
    ldap_tls: str
    ldap_base_dn: str
    stores: tuple[Store, ...] = field(default=())
    knowledge: tuple[Login, ...] = field(default=())
    mlflow: Login | None = None
    golden_document: Path = Path("/app/context_questions/translated_questions.md")
    snippets_document: Path = Path("/app/context_questions/sql_snippets.md")

    @classmethod
    def from_env(cls) -> "OpsSettings":
        return cls(
            sockets=Path(env_str("NL2SQL_SOCKETS_DIR", DEFAULT_SOCKETS)),
            retail_database=env_str("POSTGRES_DB", "nl2sql_retail"),
            retail_owner=env_str("POSTGRES_USER", "nl2sql"),
            reader=env_str("POSTGRES_READER_USER", "nl2sql_reader"),
            reader_password=secret("POSTGRES_READER_PASSWORD"),
            # On unless switched off by name, as in every service.
            signin=env_bool("AUTH_ENABLED", True),
            rolesync=env_str("AUTH_ROLESYNC_USER", "nl2sql_rolesync"),
            rolesync_password=secret("AUTH_ROLESYNC_PASSWORD"),
            ldap_host=env_str("NL2SQL_LDAP_HOST", "nl2sql-ldap"),
            ldap_port=env_int("NL2SQL_LDAP_PORT", 389),
            ldap_tls=env_str("NL2SQL_LDAP_TLS", "starttls"),
            ldap_base_dn=env_str("LDAP_BASE_DN", "dc=nl2sql,dc=local"),
            stores=(
                Store("feedback", env_str("FEEDBACK_DB_USER", "feedback"), env_str("FEEDBACK_DB_NAME", "nl2sql_feedback"),
                      secret("FEEDBACK_DB_PASSWORD"), creates_roles=True),
                Store("corrections", env_str("CORRECTIONS_DB_USER", "corrections"),
                      env_str("CORRECTIONS_DB_NAME", "nl2sql_corrections"), secret("CORRECTIONS_DB_PASSWORD"), vectors=True),
                Store("completions", env_str("COMPLETIONS_DB_USER", "completions"),
                      env_str("COMPLETIONS_DB_NAME", "nl2sql_completions"), secret("COMPLETIONS_DB_PASSWORD"), vectors=True),
                Store("snippets", env_str("SNIPPETS_DB_USER", "snippets"), env_str("SNIPPETS_DB_NAME", "nl2sql_snippets"),
                      secret("SNIPPETS_DB_PASSWORD"), creates_roles=True, vectors=True),
            ),
            knowledge=(
                Login("context", env_str("CONTEXT_DB_USER", "ragproc"), env_str("CONTEXT_DB_NAME", "nl2sql_chunks"),
                      secret("CONTEXT_DB_PASSWORD")),
                Login("vector", env_str("VECTOR_DB_USER", "ragproc"), env_str("VECTOR_DB_NAME", "nl2sql_vectors"),
                      secret("VECTOR_DB_PASSWORD")),
            ),
            mlflow=Login("mlflow", env_str("MLFLOW_DB_USER", "mlflow"), env_str("MLFLOW_DB_NAME", "mlflow"),
                         secret("MLFLOW_DB_PASSWORD")),
            golden_document=Path(env_str("NL2SQL_GOLDEN_DOCUMENT", "/app/context_questions/translated_questions.md")),
            snippets_document=Path(env_str("NL2SQL_SNIPPETS_DOCUMENT", "/app/context_questions/sql_snippets.md")),
        )

    def store(self, key: str) -> Store:
        return next(store for store in self.stores if store.key == key)

    def login(self, socket: str) -> Login:
        return next(login for login in self.knowledge if login.socket == socket)
