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

#: The tag `setup.sh` pins, read rather than written down. A release moves it
#: and a test that spelled it out would be a file to hand-edit every time --
#: which is exactly the cost a patch release should not have.
DESKTOP_TAG = re.search(
    r'^DESKTOP_TAG="(\S+)"', (REPO_ROOT / "setup.sh").read_text(), re.MULTILINE
).group(1)



def _shipped_tag(name: str) -> str:
    """The image tag this checkout of setup.sh pins, e.g. AGENT_TAG."""
    match = re.search(rf'^{name}="([^"]+)"', (REPO_ROOT / "setup.sh").read_text(), re.MULTILINE)
    assert match, f"setup.sh no longer defines {name}"
    return match.group(1)


# ---------------------------------------------------------------------------
# Static checks (no execution)
# ---------------------------------------------------------------------------
#
# `set -euo pipefail`, `bash -n` and `cd "$(dirname "$0")"` are not checked
# here. `test_script_coverage.py` asserts all three, parametrized over every
# script including this one, so a copy for setup.sh alone tested nothing the
# parametrized version did not -- and would have gone on passing if the
# parametrized one were deleted.


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
    """Exact calls, not `called()`: that is a substring match, so an expected
    `...agent:v4` went on passing against an actual `...agent:v4_2` and the
    tag stopped being pinned by anything. The tags are read out of setup.sh
    for the reason the .env test gives -- a release should not fail this.
    """
    calls = run_setup().calls
    for image, tag in (
        ("mcfaddja/nl2sql-retail-postgres", _shipped_tag("POSTGRES_TAG")),
        ("mcfaddja/nl2sql-rag-vectordb", _shipped_tag("VECTOR_TAG")),
        ("mcfaddja/nl2sql-rag-chunkdb", _shipped_tag("CONTEXT_TAG")),
        ("mcfaddja/nl2sql-agent", _shipped_tag("AGENT_TAG")),
    ):
        assert f"pull {image}:{tag}" in calls, f"{image} was not pulled at {tag}"


def test_default_run_starts_every_database(run_setup):
    result = run_setup()
    assert result.called("compose up -d postgres stores vectordb chunkdb")


def test_without_retrieval_the_knowledge_stores_are_not_started(run_setup):
    result = run_setup("--no-rag")
    assert result.called("compose up -d postgres stores")
    assert not result.called("vectordb chunkdb")


def test_default_run_writes_env_pinning_every_image(run_setup):
    """The tag is read back out of the script rather than repeated here: the
    property is that what .env pins is what setup.sh ships, and hard-coding
    the value made this fail on every release instead of on a real defect.
    """
    env = run_setup().env_file()
    assert env["IMAGE_NAME"] == "mcfaddja/nl2sql-retail-postgres"
    assert env["IMAGE_TAG"] == _shipped_tag("POSTGRES_TAG")
    assert env["AGENT_IMAGE_NAME"] == "mcfaddja/nl2sql-agent"
    assert env["AGENT_IMAGE_TAG"] == _shipped_tag("AGENT_TAG")
    assert env["VECTOR_IMAGE_NAME"] == "mcfaddja/nl2sql-rag-vectordb"
    assert env["VECTOR_IMAGE_TAG"] == _shipped_tag("VECTOR_TAG")
    assert env["CONTEXT_IMAGE_NAME"] == "mcfaddja/nl2sql-rag-chunkdb"
    assert env["CONTEXT_IMAGE_TAG"] == _shipped_tag("CONTEXT_TAG")
    assert env["RAG_ENABLED"] == "true"


@pytest.mark.parametrize("flags", [(), ("--agent-tag", "v5_3")])
def test_env_records_the_release_that_wrote_it_whatever_was_pinned(run_setup, flags):
    """Not the agent's tag: the release this checkout ships, which is what
    start.sh needs to tell a tag chosen here from one an older checkout left."""
    env = run_setup(*flags).env_file()
    assert env["SETUP_RELEASE"] == _shipped_tag("AGENT_TAG")
    assert env["AGENT_IMAGE_TAG"] == (flags[1] if flags else _shipped_tag("AGENT_TAG"))


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
    assert f"pull mcfaddja/nl2sql-retail-postgres:{_shipped_tag('POSTGRES_TAG')}" in result.calls
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


def test_the_pages_image_is_not_pulled_unless_a_page_is_wanted(run_setup):
    """Most people ask questions from a terminal. An image for a container
    that is never started is a download nobody asked for -- and with sign-in
    off there is no directory page either.
    """
    result = run_setup("--no-auth")
    assert not result.called("pull mcfaddja/nl2sql-proxy")
    assert "PROXY_IMAGE_NAME" not in result.env_file()


def test_the_pages_image_is_pulled_and_pinned_with_sign_in_on(run_setup):
    """Sign-in has a page of its own, the directory's, so the pages' image is
    wanted whenever it is on -- and is one image for every page (V6-37).
    Pinning is what makes the published image the one that runs: compose
    builds a service with a `build:` section whenever its image is missing.
    """
    result = run_setup()
    tag = _shipped_tag("PROXY_TAG")
    assert f"pull mcfaddja/nl2sql-proxy:{tag}" in result.calls
    assert result.env_file()["PROXY_IMAGE_NAME"] == "mcfaddja/nl2sql-proxy"
    assert result.env_file()["PROXY_IMAGE_TAG"] == tag
    for gone in ("nl2sql-gui:", "nl2sql-review-gui:", "nl2sql-console-gui:", "nl2sql-directory-gui:"):
        assert not result.calls_matching(f"pull mcfaddja/{gone}"), gone


