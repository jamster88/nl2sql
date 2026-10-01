"""The console interface's TypeScript types, checked against the models they mirror.

`console/src/api/types.ts` is written by hand, like the other two
interfaces' -- it is the file an author reads first -- and the cost of that
is drift. This is what makes it affordable: every interface is compared,
name for name, with the pydantic model it mirrors, and the two literal
unions with the ones the server validates against.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

import pytest
from pydantic import BaseModel

from nl2sql_agent.console import models
from nl2sql_agent.console.query import MODES
from nl2sql_agent.state import PLANNER, RUNTIME, STATIC

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TYPES_TS = REPO_ROOT / "console" / "src" / "api" / "types.ts"

#: TypeScript interface -> the model it mirrors. Every wire model is here; a
#: new one is a failing test below until it is added to both.
MIRRORED = {
    "ConsoleLimits": models.ConsoleLimits,
    "ConsoleMeta": models.ConsoleMeta,
    "ColumnModel": models.ColumnModel,
    "TableModel": models.TableModel,
    "SchemaModel": models.SchemaModel,
    "PromptModel": models.PromptModel,
    "QueryRequest": models.QueryRequest,
    "IssueModel": models.IssueModel,
    "AgentVerdict": models.AgentVerdict,
    "ResultColumn": models.ResultColumn,
    "QueryResult": models.QueryResult,
    "Health": models.Health,
    "Check": models.Check,
    "Readiness": models.Readiness,
}


@pytest.fixture(scope="module")
def types_ts() -> str:
    return TYPES_TS.read_text()


@pytest.fixture(scope="module")
def interfaces(types_ts: str) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for match in re.finditer(r"export interface (\w+) \{(.*?)\n\}", types_ts, re.S):
        body = re.sub(r"/\*.*?\*/", "", match.group(2), flags=re.S)
        body = re.sub(r"//.*", "", body)
        found[match.group(1)] = set(re.findall(r"^\s*(\w+)\??:", body, re.M))
    return found


def _union(text: str, name: str) -> set[str]:
    match = re.search(rf"export type {name} =([^;]+);", text)
    assert match, f"{name} is not declared in types.ts"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def test_every_wire_model_has_a_typescript_interface():
    """The error envelope is mirrored as ApiErrorBody, with its payload
    spelled out, which cannot be compared field for field with `dict`."""
    wire = {
        name
        for name in models.__all__
        if isinstance(getattr(models, name), type)
        and issubclass(getattr(models, name), BaseModel)
        and name != "ApiError"
    }
    assert wire == set(MIRRORED)


def test_every_mirrored_interface_exists_in_the_typescript(interfaces):
    assert set(MIRRORED) - set(interfaces) == set()


@pytest.mark.parametrize("name", sorted(MIRRORED))
def test_the_fields_match(name: str, interfaces):
    expected = set(MIRRORED[name].model_fields)
    actual = interfaces[name]
    assert actual == expected, (
        f"{name} has drifted: types.ts is missing {sorted(expected - actual)} "
        f"and has extra {sorted(actual - expected)}"
    )


def test_the_modes_match(types_ts: str):
    assert _union(types_ts, "Mode") == set(get_args(models.Mode)) == set(MODES)


def test_the_stages_are_the_agents_gates(types_ts: str):
    """The pipeline's own names, so a verdict here and a trace from the agent
    are read in the same words."""
    assert _union(types_ts, "Stage") == set(get_args(models.Stage)) == {STATIC, PLANNER, RUNTIME}
