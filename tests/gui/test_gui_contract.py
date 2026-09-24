"""The GUI's TypeScript types, checked against the models they mirror.

`gui/src/api/types.ts` is written by hand rather than generated, because a
generated `api.d.ts` is not something anyone reads and this file is the first
thing a GUI author opens. The cost of that choice is drift: a field added to
`models.py` is invisible to the GUI, and a field removed from it leaves the
GUI rendering something the server stopped sending.

These tests are what makes the choice affordable. They introspect the pydantic
models and parse the TypeScript, and fail when either side gains or loses a
field the other does not have. Nothing here checks types -- only names, which
is where drift actually happens and which can be compared without writing a
TypeScript parser.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, get_args, get_origin

import pytest

from nl2sql_agent.api import models

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TYPES_TS = REPO_ROOT / "gui" / "src" / "api" / "types.ts"

#: TypeScript interface -> the pydantic model it mirrors. Every wire model is
#: here; a new one is a failing test below until it is added to both.
MIRRORED = {
    "ProgressEvent": models.ProgressEvent,
    "ResultTable": models.ResultTable,
    "ChartSpec": models.ChartSpec,
    "Claim": models.Claim,
    "AuditReport": models.AuditReport,
    "TraceEntry": models.TraceEntry,
    "LiteralMatch": models.LiteralMatch,
    "Answer": models.Answer,
    "JobLinks": models.JobLinks,
    "Job": models.Job,
    "JobList": models.JobList,
    "Limits": models.Limits,
    "Pipeline": models.Pipeline,
    "Meta": models.Meta,
    "Health": models.Health,
    "Check": models.Check,
    "Readiness": models.Readiness,
    "AskRequest": models.AskRequest,
    "FeedbackRequest": models.FeedbackRequest,
    "FeedbackModel": models.FeedbackModel,
}


@pytest.fixture(scope="module")
def types_ts() -> str:
    return TYPES_TS.read_text()


@pytest.fixture(scope="module")
def interfaces(types_ts: str) -> dict[str, set[str]]:
    """Every exported interface, as a set of its field names.

    An interface body ends at the first `}` in the first column, which is
    enough structure for this file: nested objects are indented, so they are
    skipped by the field pattern rather than needing to be parsed.
    """
    found: dict[str, set[str]] = {}
    for match in re.finditer(
        r"^export interface (\w+) \{\n(.*?)^\}", types_ts, re.DOTALL | re.MULTILINE
    ):
        name, body = match.group(1), match.group(2)
        found[name] = set(re.findall(r"^  (\w+)\??:", body, re.MULTILINE))
    return found


def _union_members(source: str, alias: str) -> set[str]:
    """The string members of `export type <alias> = "a" | "b";`."""
    match = re.search(rf"^export type {alias} =([^;]+);", source, re.MULTILINE)
    assert match, f"gui/src/api/types.ts has no {alias} union"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


# ---------------------------------------------------------------------------
# Every model is mirrored, and every mirror is a model
# ---------------------------------------------------------------------------


def test_every_wire_model_has_a_typescript_interface():
    """A model the GUI has no type for is a model the GUI cannot render."""
    wire = {
        name
        for name, value in vars(models).items()
        if isinstance(value, type)
        and issubclass(value, models.BaseModel)
        and value is not models.BaseModel
        # ApiError is the error envelope; the GUI mirrors it as
        # ApiErrorBody with the payload's own shape spelled out, which is
        # more useful than `dict[str, Any]` and cannot be compared field
        # for field against it.
        and name != "ApiError"
    }
    assert wire - set(MIRRORED) == set(), (
        "these wire models have no entry in MIRRORED, so nothing checks that "
        "gui/src/api/types.ts knows about them"
    )


def test_every_mirrored_interface_exists_in_the_typescript(interfaces: dict[str, set[str]]):
    assert set(MIRRORED) - set(interfaces) == set()


@pytest.mark.parametrize("name", sorted(MIRRORED))
def test_the_fields_match(name: str, interfaces: dict[str, set[str]]):
    model = MIRRORED[name]
    expected = set(model.model_fields)
    actual = interfaces[name]

    assert actual == expected, (
        f"{name} has drifted: gui/src/api/types.ts is missing "
        f"{sorted(expected - actual)} and has extra {sorted(actual - expected)}"
    )


# ---------------------------------------------------------------------------
# The unions, which are the other half of the contract
# ---------------------------------------------------------------------------


def test_the_job_statuses_match(types_ts: str):
    assert _union_members(types_ts, "JobStatus") == set(get_args(models.JobStatus))


def test_the_chart_kinds_cover_everything_the_pipeline_can_choose(types_ts: str):
    """`ChartKind` is a closed union, so a new kind must reach the GUI.

    The kinds are not in a model -- `ChartSpec.kind` is a plain string, since
    the pipeline decides it -- so the source of truth is `choose_chart`
    itself, which is where the strings are written.
    """
    present = (REPO_ROOT / "agent" / "nl2sql_agent" / "present.py").read_text()
    emitted = set(re.findall(r'ChartSpec\(kind="(\w+)"', present))
    emitted |= set(re.findall(r'kind="(\w+)" if ', present))
    emitted |= set(re.findall(r'if \w+ == "\w+" else "(\w+)"', present))

    assert emitted, "no chart kinds found in present.py -- has choose_chart moved?"
    assert emitted <= _union_members(types_ts, "ChartKind"), (
        "present.py can produce a chart kind the GUI's ChartKind union does not list"
    )


def test_the_authentication_values_match(types_ts: str):
    """`Meta.authentication` is what the GUI shows in its status bar."""
    field = models.Meta.model_fields["authentication"]
    assert get_origin(field.annotation) is Literal
    expected = set(get_args(field.annotation))

    match = re.search(r'authentication: ([^;]+);', types_ts)
    assert match
    assert set(re.findall(r'"([^"]+)"', match.group(1))) == expected


# ---------------------------------------------------------------------------
# The two rules the models were written to
# ---------------------------------------------------------------------------


def test_nothing_the_gui_iterates_over_is_optional(interfaces: dict[str, set[str]], types_ts: str):
    """"Nothing is ever `None` where a list would do" is the models' first rule.

    It is what lets the GUI write `result.rows.map(...)` without a null check,
    so a list that arrives as `T[] | null` would be a silent crash rather
    than a type error. This checks the TypeScript kept the promise: a list
    field is a list, never a nullable one.
    """
    nullable_lists = re.findall(r"^  (\w+): \w+\[\] \| null;", types_ts, re.MULTILINE)
    assert nullable_lists == [], (
        f"these fields are typed as a nullable list: {nullable_lists}. The wire "
        "contract promises a list is always a list."
    )


def test_the_two_fields_that_are_genuinely_nullable_still_are(interfaces: dict[str, set[str]]):
    """`result` and `chart` are null for a refusal, and the GUI must branch.

    The opposite failure to the one above: if these lost their `| null` the
    GUI would stop checking and render an empty table for every refusal.
    """
    answer = (REPO_ROOT / "gui" / "src" / "api" / "types.ts").read_text()
    assert re.search(r"^  result: ResultTable \| null;", answer, re.MULTILINE)
    assert re.search(r"^  chart: ChartSpec \| null;", answer, re.MULTILINE)