def test_naming_the_pages_image_or_tag_implies_the_flag(run_setup):
    """Asking for a particular image and then not getting one would be a
    silent no-op, which is the worst kind of flag."""
    result = run_setup("--no-auth", "--proxy-tag", "v9_9")
    assert result.called("pull mcfaddja/nl2sql-proxy:v9_9")
    assert result.env_file()["PROXY_IMAGE_TAG"] == "v9_9"
    result = run_setup("--no-auth", "--proxy-image", "example.com/pages")
    assert result.called(f"pull example.com/pages:{_shipped_tag('PROXY_TAG')}")
    assert result.env_file()["PROXY_IMAGE_NAME"] == "example.com/pages"


def test_a_failed_pages_pull_is_not_fatal(run_setup):
    """There is a Dockerfile right here, so a registry nobody can reach costs
    a build rather than the whole setup."""
    result = run_setup("--gui", env={"FAKE_FAIL_PULL": "nl2sql-proxy"})
    assert result.returncode == 0
    assert "./launch.sh will build it from source the first time a page starts." in result.output


def test_a_failed_context_store_pull_is_fatal_with_actionable_guidance(run_setup):
    """The golden pairs and their BM25 statistics only exist in that image;
    there is nothing to fall back to, so the message has to name both ways
    forward rather than just stopping.
    """
    result = run_setup(env={"FAKE_FAIL_PULL": "nl2sql-rag-chunkdb"})
    assert result.returncode != 0
    assert "could not pull mcfaddja/nl2sql-rag-chunkdb" in result.output
    assert "run 'docker login' first" in result.output
    assert "re-run with --no-rag" in result.output


def test_a_context_store_that_never_becomes_healthy_is_reported(run_setup):
    """Waited out rather than assumed -- and the wait is what makes this the
    one path where the retry `sleep` in that loop actually runs.
    """
    result = run_setup(env={"FAKE_CONTEXT_HEALTH": "starting"}, timeout=180)
    assert result.returncode != 0
    assert "chunkdb did not become healthy. Check 'docker compose logs chunkdb'." in result.output


def test_the_runtime_stores_never_becoming_healthy_is_fatal(run_setup):
    result = run_setup(env={"FAKE_STORES_HEALTH": "starting"}, timeout=180)
    assert result.returncode != 0
    assert "stores did not become healthy. Check 'docker compose logs stores'." in result.output


def test_what_the_one_shot_reports_is_said_as_it_said_it(run_setup):
    """The checks are the dbprep service's (nl2sql_ops.report): its steps are
    this script's steps, its warnings its warnings, and an empty store is a
    working stack with worse answers, not a reason to stop."""
    report = "\n".join((
        "STEP Checking what is actually in each database", "STATE retail_rows=194101",
        "INFO retail dataset: 194101 sales rows", "WARN the vector store is up but holds no embedded chunks.",
        "SOMETHING else this script does not know",
    ))
    result = run_setup(env={"FAKE_REPORT": report})
    assert result.returncode == 0
    assert "==> Checking what is actually in each database" in result.output
    assert "    retail dataset: 194101 sales rows" in result.output
    assert "WARNING: the vector store is up but holds no embedded chunks." in result.output
    assert "SOMETHING" not in result.output and "STATE" not in result.output


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


@pytest.mark.parametrize("rows", ["", "0"])
def test_a_missing_dataset_is_fatal_and_suggests_reset(run_setup, rows):
    result = run_setup(env={"FAKE_ROW_COUNT": rows})
    assert result.returncode != 0
    assert "the database is up but the dataset is missing. Try --reset." in result.output


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


def test_final_message_advertises_a_question_that_needs_the_knowledge_base(run_setup):
    result = run_setup()
    assert "market share" in result.output


@pytest.mark.parametrize(
    ("flags", "running"),
    [
        ((), ["nl2sql-postgres", "nl2sql-stores", "nl2sql-vectordb", "nl2sql-chunkdb"]),
        (("--no-rag",), ["nl2sql-postgres", "nl2sql-stores"]),
    ],
)
def test_final_message_lists_exactly_the_databases_it_started(run_setup, flags, running):
    """It said two for a long time: the context store was started and never
    named, and without retrieval it named one that was never started. The
    substring check it replaced passed anyway -- the pull lines name them."""
    output = run_setup(*flags).output
    listed = output.split("Running now:")[1].split("The agent runs on demand")[0]
    assert [line.split()[0] for line in listed.strip().splitlines()] == running
    assert 'docker compose run --rm agent "How many stores are there?"' in output


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
    assert "The agent will still answer, but without knowledge context." in result.output
    assert "Setup complete" in result.output


def test_a_check_that_returns_no_chunks_warns(run_setup):
    """Reaching the store and getting nothing back is different from not
    reaching it, and the guidance differs too: the agent still answers.
    """
    result = run_setup(env={"FAKE_PROBE_CHUNKS": "0"})
    assert result.returncode == 0
    assert "returned nothing" in result.output
    assert "just without retrieved context" in result.output


# ---------------------------------------------------------------------------
# The agent's read-only role
# ---------------------------------------------------------------------------


def test_the_databases_are_prepared_by_the_one_shot_after_they_are_healthy(run_setup):
    """The agent connects as the reader role, so it has to exist before
    setup tells the user to run the agent. The dbprep service makes it, and
    everything else each database needs; this script runs no SQL (V6-41)."""
    result = run_setup()
    assert result.index_of("compose run --rm --no-deps -T dbprep") > result.index_of("nl2sql-chunkdb")
    assert "==> Preparing the databases" in result.output
    assert "role nl2sql_reader can read every table and write none" in result.output
    assert not result.calls_matching("psql") and not result.calls_matching("compose exec")


