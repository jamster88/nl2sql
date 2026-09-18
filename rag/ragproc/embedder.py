"""Embedding backends.

Ollama is the default so the pipeline uses the bge-m3 model already pulled on
the local machine rather than downloading a second copy of the weights.
"""

from __future__ import annotations

from typing import Protocol

import requests


class Embedder(Protocol):
    model_name: str
    dimension: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OllamaEmbedder:
    def __init__(self, model: str, base_url: str, timeout: int = 300) -> None:
        self.model_name = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._dimension: int | None = None

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            self._dimension = len(self.embed(["dimension probe"])[0])
        return self._dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = requests.post(
            f"{self.base_url}/api/embed",
            json={"model": self.model_name, "input": texts},
            timeout=self.timeout,
        )
        if response.status_code == 404:
            raise RuntimeError(
                f"Ollama at {self.base_url} does not have model {self.model_name!r}. "
                f"Pull it with: ollama pull {self.model_name}"
            )
        response.raise_for_status()
        payload = response.json()
        vectors = payload.get("embeddings")
        if vectors is None and "embedding" in payload:
            vectors = [payload["embedding"]]
        if not vectors:
            raise RuntimeError(f"Ollama returned no embeddings: {payload}")
        return vectors

    def check(self) -> None:
        try:
            requests.get(f"{self.base_url}/api/tags", timeout=10).raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Cannot reach Ollama at {self.base_url}. Start it, or pass "
                f"--ollama-url. Note port 11434 serves http, not https. ({exc})"
            ) from exc


class SentenceTransformerEmbedder:
    """Local weights via sentence-transformers, as in the original chunker."""

    def __init__(self, model: str) -> None:
        from sentence_transformers import SentenceTransformer

        self.model_name = model
        self._model = SentenceTransformer(model)

    @property
    def dimension(self) -> int:
        return int(self._model.get_sentence_embedding_dimension())

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return [v.tolist() for v in self._model.encode(texts, convert_to_numpy=True)]

    def check(self) -> None:
        return None


def build_embedder(backend: str, model: str, ollama_url: str) -> Embedder:
    if backend == "ollama":
        return OllamaEmbedder(model, ollama_url)
    if backend in {"sentence-transformers", "st"}:
        return SentenceTransformerEmbedder(model)
    raise ValueError(f"Unknown embedding backend {backend!r}")
