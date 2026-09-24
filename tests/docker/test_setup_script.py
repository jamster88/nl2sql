"""setup.sh: the one-command bootstrap, which v2 extended to pull and start
the knowledge base alongside the retail database.

These run the real script against fake docker/curl binaries (see conftest.py),
so they cover its actual decision logic -- which images it pulls, what it
writes to .env, which services it starts, and which warnings it emits -- with
no Docker daemon and no network.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _shipped_tag(name: str) -> str:
    """The image tag this checkout of setup.sh pins, e.g. AGENT_TAG."""
    match = re.search(rf'^{name}="([^"]+)"', (REPO_ROOT / "setup.sh").read_text(), re.MULTILINE)
    assert match, f"setup.sh no longer defines {name}"
    return match.group(1)


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
        "--no-rag", "--no-verify", "--build", "--reset",
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


def test_default_run_pulls_all_four_images(run_setup):
    result = run_setup()
    assert result.called("pull mcfaddja/nl2sql-retail-postgres:v1")
    assert result.called("pull mcfaddja/nl2sql-rag-vectordb:v3")
    assert result.called("pull mcfaddja/nl2sql-rag-chunkdb:v3")
    assert result.called("pull mcfaddja/nl2sql-agent:v4")


def test_default_run_starts_every_database(run_setup):
    result = run_setup()
    assert result.called("compose up -d postgres")
    assert result.called("compose up -d vectordb")
    assert result.called("compose up -d chunkdb")


def test_default_run_writes_env_pinning_every_image(run_setup):
    """The tag is read back out of the script rather than repeated here: the
    property is that what .env pins is what setup.sh ships, and hard-coding
    the value made this fail on every release instead of on a real defect.
    """
    env = run_setup().env_file()
    assert env["IMAGE_NAME"] == "mcfaddja/nl2sql-retail-postgres"
    assert env["IMAGE_TAG"] == "v1"
    assert env["AGENT_IMAGE_NAME"] == "mcfaddja/nl2sql-agent"
    assert env["AGENT_IMAGE_TAG"] == _shipped_tag("AGENT_TAG")
    assert env["VECTOR_IMAGE_NAME"] == "mcfaddja/nl2sql-rag-vectordb"
    assert env["VECTOR_IMAGE_TAG"] == "v3"
    assert env["CONTEXT_IMAGE_NAME"] == "mcfaddja/nl2sql-rag-chunkdb"
    assert env["CONTEXT_IMAGE_TAG"] == "v3"
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


def test_the_retrieval_image_flags_are_written_to_env(run_setup):
    """Both stores can be pointed somewhere else -- at a fork, or at a tag
    being tested -- without editing the compose file.
    """
    env = run_setup(
        "--vector-image", "example.org/vec", "--vector-tag", "v9",
        "--context-image", "example.org/ctx", "--context-tag", "v8",
    ).env_file()
    assert env["VECTOR_IMAGE_NAME"] == "example.org/vec"
    assert env["VECTOR_IMAGE_TAG"] == "v9"
    assert env["CONTEXT_IMAGE_NAME"] == "example.org/ctx"
    assert env["CONTEXT_IMAGE_TAG"] == "v8"


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
# Postgres image: pull or build
# ---------------------------------------------------------------------------


def test_the_default_run_pulls_the_dataset_rather_than_regenerating_it(run_setup):
    result = run_setup()
    assert result.called("pull mcfaddja/nl2sql-retail-postgres:v1")
    assert not result.called("compose build postgres")


def test_build_regenerates_the_dataset_locally_instead_of_pulling(run_setup):
    """The slow path, and the only one that does not need the registry. It
    had never been run by a test: the flag was named in the list of things
    `--help` should mention, which read as coverage without being any.
    """
    result = run_setup("--build")
    assert result.returncode == 0
    assert result.called("compose build postgres")
    assert not result.called("pull mcfaddja/nl2sql-retail-postgres")
    assert "regenerates the dataset" in result.output


def test_a_locally_built_image_is_what_compose_is_pinned_to(run_setup):
    """Building and then writing the published image into .env would start
    the pulled dataset and quietly discard what was just generated.
    """
    result = run_setup("--build")
    env = result.env_file()
    assert env["IMAGE_NAME"] == "nl2sql-retail-postgres"
    assert env["IMAGE_TAG"] == "latest"


def test_building_postgres_leaves_the_agent_image_alone(run_setup):
    """Two independent decisions: --build is about the dataset, --build-agent
    about the agent. Conflating them is a twenty-minute rebuild nobody asked
    for.
    """
    result = run_setup("--build")
    assert result.called("pull mcfaddja/nl2sql-agent")
    assert not result.called("compose build agent")


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
    assert "from source instead" in result.output
    assert "which needs no registry access" in result.output
    assert result.called("compose build agent")


# ---------------------------------------------------------------------------
# GUI image: opt-in, pinned only when asked for
# ---------------------------------------------------------------------------


def test_the_gui_image_is_not_pulled_unless_it_is_asked_for(run_setup):
    """Most people ask questions from a terminal. An image for a container
    that is never started is a download nobody asked for.
    """
    result = run_setup()
    assert not result.called("pull mcfaddja/nl2sql-gui")
    assert "GUI_IMAGE_NAME" not in result.env_file()


def test_the_gui_flag_pulls_and_pins_it(run_setup):
    """Pinning is what makes the published image the one that runs: compose
    builds a service with a `build:` section whenever its image is missing.
    """
    result = run_setup("--gui")
    assert result.called("pull mcfaddja/nl2sql-gui:v4_2")
    assert result.env_file()["GUI_IMAGE_NAME"] == "mcfaddja/nl2sql-gui"
    assert result.env_file()["GUI_IMAGE_TAG"] == "v4_2"


def test_naming_a_gui_image_or_tag_implies_the_flag(run_setup):
    """Asking for a particular GUI image and then not getting one would be a
    silent no-op, which is the worst kind of flag.
    """
    result = run_setup("--gui-tag", "v9_9")
    assert result.called("pull mcfaddja/nl2sql-gui:v9_9")
    assert result.env_file()["GUI_IMAGE_TAG"] == "v9_9"

    result = run_setup("--gui-image", "example.com/other-gui")
    assert result.called("pull example.com/other-gui:v4_2")
    assert result.env_file()["GUI_IMAGE_NAME"] == "example.com/other-gui"


def test_a_failed_gui_pull_is_not_fatal(run_setup):
    """There is a Dockerfile right here, so a registry nobody can reach costs
    a build rather than the whole setup.
    """
    result = run_setup("--gui", env={"FAKE_FAIL_PULL": "nl2sql-gui"})
    assert result.returncode == 0
    assert "will build it from source instead" in result.output


def test_a_failed_vector_pull_is_fatal_with_actionable_guidance(run_setup):
    """The knowledge base image is the one thing v2 cannot synthesize locally,
    so the failure has to name both ways forward: authenticate, or opt out.
    """
    result = run_setup(env={"FAKE_FAIL_PULL": "nl2sql-rag-vectordb"})
    assert result.returncode != 0
    assert "--no-rag" in result.output
    assert "docker login" in result.output


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
    # The warning is only useful if it also says what to do about it.
    assert "what you will query" in result.output
    assert "start from the image's dataset" in result.output


# ---------------------------------------------------------------------------
# Ollama and embedding-model probes
# ---------------------------------------------------------------------------


def test_both_models_are_confirmed_when_present(run_setup):
    result = run_setup()
    assert "qwen3.8-256k is available" in result.output
    assert "bge-m3 is available" in result.output


def test_a_missing_chat_model_warns_without_failing(run_setup):
    result = run_setup(env={"FAKE_OLLAMA_MODELS": '{"name":"bge-m3:latest"}'})
    assert result.returncode == 0
    assert "does not have qwen3.8-256k" in result.output
    assert "re-run with --model" in result.output


def test_a_missing_embedding_model_warns_with_the_pull_command(run_setup):
    result = run_setup(env={"FAKE_OLLAMA_MODELS": '{"name":"qwen3.8-256k"}'})
    assert result.returncode == 0
    assert "ollama pull bge-m3" in result.output
    assert "without knowledge retrieval" in result.output


def test_an_unreachable_ollama_warns_without_failing(run_setup):
    result = run_setup(env={"FAKE_OLLAMA_DOWN": "1"})
    assert result.returncode == 0
    assert "could not reach Ollama" in result.output
    assert "Retrieval will be skipped" in result.output
    assert "re-run with --ollama-url URL" in result.output


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


# ---------------------------------------------------------------------------
# The end-to-end retrieval check
#
# Each earlier step proves one piece is up; this proves the agent container can
# actually reach the knowledge base, which is what the command setup.sh tells
# the user to run next depends on.
# ---------------------------------------------------------------------------


def test_the_default_run_verifies_retrieval_from_inside_the_agent_container(run_setup):
    result = run_setup()
    assert "Checking the agent can reach the knowledge base" in result.output
    assert "retrieval works end to end" in result.output
    assert result.called("compose run --rm --entrypoint python agent")


def test_the_check_reports_how_many_collections_were_searched(run_setup):
    result = run_setup(env={"FAKE_PROBE_COLLECTIONS": "3"})
    assert "3 collections searched" in result.output


def test_no_verify_skips_the_check(run_setup):
    result = run_setup("--no-verify")
    assert result.returncode == 0
    assert "Checking the agent can reach the knowledge base" not in result.output
    assert not result.called("compose run --rm --entrypoint python agent")


def test_no_rag_skips_the_check_too(run_setup):
    # There is no knowledge base to reach.
    result = run_setup("--no-rag")
    assert "Checking the agent can reach the knowledge base" not in result.output


def test_a_failed_check_warns_but_leaves_setup_successful(run_setup):
    """The stack is still usable without retrieval, so this must not undo a
    successful setup -- but it has to say so loudly.
    """
    result = run_setup(env={"FAKE_PROBE_FAILS": "1"})
    assert result.returncode == 0
    assert "could not retrieve from the knowledge base" in result.output
    assert "without knowledge context" in result.output
    assert "Setup complete" in result.output


def test_a_check_that_returns_no_chunks_warns(run_setup):
    """Reaching the store and getting nothing back is different from not
    reaching it, and the guidance differs too: the agent still answers.
    """
    result = run_setup(env={"FAKE_PROBE_CHUNKS": "0"})
    assert result.returncode == 0
    assert "returned nothing" in result.output
    assert "just without retrieved context" in result.output


def test_help_documents_the_no_verify_flag(run_setup):
    assert "--no-verify" in run_setup("--help").output


def test_the_closing_message_names_all_three_containers(run_setup):
    result = run_setup()
    assert "nl2sql-postgres" in result.output
    assert "nl2sql-vectordb" in result.output
    assert 'docker compose run --rm agent "How many stores are there?"' in result.output


# ---------------------------------------------------------------------------
# The agent's read-only role
# ---------------------------------------------------------------------------


def test_default_run_creates_the_agents_read_only_role(run_setup):
    """Setup ends by telling the user to run the agent, and the agent connects
    as the reader role -- so setup has to create it, as the superuser, before
    that command can work.
    """
    result = run_setup()
    [call] = result.calls_matching("reader=")
    assert "compose exec -T postgres psql -U postgres" in call
    assert "reader=nl2sql_reader" in call and "owner=nl2sql" in call
    assert "read-only role nl2sql_reader ready" in result.output


def test_a_role_that_cannot_be_created_is_fatal(run_setup):
    result = run_setup(env={"FAKE_READER_ROLE_FAILS": "1"})
    assert result.returncode != 0
    assert "read-only role" in result.output
    assert "docker compose logs postgres" in result.output


def test_default_run_installs_the_trigram_extension(run_setup):
    result = run_setup()
    [call] = result.calls_matching("CREATE EXTENSION")
    assert "IF NOT EXISTS pg_trgm" in call


def test_a_chat_model_tagged_latest_is_not_reported_as_missing(run_setup):
    """Same fix as launch.sh: Ollama reports an untagged pull as
    `name:latest`, and an exact match called a working host broken.
    """
    result = run_setup(env={"FAKE_OLLAMA_MODELS": '{"name":"qwen3.8-256k:latest"},{"name":"bge-m3:latest"}'})
    assert "is available at" in result.output
    assert "does not have qwen3.8-256k" not in result.output
