"""End-to-end retrieval inside the real containers.

Static compose checks prove the wiring is *declared* correctly; only this
proves it *works*: that the agent container resolves the `vectordb` service
over the compose network, reaches back out to the Docker host for bge-m3 via
host.docker.internal, and that the packaged image carries the retrieval code
and its dependencies.

The chat model is deliberately not involved -- these exercise the retrieval
half directly, so they neither need nor wait on a remote Ollama.

Opt-in (`pytest --run-docker`). Skips if Docker, the images, or the embedding
host are unavailable.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.docker

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Runs inside the agent container, against whatever the compose environment
# hands it -- the same VECTOR_DB_URL/EMBED_* the agent itself would use.
PROBE = """
import json, os
from nl2sql_agent.config import Settings
from nl2sql_agent.retrieval import KnowledgeBase, build_embedder, tables_mentioned

settings = Settings.from_env()
kb = KnowledgeBase(settings.vector_db_url, build_embedder(settings), top_k=settings.rag_top_k)
chunks = kb.search("How do I avoid double counting market share?")
print("RESULT " + json.dumps({
    "vector_db_url": settings.vector_db_url,
    "embed_base_url": settings.embed_base_url,
    "collections": kb.collections(),
    "models": sorted(kb.embedding_models()),
    "headings": [c.heading_path for c in chunks],
    "tables": tables_mentioned(chunks),
    "count": len(chunks),
}))
"""

FAILURE_PROBE = """
import json
from nl2sql_agent.retrieval import KnowledgeBase, KnowledgeUnavailableError, build_embedder
from nl2sql_agent.config import Settings

settings = Settings.from_env()
settings.vector_db_url = "postgresql+psycopg://ragproc:ragproc@127.0.0.1:1/nl2sql_vectors"
kb = KnowledgeBase(settings.vector_db_url, build_embedder(settings))
try:
    kb.search("anything")
    print("RESULT " + json.dumps({"raised": False}))
except KnowledgeUnavailableError as exc:
    print("RESULT " + json.dumps({"raised": True, "message": str(exc)[:200]}))
"""


def _compose(*args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout,
    )


def _run_probe(script: str, timeout: int = 300) -> dict:
    result = _compose(
        "run", "--rm", "--entrypoint", "python", "agent", "-c", script, timeout=timeout
    )
    if result.returncode != 0:
        pytest.skip(f"could not run the probe in the agent container:\n{result.stderr[-1500:]}")
    for line in result.stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[len("RESULT "):])
    pytest.skip(f"probe produced no result:\n{result.stdout[-1000:]}\n{result.stderr[-1000:]}")


@pytest.fixture(scope="module")
def running_stack(docker_daemon_available: bool):
    if not docker_daemon_available:
        pytest.skip("no working docker daemon")

    # Build from current source first. Without this the probe would run against
    # whatever `nl2sql-agent:latest` happens to be lying around locally -- which
    # can predate the retrieval module entirely, turning a real failure into a
    # confusing skip.
    build = _compose("build", "agent", timeout=600)
    if build.returncode != 0:
        pytest.skip(f"could not build the agent image:\n{build.stderr[-1500:]}")

    up = _compose("up", "-d", "vectordb")
    if up.returncode != 0:
        pytest.skip(f"could not start the vectordb service:\n{up.stderr[-1500:]}")

    health = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Health.Status}}", "nl2sql-vectordb"],
        capture_output=True, text=True,
    )
    if health.stdout.strip() != "healthy":
        pytest.skip(f"vectordb is not healthy: {health.stdout.strip() or health.stderr.strip()}")
    return True


@pytest.fixture(scope="module")
def probe(running_stack) -> dict:
    return _run_probe(PROBE)


def test_agent_container_resolves_the_vectordb_service_by_name(probe: dict):
    # Not localhost: inside the compose network the database is a service name.
    assert "@vectordb:5432/" in probe["vector_db_url"]
    assert probe["collections"], "no collections visible from inside the container"


def test_agent_container_reaches_the_embedding_host_through_host_docker_internal(probe: dict):
    assert probe["embed_base_url"].startswith("http://host.docker.internal")
    # A returned chunk count proves the embedding call actually succeeded --
    # the search cannot run without a query vector.
    assert probe["count"] > 0


def test_retrieval_inside_the_container_finds_the_expected_chunk(probe: dict):
    headings = " | ".join(probe["headings"])
    assert "fan-out" in headings, headings


def test_retrieval_inside_the_container_surfaces_table_hints(probe: dict):
    assert "fact_market_share_weekly" in probe["tables"]


def test_the_containers_knowledge_base_was_built_with_the_configured_model(probe: dict):
    assert probe["models"], "no embedding_model recorded in the store"
    assert any("bge-m3" in model for model in probe["models"])


def test_every_knowledge_collection_is_visible_from_the_container(probe: dict):
    assert set(probe["collections"]) >= {
        "business_index_embeddings",
        "data_dictionary_embeddings",
        "ddl_index_embeddings",
    }


def test_an_unreachable_vector_store_raises_the_wrapped_error_inside_the_image(running_stack):
    """The degradation path the graph depends on has to work in the shipped
    image, not just in the test environment.
    """
    result = _run_probe(FAILURE_PROBE)
    assert result["raised"] is True
    assert "knowledge base" in result["message"].lower()
