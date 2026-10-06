"""The auth service's image (`auth/Dockerfile`), read.

Run, it is in tests/auth/test_auth_live.py, beside the directory and the
retail database it signs people in against.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from nl2sql_agent import __version__
from nl2sql_auth import __version__ as auth_version

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DOCKERFILE = REPO_ROOT / "auth" / "Dockerfile"


@pytest.fixture(scope="module")
def dockerfile() -> str:
    return DOCKERFILE.read_text()


def test_the_base_image_is_pinned(dockerfile: str):
    assert re.search(r"^FROM python:3\.\d+-slim@sha256:[0-9a-f]{64}$", dockerfile, re.MULTILINE)


def test_it_carries_its_three_packages_and_nothing_of_the_agents(dockerfile: str):
    copied = re.findall(r"^COPY (\S+)", dockerfile, re.MULTILINE)
    assert copied == [
        "auth/requirements.lock",
        "common/",
        "auth/nl2sql_auth/",
        "ldap/nl2sql_ldap/",
    ]


def test_the_signing_key_has_a_private_home(dockerfile: str):
    assert "chmod 700 /var/lib/nl2sql-auth" in dockerfile


def test_the_image_is_the_versions_the_package_says(dockerfile: str):
    assert auth_version == __version__
    assert f"ARG AUTH_VERSION={__version__}" in dockerfile
    assert "HEALTHCHECK" in dockerfile and "EXPOSE 8446" in dockerfile
    assert 'ENTRYPOINT ["python", "-m", "nl2sql_auth"]' in dockerfile
