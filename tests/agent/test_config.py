"""Settings: env-driven configuration, so the same image can point at a
different Ollama host/model/database without a rebuild.
"""

from __future__ import annotations

from nl2sql_agent.config import (
    DEFAULT_DATABASE_URL,
    DEFAULT_EMBED_BASE_URL,
    DEFAULT_EMBED_MODEL,
    DEFAULT_OLLAMA_BASE_URL,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_VECTOR_DB_URL,
    Settings,
)


def test_the_default_database_url_is_the_read_only_role_not_the_owner():
    """Least privilege starts here: the agent's built-in identity is the
    reader created by docker/reader_role.sql. The owner that loads the data
    (`nl2sql`, per docker/Dockerfile) must never be the default, or a run
    with DATABASE_URL unset would silently have write access.
    """
    from sqlalchemy.engine import make_url

    url = make_url(DEFAULT_DATABASE_URL)
    assert url.username == "nl2sql_reader"
    assert url.username != "nl2sql"
    assert url.database == "nl2sql_retail"


def test_defaults_when_env_is_empty(monkeypatch):
    for var in (
        "OLLAMA_BASE_URL", "OLLAMA_MODEL", "OLLAMA_TEMPERATURE", "OLLAMA_REASONING",
        "OLLAMA_NUM_CTX", "DATABASE_URL", "DB_SCHEMA", "SAMPLE_ROWS", "MAX_ROWS",
        "STATEMENT_TIMEOUT_MS", "MAX_ATTEMPTS", "MAX_SQL_ATTEMPTS", "RAG_ENABLED", "VECTOR_DB_URL",
        "EMBED_MODEL", "EMBED_BASE_URL", "RAG_TOP_K", "RAG_MAX_CONTEXT_CHARS",
    ):
        monkeypatch.delenv(var, raising=False)

    settings = Settings.from_env()
    assert settings.ollama_base_url == DEFAULT_OLLAMA_BASE_URL
    assert settings.ollama_model == DEFAULT_OLLAMA_MODEL
    assert settings.database_url == DEFAULT_DATABASE_URL
    assert settings.temperature == 0.0
    assert settings.reasoning is False
    assert settings.num_ctx == 262144
    assert settings.db_schema == "public"
    assert settings.sample_rows == 3
    assert settings.max_rows == 50
    assert settings.statement_timeout_ms == 30000
    # arch5 section 6.4: one draft and six repairs.
    assert settings.max_attempts == 7
    assert settings.rag_enabled is True
    assert settings.vector_db_url == DEFAULT_VECTOR_DB_URL
    assert settings.embed_model == DEFAULT_EMBED_MODEL
    assert settings.embed_base_url == DEFAULT_EMBED_BASE_URL
    assert settings.rag_top_k == 4
    assert settings.rag_max_context_chars == 12000


def test_retrieval_defaults_target_the_compose_services(monkeypatch):
    """The container defaults have to match docker-compose.yml: the vector
    store is a compose service, but the embedding model is served by the
    Ollama on the Docker host, not inside the compose network.
    """
    assert "@vectordb:5432/" in DEFAULT_VECTOR_DB_URL
    assert DEFAULT_EMBED_BASE_URL.startswith("http://host.docker.internal")


def test_default_base_url_is_plain_http_not_https():
    # The Ollama host serves plain HTTP on 11434; https:// there fails with an
    # SSL error, not a clean connection error. Pin the scheme explicitly.
    assert DEFAULT_OLLAMA_BASE_URL.startswith("http://")


