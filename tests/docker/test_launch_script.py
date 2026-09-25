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

# No marker. Every test here drives launch.sh against fake `docker`, `curl`
# and `sleep` binaries and never touches a daemon -- the same arrangement
# test_setup_script.py and test_start_script.py use, and neither of those is
# marked. It carried `pytest.mark.docker` for its first few months anyway,
# which kept sixty tests out of the default run for no reason.


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


# ---------------------------------------------------------------------------
# The feedback staging database (--feedback) and the review stack (--review)
# ---------------------------------------------------------------------------


def test_the_feedback_stack_is_not_started_unless_it_is_asked_for(run_launch):
    """Most people ask questions and never review anything, and this stack
    opens the port that can rewrite the golden question set."""
    result = run_launch()
    assert not result.called("up -d feedbackdb")
    assert not result.called("up -d review")
    assert "staging database" not in result.output


def test_the_feedback_flag_starts_the_staging_database(run_launch):
    result = run_launch("--feedback")
    assert result.called("--profile feedback up -d feedbackdb")
    assert "Staging database is healthy on port 5435" in result.output


def test_asking_for_feedback_asks_for_the_api_that_writes_to_it(run_launch):
    """A staging database nothing writes to is a container burning memory."""
    result = run_launch("--feedback")
    assert result.called("--profile api up -d api")


def test_feedback_alone_does_not_drag_the_review_stack_in(run_launch):
    result = run_launch("--feedback")
    assert not result.called("up -d review")
    assert not result.called("up -d reviewgui")


def test_the_staging_port_follows_what_compose_will_use(run_launch):
    result = run_launch("--feedback", env_file="IMAGE_NAME=x\nFEEDBACK_DB_PORT=5999\n")
    assert "Staging database is healthy on port 5999" in result.output


def test_an_api_with_nowhere_to_write_is_warned_about(run_launch):
    """The database is up and the API has not been told where it is, which
    looks exactly like working until someone votes."""
    result = run_launch("--feedback")
    assert "API_FEEDBACK_DB_URL is not set" in result.output
    assert "setup.sh writes one into .env" in result.output


def test_a_configured_api_is_not_warned_about(run_launch):
    result = run_launch(
        "--feedback",
        env_file="IMAGE_NAME=x\nAPI_FEEDBACK_DB_URL=postgresql://w:p@nl2sql-feedbackdb:5432/nl2sql_feedback\n",
    )
    assert "API_FEEDBACK_DB_URL is not set" not in result.output


def test_a_staging_database_that_never_comes_up_is_reported(run_launch):
    result = run_launch("--feedback", env={"FAKE_FEEDBACK_HEALTH": "starting"}, timeout=180)
    assert "the staging database did not become healthy" in result.output
    assert "docker compose --profile feedback logs feedbackdb" in result.output


def test_the_review_flag_starts_the_service_and_its_interface(run_launch):
    result = run_launch("--review")
    assert result.called("--profile feedback --profile review up -d review")
    assert result.called("--profile feedback --profile review --profile reviewgui up -d reviewgui")
    assert "Review service is healthy at https://localhost:8444" in result.output
    assert "Review interface is healthy at http://localhost:8081" in result.output


def test_asking_for_review_asks_for_everything_under_it(run_launch):
    """The interface is nothing without the service, and the service is
    nothing without the database in front of it."""
    result = run_launch("--review")
    assert result.called("--profile feedback up -d feedbackdb")
    assert result.called("--profile api up -d api")


def test_the_review_scheme_follows_the_tls_setting(run_launch):
    result = run_launch("--review", env_file="IMAGE_NAME=x\nREVIEW_TLS_ENABLED=false\n")
    assert "Review service is healthy at http://localhost:8444" in result.output


def test_the_review_ports_follow_what_compose_will_use(run_launch):
    result = run_launch(
        "--review", env_file="IMAGE_NAME=x\nREVIEW_PORT=9444\nREVIEW_GUI_PORT=9081\n"
    )
    assert "https://localhost:9444" in result.output
    assert "http://localhost:9081" in result.output


def test_a_review_service_that_never_comes_up_is_reported(run_launch):
    result = run_launch("--review", env={"FAKE_REVIEW_HEALTH": "starting"}, timeout=240)
    assert "the review service did not become healthy" in result.output
    assert "--profile feedback --profile review logs review" in result.output


def test_a_review_interface_that_never_comes_up_is_reported(run_launch):
    result = run_launch("--review", env={"FAKE_REVIEW_GUI_HEALTH": "starting"}, timeout=240)
    assert "the review interface did not become healthy" in result.output
    assert "--profile reviewgui logs reviewgui" in result.output


def test_a_container_that_died_is_not_waited_out(run_launch):
    """A container that exited is never going to become healthy, so the wait
    gives up on it rather than sitting through the whole timeout."""
    result = run_launch(
        "--review",
        env={"FAKE_REVIEW_HEALTH": "starting", "FAKE_REVIEW_RUNNING": "false"},
        timeout=60,
    )
    assert "the review service did not become healthy" in result.output


