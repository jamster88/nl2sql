"""Ollama chat model construction."""

from __future__ import annotations

from langchain_ollama import ChatOllama
from pydantic import ValidationError

from .config import Settings


class LlmUnavailableError(RuntimeError):
    """Ollama is unreachable, or it does not have the requested model."""


def build_llm(settings: Settings) -> ChatOllama:
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
