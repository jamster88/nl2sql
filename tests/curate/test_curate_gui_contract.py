"""The curation GUI's TypeScript types, checked against the models they mirror.

`curate/src/api/types.ts` is written by hand, as the other interfaces' are,
and held to `review/nl2sql_review/models.py` the same way: names only, which
is where drift happens and what can be compared without a TypeScript parser.

Between this page and the review interface every wire model is mirrored
somewhere; `tests/review/test_review_gui_contract.py` checks that, with this
module's list beside its own.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

import pytest

from nl2sql_review import models
from nl2sql_review.snippet_validation import KINDS

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TYPES_TS = REPO_ROOT / "curate" / "src" / "api" / "types.ts"

#: TypeScript interface -> the pydantic model it mirrors: the curation models,
#: and the shared ones this page reads.
MIRRORED = {
    "ReviewLimits": models.ReviewLimits,
    "ReviewMeta": models.ReviewMeta,
    "StepModel": models.StepModel,
    "ValidationModel": models.ValidationModel,
    "SubmissionModel": models.SubmissionModel,
    "DraftModel": models.DraftModel,
    "PreviewModel": models.PreviewModel,
    "PromotionModel": models.PromotionModel,
    "GoldenPairModel": models.GoldenPairModel,
    "GoldenSet": models.GoldenSet,
    "GoldenResultModel": models.GoldenResultModel,
    "WithdrawalModel": models.WithdrawalModel,
    "GoldenRemovalModel": models.GoldenRemovalModel,
    "FixModel": models.FixModel,
    "FixList": models.FixList,
    "CuratedFixValidateRequest": models.CuratedFixValidateRequest,
    "CuratedFixRequest": models.CuratedFixRequest,
    "CuratedFixResultModel": models.CuratedFixResultModel,
    "FixRemovalModel": models.FixRemovalModel,
    "SnippetDraftModel": models.SnippetDraftModel,
    "SnippetRequest": models.SnippetRequest,
    "SnippetPreviewRequest": models.SnippetPreviewRequest,
    "SnippetPreviewModel": models.SnippetPreviewModel,
    "SnippetValidationModel": models.SnippetValidationModel,
    "SnippetModel": models.SnippetModel,
    "SnippetStoreModel": models.SnippetStoreModel,
    "SnippetSet": models.SnippetSet,
    "SnippetResultModel": models.SnippetResultModel,
    "SchemaColumn": models.SchemaColumn,
    "SchemaTable": models.SchemaTable,
    "SchemaModel": models.SchemaModel,
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


def test_every_mirrored_interface_exists_in_the_typescript(interfaces):
    assert set(MIRRORED) - set(interfaces) == set()


@pytest.mark.parametrize("name", sorted(MIRRORED))
def test_the_fields_match(name: str, interfaces):
    expected = set(MIRRORED[name].model_fields)
    actual = interfaces[name]
    assert actual == expected, (
        f"{name} has drifted: curate/src/api/types.ts is missing {sorted(expected - actual)} "
        f"and has extra {sorted(actual - expected)}"
    )


def _union(text: str, name: str) -> set[str]:
    match = re.search(rf"export type {name} =([^;]+);", text)
    assert match, f"{name} is not declared in types.ts"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def test_the_fix_kinds_and_the_snippet_kinds_match(types_ts: str):
    assert _union(types_ts, "FixKind") == set(get_args(models.FixKind))
    assert _union(types_ts, "SnippetKind") == set(get_args(models.SnippetKind)) == set(KINDS)


def test_the_page_asks_for_every_curation_route_the_service_has():
    """A route nothing calls is a route nobody tested from the page."""
    client = (REPO_ROOT / "curate" / "src" / "api" / "client.ts").read_text()
    for path in ("/v1/snippets", "/v1/snippets/validate", "/v1/snippets/preview", "/v1/golden/validate",
                 "/v1/golden/preview", "/v1/schema", "/v1/meta"):
        assert f'"{path}"' in client, path
    for template in ("/v1/snippets/${", "/v1/golden/${", "/v1/fixes/${kind}/validate", "/v1/fixes/${kind}/${"):
        assert template in client, template