def test_the_closing_lines_say_where_to_open_the_review_interface(run_launch):
    output = run_launch("--review").output
    assert "open http://localhost:8081" in output
    assert "review/README.md" in output


def test_the_closing_lines_say_that_promoting_edits_this_checkout(run_launch):
    """The single most surprising thing about the review interface: it
    writes a tracked file, and the reviewer is expected to commit it."""
    output = run_launch("--review").output
    assert "context_questions/translated_questions.md" in output
    assert "git diff" in output


def test_the_closing_lines_name_the_three_fields_a_reviewer_must_write(run_launch):
    """The part of the job no thumbs-up can do for them."""
    output = run_launch("--review").output
    assert "keywords" in output
    assert "reasoning target" in output
    assert "expected result" in output


def test_a_database_without_the_trigram_extension_is_warned_about(run_launch):
    """The agent falls back to difflib without pg_trgm, which is a quieter
    kind of wrong: literal matching still works, just less well, so nothing
    fails and the only sign is this line.

    Asserted explicitly rather than incidentally. It used to be covered by a
    test that happened to quote two words shared with nothing else in the
    script; adding an unrelated warning elsewhere made those two words
    ambiguous and the coverage evaporated without anything breaking.
    """
    result = run_launch(env={"FAKE_TRGM_INSTALLED": "0"})
    assert "pg_trgm is not installed" in result.output
    assert "falls back to difflib" in result.output


def test_a_database_with_the_trigram_extension_says_so_instead(run_launch):
    result = run_launch(env={"FAKE_TRGM_INSTALLED": "1"})
    assert "literal matching: pg_trgm installed (trigram search)" in result.output
    assert "pg_trgm is not installed" not in result.output


# ---------------------------------------------------------------------------
# A proxy still trusting a certificate that has been reissued
# ---------------------------------------------------------------------------
#
# nginx reads the API's certificate once, while it parses its config. When
# the API reissues one -- which it does when API_TLS_HOSTNAMES grows to cover
# a service that did not exist before -- an already-running proxy is trusting
# a certificate nobody presents any more. The container stays *healthy*,
# because its health check asks for index.html and that is served from disk;
# every request to the API through it returns 502. And `docker compose up -d`
# does not restart a healthy container, so it stays that way.
#
# This is what an upgrade looks like from the outside, and it is why the
# check is a request for a route the API owns rather than a health status.


def test_the_gui_is_checked_through_its_proxy_not_just_for_health(run_launch):
    result = run_launch("--gui")
    assert result.called("readyz"), "nothing asked the API for anything through the proxy"
    assert "GUI is healthy at http://localhost:8080" in result.output


def test_a_gui_whose_proxy_cannot_reach_the_api_is_restarted(run_launch):
    result = run_launch("--gui", env={"FAKE_PROXY_BROKEN": "8080"})

    assert "cannot reach the API through its proxy" in result.output
    assert result.called("restart gui")
    # And once restarted it is reported as working, not as broken.
    assert "GUI is healthy at http://localhost:8080" in result.output


def test_a_working_gui_proxy_is_not_restarted(run_launch):
    """A restart drops every connection through it. Only do it when it is the
    cure for something."""
    result = run_launch("--gui")
    assert not result.called("restart gui")


def test_the_restart_says_why_rather_than_just_doing_it(run_launch):
    result = run_launch("--gui", env={"FAKE_PROXY_BROKEN": "8080"})
    assert "pick up" in result.output
    assert "certificate" in result.output


def test_the_review_interface_is_checked_through_its_proxy_too(run_launch):
    result = run_launch("--review")
    assert "Review interface is healthy at http://localhost:8081" in result.output


def test_a_review_interface_whose_proxy_is_stale_is_restarted(run_launch):
    result = run_launch("--review", env={"FAKE_PROXY_BROKEN": "8081"})

    assert "cannot reach the API through its proxy" in result.output
    assert result.called("restart reviewgui")
    assert "Review interface is healthy at http://localhost:8081" in result.output


def test_a_gui_proxy_a_restart_does_not_fix_is_reported_not_papered_over(run_launch):
    """A restart cures a stale certificate and nothing else. When the proxy
    still cannot reach the API, saying "healthy" would be the wrong answer to
    the only question the user has."""
    result = run_launch("--gui", env={"FAKE_PROXY_DEAD": "8080"})

    assert result.called("restart gui")
    assert "cannot reach the API through its proxy" in result.output
    assert "GUI is healthy" not in result.output
    assert "docker compose --profile api --profile gui logs gui" in result.output


def test_a_review_proxy_a_restart_does_not_fix_is_reported_too(run_launch):
    result = run_launch("--review", env={"FAKE_PROXY_DEAD": "8081"})

    assert result.called("restart reviewgui")
    assert "cannot reach the review service" in result.output
    assert "Review interface is healthy" not in result.output
    assert "docker compose --profile reviewgui logs reviewgui" in result.output