def test_a_warning_the_one_shot_gives_is_a_warning_here(run_setup):
    result = run_setup(env={"FAKE_DBPREP_WARNS": "could not create pg_trgm; literal matching falls back to difflib"})
    assert result.returncode == 0
    assert "WARNING: could not create pg_trgm" in result.output


def test_databases_that_cannot_be_prepared_stop_setup_with_the_reason(run_setup):
    result = run_setup(env={"FAKE_DBPREP_FAILS": "the retail database's socket is not in /run/nl2sql/sockets/retail"})
    assert result.returncode != 0
    assert "dbprep: the retail database's socket is not in" in result.output
    assert "the databases could not be prepared, so nothing could use them" in result.output



def test_a_chat_model_tagged_latest_is_not_reported_as_missing(run_setup):
    """Same fix as launch.sh: Ollama reports an untagged pull as
    `name:latest`, and an exact match called a working host broken.
    """
    result = run_setup(env={"FAKE_OLLAMA_MODELS": '{"name":"qwen3.8-256k:latest"},{"name":"bge-m3:latest"}'})
    assert "is available at" in result.output
    assert "does not have qwen3.8-256k" not in result.output


# ---------------------------------------------------------------------------
# The feedback review images
# ---------------------------------------------------------------------------


def test_review_pulls_the_service_and_the_pages(run_setup):
    """Feedback has to be given before it can be reviewed; both pages are the
    one pages image."""
    result = run_setup("--review", "--no-auth")
    assert result.called(f"pull mcfaddja/nl2sql-review:{_shipped_tag('REVIEW_TAG')}")
    assert result.called(f"pull mcfaddja/nl2sql-proxy:{_shipped_tag('PROXY_TAG')}")


def test_review_pins_the_service_and_the_pages_in_the_env_file(run_setup):
    env = run_setup("--review", "--no-auth").env_file()
    assert env["REVIEW_IMAGE_NAME"] == "mcfaddja/nl2sql-review"
    assert env["REVIEW_IMAGE_TAG"] == _shipped_tag("REVIEW_TAG")
    assert env["PROXY_IMAGE_NAME"] == "mcfaddja/nl2sql-proxy"


def test_the_review_service_is_pinned_with_retrieval_on(run_setup):
    """The review *service* is wanted with retrieval on, since 5.6: it carries
    the loader that fills the snippet store. Pages are the pages' image."""
    env = run_setup("--no-auth").env_file()
    assert env.get("REVIEW_IMAGE_NAME") == "mcfaddja/nl2sql-review"
    assert "PROXY_IMAGE_NAME" not in env


def test_without_retrieval_nothing_about_review_is_pinned(run_setup):
    env = run_setup("--no-rag").env_file()
    assert "REVIEW_IMAGE_NAME" not in env


def test_naming_any_review_image_or_tag_implies_the_flag(run_setup):
    """Asking for a particular image and then not getting one would be a
    silent no-op, which is the worst kind of flag.

    Written out rather than parametrized on purpose. The guard in
    `test_script_coverage.py` reads the syntax tree for flags passed as
    literals, precisely so that a flag merely *named* in a test does not
    count as one that was run -- and a parametrized value is not a literal
    at the call site.
    """
    assert run_setup("--review-image", "example.com/rev").env_file()[
        "REVIEW_IMAGE_NAME"
    ] == "example.com/rev"
    assert run_setup("--review-tag", "v9_9").env_file()["REVIEW_IMAGE_TAG"] == "v9_9"


def test_a_failed_review_pull_is_not_fatal(run_setup):
    """Same as the GUI: there are Dockerfiles right here, so an unreachable
    registry costs a build rather than the whole setup."""
    result = run_setup("--review", env={"FAKE_FAIL_PULL": "nl2sql-review"})
    assert result.returncode == 0
    assert "compose will build it from source the first time it is needed." in result.output


# ---------------------------------------------------------------------------
# The SQL console's interface
# ---------------------------------------------------------------------------


def test_console_needs_only_the_pages_image(run_setup):
    """The console behind its page is the agent's own image, pulled anyway,
    started with a different command; its page is the pages' image."""
    result = run_setup("--console", "--no-auth")
    assert not [call for call in result.calls if call.startswith("pull") and "console" in call]
    assert result.called(f"pull mcfaddja/nl2sql-proxy:{_shipped_tag('PROXY_TAG')}")



def test_a_pinned_pages_image_stays_pinned_without_the_flag(run_setup):
    """"Was it asked for last time" is the same question as "is it pinned"."""
    run_setup("--console", "--no-auth")
    assert run_setup().env_file()["PROXY_IMAGE_NAME"] == "mcfaddja/nl2sql-proxy"


def test_a_page_pinned_by_a_release_before_one_image_keeps_the_pages_pinned(run_setup):
    """6.2 pinned each page's own image; any of them is the pages' now."""
    first = run_setup("--no-auth")
    dotenv = first.workdir / ".env"
    dotenv.write_text(dotenv.read_text() + "CONSOLE_GUI_IMAGE_NAME=mcfaddja/nl2sql-console-gui\n")
    env = run_setup().env_file()
    assert env["PROXY_IMAGE_NAME"] == "mcfaddja/nl2sql-proxy"
    assert "CONSOLE_GUI_IMAGE_NAME" not in env, "retired, not kept as a setting added by hand"


def test_setup_ends_by_saying_the_console_is_there(run_setup):
    output = run_setup().output
    assert "./launch.sh --console" in output
    assert "https://localhost:8082" in output


# ---------------------------------------------------------------------------
# MLflow (--mlflow)
# ---------------------------------------------------------------------------


