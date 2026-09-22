"""build_llm: fails fast with a clear message instead of letting a raw
traceback (connection refused, wrong model tag) surface several steps into
the pipeline. ChatOllama itself is monkeypatched out -- these tests must not
require a reachable Ollama host.

"Fast" is the part that needed work. A host that refuses a connection says so
at once, but one that is routed and silent holds it open until the operating
system gives up -- measured at just under three minutes, which turned
`/readyz` into something no orchestrator would wait for. So there is now a
bounded reachability check in front of the model validation, and most of what
follows is about that bound being real.
"""

from __future__ import annotations

import socket
import time
import urllib.error

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


@pytest.fixture
def reachable(monkeypatch):
    """A host that answers the reachability probe, so the tests below are
    about what happens after it."""
    monkeypatch.setattr("nl2sql_agent.llm._check_reachable", lambda settings: None)


def test_build_llm_returns_the_model_on_success(monkeypatch, reachable):
    sentinel = object()

    def fake_chat_ollama(**kwargs):
        assert kwargs["model"] == "qwen3.8-256k"
        assert kwargs["base_url"] == "http://host:11434"
        assert kwargs["validate_model_on_init"] is True
        return sentinel

    monkeypatch.setattr("nl2sql_agent.llm.ChatOllama", fake_chat_ollama)
    settings = Settings(ollama_model="qwen3.8-256k", ollama_base_url="http://host:11434")
    assert build_llm(settings) is sentinel


def test_build_llm_wraps_connection_error(monkeypatch, reachable):
    def fake_chat_ollama(**kwargs):
        raise ConnectionError("refused")

    monkeypatch.setattr("nl2sql_agent.llm.ChatOllama", fake_chat_ollama)
    settings = Settings(ollama_base_url="https://192.168.10.82:11434")

    with pytest.raises(LlmUnavailableError) as excinfo:
        build_llm(settings)
    message = str(excinfo.value)
    assert "https://192.168.10.82:11434" in message
    assert "port 11434 serves http, not https" in message


def test_build_llm_wraps_validation_error_with_a_clean_message(monkeypatch, reachable):
    def fake_chat_ollama(**kwargs):
        raise _make_validation_error()

    monkeypatch.setattr("nl2sql_agent.llm.ChatOllama", fake_chat_ollama)
    settings = Settings()

    with pytest.raises(LlmUnavailableError) as excinfo:
        build_llm(settings)
    # The "Value error, " pydantic prefix must be stripped -- the point of
    # this wrapper is a clean, user-facing message, not a raw pydantic dump.
    assert str(excinfo.value) == "model 'bogus:latest' not found, try pulling it first"


# ---------------------------------------------------------------------------
# The reachability bound
# ---------------------------------------------------------------------------


def test_a_silent_host_fails_within_the_timeout_rather_than_minutes_later():
    """The defect this check exists for. 192.0.2.1 is TEST-NET-1 (RFC 5737):
    routable, reserved, and answered by nothing, which is exactly the shape
    of a powered-off machine or a dropping firewall.
    """
    settings = Settings(ollama_base_url="http://192.0.2.1:11434", ollama_connect_timeout=1.0)

    started = time.monotonic()
    with pytest.raises(LlmUnavailableError) as raised:
        build_llm(settings)
    elapsed = time.monotonic() - started

    assert elapsed < 10, f"it waited {elapsed:.0f}s; the bound is not being applied"
    assert "http://192.0.2.1:11434" in str(raised.value)
    assert "1s timeout" in str(raised.value)


def test_the_probe_is_told_how_long_to_wait(monkeypatch):
    """The timeout is a setting, so a slow network can be given more without
    a rebuild -- and so a test can ask for less.
    """
    seen: dict = {}

    def fake_urlopen(url, timeout=None):
        seen["url"], seen["timeout"] = url, timeout
        raise socket.timeout("timed out")

    monkeypatch.setattr("nl2sql_agent.llm.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(LlmUnavailableError):
        build_llm(Settings(ollama_base_url="http://host:11434/", ollama_connect_timeout=2.5))

    assert seen["url"] == "http://host:11434/api/tags", "a trailing slash doubled up"
    assert seen["timeout"] == 2.5


def test_the_model_is_never_validated_against_a_host_that_did_not_answer(monkeypatch):
    """The probe has to come first, or the three-minute wait happens anyway."""

    def fake_urlopen(url, timeout=None):
        raise socket.timeout("timed out")

    def never(**kwargs):
        raise AssertionError("ChatOllama was constructed against an unreachable host")

    monkeypatch.setattr("nl2sql_agent.llm.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("nl2sql_agent.llm.ChatOllama", never)
    with pytest.raises(LlmUnavailableError):
        build_llm(Settings())


def test_a_host_that_answers_with_an_error_is_left_to_chat_ollama(monkeypatch):
    """Something is listening -- a proxy, or a different service on the port.
    The probe is about reachability, and a real complaint about what is there
    is better made by the client that knows what it expects.
    """
    sentinel = object()

    def fake_urlopen(url, timeout=None):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr("nl2sql_agent.llm.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("nl2sql_agent.llm.ChatOllama", lambda **kwargs: sentinel)
    assert build_llm(Settings()) is sentinel


def test_a_reachable_host_gets_through_to_the_model(monkeypatch):
    """The probe must not become a second thing that can refuse a host that
    is working.
    """
    sentinel = object()

    class _Response:
        def read(self, _n=None):
            return b"{"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr("nl2sql_agent.llm.urllib.request.urlopen",
                        lambda url, timeout=None: _Response())
    monkeypatch.setattr("nl2sql_agent.llm.ChatOllama", lambda **kwargs: sentinel)
    assert build_llm(Settings()) is sentinel