# ---------------------------------------------------------------------------
# The desktop client
# ---------------------------------------------------------------------------


def test_the_desktop_client_needs_the_api_and_says_so_by_starting_it(run_launch):
    """It talks to the API directly rather than through a proxy of its own,
    so that is the one thing it cannot do without."""
    result = run_launch("--desktop")
    assert result.returncode == 0
    assert result.calls_matching("compose --profile api up -d api")


def test_it_builds_the_jar_for_this_machine_and_copies_the_certificate_out(run_launch):
    result = run_launch("--desktop", env={"FAKE_UNAME_S": "Darwin", "FAKE_UNAME_M": "arm64"})

    assert "Building it for mac-aarch64" in result.output
    assert result.calls_matching("run --rm desktop")
    assert "Copied the API certificate" in result.output
    assert result.calls_matching("cp api:/etc/nl2sql/tls/server.crt")
    assert (result.workdir / "nl2sql-api.crt").is_file()
    assert (result.workdir / "desktop/target/nl2sql-desktop.jar").is_file()


@pytest.mark.parametrize(
    ("system", "machine", "classifier"),
    [
        ("Darwin", "arm64", "mac-aarch64"),
        ("Darwin", "x86_64", "mac"),
        ("Linux", "x86_64", "linux"),
        ("Linux", "aarch64", "linux-aarch64"),
        ("MINGW64_NT-10.0", "x86_64", "win"),
        # Anything unrecognised gets what the container would have assumed
        # about itself, which at least starts on the commonest machine there
        # is rather than refusing to build at all.
        ("SunOS", "sparc", "linux"),
    ],
)
def test_the_jar_is_built_for_the_machine_it_will_run_on(run_launch, system, machine, classifier):
    """One jar is one platform: the same library file names are used on macOS
    x86-64 and arm64, so a jar carrying both would carry one of them twice
    under one name and load whichever came first."""
    result = run_launch("--desktop", env={"FAKE_UNAME_S": system, "FAKE_UNAME_M": machine})

    assert f"Building it for {classifier}" in result.output
    assert (result.workdir / "desktop/target/.platform").read_text() == classifier


def test_a_jar_already_built_from_these_sources_is_not_built_again(run_launch):
    """The build takes minutes. Doing it on every start would make the fast
    path the slow one."""
    first = run_launch("--desktop", env={"FAKE_UNAME_S": "Linux", "FAKE_UNAME_M": "x86_64"})
    assert "Building it for" in first.output

    second = run_launch("--desktop", env={"FAKE_UNAME_S": "Linux", "FAKE_UNAME_M": "x86_64"})
    assert "already built for linux" in second.output


def test_a_jar_built_for_another_machine_is_built_again(run_launch):
    """It would not start on this one, and nothing but the note beside it
    says which machine it was for."""
    run_launch("--desktop", env={"FAKE_UNAME_S": "Linux", "FAKE_UNAME_M": "x86_64"})

    moved = run_launch("--desktop", env={"FAKE_UNAME_S": "Darwin", "FAKE_UNAME_M": "arm64"})

    assert "Building it for mac-aarch64" in moved.output


def test_a_source_newer_than_the_jar_is_built_again(run_launch):
    result = run_launch("--desktop", env={"FAKE_UNAME_S": "Linux", "FAKE_UNAME_M": "x86_64"})
    (result.workdir / "desktop" / "src" / "Main.java").write_text("// edited\n")

    again = run_launch("--desktop", env={"FAKE_UNAME_S": "Linux", "FAKE_UNAME_M": "x86_64"})

    assert "Building it for linux" in again.output


def test_a_build_that_failed_says_how_to_see_why(run_launch):
    result = run_launch("--desktop", env={"FAKE_DESKTOP_BUILD_FAILS": "1"})

    assert result.returncode == 0
    assert "could not be built" in result.output
    assert "docker compose" in result.output
    # And the closing notes do not then tell the user to run a jar that is
    # not there.
    assert "The desktop client is built" not in result.output


def test_a_certificate_that_could_not_be_copied_names_the_fallback(run_launch):
    """Without it the client has nothing to verify against. --insecure is the
    answer and it says so in the status bar for as long as it is on."""
    result = run_launch("--desktop", env={"FAKE_CERT_COPY_FAILS": "1"})

    assert "could not copy the API's certificate" in result.output
    assert "--insecure is" in result.output


def test_the_closing_notes_say_how_to_run_it(run_launch):
    result = run_launch("--desktop")

    assert "java -jar desktop/target/nl2sql-desktop.jar --cacert ./nl2sql-api.crt" in result.output
    assert "Java runtime of 21 or later" in result.output
    # The point of the whole thing: one queue, whichever client was used.
    assert "the same staging table, the same review" in result.output