def test_mlflow_pulls_and_pins_its_server_and_its_store(run_setup):
    """Two images, as the review service and its interface are: the server
    is no use without the store behind it."""
    result = run_setup("--mlflow")

    server, store = _shipped_tag("MLFLOW_TAG"), _shipped_tag("MLFLOW_DB_TAG")
    pulled = [call for call in result.calls if call.startswith("pull") and "mlflow" in call]
    assert pulled == [f"pull mcfaddja/nl2sql-mlflow:{server}", f"pull mcfaddja/nl2sql-mlflowdb:{store}"]
    env = result.env_file()
    assert (env["MLFLOW_IMAGE_NAME"], env["MLFLOW_IMAGE_TAG"]) == ("mcfaddja/nl2sql-mlflow", server)
    assert (env["MLFLOW_DB_IMAGE_NAME"], env["MLFLOW_DB_IMAGE_TAG"]) == ("mcfaddja/nl2sql-mlflowdb", store)
    # Its front door is the pages' image.
    assert env["PROXY_IMAGE_NAME"] == "mcfaddja/nl2sql-proxy"



def test_nothing_about_mlflow_is_pulled_or_pinned_unless_it_was_asked_for(run_setup):
    result = run_setup()
    assert not [call for call in result.calls if call.startswith("pull") and "mlflow" in call]
    assert "MLFLOW_IMAGE_NAME" not in result.env_file()


def test_naming_an_mlflow_image_or_tag_implies_the_flag(run_setup):
    """Written out rather than parametrized, for the reason the review
    images' test gives."""
    assert run_setup("--mlflow-image", "example.com/mlflow").env_file()["MLFLOW_IMAGE_NAME"] == "example.com/mlflow"
    assert run_setup("--mlflow-tag", "v9_9").env_file()["MLFLOW_IMAGE_TAG"] == "v9_9"
    assert run_setup("--mlflow-db-image", "example.com/store").env_file()[
        "MLFLOW_DB_IMAGE_NAME"
    ] == "example.com/store"
    assert run_setup("--mlflow-db-tag", "v9_8").env_file()["MLFLOW_DB_IMAGE_TAG"] == "v9_8"


def test_a_failed_mlflow_pull_is_not_fatal(run_setup):
    result = run_setup("--mlflow", env={"FAKE_FAIL_PULL": "nl2sql-mlflowdb"})
    assert result.returncode == 0
    assert "could not pull mcfaddja/nl2sql-mlflowdb:" in result.output
    assert "./launch.sh --mlflow will build it from source instead." in result.output


def test_a_pinned_mlflow_stays_pinned_without_the_flag(run_setup):
    run_setup("--mlflow")
    assert run_setup().env_file()["MLFLOW_DB_IMAGE_NAME"] == "mcfaddja/nl2sql-mlflowdb"


def test_setup_ends_by_saying_mlflow_is_there(run_setup):
    output = run_setup().output
    assert "./launch.sh --mlflow" in output
    assert "https://localhost:5001" in output


# ---------------------------------------------------------------------------
# Re-running it should not undo the last run
# ---------------------------------------------------------------------------
#
# .env is rewritten from scratch every time, and most of what goes into it is
# written only when a flag gave it a value. That made every re-run a quiet
# downgrade: `./setup.sh --review` on a machine first set up with
# `--ollama-url` produced a .env with no Ollama host in it, and the agent
# started looking for a model on localhost.
#
# These run the script twice in one sandbox, which is what the upgrade
# actually is, rather than writing a .env by hand and hoping it looks like
# one the script would have written.


def test_the_ollama_host_survives_a_re_run(run_setup):
    first = run_setup("--ollama-url", "http://192.168.1.5:11434")
    assert first.env_file()["OLLAMA_BASE_URL"] == "http://192.168.1.5:11434"

    second = run_setup("--review")
    assert second.env_file()["OLLAMA_BASE_URL"] == "http://192.168.1.5:11434"
    assert second.env_file()["REVIEW_IMAGE_NAME"] == "mcfaddja/nl2sql-review"


def test_a_flag_still_wins_over_what_was_there(run_setup):
    """Carrying values over must not make them impossible to change."""
    run_setup("--ollama-url", "http://old:11434")
    second = run_setup("--ollama-url", "http://new:11434")
    assert second.env_file()["OLLAMA_BASE_URL"] == "http://new:11434"


def test_every_optional_value_survives_a_re_run(run_setup):
    run_setup(
        "--ollama-url", "http://chat:11434",
        "--model", "qwen3.8-256k",
        "--embed-url", "http://embed:11434",
        "--embed-model", "bge-m3",
        "--port", "5999",
    )
    env = run_setup().env_file()

    assert env["OLLAMA_BASE_URL"] == "http://chat:11434"
    assert env["OLLAMA_MODEL"] == "qwen3.8-256k"
    assert env["EMBED_BASE_URL"] == "http://embed:11434"
    assert env["EMBED_MODEL"] == "bge-m3"
    assert env["POSTGRES_PORT"] == "5999"



def test_a_pinned_review_stays_pinned_without_the_flag(run_setup):
    run_setup("--review")
    env = run_setup().env_file()
    assert env["REVIEW_IMAGE_NAME"] == "mcfaddja/nl2sql-review"
    assert env["PROXY_IMAGE_NAME"] == "mcfaddja/nl2sql-proxy"


def test_nothing_is_carried_over_on_a_first_run(run_setup):
    """There is no previous .env, and reading one that is not there must not
    write empty values into the new one."""
    env = run_setup().env_file()
    assert "OLLAMA_BASE_URL" not in env
    assert not [key for key in env if key.endswith("_GUI_IMAGE_NAME")]


