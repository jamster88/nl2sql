"""The directory page's TypeScript types, checked against the models they mirror.

`auth/gui/src/api/types.ts` is written by hand, as every page's is, and held
to `auth/nl2sql_auth/models.py` by name -- which is where drift happens, and
what can be compared without a TypeScript parser.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from nl2sql_auth import models

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GUI = REPO_ROOT / "auth" / "gui"

MIRRORED = {
    "Person": models.Person,
    "PersonList": models.PersonList,
    "NewPerson": models.NewPerson,
    "PersonChange": models.PersonChange,
    "Group": models.Group,
    "GroupList": models.GroupList,
    "ImportRequest": models.ImportRequest,
    "ImportResult": models.ImportResult,
    "SyncReport": models.SyncReport,
    "DirectoryMeta": models.DirectoryMeta,
}

#: The sign-in client every page carries, against the auth service's models.
SESSION = {"Session": models.Session, "AuthMeta": models.AuthMeta}


def _interfaces(path: Path) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for match in re.finditer(r"export interface (\w+) \{(.*?)\n\}", path.read_text(), re.S):
        body = re.sub(r"/\*.*?\*/", "", match.group(2), flags=re.S)
        body = re.sub(r"//.*", "", body)
        found[match.group(1)] = set(re.findall(r"^\s*(\w+)\??:", body, re.M))
    return found


@pytest.mark.parametrize("name", sorted(MIRRORED))
def test_the_directory_types_match(name: str):
    actual = _interfaces(GUI / "src" / "api" / "types.ts")[name]
    expected = set(MIRRORED[name].model_fields)
    assert actual == expected, f"{name}: missing {sorted(expected - actual)}, extra {sorted(actual - expected)}"


@pytest.mark.parametrize("name", sorted(SESSION))
def test_the_shared_sign_in_types_match(name: str):
    actual = _interfaces(REPO_ROOT / "web" / "src" / "auth" / "session.ts")[name]
    assert actual == set(SESSION[name].model_fields)


@pytest.mark.parametrize("page", ["gui", "review/gui", "curate", "console", "auth/gui"])
def test_every_page_signs_in_with_the_one_shared_gate(page: str):
    """Five identical copies until 6.2 (V6-25); one, in `web/`, since."""
    assert not (REPO_ROOT / page / "src" / "auth").exists(), page
    assert 'import { SignInGate } from "@nl2sql/web/auth/SignInGate";' in (REPO_ROOT / page / "src" / "main.tsx").read_text()
    client = (REPO_ROOT / page / "src" / "api" / "client.ts").read_text()
    assert 'from "@nl2sql/web/auth/session"' in client and 'from "@nl2sql/web/api/errors"' in client, page


def test_the_page_asks_for_every_directory_route_the_service_has():
    client = (GUI / "src" / "api" / "client.ts").read_text()
    for path in ("/meta", "/people", "/groups", "/import", "/sync"):
        assert f'"{path}"' in client, path
    for suffix in ("/password", "/unlock"):
        assert f"${{person(uid)}}{suffix}" in client, suffix
