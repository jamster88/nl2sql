"""The desktop client's Java records, checked against the models they mirror.

`desktop/src/main/java/org/nl2sql/desktop/api/Models.java` is written by hand
rather than generated, for the reason `gui/src/api/types.ts` is: a generated
file is not something anyone reads, and this one is the first thing somebody
writing a third client opens. The cost of that choice is drift -- a field
added to `models.py` is invisible to the client, and a field removed from it
leaves the client rendering something the server stopped sending.

These tests are what make the choice affordable. They introspect the pydantic
models and parse the Java, and fail when either side gains or loses a field
the other does not have. Nothing here checks types -- only names, which is
where drift actually happens and which can be compared without writing a Java
parser.

None of it needs Maven or a JDK: it reads the file.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, get_args, get_origin

import pytest

from nl2sql_agent.api import models

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MODELS_JAVA = REPO_ROOT / "desktop" / "src" / "main" / "java" / "org" / "nl2sql" / "desktop" / "api" / "Models.java"
CHART_KIND_JAVA = REPO_ROOT / "desktop" / "src" / "main" / "java" / "org" / "nl2sql" / "desktop" / "chart" / "ChartKind.java"

#: The two records this client constructs rather than receives. Everything
#: else below arrives as JSON from a server whose version is not this one's.
OUTBOUND = {"AskRequest", "FeedbackRequest"}

#: Java record -> the pydantic model it mirrors. Every wire model is here; a
#: new one is a failing test below until it is added to both.
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
def models_java() -> str:
    return MODELS_JAVA.read_text()


@pytest.fixture(scope="module")
def records(models_java: str) -> dict[str, list[str]]:
    """Every nested record, as the list of its component names.

    A record header runs from `public record Name(` to the matching `)`,
    which may be several lines. Splitting the parameters on commas that are
    not inside angle brackets is enough structure for this file: the types
    are generics at worst, and nothing here has a default.
    """
    found: dict[str, list[str]] = {}
    for match in re.finditer(r"public record (\w+)\(", models_java):
        name = match.group(1)
        depth = 1
        index = match.end()
        while depth:
            depth += {"(": 1, ")": -1}.get(models_java[index], 0)
            index += 1
        header = models_java[match.end():index - 1]
        found[name] = _components(header)
    return found


def _components(header: str) -> list[str]:
    parts: list[str] = []
    angle = 0
    current = ""
    for character in header:
        if character == "<":
            angle += 1
        elif character == ">":
            angle -= 1
        if character == "," and angle == 0:
            parts.append(current)
            current = ""
        else:
            current += character
    parts.append(current)
    # "List<String> tables" -> "tables"
    return [part.split()[-1] for part in parts if part.strip()]


def _enum_constants(source: str, name: str) -> set[str]:
    """The wire names of `enum Name { A("a"), B("b"); ... }`."""
    match = re.search(rf"enum {name} \{{(.*?);", source, re.DOTALL)
    assert match, f"Models.java has no {name} enum"
    return set(re.findall(r'\("([^"]+)"\)', match.group(1)))


# ---------------------------------------------------------------------------
# Every model is mirrored, and every mirror is a model
# ---------------------------------------------------------------------------


def test_every_wire_model_has_a_java_record():
    """A model the client has no record for is one it cannot read."""
    wire = {
        name
        for name, value in vars(models).items()
        if isinstance(value, type)
        and issubclass(value, models.BaseModel)
        and value is not models.BaseModel
        # ApiError is the error envelope; the client mirrors it as
        # ApiErrorBody with the payload left as a map, because it branches on
        # one key of it and never renders the rest.
        and name != "ApiError"
    }
    assert wire - set(MIRRORED) == set(), (
        "these wire models have no entry in MIRRORED, so nothing checks that "
        "the desktop client knows about them"
    )


def test_every_mirrored_record_exists_in_the_java(records: dict[str, list[str]]):
    assert set(MIRRORED) - set(records) == set()


@pytest.mark.parametrize("name", sorted(MIRRORED))
def test_the_fields_match(name: str, records: dict[str, list[str]]):
    model = MIRRORED[name]
    expected = set(model.model_fields)
    actual = set(records[name])

    assert actual == expected, (
        f"{name} has drifted: Models.java is missing {sorted(expected - actual)} "
        f"and has extra {sorted(actual - expected)}"
    )


@pytest.mark.parametrize("name", sorted(MIRRORED))
def test_the_field_order_matches(name: str, records: dict[str, list[str]]):
    """Records are positional, so order is part of this contract in a way it
    is not for the TypeScript mirror: a component moved rather than renamed
    still compiles and still parses, and puts one field's value in another's
    place for anything constructing one by hand."""
    assert records[name] == list(MIRRORED[name].model_fields)


# ---------------------------------------------------------------------------
# The enumerations, which are the other half of the contract
# ---------------------------------------------------------------------------


def test_the_job_statuses_match(models_java: str):
    assert _enum_constants(models_java, "JobStatus") == set(get_args(models.JobStatus))


def test_the_verdicts_match(models_java: str):
    field = models.FeedbackRequest.model_fields["verdict"]
    assert get_origin(field.annotation) is Literal
    assert _enum_constants(models_java, "Verdict") == set(get_args(field.annotation))


def test_the_chart_kinds_cover_everything_the_pipeline_can_choose():
    """`ChartKind` falls back to TABLE for a kind it does not know, so an
    unlisted one is a chart silently not drawn rather than a crash. That is
    the right failure and still a failure, so the list is checked against
    `choose_chart` itself -- which is where the strings are written.
    """
    present = (REPO_ROOT / "agent" / "nl2sql_agent" / "present.py").read_text()
    emitted = set(re.findall(r'ChartSpec\(kind="(\w+)"', present))
    emitted |= set(re.findall(r'kind="(\w+)" if ', present))
    emitted |= set(re.findall(r'if \w+ == "\w+" else "(\w+)"', present))
    assert emitted, "no chart kinds found in present.py -- has choose_chart moved?"

    java = CHART_KIND_JAVA.read_text()
    block = re.search(r"public enum ChartKind \{(.*?)\}", java, re.DOTALL)
    assert block
    known = {name.lower() for name in re.findall(r"^\s*([A-Z_]+)[,;]$", block.group(1),
                                                 re.MULTILINE)}
    assert emitted <= known, (
        f"present.py can produce {sorted(emitted - known)}, which ChartKind does not list"
    )


def test_the_authentication_values_are_not_an_enum_here(models_java: str):
    """`Meta.authentication` is a plain string in the record on purpose: the
    client shows it and branches on one value, and a closed enum would refuse
    a document from a server that grew a third scheme."""
    assert re.search(r"String authentication", models_java)


# ---------------------------------------------------------------------------
# The two rules the models were written to
# ---------------------------------------------------------------------------


def test_every_list_is_filled_in_rather_than_left_null(models_java: str, records):
    """"Nothing is ever `None` where a list would do" is the models' first
    rule, and the server keeps it. This keeps it against a server that does
    not -- an older one, or one behind a proxy that rewrote the document --
    because one null is all it takes to put a stack trace on screen.
    """
    missing = []
    for name, model in MIRRORED.items():
        if name in OUTBOUND:
            # Built here and sent, never parsed. The rule is about what a
            # server might leave out of a document, and this client is the
            # one writing these.
            continue
        body = re.search(rf"public record {name}\(.*?\n    \}}", models_java, re.DOTALL)
        if body is None:
            # A record with no compact constructor at all is only correct
            # when it has no list to fill in.
            body_text = ""
        else:
            body_text = body.group(0)
        for field, info in model.model_fields.items():
            annotation = str(info.annotation)
            if not annotation.startswith(("list[", "dict[")):
                continue
            if f"{field} = list({field})" in body_text or f"{field} = map({field})" in body_text:
                continue
            missing.append(f"{name}.{field}")
    assert missing == [], (
        f"these list or map fields are not normalised in Models.java: {missing}"
    )


def test_the_two_fields_that_are_genuinely_nullable_still_are(models_java: str):
    """`result` and `chart` are null for a refusal, and the client branches
    on both. If they were filled in, every refusal would render an empty
    table for a query that never ran."""
    answer = re.search(r"public record Answer\((.*?)\) \{", models_java, re.DOTALL)
    assert answer
    assert "ResultTable result" in answer.group(1)
    assert "ChartSpec chart" in answer.group(1)
    body = re.search(r"public record Answer\(.*?\n    \}", models_java, re.DOTALL).group(0)
    assert "result = " not in body
    assert "chart = " not in body
