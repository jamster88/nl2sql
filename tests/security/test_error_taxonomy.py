"""Catch what you mean (V6-23, the second review's C-02).

There were 79 `except Exception` in the agent and the review service. Most
meant a database, a model host or a text that would not parse, and caught a
bug besides -- reporting a `KeyError` from a changed shape as "the store is
down". They name the family they mean now (`nl2sql_common.errors`), and the
broad catches that are left are boundaries that must survive anything: the
job runner, a node whose failure is part of the answer, `/readyz`. Each says
so on its own line, and these tests keep it that way.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent

#: Every package of this repository's own code.
PRODUCT = ("agent/nl2sql_agent", "review/nl2sql_review", "auth/nl2sql_auth", "common", "ldap/nl2sql_ldap",
           "rag", "benchmarks", "models")
BROAD = re.compile(r"^\s*except\s*(?:(?:Exception|BaseException)\b[^:]*)?:")
REASON = re.compile(r"#\s*noqa:\s*BLE001\s*(?:-|--)\s*\S")
#: The boundaries left in the two packages the review counted. A new broad
#: catch there is either a family it should name or a boundary worth adding
#: here on purpose. The seventh is the review service's loads, in its own
#: process since V6-27: the document is written by then, so whatever a load
#: does wrong is reported beside the pair, as the script's exit code was.
CEILING = 7


def _broad_catches(*roots: str) -> list[tuple[str, int, str]]:
    found = []
    for root in roots:
        for path in sorted((ROOT / root).rglob("*.py")):
            if "node_modules" in path.parts:
                continue
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if BROAD.match(line):
                    found.append((str(path.relative_to(ROOT)), number, line.strip()))
    return found


@pytest.mark.parametrize("root", PRODUCT)
def test_every_broad_catch_says_why_it_is_broad(root: str):
    unexplained = [f"{path}:{number}" for path, number, line in _broad_catches(root) if not REASON.search(line)]
    assert unexplained == [], f"catch the family meant (nl2sql_common.errors), or say why on the line: {unexplained}"


def test_the_agent_and_the_review_service_keep_only_their_boundaries():
    found = _broad_catches("agent/nl2sql_agent", "review/nl2sql_review")
    assert len(found) <= CEILING, "\n".join(f"{path}:{number}: {line}" for path, number, line in found)


def test_their_own_failures_are_in_the_taxonomy():
    from nl2sql_common.errors import DATABASE_ERRORS, MODEL_ERRORS, Invalid, Unavailable

    from nl2sql_agent.api.feedback import FeedbackUnavailable
    from nl2sql_agent.console.query import DatabaseUnavailable
    from nl2sql_agent.database import UnsafeQueryError
    from nl2sql_agent.examples import ExamplesUnavailableError
    from nl2sql_agent.llm import LlmUnavailableError
    from nl2sql_agent.present import FormulaError
    from nl2sql_agent.retrieval import KnowledgeUnavailableError
    from nl2sql_agent.snippets import SnippetsUnavailableError

    for unavailable in (FeedbackUnavailable, DatabaseUnavailable, ExamplesUnavailableError, LlmUnavailableError,
                        KnowledgeUnavailableError, SnippetsUnavailableError):
        assert issubclass(unavailable, Unavailable) and issubclass(unavailable, DATABASE_ERRORS + MODEL_ERRORS)
        assert issubclass(unavailable, RuntimeError), "what callers caught before still catches it"
    for invalid in (UnsafeQueryError, FormulaError):
        assert issubclass(invalid, Invalid) and issubclass(invalid, ValueError)
