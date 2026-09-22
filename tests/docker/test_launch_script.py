"""launch.sh, run against fake docker and curl.

The script's whole value is in what it notices: a container that is up but
empty, a chat host that moved, an embedding model that is not the one the
vectors were built with. None of those stop it from starting; all of them make
the agent look bad at its job. So the tests are mostly about which warnings
come out, and they need a sandbox because the real answers depend on whatever
happens to be running on the developer's machine.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.docker


# ---------------------------------------------------------------------------
# The two-command contract
# ---------------------------------------------------------------------------


def test_a_clean_run_succeeds_and_ends_by_showing_the_next_command(run_launch):
    """The point of the script: run it, then ask a question. If the closing
    lines ever stop printing that command, the contract is broken.
    """
    result = run_launch()
    assert result.returncode == 0
    assert 'docker compose run --rm agent "How many stores are there?"' in result.output


def test_it_starts_all_three_databases(run_launch):
    result = run_launch()
    assert result.called("compose up -d postgres vectordb chunkdb")


def test_it_waits_for_every_container_to_be_healthy(run_launch):
    """Compose reports a container "started" well before Postgres is accepting
    connections; the agent would then fail on its first query.
    """
    result = run_launch()
    for container in ("nl2sql-postgres", "nl2sql-vectordb", "nl2sql-chunkdb"):
        assert result.called(f"inspect --format {{{{.State.Health.Status}}}} {container}")


def test_it_does_not_pull_or_rebuild_anything(run_launch):
    """That is setup.sh's job. A launch that re-pulled would turn a five-second
    start into a minutes-long one every time.
    """
    result = run_launch()
    assert not result.calls_matching("pull ")
    assert not result.calls_matching("compose build")


def test_restart_recreates_rather_than_reusing(run_launch):
    assert run_launch("--restart").called("compose up -d --force-recreate")


# ---------------------------------------------------------------------------
# Handing off to setup.sh
# ---------------------------------------------------------------------------


def test_a_missing_env_file_hands_off_to_setup_rather_than_guessing(run_launch):
    """Without .env, compose falls back to building images locally, which is
    not what someone running launch.sh wants and takes very much longer.
    """
    result = run_launch(env_file=None)
    assert result.returncode == 0
    assert "running setup.sh first" in result.output
    # setup.sh's own work is visible: it pulls, which launch.sh never does.
    assert result.calls_matching("pull ")


# ---------------------------------------------------------------------------
# Noticing an empty database
# ---------------------------------------------------------------------------


def test_it_reports_what_each_database_actually_holds(run_launch):
    result = run_launch()
    assert "194101 sales rows" in result.output
    assert "53 embedded chunks" in result.output
    assert "45 golden pairs" in result.output


def test_an_empty_retail_database_is_called_out(run_launch):
    """Healthy and empty is the failure mode a volume created before the image
    shipped its data produces, and nothing else reports it.
    """
    result = run_launch(env={"FAKE_ROW_COUNT": "0"})
    assert "no sales rows" in result.output
    assert "--reset" in result.output
    assert "connect and then answer nothing" in result.output


def test_an_empty_knowledge_base_warns_that_retrieval_will_be_skipped(run_launch):
    result = run_launch(env={"FAKE_CHUNK_COUNT": "0"})
    assert "no embedded chunks" in result.output
    assert "schema-only" in result.output


def test_golden_pairs_without_their_vectors_is_reported(run_launch):
    """The pairs live in one database and their embeddings in another, so they
    can be out of step. Multi-shot needs both.
    """
    result = run_launch(env={"FAKE_VECTOR_COUNT": "0"})
    assert "Multi-shot needs both" in result.output


def test_vectors_without_their_pairs_is_reported(run_launch):
    result = run_launch(env={"FAKE_PAIR_COUNT": "0"})
    assert "Multi-shot needs both" in result.output


def test_a_container_that_never_becomes_healthy_fails_loudly(run_launch):
    result = run_launch(env={"FAKE_CONTEXT_HEALTH": "unhealthy"})
    assert result.returncode != 0
    assert "did not become healthy" in result.output
    assert "docker compose logs chunkdb" in result.output


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


def test_it_probes_the_chat_host_compose_will_really_use(run_launch):
    """Read back from `compose config`, not from this script's own defaults --
    otherwise it cheerfully validates a host the agent is not going to use.
    """
    result = run_launch()
    assert result.called("curl http://192.168.10.82:11434/api/tags")
    assert "qwen3.8-256k is available" in result.output


def test_an_env_override_of_the_chat_host_is_followed(run_launch):
    env_file = "RAG_ENABLED=true\nOLLAMA_BASE_URL=http://elsewhere:11434\n"
    result = run_launch(env_file=env_file)
    assert result.called("curl http://elsewhere:11434/api/tags")


def test_an_unreachable_chat_host_warns_that_questions_will_fail(run_launch):
    result = run_launch(env={"FAKE_OLLAMA_DOWN": "1"})
    assert result.returncode == 0, "an unreachable model host is a warning, not a failure"
    assert "could not reach the chat host" in result.output
    assert "Every question will fail until it is reachable" in result.output
    # The embedding host is probed separately and is down too.
    assert "Retrieval and worked examples will be skipped" in result.output


def test_a_chat_host_without_the_model_is_distinguished_from_one_that_is_down(run_launch):
    """Different fixes: pull the model, versus start the host."""
    result = run_launch(env={"FAKE_OLLAMA_MODELS": '{"name":"some-other-model"}'})
    assert "does not have qwen3.8-256k" in result.output
    assert "could not reach the chat host" not in result.output


def test_a_missing_embedding_model_explains_why_it_matters(run_launch):
    """A different embedding model does not fail -- it puts the query in another
    vector space and retrieves confident nonsense.
    """
    result = run_launch(env={"FAKE_OLLAMA_MODELS": '{"name":"qwen3.8-256k"}'})
    assert "does not have bge-m3" in result.output
    assert "ollama pull bge-m3" in result.output


# ---------------------------------------------------------------------------
# --no-rag
# ---------------------------------------------------------------------------


def test_no_rag_starts_only_the_retail_database(run_launch):
    result = run_launch("--no-rag")
    assert result.called("compose up -d postgres")
    assert not result.calls_matching("up -d postgres vectordb")


def test_no_rag_skips_the_retrieval_checks_entirely(run_launch):
    result = run_launch("--no-rag")
    assert "embedded chunks" not in result.output
    assert "golden pairs" not in result.output
    assert "bge-m3" not in result.output


def test_no_rag_still_checks_the_chat_model(run_launch):
    """Without retrieval the agent still needs the model that writes the SQL."""
    assert "qwen3.8-256k is available" in run_launch("--no-rag").output


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------


def test_help_exits_cleanly_and_points_first_timers_at_setup(run_launch):
    result = run_launch("--help")
    assert result.returncode == 0
    assert "./setup.sh" in result.output
    assert not result.calls_matching("compose up")


def test_an_unknown_option_fails_rather_than_starting_anything(run_launch):
    result = run_launch("--wat")
    assert result.returncode != 0
    assert "unknown option: --wat" in result.output
    assert not result.calls_matching("compose up")


def test_quiet_prints_nothing_when_all_is_well(run_launch):
    assert run_launch("-q").stdout.strip() == ""


def test_quiet_still_prints_problems(run_launch):
    """Silencing the good news must not silence the bad."""
    result = run_launch("-q", env={"FAKE_OLLAMA_DOWN": "1"})
    assert "could not reach the chat host" in result.stderr


# ---------------------------------------------------------------------------
# The agent's read-only role
# ---------------------------------------------------------------------------


def test_it_creates_the_agents_read_only_role_on_every_start(run_launch):
    """The published image predates the role and a volume keeps whatever roles
    it had, so launch (re)creates it each time rather than assuming it.
    """
    result = run_launch()
    [call] = result.calls_matching("reader=")
    assert "compose exec -T postgres psql -U postgres" in call
    assert "-d nl2sql_retail" in call
    assert "reader=nl2sql_reader" in call and "owner=nl2sql" in call
    assert "can read every table and write none" in result.output


def test_the_role_is_created_even_without_rag(run_launch):
    assert run_launch("--no-rag").called("reader=nl2sql_reader")


def test_the_role_follows_the_credentials_compose_will_use(run_launch):
    """Shell overrides win, then .env -- the same precedence compose applies
    to DATABASE_URL, or the agent would be handed a role that does not exist.
    """
    from_env_file = run_launch(env_file="IMAGE_NAME=x\nPOSTGRES_READER_USER=dotenv_reader\nPOSTGRES_DB=other_db\n")
    [call] = from_env_file.calls_matching("reader=")
    assert "reader=dotenv_reader" in call and "-d other_db" in call

    from_shell = run_launch(env={"POSTGRES_READER_USER": "shell_reader", "POSTGRES_USER": "shell_owner"},
                            env_file="POSTGRES_READER_USER=dotenv_reader\n")
    call = from_shell.calls_matching("reader=")[-1]  # the sandbox log spans both runs
    assert "reader=shell_reader" in call and "owner=shell_owner" in call


def test_a_role_that_cannot_be_created_warns_that_the_agent_cannot_connect(run_launch):
    result = run_launch(env={"FAKE_READER_ROLE_FAILS": "1"})
    assert result.returncode == 0
    assert "read-only role" in result.output
    assert "fail to connect" in result.output


def test_it_installs_the_trigram_extension_the_literal_matcher_prefers(run_launch):
    """Same reasoning as the reader role: the published image predates
    pg_trgm and an existing volume keeps whatever extensions it had. The
    agent falls back to difflib without it, so a failure warns rather than
    stopping the launch.
    """
    result = run_launch()
    [call] = result.calls_matching("CREATE EXTENSION")
    assert "compose exec -T postgres psql -U postgres" in call
    assert "IF NOT EXISTS pg_trgm" in call


# ---------------------------------------------------------------------------
# The v4 pipeline's own prerequisites
# ---------------------------------------------------------------------------


def test_it_reports_the_schema_index_table_selection_depends_on(run_launch):
    """v4 picks tables from the DDL-chunk vectors instead of asking the
    model. An empty collection is the failure that looks like bad table
    selection rather than like a missing store.
    """
    result = run_launch()
    assert "schema index: 20 DDL chunks" in result.output


def test_a_missing_schema_index_warns_about_table_selection(run_launch):
    result = run_launch(env={"FAKE_DDL_CHUNKS": "0"})
    assert "no ddl_index_embeddings collection" in result.output
    assert "Table selection falls back" in result.output


def test_it_reports_which_scorer_literal_matching_will_use(run_launch):
    assert "pg_trgm installed" in run_launch().output


def test_a_missing_trigram_extension_warns_without_stopping(run_launch):
    """The matcher falls back to difflib, so this costs precision and not
    the run.
    """
    result = run_launch(env={"FAKE_TRGM_INSTALLED": "0"})
    assert result.returncode == 0
    assert "falls back to difflib" in result.output


def test_it_confirms_the_agents_role_holds_select_and_nothing_else(run_launch):
    assert "the agent's role holds SELECT and nothing else" in run_launch().output


def test_a_role_that_gained_a_write_grant_is_called_out(run_launch):
    """Least privilege is checked on every start, not only in the test
    suite: a volume outlives the image that set the grants.
    """
    result = run_launch(env={"FAKE_READER_EXTRA_GRANTS": "3"})
    assert "holds 3 non-SELECT grants" in result.output


def test_an_env_pinning_an_older_agent_image_is_called_out(run_launch):
    """The two-command flow would otherwise keep running the previous agent
    after an upgrade, which looks like the new work having no effect.
    """
    result = run_launch(env_file="IMAGE_NAME=x\nAGENT_IMAGE_TAG=v3\n")
    assert "pins the agent image at v3" in result.output
    assert "running the older agent" in result.output


def test_an_env_pinning_the_shipped_agent_image_says_nothing(run_launch):
    """Read out of setup.sh rather than written here, so bumping a release
    does not fail this test for a reason that is not a defect.
    """
    shipped = re.search(
        r'^AGENT_TAG="([^"]+)"', (REPO_ROOT / "setup.sh").read_text(), re.MULTILINE
    ).group(1)
    result = run_launch(env_file=f"IMAGE_NAME=x\nAGENT_IMAGE_TAG={shipped}\n")
    assert "pins the agent image" not in result.output


def test_the_closing_lines_show_a_question_that_needs_the_literal_matcher(run_launch):
    """The script's contract is two commands, and the second one should
    demonstrate what this version added.
    """
    output = run_launch().output
    assert 'docker compose run --rm agent "total net sales for dairy and eggs in FY2025"' in output


def test_a_model_tagged_latest_is_not_reported_as_missing(run_launch):
    """Ollama answers with `name:latest` when the user gave no tag. An exact
    match warned that every question would fail about a host that was
    serving the model perfectly well.
    """
    result = run_launch(env={"FAKE_OLLAMA_MODELS": '{"name":"qwen3.8-256k:latest"},{"name":"bge-m3:latest"}'})
    assert "is available at" in result.output
    assert "does not have qwen3.8-256k" not in result.output


# ---------------------------------------------------------------------------
# The REST API (--api)
# ---------------------------------------------------------------------------


def test_the_api_is_not_started_unless_it_is_asked_for(run_launch):
    """A port nobody asked to have opened should not be opened, so the
    default run leaves the API alone.
    """
    result = run_launch()
    assert not result.called("up -d api")
    assert "REST API" not in result.output


def test_the_api_flag_starts_the_api_service(run_launch):
    result = run_launch("--api")
    assert result.called("--profile api up -d api")
    assert "REST API is healthy at https://localhost:8443" in result.output


def test_the_api_flag_prints_where_the_openapi_document_is(run_launch):
    """The document is the client library. Anyone wiring up a GUI needs the
    URL more than they need anything else the script prints.
    """
    assert "OpenAPI document: https://localhost:8443/openapi.json" in run_launch("--api").output


def test_the_api_port_follows_what_compose_will_use(run_launch):
    result = run_launch("--api", env_file="IMAGE_NAME=x\nAPI_PORT=9443\n")
    assert "https://localhost:9443" in result.output


def test_an_api_container_that_never_comes_up_is_reported(run_launch):
    result = run_launch("--api", env={"FAKE_API_HEALTH": "starting", "FAKE_API_RUNNING": "false"})
    assert "the REST API container did not become healthy" in result.output
    assert "Check what it said: docker compose --profile api logs api" in result.output


def test_an_api_container_that_is_up_but_never_healthy_is_waited_out_then_reported(run_launch):
    """A container that is running and failing its healthcheck is different
    from one that exited: nothing has crashed, so the poll has to run out
    rather than give up at the first look. This is the path that takes the
    full wait, and the only one that reaches the end of the loop.
    """
    result = run_launch("--api", env={"FAKE_API_HEALTH": "starting", "FAKE_API_RUNNING": "true"})
    assert "the REST API container did not become healthy" in result.output
    assert "Check what it said: docker compose --profile api logs api" in result.output


def test_an_agent_image_that_predates_the_api_says_so_instead(run_launch):
    """The exact failure a pinned .env produces after an upgrade: the image
    starts, Python cannot find the module, and the message would otherwise
    be "did not become healthy".
    """
    result = run_launch(
        "--api",
        env={
            "FAKE_API_HEALTH": "starting",
            "FAKE_API_RUNNING": "false",
            "FAKE_API_LOGS": "ModuleNotFoundError: No module named 'fastapi'",
        },
    )
    assert "The pinned agent image has no REST API in it" in result.output
    assert "Build it here instead: docker compose --profile api build api" in result.output


def test_turning_tls_off_is_called_out_as_clear_text(run_launch):
    result = run_launch("--api", env_file="IMAGE_NAME=x\nAPI_TLS_ENABLED=false\n")
    assert "the API serves plain HTTP: questions, SQL" in result.output
    assert "cross the network in clear text" in result.output
    assert "http://localhost:8443" in result.output


def test_an_api_with_no_token_warns_before_it_is_exposed(run_launch):
    result = run_launch("--api")
    assert "no API_TOKEN is set" in result.output
    assert "Set API_TOKEN in .env before exposing this off this machine" in result.output


def test_a_token_in_the_env_silences_that_warning(run_launch):
    result = run_launch("--api", env_file="IMAGE_NAME=x\nAPI_TOKEN=s3cret\n")
    assert "no API_TOKEN is set" not in result.output


def test_the_closing_lines_show_how_to_trust_the_development_certificate(run_launch):
    """Self-signed means every client refuses it until it is trusted, and
    copying it out is the one step nobody guesses.
    """
    output = run_launch("--api").output
    assert "cp api:/etc/nl2sql/tls/server.crt" in output
    assert 'curl --cacert ./nl2sql-api.crt "https://localhost:8443/v1/meta"' in output


def test_the_closing_lines_point_at_the_outside_client_and_the_contract(run_launch):
    output = run_launch("--api").output
    assert "docker compose --profile api run --rm apitest" in output
    assert "agent/API.md" in output


# ---------------------------------------------------------------------------
# The web interface (--gui)
# ---------------------------------------------------------------------------


def test_the_gui_is_not_started_unless_it_is_asked_for(run_launch):
    result = run_launch()
    assert not result.called("up -d gui")
    assert "web interface" not in result.output


def test_the_gui_flag_starts_it_with_both_profiles(run_launch):
    """The gui service depends on the api service, which lives in another
    profile, and compose will not start what no active profile names.
    """
    result = run_launch("--gui")
    assert result.called("--profile api --profile gui up -d gui")
    assert "GUI is healthy at http://localhost:8080" in result.output


def test_asking_for_the_gui_asks_for_the_api_behind_it(run_launch):
    """A page whose API is not running is a page that loads and then fails,
    which reads as the application being broken rather than as absent.
    """
    result = run_launch("--gui")
    assert result.called("--profile api up -d api")
    assert "REST API is healthy" in result.output


def test_the_api_alone_does_not_drag_the_gui_in(run_launch):
    result = run_launch("--api")
    assert not result.called("up -d gui")


def test_the_gui_port_follows_what_compose_will_use(run_launch):
    result = run_launch("--gui", env_file="IMAGE_NAME=x\nGUI_PORT=9080\n")
    assert "GUI is healthy at http://localhost:9080" in result.output


def test_a_gui_container_that_never_comes_up_is_reported(run_launch):
    result = run_launch("--gui", env={"FAKE_GUI_HEALTH": "starting", "FAKE_GUI_RUNNING": "false"})
    assert "the GUI container did not become healthy" in result.output
    assert "docker compose --profile api --profile gui logs gui" in result.output


def test_a_gui_container_that_is_up_but_never_healthy_is_waited_out_then_reported(run_launch):
    result = run_launch(
        "--gui",
        env={"FAKE_GUI_HEALTH": "starting", "FAKE_GUI_RUNNING": "true"},
        timeout=180,
    )
    assert "the GUI container did not become healthy" in result.output


def test_the_closing_lines_say_where_to_open_it(run_launch):
    output = run_launch("--gui").output
    assert "open http://localhost:8080" in output
    assert "gui/README.md" in output


def test_the_closing_lines_say_what_the_browser_is_spared(run_launch):
    """The reason the GUI ships with a proxy rather than CORS settings, said
    once where someone deploying it will read it.
    """
    output = run_launch("--gui").output
    assert "holds the API token and" in output
    assert "verifies the API's certificate" in output
