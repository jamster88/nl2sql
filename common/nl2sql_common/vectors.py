"""Embeddings: what a retriever or a loader is given, and pgvector's text."""

from __future__ import annotations

from typing import Protocol, Sequence


class QueryEmbedder(Protocol):
    """One question in, one vector out: what a retriever asks of a model.

    The agent's is langchain-ollama's client; a test's is a fake.
    """

    def embed_query(self, text: str) -> list[float]: ...


class Embedder(Protocol):
    """Many texts in, as many vectors out: what a loader or a fix store asks.

    `model_name` is recorded beside every stored vector, so a store embedded
    with one model is never searched with another's vectors.
    """

    model_name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def vector_literal(vector: Sequence[float]) -> str:
    """pgvector's text form, `[0.1,0.2,...]`, for a parameter cast `::vector`.

    A plain list of floats binds as `double precision[]`, which has no `<=>`
    operator, so the query would fail rather than answer. The text form and
    an explicit cast need nothing registered on the connection.
    """
    return "[" + ",".join(repr(float(component)) for component in vector) + "]"
