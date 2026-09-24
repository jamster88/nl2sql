"""The review GUI's TypeScript types, checked against the models they mirror.

`review/gui/src/api/types.ts` is written by hand for the same reason the web
GUI's is: a generated `api.d.ts` is not something anyone reads, and that file
is the first thing a GUI author opens. The cost of the choice is drift -- a
field added to `models.py` is invisible to the GUI, and one removed leaves
the GUI rendering something the service stopped sending.

This is what makes the choice affordable. Nothing here checks types, only
names, which is where drift actually happens and which can be compared
without writing a TypeScript parser.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, get_args

import pytest

from nl2sql_review import models

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TYPES_TS = REPO_ROOT / "review" / "gui" / "src" / "api" / "types.ts"

#: TypeScript interface -> the pydantic model it mirrors. Every wire model is
#: here; a new one is a failing test below until it is added to both.
MIRRORED = {
    "SubmissionModel": models.SubmissionModel,
    "SubmissionList": models.SubmissionList,
    "DraftModel": models.DraftModel,
    "ReviewRequest": models.ReviewRequest,
    "PreviewRequest": models.PreviewRequest,
    "PreviewModel": models.PreviewModel,
    "StepModel": models.StepModel,
    "PromotionModel": models.PromotionModel,
    "PromotionRecord": models.PromotionRecord,
    "PromotionList": models.PromotionList,
    "GoldenPairModel": models.GoldenPairModel,
    "GoldenSet": models.GoldenSet,
    "ReviewLimits": models.ReviewLimits,
    "ReviewMeta": models.ReviewMeta,
    "Health": models.Health,
    "Check": models.Check,
    "Readiness": models.Readiness,
}


@pytest.fixture(scope="module")
def types_ts() -> str:
    return TYPES_TS.read_text()


@pytest.fixture(scope="module")
def interfaces(types_ts: str) -> dict[str, set[str]]:
    """Every exported interface, as a set of its field names."""
    found: dict[str, set[str]] = {}
    for match in re.finditer(r"export interface (\w+) \{(.*?)\n\}", types_ts, re.S):
        name, body = match.group(1), match.group(2)
        # Strip comments first: a field name inside a doc comment is not a
        # field, and "id: string" in an example would otherwise become one.
        body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
        body = re.sub(r"//.*", "", body)
        found[name] = set(re.findall(r"^\s*(\w+)\??:", body, re.M))
    return found


def _union_members(text: str, name: str) -> set[str]:
    match = re.search(rf"export type {name} =([^;]+);", text)
    assert match, f"{name} is not declared in types.ts"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def test_every_wire_model_has_a_typescript_interface():
    """A model the GUI has no type for is a model the GUI cannot render."""
    wire = {
        name
        for name, value in vars(models).items()
        if isinstance(value, type)
        and issubclass(value, models.BaseModel)
        and value is not models.BaseModel
        # ApiError is the error envelope; the GUI mirrors it as ApiErrorBody
        # with the payload's own shape spelled out, which is more useful than
        # `dict[str, Any]` and cannot be compared field for field against it.
        and name != "ApiError"
    }
    assert wire - set(MIRRORED) == set(), (
        "these wire models have no entry in MIRRORED, so nothing checks that "
        "review/gui/src/api/types.ts knows about them"
    )


def test_every_mirrored_interface_exists_in_the_typescript(interfaces):
    assert set(MIRRORED) - set(interfaces) == set()


@pytest.mark.parametrize("name", sorted(MIRRORED))
def test_the_fields_match(name: str, interfaces):
    model = MIRRORED[name]
    expected = set(model.model_fields)
    actual = interfaces[name]

    assert actual == expected, (
        f"{name} has drifted: review/gui/src/api/types.ts is missing "
        f"{sorted(expected - actual)} and has extra {sorted(actual - expected)}"
    )


def test_the_states_match(types_ts: str):
    assert _union_members(types_ts, "State") == set(get_args(models.State))


def test_the_verdicts_match(types_ts: str):
    assert _union_members(types_ts, "Verdict") == set(get_args(models.Verdict))


def test_the_states_match_the_database(types_ts: str):
    """The GUI, the wire model and the CHECK constraint are one list.

    A state the database allows and the GUI has no chip for is a queue
    nobody can see.
    """
    from nl2sql_review.store import STATES

    assert _union_members(types_ts, "State") == set(STATES)
    assert set(get_args(models.State)) == set(STATES)


def test_the_error_envelope_is_the_same_one_the_agent_api_uses(types_ts: str):
    """One envelope across both services, so one client parses both."""
    assert "export interface ApiErrorBody" in types_ts
    agent_types = (REPO_ROOT / "gui" / "src" / "api" / "types.ts").read_text()
    body = re.search(r"export interface ApiErrorBody \{(.*?)\n\}", types_ts, re.S).group(1)
    agent_body = re.search(r"export interface ApiErrorBody \{(.*?)\n\}", agent_types, re.S).group(1)
    assert set(re.findall(r"^\s*(\w+)\??:", body, re.M)) == set(
        re.findall(r"^\s*(\w+)\??:", agent_body, re.M)
    )
