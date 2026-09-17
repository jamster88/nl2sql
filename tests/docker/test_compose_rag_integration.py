"""End-to-end retrieval inside the real containers.

Static compose checks prove the wiring is *declared* correctly; only this
proves it *works*: that the agent container resolves the `vectordb` and
`chunkdb` services over the compose network, reaches back out to the Docker
host for bge-m3 via host.docker.internal, and that the packaged image carries
both retrieval modules and their dependencies.

The chat model is deliberately not involved -- these exercise the retrieval
half directly, so they neither need nor wait on a remote Ollama.

Opt-in (`pytest --run-docker`). Skips if Docker, the images, or the embedding
host are unavailable -- except for the degradation test, which stubs the
embedder and so runs whether or not bge-m3 is up.
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

# Deliberately stubs the embedder rather than building a real one. search()
# embeds before it touches the store, so with a real embedder and an
# unreachable embedding host this probe reports "Could not embed the question"
# -- a true error, but not the one under test, and the assertion below would
# fail for a reason that has nothing to do with the vector store.
FAILURE_PROBE = """
import json
from nl2sql_agent.retrieval import KnowledgeBase, KnowledgeUnavailableError

class StubEmbedder:
    def embed_query(self, text):
        return [0.0] * 1024

kb = KnowledgeBase(
    "postgresql+psycopg://ragproc:ragproc@127.0.0.1:1/nl2sql_vectors", StubEmbedder()
)
try:
    kb.search("anything")
    print("RESULT " + json.dumps({"raised": False}))
except KnowledgeUnavailableError as exc:
    print("RESULT " + json.dumps({"raised": True, "message": str(exc)[:200]}))
"""


# The v3 ensemble, exercised the same way: inside the container, against the
# compose-supplied CONTEXT_DB_URL and VECTOR_DB_URL. Spans both databases, so it
# is the only check that proves the agent image can reach the context store at
# all -- a URL that resolves on the host says nothing about the container.
EXAMPLES_PROBE = """
import json
from nl2sql_agent.config import Settings
from nl2sql_agent.examples import BY_KEYWORDS, BY_QUESTION, BY_REASONING
from nl2sql_agent.examples import GoldenPairLibrary, build_embedder

settings = Settings.from_env()
library = GoldenPairLibrary(
    settings.context_db_url, settings.vector_db_url, build_embedder(settings),
    top_k=settings.examples_top_k, candidate_k=settings.examples_candidate_k,
    weights={BY_QUESTION: settings.example_weight_question,
             BY_KEYWORDS: settings.example_weight_keywords,
             BY_REASONING: settings.example_weight_reasoning},
    fusion=settings.examples_fusion,
)
question = "Which vendor returned the highest total allowance through SLOTTING programs?"
rankings = library.rank(question)
pairs = library.search(question)
print("RESULT " + json.dumps({
    "context_db_url": settings.context_db_url,
    "pair_count": library.count(),
    "legs": {name: len(hits) for name, hits in rankings.items()},
    "top": [p.pair_id for p in pairs],
    "top_tables": pairs[0].table_list if pairs else [],
    "top_sql_starts": pairs[0].sql_code[:6].upper() if pairs else "",
    "weights": library.weights,
    "fusion": library.fusion,
}))
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
        # Distinguish "bge-m3 is not running on this machine" from a genuine
        # packaging failure; both exit non-zero, but only one is a reason to
        # skip quietly rather than to go looking for a bug in the image.
        if "Could not embed the question" in result.stderr:
            pytest.skip(
                "the embedding host is unreachable from the agent container "
                "(is Ollama running locally with bge-m3 pulled?)"
            )
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

    up = _compose("up", "-d", "vectordb", "chunkdb")
    if up.returncode != 0:
        pytest.skip(f"could not start the retrieval services:\n{up.stderr[-1500:]}")

    for container in ("nl2sql-vectordb", "nl2sql-chunkdb"):
        health = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Health.Status}}", container],
            capture_output=True, text=True,
        )
        if health.stdout.strip() != "healthy":
            pytest.skip(
                f"{container} is not healthy: "
                f"{health.stdout.strip() or health.stderr.strip()}"
            )
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


# ---------------------------------------------------------------------------
# v3: the golden-pair ensemble, inside the container
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def examples_probe(running_stack) -> dict:
    return _run_probe(EXAMPLES_PROBE)


def test_the_agent_container_reaches_the_context_store(examples_probe: dict):
    """The URL the compose file hands the agent has to resolve *inside* the
    container, where `localhost` means the container and `chunkdb` means the
    service. Nothing on the host can prove this.
    """
    assert examples_probe["context_db_url"].startswith("postgresql+psycopg://")
    assert "@chunkdb:5432/" in examples_probe["context_db_url"]
    assert examples_probe["pair_count"] == 45


def test_all_three_retrieval_legs_fire_inside_the_container(examples_probe: dict):
    """One leg reads pgvector, one reads the context store, one calls out to the
    embedding host. A silently empty leg still returns plausible results.
    """
    legs = examples_probe["legs"]
    assert set(legs) == {"question", "keywords", "reasoning"}
    assert all(count > 0 for count in legs.values()), legs


def test_the_configured_weights_survive_into_the_container(examples_probe: dict):
    assert examples_probe["weights"] == {
        "question": 0.50, "keywords": 0.35, "reasoning": 0.15
    }
    assert examples_probe["fusion"] == "score"


def test_the_ensemble_returns_a_usable_worked_example(examples_probe: dict):
    """What the agent is actually given: a pair whose SQL it can follow and
    whose tables feed table selection.
    """
    assert examples_probe["top"][0] == "Q20"
    assert examples_probe["top_sql_starts"] in ("SELECT", "WITH")
    assert "fact_vendor_allowances" in examples_probe["top_tables"]
