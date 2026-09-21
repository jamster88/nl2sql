"""Ollama chat model construction."""

from __future__ import annotations

import urllib.error
import urllib.request

from langchain_ollama import ChatOllama
from pydantic import ValidationError

from .config import Settings


class LlmUnavailableError(RuntimeError):
    """Ollama is unreachable, or it does not have the requested model."""


def _check_reachable(settings: Settings) -> None:
    """Fail now, with a timeout, rather than when the socket eventually gives up.

    `validate_model_on_init` below asks the host for its model list, and that
    request has no timeout of its own. A host that refuses the connection
    answers immediately, but one that is *routed and silent* -- powered off,
    behind a dropping firewall, or on a subnet something else has claimed --
    holds the connection open until the operating system decides, which was
    measured at just under three minutes.

    That is survivable for the CLI, where the user is waiting for one answer
    anyway. It is not survivable for `/readyz`, which exists to be polled: a
    readiness probe that blocks for three minutes is a readiness probe an
    orchestrator times out on and a GUI cannot use. So reachability is
    checked first, with a bound, and only then is the model validated.
    """
    url = settings.ollama_base_url.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=settings.ollama_connect_timeout) as response:
            response.read(1)
    except urllib.error.HTTPError:
        # It answered, just not with a model list -- a proxy or a different
        # service on the port. Let ChatOllama produce the real complaint.
        return
    except Exception as exc:
        raise LlmUnavailableError(
            f"Cannot reach Ollama at {settings.ollama_base_url} "
            f"({settings.ollama_connect_timeout:g}s timeout): {exc}. "
            "Check that the host is running and reachable, and note that port "
            "11434 serves http, not https."
        ) from exc


def build_llm(settings: Settings) -> ChatOllama:
    _check_reachable(settings)
    try:
        return ChatOllama(
            model=settings.ollama_model,
            base_url=settings.ollama_base_url,
            temperature=settings.temperature,
            reasoning=settings.reasoning,
            num_ctx=settings.num_ctx,
            # Fail here, with the model list in hand, rather than several steps
            # into the pipeline.
            validate_model_on_init=True,
        )
    except ConnectionError as exc:
        raise LlmUnavailableError(
            f"Cannot reach Ollama at {settings.ollama_base_url}. "
            "Check that the host is running and reachable, and note that port "
            "11434 serves http, not https."
        ) from exc
    except ValidationError as exc:
        raise LlmUnavailableError(exc.errors()[0]["msg"].removeprefix("Value error, ")) from exc
