"""The review service's image, read rather than run.

The property worth asserting here is what the image *does not* contain. This
is the process that can rewrite the golden question set and reload the stores
built from it; it has no business also being able to answer a question, and
the way that stays true is that its requirements file never grows the agent's
dependencies.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REVIEW = REPO_ROOT / "review"


@pytest.fixture(scope="module")
def dockerfile() -> str:
    return (REVIEW / "Dockerfile").read_text()


@pytest.fixture(scope="module")
def requirements() -> list[str]:
    return [
        line.split("#")[0].strip()
        for line in (REVIEW / "requirements.txt").read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _names(requirements: list[str]) -> set[str]:
    return {re.split(r"[<>=\[]", line)[0].strip().lower() for line in requirements}


# ---------------------------------------------------------------------------
# What it carries
# ---------------------------------------------------------------------------


def test_it_serves_http_and_talks_to_postgres(requirements: list[str]):
    assert {"fastapi", "uvicorn", "psycopg", "pydantic"} <= _names(requirements)


def test_it_carries_what_the_loaders_need(requirements: list[str]):
    """Promotion runs `05_load_golden_pairs.py` and `06_embed_golden_pairs.py`
    as scripts. They import ragproc, which needs these."""
    assert {"pgvector", "requests", "numpy"} <= _names(requirements)


def test_it_does_not_carry_the_agent(requirements: list[str]):
    """This process never answers a question.

    The separation is the point of there being two services: the one that
    can rewrite the benchmark is not the one exposed to whoever can reach a
    port. An image that grew langchain would be an image that could be
    repurposed into one that does both.
    """
    forbidden = {"langchain", "langchain-core", "langchain-ollama", "langgraph", "sqlalchemy", "pglast"}
    assert not (_names(requirements) & forbidden)


def test_every_dependency_is_bounded(requirements: list[str]):
    """An unbounded requirement is a build that stops reproducing."""
    for line in requirements:
        assert re.search(r"[=<>]", line), f"{line} has no version bound"


def test_the_loaders_are_in_the_image(dockerfile: str):
    assert "rag/ragproc/" in dockerfile
    assert "05_load_golden_pairs.py" in dockerfile
    assert "06_embed_golden_pairs.py" in dockerfile


def test_the_loaders_land_where_the_settings_look_for_them(dockerfile: str):
    from nl2sql_review.settings import DEFAULT_RAG_DIR

    assert f"./rag/" in dockerfile
    assert dockerfile.count("WORKDIR /app") == 1
    assert DEFAULT_RAG_DIR == "/app/rag"


def test_the_question_document_is_in_the_image_as_a_fallback(dockerfile: str):
    """Compose bind-mounts the checkout's copy over this one.

    The image's copy exists so the container starts and `/readyz` can say
    something true when nothing is mounted -- but a promotion against it
    would be a promotion nobody ever sees, which is why the Dockerfile says
    so where someone changing the mount would read it.
    """
    assert "context_questions/translated_questions.md" in dockerfile
    assert "bind-mounted" in dockerfile.lower()


def test_it_expects_the_certificate_the_api_writes(dockerfile: str):
    from nl2sql_review.settings import DEFAULT_TLS_DIR

    assert f"mkdir -p {DEFAULT_TLS_DIR}" in dockerfile
    assert "never generates one" in dockerfile


def test_the_entrypoint_is_the_review_module(dockerfile: str):
    assert 'ENTRYPOINT ["python", "-m", "nl2sql_review"]' in dockerfile


def test_the_port_it_exposes_is_the_one_it_serves(dockerfile: str):
    from nl2sql_review.settings import DEFAULT_PORT

    assert f"EXPOSE {DEFAULT_PORT}" in dockerfile


def test_it_has_a_health_check_that_follows_its_own_tls_setting(dockerfile: str):
    """Assuming https would make a working container look unhealthy the
    moment someone turns TLS off."""
    check = re.search(r"HEALTHCHECK.*?(?=\nLABEL)", dockerfile, re.S).group(0)
    assert "/healthz" in check
    assert "REVIEW_TLS_ENABLED" in check


def test_the_dependency_layer_is_cached_separately(dockerfile: str):
    requirements = dockerfile.index("review/requirements.txt")
    source = dockerfile.index("review/nl2sql_review/")
    assert requirements < source


def test_it_is_labelled(dockerfile: str):
    assert "org.opencontainers.image.title" in dockerfile
    assert "org.opencontainers.image.version" in dockerfile


def test_the_version_matches_the_package(dockerfile: str):
    from nl2sql_review.app import __version__

    assert f"REVIEW_VERSION={__version__}" in dockerfile


def test_the_review_gui_version_matches_the_service():
    import json

    from nl2sql_review.app import __version__

    package = json.loads((REVIEW / "gui" / "package.json").read_text())
    assert package["version"] == __version__
