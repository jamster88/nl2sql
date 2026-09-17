"""Structure-aware semantic chunking for the knowledge documents.

Extends chunking/semantic_chunker.py. The base class splits a flat string on
semantic drift between sentences, which is the right idea for prose but
destroys markdown: it would cut a CREATE TABLE statement in half and treat a
whole pipe table as one enormous "sentence".

This subclass keeps the base class's drift logic and applies it only where it
helps:

1. The document is parsed into sections on markdown headings. Two of the
   knowledge documents were authored with one self-contained topic per `##`
   heading, so those boundaries are real information, not guesses.
2. Fenced code blocks, ```meta blocks and pipe tables are atomic and never
   split.
3. A section that already fits under max_chunk_tokens is emitted whole.
4. Only an oversized section is split further, and then the base class's
   percentile-threshold drift detection decides where, with atomic blocks kept
   intact.

Every emitted chunk carries its heading path, so a chunk retrieved on its own
still says what it is about.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Callable, Iterable

from semantic_chunker import SemanticChunker

FENCE_RE = re.compile(r"^```(\w*)\s*$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
TABLE_ROW_RE = re.compile(r"^\s*\|")
META_KV_RE = re.compile(r"^(\w[\w.-]*):\s*(.*)$")


@dataclass
class Block:
    kind: str  # heading | code | table | prose | rule
    text: str
    level: int = 0
    lang: str = ""

    @property
    def atomic(self) -> bool:
        return self.kind in {"code", "table"}


@dataclass
class Chunk:
    ordinal: int
    heading_path: str
    content: str
    meta: dict = field(default_factory=dict)

    @property
    def token_estimate(self) -> int:
        return approx_tokens(self.content)

    @property
    def content_hash(self) -> str:
        normalized = "\n".join(line.rstrip() for line in self.content.strip().splitlines())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def approx_tokens(text: str) -> int:
    """Same 1 word ~ 1.3 tokens estimate the base chunker uses."""
    return int(len(text.split()) * 1.3)


def parse_blocks(markdown: str) -> list[Block]:
    """Split markdown into headings, fenced blocks, tables and prose runs."""
    blocks: list[Block] = []
    prose: list[str] = []

    def flush_prose() -> None:
        if prose:
            text = "\n".join(prose).strip()
            if text:
                blocks.append(Block("prose", text))
            prose.clear()

    lines = markdown.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]

        fence = FENCE_RE.match(line)
        if fence:
            flush_prose()
            lang = fence.group(1)
            body = [line]
            i += 1
            while i < len(lines):
                body.append(lines[i])
                if FENCE_RE.match(lines[i]):
                    i += 1
                    break
                i += 1
            blocks.append(Block("code", "\n".join(body), lang=lang))
            continue

        heading = HEADING_RE.match(line)
        if heading:
            flush_prose()
            blocks.append(Block("heading", heading.group(2), level=len(heading.group(1))))
            i += 1
            continue

        if TABLE_ROW_RE.match(line):
            flush_prose()
            body = []
            while i < len(lines) and TABLE_ROW_RE.match(lines[i]):
                body.append(lines[i])
                i += 1
            blocks.append(Block("table", "\n".join(body)))
            continue

        if line.strip() in {"---", "***", "___"}:
            flush_prose()
            i += 1
            continue

        if not line.strip():
            flush_prose()
            i += 1
            continue

        prose.append(line)
        i += 1

    flush_prose()
    return blocks


def parse_meta_block(block: Block) -> dict:
    """Turn a ```meta fenced block into a dict."""
    if block.kind != "code" or block.lang != "meta":
        return {}
    meta = {}
    for line in block.text.splitlines()[1:-1]:
        match = META_KV_RE.match(line.strip())
        if match:
            key, value = match.group(1), match.group(2).strip()
            meta[key] = value
    return meta


@dataclass
class Section:
    heading_path: list[str]
    blocks: list[Block]

    @property
    def path_str(self) -> str:
        return " > ".join(self.heading_path)


def split_sections(blocks: Iterable[Block], split_level: int = 2) -> list[Section]:
    """Group blocks into sections, starting a new one at each heading at or
    above split_level (## by default)."""
    sections: list[Section] = []
    path: list[str] = []
    current: Section | None = None

    for block in blocks:
        if block.kind == "heading":
            path = path[: block.level - 1]
            while len(path) < block.level - 1:
                path.append("")
            path.append(block.text)
            if block.level <= split_level or current is None:
                current = Section([p for p in path if p], [])
                sections.append(current)
            else:
                current.blocks.append(block)
            continue
        if current is None:
            current = Section(["(preamble)"], [])
            sections.append(current)
        current.blocks.append(block)

    return [s for s in sections if s.blocks]


class MarkdownSemanticChunker(SemanticChunker):
    """Semantic chunker that respects markdown structure.

    embed_fn takes a list of strings and returns a list of vectors. It is only
    called for oversized prose sections, so most documents never embed
    anything at chunk time.
    """

    def __init__(
        self,
        embed_fn: Callable[[list[str]], list[list[float]]] | None = None,
        threshold_percentile: int = 60,
        max_chunk_tokens: int = 500,
        min_chunk_tokens: int = 40,
        split_level: int = 2,
    ) -> None:
        # Deliberately not calling super().__init__: that loads a local
        # SentenceTransformer, and the embedding backend is injected instead.
        self.model = None
        self.embed_fn = embed_fn
        self.threshold_percentile = threshold_percentile
        self.max_chunk_tokens = max_chunk_tokens
        self.min_chunk_tokens = min_chunk_tokens
        self.split_level = split_level

    def _embed(self, texts: list[str]):
        if self.embed_fn is None:
            raise RuntimeError(
                "This document needs semantic splitting but no embedding backend "
                "was supplied to the chunker."
            )
        import numpy as np

        return np.asarray(self.embed_fn(texts))

    def chunk_document(self, markdown: str) -> list[Chunk]:
        sections = split_sections(parse_blocks(markdown), self.split_level)
        chunks: list[Chunk] = []

        for section in sections:
            meta: dict = {}
            for block in section.blocks:
                meta.update(parse_meta_block(block))

            for body in self._chunk_section(section):
                content = self._with_heading(section, body)
                chunks.append(
                    Chunk(
                        ordinal=len(chunks),
                        heading_path=section.path_str,
                        content=content,
                        meta=meta,
                    )
                )

        return self._merge_runts(chunks)

    def _with_heading(self, section: Section, body: str) -> str:
        """Prefix the heading path so a retrieved chunk is self-describing."""
        header = "# " + section.path_str if section.path_str else ""
        return f"{header}\n\n{body}".strip() if header else body.strip()

    def _chunk_section(self, section: Section) -> list[str]:
        rendered = "\n\n".join(b.text for b in section.blocks).strip()
        if approx_tokens(rendered) <= self.max_chunk_tokens:
            return [rendered]

        # Oversized: pack blocks greedily, splitting prose semantically when a
        # single prose block would overflow on its own.
        pieces: list[str] = []
        buffer: list[str] = []
        buffer_tokens = 0

        def flush() -> None:
            nonlocal buffer, buffer_tokens
            if buffer:
                pieces.append("\n\n".join(buffer).strip())
                buffer = []
                buffer_tokens = 0

        for block in section.blocks:
            units = (
                [block.text]
                if block.atomic or approx_tokens(block.text) <= self.max_chunk_tokens
                else self._semantic_split(block.text)
            )
            for unit in units:
                unit_tokens = approx_tokens(unit)
                if buffer and buffer_tokens + unit_tokens > self.max_chunk_tokens:
                    flush()
                buffer.append(unit)
                buffer_tokens += unit_tokens

        flush()
        return [p for p in pieces if p]

    def _semantic_split(self, text: str) -> list[str]:
        """Base-class drift detection, applied to one oversized prose block."""
        sentences = self._split_into_sentences(text)
        if len(sentences) < 3:
            return [text]

        embeddings = self._embed(sentences)
        distances = self._calculate_cosine_distances(embeddings)
        if not distances:
            return [text]

        import numpy as np

        threshold = np.percentile(distances, self.threshold_percentile)

        out: list[str] = []
        current = [sentences[0]]
        for i, distance in enumerate(distances):
            too_big = approx_tokens(" ".join(current)) > self.max_chunk_tokens
            if distance > threshold or too_big:
                out.append(" ".join(current))
                current = [sentences[i + 1]]
            else:
                current.append(sentences[i + 1])
        if current:
            out.append(" ".join(current))
        return out

    def _merge_runts(self, chunks: list[Chunk]) -> list[Chunk]:
        """Fold a too-small chunk into its neighbour from the same section."""
        merged: list[Chunk] = []
        for chunk in chunks:
            if (
                merged
                and chunk.token_estimate < self.min_chunk_tokens
                and merged[-1].heading_path == chunk.heading_path
            ):
                previous = merged[-1]
                body = chunk.content.split("\n\n", 1)[-1] if chunk.heading_path else chunk.content
                previous.content = f"{previous.content}\n\n{body}".strip()
                continue
            merged.append(chunk)

        for ordinal, chunk in enumerate(merged):
            chunk.ordinal = ordinal
        return merged
