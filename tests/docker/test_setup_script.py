"""setup.sh: the one-command bootstrap, which v2 extended to pull and start
the knowledge base alongside the retail database.

These run the real script against fake docker/curl binaries (see conftest.py),
so they cover its actual decision logic -- which images it pulls, what it
writes to .env, which services it starts, and which warnings it emits -- with
no Docker daemon and no network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture(scope="module")
def setup_source() -> str:
    return (REPO_ROOT / "setup.sh").read_text()


# ---------------------------------------------------------------------------
# Static checks (no execution)
# ---------------------------------------------------------------------------


def test_script_fails_fast(setup_source: str):
    assert "set -euo pipefail" in setup_source


def test_script_is_syntactically_valid():
    import subprocess

    result = subprocess.run(["bash", "-n", str(REPO_ROOT / "setup.sh")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_script_runs_from_its_own_directory(setup_source: str):
    # It writes .env next to docker-compose.yml, so it must not depend on the
    # caller's working directory.
    assert 'cd "$(dirname "$0")"' in setup_source


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------


def test_help_exits_zero_and_documents_every_flag(run_setup):
    result = run_setup("--help")
    assert result.returncode == 0
    for flag in (
        "--tag", "--image", "--ollama-url", "--model", "--port",
        "--agent-image", "--agent-tag", "--build-agent",
        "--vector-image", "--vector-tag", "--embed-url", "--embed-model",
        "--no-rag", "--build", "--reset",
    ):
        assert flag in result.output, f"{flag} is not documented in --help"


def test_unknown_flag_is_rejected(run_setup):
    result = run_setup("--not-a-flag")
    assert result.returncode == 1
    assert "unknown option" in result.output


# ---------------------------------------------------------------------------
# The default run
# ---------------------------------------------------------------------------


def test_default_run_succeeds(run_setup):
    result = run_setup()
    assert result.returncode == 0, result.output
    assert "Setup complete" in result.output


def test_default_run_pulls_all_three_images(run_setup):
    result = run_setup()
    assert result.called("pull mcfaddja/nl2sql-retail-postgres:v1")
    assert result.called("pull mcfaddja/nl2sql-rag-vectordb:v1")
    assert result.called("pull mcfaddja/nl2sql-agent:v2")


def test_default_run_starts_both_databases(run_setup):
    result = run_setup()
    assert result.called("compose up -d postgres")
    assert result.called("compose up -d vectordb")


def test_default_run_writes_env_pinning_every_image(run_setup):
    env = run_setup().env_file()
    assert env["IMAGE_NAME"] == "mcfaddja/nl2sql-retail-postgres"
    assert env["IMAGE_TAG"] == "v1"
    assert env["AGENT_IMAGE_NAME"] == "mcfaddja/nl2sql-agent"
    assert env["AGENT_IMAGE_TAG"] == "v2"
    assert env["VECTOR_IMAGE_NAME"] == "mcfaddja/nl2sql-rag-vectordb"
    assert env["VECTOR_IMAGE_TAG"] == "v1"
    assert env["RAG_ENABLED"] == "true"


def test_default_run_reports_dataset_and_knowledge_base_sizes(run_setup):
    result = run_setup()
    assert "194101 sales rows" in result.output
    assert "53 embedded chunks" in result.output


def test_default_run_does_not_write_unset_optional_keys(run_setup):
    # Absent keys let docker-compose.yml's own defaults apply, rather than
    # freezing them into .env where they would silently outlive a change.
    env = run_setup().env_file()
    for key in ("OLLAMA_BASE_URL", "OLLAMA_MODEL", "EMBED_BASE_URL", "EMBED_MODEL", "POSTGRES_PORT"):
        assert key not in env


# ---------------------------------------------------------------------------
# Flags that shape .env
# ---------------------------------------------------------------------------


def test_image_and_tag_flags_are_honored(run_setup):
    result = run_setup(
        "--image", "example.org/pg", "--tag", "v9",
        "--agent-image", "example.org/agent", "--agent-tag", "v3",
        "--vector-image", "example.org/vec", "--vector-tag", "v4",
    )
    env = result.env_file()
    assert env["IMAGE_NAME"] == "example.org/pg"
    assert env["IMAGE_TAG"] == "v9"
    assert env["AGENT_IMAGE_NAME"] == "example.org/agent"
    assert env["AGENT_IMAGE_TAG"] == "v3"
    assert env["VECTOR_IMAGE_NAME"] == "example.org/vec"
    assert env["VECTOR_IMAGE_TAG"] == "v4"
    assert result.called("pull example.org/pg:v9")
    assert result.called("pull example.org/vec:v4")
    assert result.called("pull example.org/agent:v3")


def test_model_and_host_flags_are_written_to_env(run_setup):
    env = run_setup(
        "--ollama-url", "http://chat-host:11434", "--model", "llama3:latest",
        "--embed-url", "http://embed-host:11434", "--embed-model", "nomic-embed-text",
        "--port", "15432",
    ).env_file()
    assert env["OLLAMA_BASE_URL"] == "http://chat-host:11434"
    assert env["OLLAMA_MODEL"] == "llama3:latest"
    assert env["EMBED_BASE_URL"] == "http://embed-host:11434"
    assert env["EMBED_MODEL"] == "nomic-embed-text"
    assert env["POSTGRES_PORT"] == "15432"


def test_existing_env_is_backed_up_rather_than_overwritten(run_setup):
    first = run_setup()
    (first.workdir / ".env").write_text("HAND_EDITED=yes\n")
    second = run_setup()
    assert (second.workdir / ".env.bak").read_text() == "HAND_EDITED=yes\n"
    assert "moved to .env.bak" in second.output


# ---------------------------------------------------------------------------
# --no-rag
# ---------------------------------------------------------------------------


def test_no_rag_skips_the_knowledge_base_entirely(run_setup):
    result = run_setup("--no-rag")
    assert result.returncode == 0
    assert result.env_file()["RAG_ENABLED"] == "false"
    assert not result.called("pull mcfaddja/nl2sql-rag-vectordb")
    assert not result.called("compose up -d vectordb")


def test_no_rag_still_sets_up_the_retail_database(run_setup):
    result = run_setup("--no-rag")
    assert result.called("compose up -d postgres")
    assert "194101 sales rows" in result.output


def test_no_rag_skips_the_embedding_model_check(run_setup):
    result = run_setup("--no-rag")
    assert "Checking the embedding model" not in result.output


# ---------------------------------------------------------------------------
# Agent image: pull, build, and fallback
# ---------------------------------------------------------------------------


def test_build_agent_builds_instead_of_pulling(run_setup):
    result = run_setup("--build-agent")
    assert result.called("compose build agent")
    assert not result.called("pull mcfaddja/nl2sql-agent")


def test_a_failed_agent_pull_falls_back_to_building_from_source(run_setup):
    """A private or missing agent repo must not block setup -- the source is
    right there.
    """
    result = run_setup(env={"FAKE_FAIL_PULL": "nl2sql-agent"})
    assert result.returncode == 0
    assert "building from source instead" in result.output
    assert result.called("compose build agent")


def test_a_failed_vector_pull_is_fatal_with_a_pointer_to_no_rag(run_setup):
    result = run_setup(env={"FAKE_FAIL_PULL": "nl2sql-rag-vectordb"})
    assert result.returncode != 0
    assert "--no-rag" in result.output


# ---------------------------------------------------------------------------
# The legacy standalone container
# ---------------------------------------------------------------------------


def test_a_running_standalone_vector_container_is_stopped_so_compose_can_take_over(run_setup):
    # The RAG pipeline leaves nl2sql-rag-vectordb bound to the same port and
    # volume the compose service wants.
    result = run_setup(env={"FAKE_LEGACY_CONTAINER": "1"})
    assert result.called("stop nl2sql-rag-vectordb")
    assert "data is kept" in result.output


def test_nothing_is_stopped_when_no_standalone_container_is_running(run_setup):
    result = run_setup()
    assert not result.called("stop nl2sql-rag-vectordb")


# ---------------------------------------------------------------------------
# Health and data checks
# ---------------------------------------------------------------------------


def test_postgres_never_becoming_healthy_is_fatal(run_setup):
    result = run_setup(env={"FAKE_PG_HEALTH": "starting"})
    assert result.returncode != 0
    assert "did not become healthy" in result.output
    assert "logs postgres" in result.output


def test_vectordb_never_becoming_healthy_is_fatal(run_setup):
    result = run_setup(env={"FAKE_VECTOR_HEALTH": "starting"})
    assert result.returncode != 0
    assert "logs vectordb" in result.output


def test_a_missing_dataset_is_fatal_and_suggests_reset(run_setup):
    result = run_setup(env={"FAKE_ROW_COUNT": ""})
    assert result.returncode != 0
    assert "--reset" in result.output


def test_an_empty_knowledge_base_warns_but_does_not_fail(run_setup):
    """Retrieval is optional, so an unpopulated store is a warning -- the
    agent still answers, just without knowledge context.
    """
    result = run_setup(env={"FAKE_CHUNK_COUNT": "0"})
    assert result.returncode == 0
    assert "no embedded chunks" in result.output


def test_reset_removes_the_existing_volumes_first(run_setup):
    result = run_setup("--reset", env={"FAKE_VOLUME_EXISTS": "1"})
    assert result.called("compose down -v")


def test_an_existing_volume_warns_that_it_shadows_the_image(run_setup):
    result = run_setup(env={"FAKE_VOLUME_EXISTS": "1"})
    assert result.returncode == 0
    assert "takes precedence over the image" in result.output


# ---------------------------------------------------------------------------
# Ollama and embedding-model probes
# ---------------------------------------------------------------------------


def test_both_models_are_confirmed_when_present(run_setup):
    result = run_setup()
    assert "qwen3.8:latest is available" in result.output
    assert "bge-m3 is available" in result.output


def test_a_missing_chat_model_warns_without_failing(run_setup):
    result = run_setup(env={"FAKE_OLLAMA_MODELS": '{"name":"bge-m3:latest"}'})
    assert result.returncode == 0
    assert "does not have qwen3.8:latest" in result.output


def test_a_missing_embedding_model_warns_with_the_pull_command(run_setup):
    result = run_setup(env={"FAKE_OLLAMA_MODELS": '{"name":"qwen3.8:latest"}'})
    assert result.returncode == 0
    assert "ollama pull bge-m3" in result.output
    assert "without knowledge retrieval" in result.output


def test_an_unreachable_ollama_warns_without_failing(run_setup):
    result = run_setup(env={"FAKE_OLLAMA_DOWN": "1"})
    assert result.returncode == 0
    assert "could not reach Ollama" in result.output
    assert "Retrieval will be skipped" in result.output


def test_the_embedding_host_is_probed_as_localhost_not_host_docker_internal(run_setup):
    """host.docker.internal is how the *container* reaches this machine; from
    the shell running setup.sh the same Ollama is on localhost, so probing the
    container-side name would always report a false failure.
    """
    result = run_setup()
    probes = result.calls_matching("curl")
    assert any("localhost:11434/api/tags" in p for p in probes)
    assert not any("host.docker.internal" in p for p in probes)


def test_a_custom_embed_url_is_probed_as_given(run_setup):
    result = run_setup("--embed-url", "http://embed-host:11434")
    assert any("embed-host:11434/api/tags" in p for p in result.calls_matching("curl"))


def test_the_chat_host_probe_follows_the_ollama_url_flag(run_setup):
    result = run_setup("--ollama-url", "http://chat-host:11434")
    assert any("chat-host:11434/api/tags" in p for p in result.calls_matching("curl"))


# ---------------------------------------------------------------------------
# Closing guidance
# ---------------------------------------------------------------------------


def test_final_message_shows_how_to_ask_a_question(run_setup):
    result = run_setup()
    assert 'docker compose run --rm agent "How many stores are there?"' in result.output


def test_final_message_advertises_a_question_that_needs_the_knowledge_base(run_setup):
    result = run_setup()
    assert "market share" in result.output
