"""Writing an approved submission into the golden question set.

The order here is the whole design, and it is chosen so that the failure
modes are all survivable:

1. **Validate the draft** against the loader's format rules (`render.py`).
2. **Render and append**, in memory.
3. **Parse the result with the loader's own parser.** Not a check that the
   text looks right -- the actual `ragproc.golden_pairs.parse_document`, on
   the actual new document, asserting that it now yields exactly one more
   pair and that every field of the new one came back the way it went in.
   This is the step that matters. `ENTRY_RE` is one regular expression over
   the whole file and a pair that does not match it is not *reported*, it is
   simply not seen; the only thing that catches that today is a heading
   count. Round-tripping before writing turns a silently-dropped pair into
   a refused promotion.
4. **Write atomically** -- temporary file in the same directory, then
   `os.replace` -- so an interrupted write cannot leave the source of truth
   half-rewritten. A backup of the previous version is kept beside it.
5. **Reload the stores**, and treat failure here as partial success rather
   than as failure. The document is the source of truth and it is already
   written; an embedder that was down does not un-promote the pair, it just
   means the agent cannot retrieve it yet. Saying so precisely is more use
   than rolling back something that was correct.

Step 5 runs `rag/`'s scripts as scripts rather than importing them. They
already handle the upsert, the delete of pairs no longer in the document,
the BM25 rebuild and incremental re-embedding; a second implementation here
would be a second set of rules to keep in agreement with the first.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import render
from .render import Draft
from .settings import ReviewSettings

#: `rag/`'s scripts start with digits, so they cannot be imported as modules
#: and are named as files throughout.
CONTEXT_LOADER = "05_load_golden_pairs.py"
VECTOR_LOADER = "06_embed_golden_pairs.py"


class PromotionError(Exception):
    """The promotion did not happen and the document was not touched.

    Carries the list of reasons rather than one, because the caller is a form
    in a browser and a curator fixing one field at a time is a bad afternoon.
    """

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


@dataclass
class StepResult:
    name: str
    ran: bool
    ok: bool = True
    detail: str = ""


@dataclass
class Promotion:
    """What happened, in enough detail to put in the log and on the screen."""

    pair_id: str
    chunk_id: str
    suite: str
    title: str
    markdown: str
    document: str
    backup: str = ""
    pairs_before: int = 0
    pairs_after: int = 0
    steps: list[StepResult] = field(default_factory=list)

    @property
    def reloaded(self) -> bool:
        """True only when something ran and everything that ran worked.

        A promotion where every step was switched off is not reloaded, it is
        *not reloaded yet* -- `all()` over no steps is `True`, and a log line
        claiming the stores were rebuilt when nothing touched them is the one
        kind of audit record that is worse than none.
        """
        ran = [step for step in self.steps if step.ran]
        return bool(ran) and all(step.ok for step in ran)

    @property
    def detail(self) -> str:
        parts = []
        for step in self.steps:
            if not step.ran:
                parts.append(f"{step.name}: skipped")
            else:
                parts.append(f"{step.name}: {'ok' if step.ok else 'FAILED'} {step.detail}".strip())
        return " | ".join(parts)


def _parser():
    """`ragproc.golden_pairs`, imported the way the loaders see it.

    `rag/` is not a package and is not on the path in a development checkout,
    so it is put there on first use. Imported lazily rather than at module
    import so that everything else in this service -- settings, the store,
    the whole HTTP surface -- works in an environment that has no `rag/`.
    """
    try:
        from ragproc import golden_pairs as gp  # type: ignore[import-not-found]
    except ModuleNotFoundError:  # pragma: no cover - exercised via rag_dir below
        raise
    return gp


def _ensure_importable(rag_dir: str) -> None:
    path = str(Path(rag_dir).resolve())
    if path not in sys.path:
        sys.path.insert(0, path)


def preview(settings: ReviewSettings, draft: Draft) -> tuple[str, str, list[str]]:
    """The pair id and markdown this draft would produce, without writing.

    The review GUI shows this beside the form, so a curator sees the block
    that is going into the document rather than trusting that it will be the
    one they meant.
    """
    document = _read(settings.document_path)
    try:
        pair_id = render.next_pair_id(document)
    except ValueError as exc:
        return "", "", [str(exc)]
    reasons = render.problems(draft, pair_id)
    if reasons:
        return pair_id, "", reasons
    return pair_id, render.render_pair(draft, pair_id), []


def promote(settings: ReviewSettings, draft: Draft) -> Promotion:
    """Add the pair to the document, then bring the stores up to date."""
    _ensure_importable(settings.rag_dir)
    path = settings.document_path
    document = _read(path)

    pair_id = render.next_pair_id(document)
    reasons = render.problems(draft, pair_id)
    if reasons:
        raise PromotionError(reasons)

    updated = render.append_pair(document, draft, pair_id)
    before, after = _round_trip(document, updated, draft, pair_id)

    backup = _write_atomically(path, updated)
    promotion = Promotion(
        pair_id=pair_id,
        chunk_id=render.chunk_id_for(pair_id),
        suite=render.suite_in_force(updated),
        title=draft.title.strip(),
        markdown=render.render_pair(draft, pair_id),
        document=str(path),
        backup=backup,
        pairs_before=before,
        pairs_after=after,
    )
    promotion.steps = _reload(settings)
    return promotion


def _round_trip(document: str, updated: str, draft: Draft, pair_id: str) -> tuple[int, int]:
    """Parse the new document with the loader's parser before writing it.

    Both versions are parsed, not just the new one: the count has to go up by
    exactly one. A rendering bug that broke an *existing* pair while adding a
    valid new one would otherwise pass -- and step 5 of the loader deletes
    rows for pairs it no longer sees, so that bug would quietly drop a golden
    question from the set.
    """
    gp = _parser()
    try:
        before = len(_parse_text(gp, document))
    except ValueError as exc:
        raise PromotionError(
            [
                "the existing question document does not parse, so nothing can be "
                f"added to it safely: {exc}"
            ]
        ) from exc

    try:
        pairs = _parse_text(gp, updated)
    except ValueError as exc:
        raise PromotionError([f"the rendered pair does not parse: {exc}"]) from exc

    if len(pairs) != before + 1:
        raise PromotionError(
            [
                f"adding {pair_id} changed the parsed pair count from {before} to "
                f"{len(pairs)}, not {before + 1}. The rendered block does not match "
                "the loader's format and would be silently dropped."
            ]
        )

    added = next((p for p in pairs if p.pair_id == pair_id), None)
    if added is None:
        raise PromotionError([f"{pair_id} is not in the reparsed document"])

    mismatches = [
        f"{name}: wrote {written!r}, read back {read!r}"
        for name, written, read in (
            ("question", draft.question.strip(), added.question),
            ("sql_code", draft.sql_code.strip(), added.sql_code),
            ("reasoning_target", draft.reasoning_target.strip(), added.reasoning_target),
            ("result", draft.result.strip(), added.result),
            ("title", draft.title.strip(), added.title),
            ("chunk_id", render.chunk_id_for(pair_id), added.chunk_id),
        )
        if written != read
    ]
    if mismatches:
        raise PromotionError(["the pair does not survive a round trip", *mismatches])
    return before, len(pairs)


def _parse_text(gp, text: str) -> list:
    """Run the real parser over text that is not on disk yet.

    `parse_document` takes a path, so the candidate is written to a temporary
    file. That is the point: validating a string with a copy of the rules
    would only prove the copy agrees with itself.
    """
    handle, name = tempfile.mkstemp(suffix=".md", prefix="golden-candidate-")
    try:
        with os.fdopen(handle, "w") as fh:
            fh.write(text)
        return gp.parse_document(Path(name))
    finally:
        os.unlink(name)


def _read(path: Path) -> str:
    try:
        return path.read_text()
    except OSError as exc:
        raise PromotionError([f"cannot read the question document at {path}: {exc}"]) from exc


def _write_atomically(path: Path, text: str) -> str:
    """Replace the document in one step, keeping the previous version.

    The temporary file is created in the same directory on purpose:
    `os.replace` is only atomic within a filesystem, and `/tmp` is routinely
    a different one -- which would turn the one operation this function
    exists for into a copy that can be interrupted.
    """
    backup = path.with_suffix(path.suffix + ".bak")
    try:
        shutil.copy2(path, backup)
        handle, name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
        with os.fdopen(handle, "w") as fh:
            fh.write(text)
        shutil.copystat(path, name)
        os.replace(name, path)
    except OSError as exc:
        raise PromotionError([f"cannot write the question document at {path}: {exc}"]) from exc
    return str(backup)


def _reload(settings: ReviewSettings) -> list[StepResult]:
    """Rebuild the context store, then the vectors, reporting each.

    The embedding step is skipped when the context step failed rather than
    attempted and failed differently: it reads the rows the first one writes,
    so running it after a failure produces a confusing error about the wrong
    thing.
    """
    steps: list[StepResult] = []
    context = _run_loader(
        settings,
        CONTEXT_LOADER,
        [settings.document, "--db-url", settings.chunk_db_url],
        enabled=settings.reload_context,
    )
    steps.append(context)

    vectors_enabled = settings.reload_vectors and context.ran and context.ok
    vectors = _run_loader(
        settings,
        VECTOR_LOADER,
        [
            "--chunk-db-url",
            settings.chunk_db_url,
            "--vector-db-url",
            settings.vector_db_url,
            "--ollama-url",
            settings.ollama_url,
            "--model",
            settings.embed_model,
        ],
        enabled=vectors_enabled,
    )
    if settings.reload_vectors and not vectors_enabled:
        vectors.detail = "not attempted: the context load did not succeed"
    steps.append(vectors)
    return steps


def _run_loader(
    settings: ReviewSettings, script: str, args: list[str], *, enabled: bool
) -> StepResult:
    name = script.split("_", 1)[-1].removesuffix(".py")
    if not enabled:
        return StepResult(name=name, ran=False)

    path = Path(settings.rag_dir) / script
    if not path.is_file():
        return StepResult(name=name, ran=True, ok=False, detail=f"{path} is not there")

    try:
        completed = subprocess.run(
            [sys.executable, script, *args],
            cwd=settings.rag_dir,
            capture_output=True,
            text=True,
            timeout=settings.reload_timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return StepResult(
            name=name,
            ran=True,
            ok=False,
            detail=f"timed out after {settings.reload_timeout_seconds:g}s",
        )
    except OSError as exc:  # pragma: no cover - needs a broken interpreter path
        return StepResult(name=name, ran=True, ok=False, detail=str(exc))

    output = _tail((completed.stdout or "") + (completed.stderr or ""))
    return StepResult(name=name, ran=True, ok=completed.returncode == 0, detail=output)


def _tail(text: str, limit: int = 600) -> str:
    """The end of the loader's output, which is where its summary is.

    Bounded because this goes into a database column and onto a screen, and
    a stack trace from a loader that could not reach its database is long.
    """
    cleaned = " ".join(text.split())
    return cleaned if len(cleaned) <= limit else "..." + cleaned[-limit:]