def test_settings_added_by_hand_survive_a_re_run(run_setup):
    """start.sh re-runs this whenever a checkout ships newer images, so
    whatever someone added to .env since -- the API token launch.sh tells
    them to set there, a port -- has to be in the new file, not only in the
    backup beside it.
    """
    first = run_setup()
    dotenv = first.workdir / ".env"
    # No newline at the end, as an editor may leave it: the last line counts.
    dotenv.write_text(dotenv.read_text() + "# a note\nGUI_PORT=9090")

    second = run_setup()
    env = second.env_file()

    assert env["GUI_PORT"] == "9090"
    assert "kept 1 other setting(s) from the previous .env" in second.output
    assert "# a note" not in (second.workdir / ".env").read_text()


def test_a_key_this_script_writes_is_written_once_with_its_new_value(run_setup):
    shipped = _shipped_tag("AGENT_TAG")
    first = run_setup()
    dotenv = first.workdir / ".env"
    dotenv.write_text(dotenv.read_text().replace(f"AGENT_IMAGE_TAG={shipped}", "AGENT_IMAGE_TAG=v1"))

    lines = (run_setup().workdir / ".env").read_text().splitlines()

    assert [line for line in lines if line.startswith("AGENT_IMAGE_TAG=")] == [f"AGENT_IMAGE_TAG={shipped}"]
    assert "# Kept from the previous .env" not in lines


def test_an_old_backup_is_not_a_source(run_setup):
    """Only the .env just moved aside is carried over. A .env.bak from some
    earlier run, with no .env beside it, is a backup and nothing more."""
    first = run_setup()
    (first.workdir / ".env").unlink()
    (first.workdir / ".env.bak").write_text("API_TOKEN=from-long-ago\n")

    assert "API_TOKEN" not in run_setup().env_file()


def test_the_previous_env_is_still_kept_beside_the_new_one(run_setup):
    """Carrying values over is about not needing the backup, not about
    replacing it."""
    run_setup("--ollama-url", "http://old:11434")
    assert "existing .env moved to .env.bak" in run_setup().output


# ---------------------------------------------------------------------------
# The desktop client's jar
# ---------------------------------------------------------------------------


def test_desktop_pulls_the_image_for_this_machine_and_pins_it(run_setup):
    """Tagged by JavaFX platform rather than by architecture, because it
    carries a jar and a jar carries native code for the machine it draws on."""
    result = run_setup("--desktop", env={"FAKE_UNAME_S": "Darwin", "FAKE_UNAME_M": "arm64"})

    assert result.returncode == 0
    assert result.called(
        f"pull mcfaddja/nl2sql-desktop-build:{DESKTOP_TAG}-mac-aarch64")
    env = (result.workdir / ".env").read_text()
    assert "DESKTOP_IMAGE_NAME=mcfaddja/nl2sql-desktop-build" in env
    assert f"DESKTOP_IMAGE_TAG={DESKTOP_TAG}" in env


def test_desktop_pulls_one_platform_and_not_five(run_setup):
    """The other four are 33 MB each of no use on this machine."""
    result = run_setup("--desktop", env={"FAKE_UNAME_S": "Linux", "FAKE_UNAME_M": "aarch64"})

    pulls = result.calls_matching("pull mcfaddja/nl2sql-desktop-build")
    assert len(pulls) == 1
    assert f"{DESKTOP_TAG}-linux-aarch64" in pulls[0]


def test_a_desktop_image_that_will_not_pull_says_what_happens_instead(run_setup):
    """A tag not published for this platform, or a machine that is offline.
    The jar is still what the user gets -- built rather than fetched."""
    result = run_setup("--desktop", env={"FAKE_FAIL_PULL": "nl2sql-desktop-build"})

    assert result.returncode == 0
    assert "could not pull" in result.output
    assert "will build it instead" in result.output


def test_without_the_flag_nothing_desktop_is_pulled_or_pinned(run_setup):
    """Unpinned, compose resolves a local tag with nowhere to be pulled from
    and launch.sh builds the jar. Most people never ask for this client."""
    result = run_setup()

    assert not result.calls_matching("pull mcfaddja/nl2sql-desktop-build")
    assert "DESKTOP_IMAGE_NAME" not in (result.workdir / ".env").read_text()


def test_a_second_run_keeps_the_desktop_pin(run_setup):
    """Re-running setup.sh is how tags are upgraded, and "was it asked for
    last time" is the same question as "is it pinned in .env"."""
    first = run_setup("--desktop", env={"FAKE_UNAME_S": "Linux", "FAKE_UNAME_M": "x86_64"})
    assert "DESKTOP_IMAGE_NAME" in (first.workdir / ".env").read_text()

    second = run_setup(env={"FAKE_UNAME_S": "Linux", "FAKE_UNAME_M": "x86_64"})

    assert "DESKTOP_IMAGE_NAME=mcfaddja/nl2sql-desktop-build" in (
        second.workdir / ".env").read_text()
    assert second.called(
        f"pull mcfaddja/nl2sql-desktop-build:{DESKTOP_TAG}-linux")


def test_the_desktop_image_and_tag_can_be_named(run_setup):
    """The same override every other published image has, for a fork or a
    tag built somewhere else."""
    result = run_setup("--desktop-image", "example.test/nl2sql-desktop",
                       "--desktop-tag", "nightly",
                       env={"FAKE_UNAME_S": "Linux", "FAKE_UNAME_M": "x86_64"})

    assert result.called("pull example.test/nl2sql-desktop:nightly-linux")
    env = (result.workdir / ".env").read_text()
    assert "DESKTOP_IMAGE_NAME=example.test/nl2sql-desktop" in env
    assert "DESKTOP_IMAGE_TAG=nightly" in env


