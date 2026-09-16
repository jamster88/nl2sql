"""Settings: env-driven configuration, so the same image can point at a
different Ollama host/model/database without a rebuild.
"""

from __future__ import annotations

from nl2sql_agent.config import DEFAULT_DATABASE_URL, DEFAULT_OLLAMA_BASE_URL, DEFAULT_OLLAMA_MODEL, Settings


def test_defaults_when_env_is_empty(monkeypatch):
    for var in (
        "OLLAMA_BASE_URL", "OLLAMA_MODEL", "OLLAMA_TEMPERATURE", "OLLAMA_REASONING",
        "OLLAMA_NUM_CTX", "DATABASE_URL", "DB_SCHEMA", "SAMPLE_ROWS", "MAX_ROWS",
        "STATEMENT_TIMEOUT_MS", "MAX_SQL_ATTEMPTS",
    ):
        monkeypatch.delenv(var, raising=False)

    settings = Settings.from_env()
    assert settings.ollama_base_url == DEFAULT_OLLAMA_BASE_URL
    assert settings.ollama_model == DEFAULT_OLLAMA_MODEL
    assert settings.database_url == DEFAULT_DATABASE_URL
    assert settings.temperature == 0.0
    assert settings.reasoning is False
    assert settings.num_ctx == 16384
    assert settings.db_schema == "public"
    assert settings.sample_rows == 3
    assert settings.max_rows == 50
    assert settings.statement_timeout_ms == 30000
    assert settings.max_sql_attempts == 3


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
    monkeypatch.setenv("MAX_SQL_ATTEMPTS", "1")

    settings = Settings.from_env()
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
    assert settings.max_sql_attempts == 1


def test_env_bool_accepts_common_truthy_spellings(monkeypatch):
    for value in ("1", "true", "True", "yes", "on", " TRUE "):
        monkeypatch.setenv("OLLAMA_REASONING", value)
        assert Settings.from_env().reasoning is True, value


def test_env_bool_treats_anything_else_as_false(monkeypatch):
    for value in ("0", "false", "no", "off", "garbage", ""):
        monkeypatch.setenv("OLLAMA_REASONING", value)
        assert Settings.from_env().reasoning is False, value