def test_env_overrides_every_field(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://example.com:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3:latest")
    monkeypatch.setenv("OLLAMA_TEMPERATURE", "0.7")
    monkeypatch.setenv("OLLAMA_REASONING", "true")
    monkeypatch.setenv("OLLAMA_NUM_CTX", "8192")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://a:b@host/db")
    monkeypatch.setenv("DB_SCHEMA", "custom")
    monkeypatch.setenv("SAMPLE_ROWS", "5")
    monkeypatch.setenv("MAX_ROWS", "10")
    monkeypatch.setenv("STATEMENT_TIMEOUT_MS", "5000")
    monkeypatch.setenv("MAX_ATTEMPTS", "1")
    monkeypatch.setenv("RAG_ENABLED", "false")
    monkeypatch.setenv("VECTOR_DB_URL", "postgresql+psycopg://v:v@vhost/vectors")
    monkeypatch.setenv("EMBED_MODEL", "nomic-embed-text")
    monkeypatch.setenv("EMBED_BASE_URL", "http://embedhost:11434")
    monkeypatch.setenv("RAG_TOP_K", "9")
    monkeypatch.setenv("RAG_MAX_CONTEXT_CHARS", "2048")

    settings = Settings.from_env()
    assert settings.rag_enabled is False
    assert settings.vector_db_url == "postgresql+psycopg://v:v@vhost/vectors"
    assert settings.embed_model == "nomic-embed-text"
    assert settings.embed_base_url == "http://embedhost:11434"
    assert settings.rag_top_k == 9
    assert settings.rag_max_context_chars == 2048
    assert settings.ollama_base_url == "http://example.com:11434"
    assert settings.ollama_model == "llama3:latest"
    assert settings.temperature == 0.7
    assert settings.reasoning is True
    assert settings.num_ctx == 8192
    assert settings.database_url == "postgresql+psycopg://a:b@host/db"
    assert settings.db_schema == "custom"
    assert settings.sample_rows == 5
    assert settings.max_rows == 10
    assert settings.statement_timeout_ms == 5000
    assert settings.max_attempts == 1


def test_env_bool_accepts_common_truthy_spellings(monkeypatch):
    for value in ("1", "true", "True", "yes", "on", " TRUE "):
        monkeypatch.setenv("OLLAMA_REASONING", value)
        assert Settings.from_env().reasoning is True, value


def test_env_bool_treats_anything_else_as_false(monkeypatch):
    for value in ("0", "false", "no", "off", "garbage", ""):
        monkeypatch.setenv("OLLAMA_REASONING", value)
        assert Settings.from_env().reasoning is False, value


# ---------------------------------------------------------------------------
# An empty environment value means unset, not blank
# ---------------------------------------------------------------------------


def test_an_empty_string_setting_falls_back_to_its_default(monkeypatch):
    """Compose forwards a variable the host never set as an empty string.
    Without this rule the containerised agent would run with a model that
    has no name and a schema-retrieval mode that is neither option.
    """
    for name in ("OLLAMA_MODEL", "SCHEMA_RETRIEVAL", "DB_SCHEMA", "DATABASE_URL"):
        monkeypatch.setenv(name, "")
    settings = Settings.from_env()
    assert settings.ollama_model == DEFAULT_OLLAMA_MODEL
    assert settings.schema_retrieval == "vector"
    assert settings.db_schema == "public"
    assert settings.database_url == DEFAULT_DATABASE_URL


def test_an_empty_numeric_setting_falls_back_to_its_default(monkeypatch):
    for name in ("MAX_TABLES", "SCHEMA_TOP_K", "MAX_ROWS", "MAX_ATTEMPTS"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("LITERAL_MIN_SCORE", "")
    monkeypatch.setenv("MAX_PLAN_COST", "")
    settings = Settings.from_env()
    assert settings.max_tables == 10
    assert settings.schema_top_k == 6
    assert settings.max_rows == 50
    assert settings.max_attempts == 7
    assert settings.literal_min_score == 0.6
    assert settings.max_plan_cost == 1_000_000.0


def test_an_empty_boolean_setting_falls_back_rather_than_reading_as_false(monkeypatch):
    """The subtlest of the three: an empty value used to read as False, so
    forwarding `SUPERVISOR_ENABLED` would have silently disabled the only
    injection screen on every containerised run.
    """
    for name in (
        "SUPERVISOR_ENABLED", "AUDIT_ENABLED", "NARRATE_ENABLED", "RAG_ENABLED",
        "REVIEW_ENABLED", "REVIEW_REFLECTION_ENABLED",
    ):
        monkeypatch.setenv(name, "")
    settings = Settings.from_env()
    assert settings.supervisor_enabled is True
    assert settings.audit_enabled is True
    assert settings.narrate_enabled is True
    assert settings.rag_enabled is True
    assert settings.review_enabled is True
    assert settings.review_reflection_enabled is True


def test_the_completeness_reviewer_and_its_reflection_switch_separately(monkeypatch):
    """arch5 section 11: the reflection is its own ablation, so turning it off
    must keep the rules -- and turning the reviewer off must not need both.
    """
    monkeypatch.setenv("REVIEW_REFLECTION_ENABLED", "false")
    settings = Settings.from_env()
    assert settings.review_enabled is True
    assert settings.review_reflection_enabled is False

    monkeypatch.setenv("REVIEW_ENABLED", "off")
    monkeypatch.delenv("REVIEW_REFLECTION_ENABLED")
    settings = Settings.from_env()
    assert settings.review_enabled is False
    assert settings.review_reflection_enabled is True


def test_the_v3_budget_name_still_sets_the_budget(monkeypatch):
    """An .env written for v3 still says MAX_SQL_ATTEMPTS; it counted
    generations too, so its value carries over, and MAX_ATTEMPTS wins when
    both are set.
    """
    monkeypatch.delenv("MAX_ATTEMPTS", raising=False)
    monkeypatch.setenv("MAX_SQL_ATTEMPTS", "3")
    assert Settings.from_env().max_attempts == 3
    monkeypatch.setenv("MAX_ATTEMPTS", "5")
    assert Settings.from_env().max_attempts == 5


def test_whitespace_around_a_value_is_trimmed(monkeypatch):
    monkeypatch.setenv("MAX_TABLES", "  7 ")
    monkeypatch.setenv("SCHEMA_RETRIEVAL", " llm ")
    monkeypatch.setenv("SUPERVISOR_ENABLED", " off ")
    settings = Settings.from_env()
    assert settings.max_tables == 7
    assert settings.schema_retrieval == "llm"
    assert settings.supervisor_enabled is False


def test_a_whitespace_only_value_is_treated_as_unset(monkeypatch):
    monkeypatch.setenv("MAX_TABLES", "   ")
    monkeypatch.setenv("OLLAMA_MODEL", "\t")
    settings = Settings.from_env()
    assert settings.max_tables == 10
    assert settings.ollama_model == DEFAULT_OLLAMA_MODEL