@pytest.mark.parametrize(
    ("system", "machine", "classifier"),
    [
        ("Darwin", "x86_64", "mac"),
        ("MINGW64_NT-10.0", "x86_64", "win"),
        # Anything unrecognised gets the commonest machine there is, which at
        # least starts somewhere rather than refusing to pull at all.
        ("SunOS", "sparc", "linux"),
    ],
)
def test_the_tag_pulled_names_the_machine_this_is(run_setup, system, machine, classifier):
    """Three of the five branches cannot happen on the machine the suite runs
    on, and a tag that is wrong for a platform is a jar that will not start."""
    result = run_setup("--desktop", env={"FAKE_UNAME_S": system, "FAKE_UNAME_M": machine})

    assert result.called(
        f"pull mcfaddja/nl2sql-desktop-build:{DESKTOP_TAG}-{classifier}")



# ---------------------------------------------------------------------------
# Where traces go
# ---------------------------------------------------------------------------


def test_the_agent_is_pointed_at_the_mlflow_service(run_setup):
    """Written whether or not MLflow is ever started: a run that finds no
    server is answered untraced, as the feedback URL beside it is harmless
    without the staging database."""
    assert run_setup().env_file()["MLFLOW_TRACKING_URI"] == "http://nl2sql-mlflow:5000"


@pytest.mark.parametrize("chosen", ["", "http://mlflow.example.org"])
def test_a_tracking_uri_someone_chose_is_written_back_as_it_was(run_setup, chosen):
    """Empty is how tracing is turned off, and start.sh re-runs this script
    on every upgrade -- writing the default over it would turn it back on."""
    first = run_setup()
    dotenv = first.workdir / ".env"
    dotenv.write_text(dotenv.read_text().replace(
        "MLFLOW_TRACKING_URI=http://nl2sql-mlflow:5000", f"MLFLOW_TRACKING_URI={chosen}"))

    second = run_setup()
    lines = (second.workdir / ".env").read_text().splitlines()
    assert [line for line in lines if line.startswith("MLFLOW_TRACKING_URI=")] == [f"MLFLOW_TRACKING_URI={chosen}"]
    assert "# Kept from the previous .env" not in lines


def test_an_old_backup_is_not_where_the_tracking_uri_comes_from(run_setup):
    """Only the file just moved aside is a source; an .env.bak lying about
    from some earlier run is a backup."""
    first = run_setup()
    (first.workdir / ".env").unlink()
    (first.workdir / ".env.bak").write_text("MLFLOW_TRACKING_URI=\n")
    assert run_setup().env_file()["MLFLOW_TRACKING_URI"] == "http://nl2sql-mlflow:5000"



# ---------------------------------------------------------------------------
# v5.6: the curation interface, and the snippet store setup.sh fills
# ---------------------------------------------------------------------------


def test_curate_pulls_and_pins_the_pages_and_the_service_behind_them(run_setup):
    result = run_setup("--curate", "--no-rag", "--no-auth")
    env = result.env_file()
    assert env.get("PROXY_IMAGE_NAME") == "mcfaddja/nl2sql-proxy"
    assert env.get("REVIEW_IMAGE_NAME") == "mcfaddja/nl2sql-review"
    assert result.called("pull mcfaddja/nl2sql-proxy:")
    assert result.called("pull mcfaddja/nl2sql-review:")


def test_a_review_service_that_will_not_pull_is_built_when_first_needed(run_setup):
    result = run_setup(env={"FAKE_FAIL_PULL": "mcfaddja/nl2sql-review:"})
    assert result.returncode == 0
    assert "compose will build it from source the first time it is needed." in result.output



def test_the_snippets_are_loaded_from_the_document_as_the_images_account(run_setup):
    result = run_setup()
    # One call, its `sh -c` script spread over the log's lines.
    log = "\n".join(result.calls)
    assert "--profile review run --rm --no-deps -T --user 10001:10001 --entrypoint sh review" in log
    assert "07_load_snippets.py" in log
    assert "--db-url" not in log, "the store's URL and password are the service's own"
    assert "32 rows written, 0 stale rows removed" in result.output
    assert "vectors -> sql_snippet_vectors: 32 embedded, 0 already current" in result.output


def test_a_snippet_load_that_fails_is_a_warning_not_a_failed_setup(run_setup):
    result = run_setup(env={"FAKE_SNIPPETS_LOAD_FAILS": "1"})
    assert result.returncode == 0
    assert "the SQL snippets did not load completely; ./launch.sh tries again on every start." in result.output
    assert "searchable by keyword" in result.output



def test_without_retrieval_no_snippet_store_is_started(run_setup):
    result = run_setup("--no-rag")
    assert not result.called("07_load_snippets.py")


def test_the_closing_lines_point_at_the_curation_interface(run_setup):
    assert "./launch.sh --curate                     # https://localhost:8083" in run_setup().output


# ---------------------------------------------------------------------------
# Sign-in
# ---------------------------------------------------------------------------

SIGNIN_SECRETS = ("ldap_admin_password", "ldap_service_password", "auth_rolesync_password")


def test_sign_in_is_pulled_pinned_and_given_its_passwords_by_default(run_setup):
    result = run_setup()
    assert result.returncode == 0
    for image, family in (("nl2sql-ldap", "LDAP"), ("nl2sql-auth", "AUTH"), ("nl2sql-proxy", "PROXY")):
        tag = _shipped_tag(f"{family}_TAG")
        assert result.called(f"pull mcfaddja/{image}:{tag}"), image
        env = result.env_file()
        assert (env[f"{family}_IMAGE_NAME"], env[f"{family}_IMAGE_TAG"]) == (f"mcfaddja/{image}", tag)
    env, secrets = result.env_file(), result.secrets()
    for name in SIGNIN_SECRETS:
        assert re.fullmatch(r"[0-9a-f]{48}", secrets[name]), name
    assert len({secrets[name] for name in SIGNIN_SECRETS}) == 3
    assert "AUTH_ENABLED" not in env
    assert (result.workdir / ".env").stat().st_mode & 0o777 == 0o600
    output = " ".join(result.output.split())
    assert "The first person is admin, whose password is in secrets/ldap_admin_password" in output
    assert "which ./launch.sh --api starts at https://localhost:8084" in output


