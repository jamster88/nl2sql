"""build_llm: fails fast with a clear message instead of letting a raw
traceback (connection refused, wrong model tag) surface several steps into
the pipeline. ChatOllama itself is monkeypatched out -- these tests must not
require a reachable Ollama host.
"""

from __future__ import annotations

import pytest
from nl2sql_agent.config import Settings
from nl2sql_agent.llm import LlmUnavailableError, build_llm
from pydantic import BaseModel, ValidationError, field_validator


class _RaisingValidatorModel(BaseModel):
    """Used only to manufacture a real pydantic ValidationError whose message
    looks like the one langchain_ollama raises for validate_model_on_init
    failures ("Value error, <message>").
    """

    x: int = 0

    @field_validator("x")
    @classmethod
    def _fail(cls, v):
        raise ValueError("model 'bogus:latest' not found, try pulling it first")


def _make_validation_error() -> ValidationError:
    try:
        _RaisingValidatorModel(x=1)
    except ValidationError as exc:
        return exc
    raise AssertionError("validator did not raise")


def test_build_llm_returns_the_model_on_success(monkeypatch):
    sentinel = object()

    def fake_chat_ollama(**kwargs):
        assert kwargs["model"] == "qwen3.8:latest"
        assert kwargs["base_url"] == "http://host:11434"
        assert kwargs["validate_model_on_init"] is True
        return sentinel

    monkeypatch.setattr("nl2sql_agent.llm.ChatOllama", fake_chat_ollama)
    settings = Settings(ollama_model="qwen3.8:latest", ollama_base_url="http://host:11434")
    assert build_llm(settings) is sentinel


def test_build_llm_wraps_connection_error(monkeypatch):
    def fake_chat_ollama(**kwargs):
        raise ConnectionError("refused")

    monkeypatch.setattr("nl2sql_agent.llm.ChatOllama", fake_chat_ollama)
    settings = Settings(ollama_base_url="https://192.168.44.129:11434")

    with pytest.raises(LlmUnavailableError) as excinfo:
        build_llm(settings)
    message = str(excinfo.value)
    assert "https://192.168.44.129:11434" in message
    assert "port 11434 serves http, not https" in message


def test_build_llm_wraps_validation_error_with_a_clean_message(monkeypatch):
    def fake_chat_ollama(**kwargs):
        raise _make_validation_error()

    monkeypatch.setattr("nl2sql_agent.llm.ChatOllama", fake_chat_ollama)
    settings = Settings()

    with pytest.raises(LlmUnavailableError) as excinfo:
        build_llm(settings)
    # The "Value error, " pydantic prefix must be stripped -- the point of
    # this wrapper is a clean, user-facing message, not a raw pydantic dump.
    assert str(excinfo.value) == "model 'bogus:latest' not found, try pulling it first"