def test_the_passwords_are_kept_from_one_env_to_the_next_and_so_is_the_backup_private(run_setup):
    first = run_setup().secrets()
    again = run_setup()
    assert [again.secrets()[name] for name in SIGNIN_SECRETS] == [first[name] for name in SIGNIN_SECRETS]
    assert (again.workdir / ".env.bak").stat().st_mode & 0o777 == 0o600


def test_no_auth_turns_sign_in_off_for_good_and_pulls_nothing_for_it(run_setup):
    result = run_setup("--no-auth")
    env = result.env_file()
    assert env["AUTH_ENABLED"] == "false"
    assert "LDAP_IMAGE_NAME" not in env and "AUTH_IMAGE_NAME" not in env
    assert not result.calls_matching("pull mcfaddja/nl2sql-ldap")
    assert "Sign-in is off (AUTH_ENABLED=false in .env)" in result.output
    # And it stays off on the next run, which is given no flag.
    again = run_setup()
    assert again.env_file()["AUTH_ENABLED"] == "false"
    assert not again.calls_matching("pull mcfaddja/nl2sql-auth")


def test_a_sign_in_image_that_will_not_pull_is_built_instead(run_setup):
    result = run_setup(env={"FAKE_FAIL_PULL": "nl2sql-auth"})
    assert result.returncode == 0
    assert "./launch.sh will build it from source the first time sign-in starts." in result.output


# ---------------------------------------------------------------------------
# Every password and token, a file in secrets/ (6.1 V6-08, 6.3 V6-38)
# ---------------------------------------------------------------------------

STORE_SECRETS = (
    "postgres_password", "postgres_reader_password", "context_db_password", "vector_db_password",
    "stores_db_password", "snippets_db_password", "snippets_reader_password", "feedback_db_password",
    "feedback_writer_password", "corrections_db_password", "completions_db_password", "mlflow_db_password",
)
TOKENS = ("api_token", "review_token", "console_token")


def test_every_store_is_given_a_password_of_its_own_in_a_file_and_none_in_env(run_setup):
    result = run_setup()
    secrets, env = result.secrets(), result.env_file()
    assert all(re.fullmatch(r"[0-9a-f]{48}", secrets[name]) for name in STORE_SECRETS)
    assert len({secrets[name] for name in STORE_SECRETS}) == len(STORE_SECRETS)
    assert not [key for key in env if key.endswith(("_PASSWORD", "_TOKEN"))], "a secret is in .env"
    assert not any(value in (result.workdir / ".env").read_text() for value in secrets.values() if value)


def test_the_directory_is_its_owners_and_the_files_are_readable_to_the_containers(run_setup):
    """A container's account -- 10001, nginx's 101, postgres's 999 -- is not
    the person who owns the checkout, and on Linux a bind-mounted file keeps
    its mode; the directory is what keeps everyone else out."""
    directory = run_setup().workdir / "secrets"
    assert directory.stat().st_mode & 0o777 == 0o700
    assert {path.stat().st_mode & 0o777 for path in directory.iterdir()} == {0o644}


def test_store_passwords_are_kept_from_one_run_to_the_next(run_setup):
    first = run_setup().secrets()
    again = run_setup().secrets()
    assert [again[name] for name in STORE_SECRETS] == [first[name] for name in STORE_SECRETS]


def test_a_password_in_a_env_from_before_6_3_is_moved_into_its_file(run_setup):
    """Generated once and never replaced: a store keeps the password it was
    given, so the old value is the one the file must hold."""
    first = run_setup()
    (first.workdir / "secrets" / "postgres_password").unlink()
    dotenv = first.workdir / ".env"
    dotenv.write_text(dotenv.read_text() + "POSTGRES_PASSWORD=from-6-2\nAPI_TOKEN=tok-6-2\n")
    (first.workdir / "secrets" / "api_token").unlink()
    again = run_setup()
    assert again.secrets()["postgres_password"] == "from-6-2"
    assert again.secrets()["api_token"] == "tok-6-2"
    assert "POSTGRES_PASSWORD" not in again.env_file() and "API_TOKEN" not in again.env_file()


def test_no_service_token_unless_asked_for(run_setup):
    secrets = run_setup().secrets()
    assert [secrets[name] for name in TOKENS] == ["", "", ""], "empty files, which compose mounts and nothing reads"
    assert secrets["ldap_upstream_bind_password"] == ""


def test_tokens_makes_the_three_service_tokens_and_keeps_them(run_setup):
    """'The three tokens when set' (V6-08): generated, not chosen, once someone
    asks for them, and carried from then on with or without the flag."""
    first = run_setup("--tokens").secrets()
    assert all(re.fullmatch(r"[0-9a-f]{48}", first[name]) for name in TOKENS)
    assert len({first[name] for name in TOKENS}) == 3
    again = run_setup().secrets()
    assert [again[name] for name in TOKENS] == [first[name] for name in TOKENS]


def test_the_backup_keeps_the_settings_and_not_the_secrets(run_setup):
    """.env.bak is a record of what the last .env said; a secret a .env from
    before 6.3 held is in secrets/ now, and a copy in the backup is one more
    place to read it."""
    first = run_setup()
    dotenv = first.workdir / ".env"
    dotenv.write_text(dotenv.read_text() + "POSTGRES_PASSWORD=old-and-secret\n")
    again = run_setup()
    backup = (again.workdir / ".env.bak").read_text()
    assert "IMAGE_NAME=" in backup and "AGENT_IMAGE_TAG=" in backup
    assert "old-and-secret" not in backup
    assert "# POSTGRES_PASSWORD: in secrets/, not kept here" in backup
    assert (again.workdir / ".env.bak").stat().st_mode & 0o777 == 0o600


# ---------------------------------------------------------------------------
# Another instance beside this one (V6-67)
# ---------------------------------------------------------------------------


def test_another_instance_has_its_own_database_volume_and_containers(run_setup):
    result = run_setup(env={"NL2SQL_INSTANCE": "nl2sql-accept"})
    assert result.returncode == 0, result.output
    assert result.called("volume inspect nl2sql-accept-pgdata")
    for name in ("postgres", "stores", "vectordb", "chunkdb"):
        assert result.called(f"{{{{.State.Health.Status}}}} nl2sql-accept-{name}"), name
    assert not result.called("nl2sql-pgdata")


def test_an_instance_named_in_dotenv_is_used_and_kept(run_setup):
    """How the acceptance tier names its stack: in the .env it starts from,
    which setup.sh rewrites -- and must not lose the name in rewriting."""
    dotenv = run_setup().workdir / ".env"  # a .env to start from
    dotenv.write_text(dotenv.read_text() + "NL2SQL_INSTANCE=nl2sql-accept\nGUI_PORT=18080\n")
    result = run_setup()
    assert result.returncode == 0, result.output
    assert result.called("volume inspect nl2sql-accept-pgdata")
    kept = result.env_file()
    assert (kept["NL2SQL_INSTANCE"], kept["GUI_PORT"]) == ("nl2sql-accept", "18080")


# ---------------------------------------------------------------------------
# --build-all: the stack as this checkout builds it (V6-67)
# ---------------------------------------------------------------------------

EVERY_SET = ("--review", "--curate", "--console", "--mlflow", "--desktop")


def test_build_all_pulls_nothing_of_ours_and_builds_everything_it_pins(run_setup):
    result = run_setup("--build-all", *EVERY_SET)
    assert result.returncode == 0, result.output
    assert not result.calls_matching("pull mcfaddja/nl2sql-agent"), "an image of ours was pulled"
    assert not [call for call in result.calls_matching("pull ") if "rag-" not in call], result.calls_matching("pull ")
    [build] = [call for call in result.calls if call.endswith(" build") and "--profile api" in call]
    for profile in ("api", "gui", "review", "mlflow", "auth", "desktop"):
        assert f"--profile {profile} " in build + " ", profile
    # The pages' image through one page: the others are the same image.
    for profile in ("reviewgui", "curategui", "consolegui", "directorygui"):
        assert f"--profile {profile} " not in build + " ", profile
    assert result.called("compose build postgres"), "the dataset is built too"
    assert "Building every image from this checkout (--build-all)" in result.output


def test_build_all_pins_the_local_builds(run_setup):
    pinned = run_setup("--build-all", *EVERY_SET).env_file()
    for image in ("AGENT", "REVIEW", "PROXY", "MLFLOW", "MLFLOW_DB", "DESKTOP", "LDAP", "AUTH"):
        assert pinned[f"{image}_IMAGE_TAG"] == "local", image
        assert not pinned[f"{image}_IMAGE_NAME"].startswith("mcfaddja/"), image
    assert (pinned["IMAGE_NAME"], pinned["IMAGE_TAG"]) == ("nl2sql-retail-postgres", "latest")
    # The knowledge-base stores have no Dockerfile here, and stay published.
    assert pinned["VECTOR_IMAGE_NAME"].startswith("mcfaddja/")


def test_build_all_builds_only_what_the_run_asked_for(run_setup):
    result = run_setup("--build-all", "--no-auth")
    [build] = [call for call in result.calls if call.endswith(" build") and "--profile api" in call]
    assert "--profile review " in build, "the review image loads the snippets"
    assert "--profile gui" not in build and "--profile auth" not in build


def test_an_image_that_does_not_build_stops_setup_and_says_so(run_setup):
    result = run_setup("--build-all", env={"FAKE_BUILD_ALL_FAILS": "1"})
    assert result.returncode != 0
    assert "an image did not build from this checkout" in result.output


# ---------------------------------------------------------------------------
# A stack from before 6.3
# ---------------------------------------------------------------------------


def test_the_old_stores_containers_are_retired_before_the_databases_start(run_setup):
    """The feedback store's own container holds the port the runtime stores
    publish since 6.3: stopped cleanly and removed, its volume kept for
    launch.sh to move."""
    result = run_setup(env={"FAKE_LEGACY_STORES": "feedbackdb correctionsdb"})
    assert result.returncode == 0, result.output
    for store in ("feedbackdb", "correctionsdb"):
        assert result.index_of(f"stop -t 60 nl2sql-{store}") < result.index_of(f"rm nl2sql-{store}")
        assert result.index_of(f"rm nl2sql-{store}") < result.index_of("compose up -d postgres stores")
    assert not result.called("stop -t 60 nl2sql-snippetsdb")
    assert not result.calls_matching("volume rm")
    assert "Retiring the stores' containers from before 6.3: feedbackdb correctionsdb" in result.output


def test_a_fresh_stack_retires_nothing(run_setup):
    result = run_setup()
    assert not result.calls_matching("stop -t 60")
    assert "Retiring" not in result.output
