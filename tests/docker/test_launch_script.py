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


def test_it_starts_every_database(run_launch):
    """The retail database, the runtime stores -- the agent's snippets and
    every verdict are in them (6.3) -- and the two knowledge stores."""
    result = run_launch()
    assert result.called("compose up -d postgres stores vectordb chunkdb")


def test_it_waits_for_every_container_to_be_healthy(run_launch):
    """Compose reports a container "started" well before Postgres is accepting
    connections; the agent would then fail on its first query.
    """
    result = run_launch()
    for container in ("nl2sql-postgres", "nl2sql-stores", "nl2sql-vectordb", "nl2sql-chunkdb"):
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
    """Read by the dbprep service (nl2sql_ops.report), said here as it said it."""
    result = run_launch()
    assert result.called("compose run --rm --no-deps -T dbprep python -m nl2sql_ops report")
    assert "==> Checking what is actually in each database" in result.output
    assert "194101 sales rows" in result.output
    assert "53 embedded chunks" in result.output
    assert "48 golden pairs" in result.output


def test_a_warning_in_the_report_is_a_warning_here_and_a_state_line_is_not_said(run_launch):
    """Healthy and empty is the failure a volume made before the image
    shipped its data produces, and nothing else reports it: the report's
    warnings are this script's, and its STATE lines are for scripts."""
    report = "\n".join((
        "STEP Checking what is actually in each database", "STATE retail_rows=0",
        "WARN the retail database is up but has no sales rows in it.",
        "WARN The agent will connect and then answer nothing. Try: ./setup.sh --reset",
    ))
    result = run_launch(env={"FAKE_REPORT": report})
    assert result.returncode == 0
    assert "WARNING: the retail database is up but has no sales rows in it." in result.output
    assert "Try: ./setup.sh --reset" in result.output
    assert "STATE" not in result.output


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


def test_the_settings_are_read_with_the_agents_profile_named(run_launch):
    """The agent is behind a compose profile of its own, and `compose config`
    leaves out a service whose profile is not named. The fake answers the way
    the real one does, which is how this came to light: every release until
    v5.2 checked the default host and model, whatever .env said."""
    result = run_launch()
    assert result.called("compose --profile agent config")
    assert not [call for call in result.calls if call == "compose config"]


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
    assert result.called("compose up -d postgres stores")
    assert not result.calls_matching("vectordb chunkdb")


def test_no_rag_skips_the_embedding_model_check(run_launch):
    """The report reads only the stores that are running (nl2sql_ops.report),
    and without retrieval there is no embedding model to ask about."""
    assert "bge-m3" not in run_launch("--no-rag").output


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


def test_the_databases_are_prepared_by_the_one_shot_on_every_start(run_launch):
    """The published image predates the role, and a volume keeps whatever
    roles it had: the dbprep service makes it -- and sign-in's roles, the
    runtime stores' databases and every password -- on every start. This
    script runs no SQL itself (V6-41)."""
    result = run_launch()
    prepare = result.index_of("compose run --rm --no-deps -T dbprep")
    assert result.index_of("nl2sql-stores") < prepare < result.index_of("nl2sql_ops report")
    assert "==> Preparing the databases" in result.output
    assert "can read every table and write none" in result.output
    assert not result.calls_matching("psql") and not result.calls_matching("compose exec")


def test_the_databases_are_prepared_even_without_rag(run_launch):
    assert run_launch("--no-rag").called("compose run --rm --no-deps -T dbprep")


def test_databases_that_cannot_be_prepared_are_warned_about_and_the_rest_goes_on(run_launch):
    """Every service that connects to a database waits for the one-shot, so
    compose will say so again; this says it first, with the reason."""
    result = run_launch(env={"FAKE_DBPREP_FAILS": "AUTH_ROLESYNC_PASSWORD's secret is empty: run ./setup.sh"})
    assert result.returncode == 0
    assert "the databases could not be prepared, so what connects to them will not start. It said:" in result.output
    assert "dbprep: AUTH_ROLESYNC_PASSWORD's secret is empty: run ./setup.sh" in result.output


def test_a_warning_the_one_shot_gives_is_a_warning_here(run_launch):
    result = run_launch(env={"FAKE_DBPREP_WARNS": "could not create pg_trgm; literal matching falls back to difflib"})
    assert "WARNING: could not create pg_trgm; literal matching falls back to difflib" in result.output


# ---------------------------------------------------------------------------
# The v4 pipeline's own prerequisites
# ---------------------------------------------------------------------------


def test_it_confirms_the_agents_role_holds_select_and_nothing_else(run_launch):
    assert "the agent's role holds SELECT and nothing else" in run_launch().output


def test_an_env_pinning_an_older_agent_image_is_called_out(run_launch):
    """The two-command flow would otherwise keep running the previous agent
    after an upgrade, which looks like the new work having no effect.
    """
    result = run_launch(env_file="IMAGE_NAME=x\nAGENT_IMAGE_TAG=v3\n")
    assert "pins the agent image at v3" in result.output
    assert "running the older agent" in result.output


def test_an_agent_tag_chosen_for_this_checkout_is_said_rather_than_warned(run_launch):
    """`setup.sh --agent-tag` on this checkout writes its own release beside
    the tag it was given: a choice, which a warning would call a mistake."""
    shipped = re.search(
        r'^AGENT_TAG="([^"]+)"', (REPO_ROOT / "setup.sh").read_text(), re.MULTILINE
    ).group(1)
    result = run_launch(env_file=f"IMAGE_NAME=x\nAGENT_IMAGE_TAG=v5_3\nSETUP_RELEASE={shipped}\n")
    assert f"the agent is pinned at v5_3, as chosen; this checkout ships {shipped}." in result.output
    assert "pins the agent image" not in result.output


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
    result = run_launch("--api", "--no-auth")
    assert "no API token is set" in result.output
    assert "./setup.sh --tokens makes one, before exposing this off this machine" in result.output


def test_with_sign_in_on_no_token_is_nothing_to_warn_about(run_launch):
    """Every caller is somebody: a session or a service token."""
    assert "no API_TOKEN is set" not in run_launch("--api").output


def test_a_token_in_the_env_silences_that_warning(run_launch):
    result = run_launch("--api", env_file="IMAGE_NAME=x\nAPI_TOKEN=s3cret\n")
    assert "no API_TOKEN is set" not in result.output


def test_the_closing_lines_show_how_to_trust_the_development_ca(run_launch):
    """Every server's certificate is issued by the stack's own CA, which no
    client trusts until it is told to, and copying it out is the one step
    nobody guesses.
    """
    output = run_launch("--api").output
    assert "cp api:/etc/nl2sql/tls/ca.crt ./nl2sql-ca.crt" in output
    assert 'curl --cacert ./nl2sql-ca.crt "https://localhost:8443/v1/meta"' in output


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
    assert "GUI is healthy at https://localhost:8080" in result.output


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
    assert "GUI is healthy at https://localhost:9080" in result.output


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
    assert "open https://localhost:8080" in output
    assert "gui/README.md" in output


def test_the_closing_lines_say_what_the_browser_is_spared(run_launch):
    """The reason the GUI ships with a proxy rather than CORS settings, said
    once where someone deploying it will read it.
    """
    output = " ".join(run_launch("--gui").output.split())
    assert "nginx in that container verifies the API's certificate" in output
    assert "the session you sign in with is what reaches the API" in output


# ---------------------------------------------------------------------------
# The feedback staging database (--feedback) and the review stack (--review)
# ---------------------------------------------------------------------------


def test_the_feedback_stack_is_not_started_unless_it_is_asked_for(run_launch):
    """Most people ask questions and never review anything, and this stack
    opens the port that can rewrite the golden question set."""
    result = run_launch()
    assert not result.called("up -d review")
    assert "Review service" not in result.output


def test_asking_for_feedback_asks_for_the_api_that_writes_to_it(run_launch):
    """A staging database nothing writes to is a container burning memory."""
    result = run_launch("--feedback")
    assert result.called("--profile api up -d api")


def test_feedback_alone_does_not_drag_the_review_stack_in(run_launch):
    result = run_launch("--feedback")
    assert not result.called("up -d review")
    assert not result.called("up -d reviewgui")


def test_the_review_flag_starts_the_service_and_its_interface(run_launch):
    result = run_launch("--review")
    assert result.called("--profile review up -d review")
    assert result.called("--profile review --profile reviewgui up -d reviewgui")
    assert "Review service is healthy at https://localhost:8444" in result.output
    assert "Review interface is healthy at https://localhost:8081" in result.output


def test_asking_for_review_asks_for_everything_under_it(run_launch):
    """The interface is nothing without the service, and the service is
    nothing without the database in front of it."""
    result = run_launch("--review")
    assert result.called("up -d postgres stores")
    assert result.called("--profile api up -d api")


def test_the_review_scheme_follows_the_tls_setting(run_launch):
    result = run_launch("--review", env_file="IMAGE_NAME=x\nREVIEW_TLS_ENABLED=false\n")
    assert "Review service is healthy at http://localhost:8444" in result.output


def test_the_review_ports_follow_what_compose_will_use(run_launch):
    result = run_launch(
        "--review", env_file="IMAGE_NAME=x\nREVIEW_PORT=9444\nREVIEW_GUI_PORT=9081\n"
    )
    assert "https://localhost:9444" in result.output
    assert "https://localhost:9081" in result.output


def test_a_review_service_that_never_comes_up_is_reported(run_launch):
    result = run_launch("--review", env={"FAKE_REVIEW_HEALTH": "starting"}, timeout=240)
    assert "the review service did not become healthy" in result.output
    assert "--profile review logs review" in result.output


def test_a_review_interface_that_never_comes_up_is_reported(run_launch):
    result = run_launch("--review", env={"FAKE_REVIEW_GUI_HEALTH": "starting"}, timeout=240)
    assert "the review interface did not become healthy" in result.output
    assert "--profile review --profile reviewgui logs reviewgui" in result.output


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
    assert "open https://localhost:8081" in output
    assert "review/README.md" in output


def test_the_closing_lines_say_that_promoting_edits_this_checkout(run_launch):
    """The single most surprising thing about the review interface: it
    writes a tracked file, and the reviewer is expected to commit it."""
    output = run_launch("--review").output
    assert "context_questions/translated_questions.md" in output
    assert "git diff" in output


def test_the_closing_lines_name_the_three_fields_a_reviewer_must_write(run_launch):
    """The part of the job no verdict can do for them."""
    output = run_launch("--review").output
    assert "keywords" in output
    assert "reasoning target" in output
    assert "expected result" in output


def test_the_stores_the_review_service_writes_are_up_before_it(run_launch):
    """It creates its schemas in the runtime stores on its own start, so they
    have to be up first -- and since 6.3 they are, with the databases."""
    result = run_launch("--review")
    assert result.index_of("up -d postgres stores") < result.index_of("--profile review up -d review")
    assert not result.called("correctionsdb") and not result.called("feedbackdb")


def test_the_closing_lines_name_the_three_panes_and_where_each_goes(run_launch):
    output = run_launch("--review").output
    assert "one pane per verdict" in output
    for pane in ("Correct ", "Wrong ", "Correct but incomplete "):
        assert pane in output
    assert "validate it against the live retail database" in output
    assert "corrections store" in output and "completions store" in output
    assert "never into the golden set" in output


def test_the_closing_lines_say_a_judgement_can_be_taken_back(run_launch):
    """5.4's Back to pending and Delete, which undo what a judgement made --
    worth a line, because the page shows them only under a reviewed row."""
    output = run_launch("--review").output
    assert "Back to" in output and "pending returns it to the queue" in output
    assert "Delete removes it" in output
    assert "its pair out of the golden set, or its fix out of its store, first" in output


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
    assert "GUI is healthy at https://localhost:8080" in result.output


def test_a_gui_whose_proxy_cannot_reach_the_api_is_restarted(run_launch):
    result = run_launch("--gui", env={"FAKE_PROXY_BROKEN": "8080"})

    assert "cannot reach the API through its proxy" in result.output
    assert result.called("restart gui")
    # And once restarted it is reported as working, not as broken.
    assert "GUI is healthy at https://localhost:8080" in result.output


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
    assert result.called("localhost:8081/readyz")
    assert not result.called("restart reviewgui")


def test_a_review_interface_whose_proxy_is_stale_is_restarted(run_launch):
    result = run_launch("--review", env={"FAKE_PROXY_BROKEN": "8081"})

    assert "cannot reach the API through its proxy" in result.output
    assert result.called("restart reviewgui")
    assert "Review interface is healthy at https://localhost:8081" in result.output


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
    assert "the review interface is up but cannot reach the review service." in result.output
    assert "Review interface is healthy" not in result.output
    assert "docker compose --profile review --profile reviewgui logs reviewgui" in result.output


# ---------------------------------------------------------------------------
# The SQL console (--console)
# ---------------------------------------------------------------------------


def test_the_console_is_not_started_unless_it_is_asked_for(run_launch):
    """It runs SQL. A port nobody asked to be opened should not be."""
    result = run_launch()
    assert not result.called("up -d console")
    assert not result.called("up -d consolegui")


def test_the_console_flag_starts_the_console_and_its_interface(run_launch):
    result = run_launch("--console")

    assert result.returncode == 0
    assert result.called("--profile console up -d console")
    assert result.called("--profile console --profile consolegui up -d consolegui")
    assert "SQL console is healthy at https://localhost:8445" in result.output
    assert "SQL console interface is healthy at https://localhost:8082" in result.output


def test_asking_for_the_console_starts_the_api_first(run_launch):
    """The console presents the certificate the API writes -- and reissues,
    on the first start after an upgrade, to cover the console's name."""
    result = run_launch("--console")
    assert result.index_of("--profile api up -d api") < result.index_of("--profile console up -d console")


def test_the_console_needs_neither_the_gui_nor_the_feedback_system(run_launch):
    result = run_launch("--console")
    assert not result.called("up -d gui")
    assert not result.called("up -d feedbackdb")


def test_the_console_scheme_follows_the_tls_setting(run_launch):
    result = run_launch("--console", env_file="IMAGE_NAME=x\nCONSOLE_TLS_ENABLED=false\n")
    assert "SQL console is healthy at http://localhost:8445" in result.output


def test_the_console_ports_follow_what_compose_will_use(run_launch):
    result = run_launch("--console", env_file="IMAGE_NAME=x\nCONSOLE_PORT=9445\nCONSOLE_GUI_PORT=9082\n")
    assert "https://localhost:9445" in result.output
    assert "SQL console interface is healthy at https://localhost:9082" in result.output
    assert "open https://localhost:9082" in result.output


def test_a_console_that_never_comes_up_is_reported(run_launch):
    result = run_launch("--console", env={"FAKE_CONSOLE_HEALTH": "starting", "FAKE_CONSOLE_RUNNING": "false"})

    assert "the SQL console did not become healthy." in result.output
    assert "Check what it said: docker compose --profile console logs console" in result.output
    assert "SQL console is healthy" not in result.output


def test_an_agent_image_older_than_the_console_is_named_as_the_reason(run_launch):
    """A pinned image from before 5.3 has no `nl2sql_agent.console` in it,
    and the container dies saying so. That is a rebuild, not a bug."""
    result = run_launch("--console", env={
        "FAKE_CONSOLE_RUNNING": "false",
        "FAKE_CONSOLE_HEALTH": "starting",
        "FAKE_API_LOGS": "/usr/local/bin/python: No module named nl2sql_agent.console",
    })

    assert "The pinned agent image has no SQL console in it -- it predates this" in result.output
    assert "docker compose --profile console build console" in result.output


def test_a_console_interface_that_never_comes_up_is_reported(run_launch):
    result = run_launch("--console", env={"FAKE_CONSOLE_GUI_HEALTH": "starting"}, timeout=240)
    assert "the SQL console's interface did not become healthy." in result.output
    assert (
        "Check what it said: docker compose --profile console --profile consolegui logs consolegui"
        in result.output
    )


def test_the_console_interface_is_checked_through_its_proxy(run_launch):
    result = run_launch("--console")
    assert result.called("localhost:8082/readyz")
    assert not result.called("restart consolegui")


def test_a_console_interface_whose_proxy_is_stale_is_restarted(run_launch):
    """The API reissues its certificate the first time it is started with the
    console's name in API_TLS_HOSTNAMES; a proxy that loaded the old one is
    cured by a restart."""
    result = run_launch("--console", env={"FAKE_PROXY_BROKEN": "8082"})

    assert result.called("--profile console --profile consolegui restart consolegui")
    assert result.called("inspect --format {{.State.Health.Status}} nl2sql-console-gui")
    assert "SQL console interface is healthy at https://localhost:8082" in result.output


def test_a_console_proxy_a_restart_does_not_fix_is_reported(run_launch):
    result = run_launch("--console", env={"FAKE_PROXY_DEAD": "8082"})

    assert result.called("restart consolegui")
    assert "the SQL console's interface is up but cannot reach the console." in result.output
    assert "docker compose --profile console --profile consolegui logs consolegui" in result.output
    assert "SQL console interface is healthy" not in result.output


@pytest.mark.parametrize("bind", ["127.0.0.1", "localhost", "::1"])
def test_a_console_on_this_machine_only_needs_no_token(run_launch, bind):
    result = run_launch("--console", env_file=f"IMAGE_NAME=x\nCONSOLE_BIND_ADDRESS={bind}\n")
    assert "with no token" not in result.output


def test_a_console_opened_to_the_network_without_a_token_is_warned_about(run_launch):
    result = run_launch("--console", "--no-auth", env_file="IMAGE_NAME=x\nCONSOLE_BIND_ADDRESS=0.0.0.0\n")
    assert "the SQL console is published on 0.0.0.0 with no token, so" in result.output
    assert "anything that can reach it may run SQL as the agent's database role." in result.output


def test_a_console_opened_to_the_network_with_sign_in_on_is_not(run_launch):
    """Only reviewers and curators get past its sign-in."""
    result = run_launch("--console", env_file="IMAGE_NAME=x\nCONSOLE_BIND_ADDRESS=0.0.0.0\n")
    assert "with no token" not in result.output


def test_a_console_opened_to_the_network_with_a_token_is_not(run_launch):
    result = run_launch(
        "--console", env_file="IMAGE_NAME=x\nCONSOLE_BIND_ADDRESS=0.0.0.0\nCONSOLE_TOKEN=s3cret\n"
    )
    assert "with no token" not in result.output


def test_the_closing_lines_say_where_the_console_is_and_what_it_answers(run_launch):
    output = " ".join(run_launch("--console").output.split())
    assert "open https://localhost:8082" in output
    assert "Run returns the rows, Plan stops at the planner's estimate, Analyze times a real run" in output
    assert "which of its gates would have refused it" in output
    assert "console/README.md" in output


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
    assert "Copied the stack's CA certificate" in result.output
    assert result.calls_matching("cp api:/etc/nl2sql/tls/ca.crt")
    assert (result.workdir / "nl2sql-ca.crt").is_file()
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

    assert "could not copy the stack's CA certificate" in result.output
    assert "--insecure is" in result.output


def test_the_closing_notes_say_how_to_run_it(run_launch):
    result = run_launch("--desktop")

    assert "java -jar desktop/target/nl2sql-desktop.jar --cacert ./nl2sql-ca.crt" in result.output
    assert "Java runtime of 21 or later" in result.output
    # The point of the whole thing: one queue, whichever client was used.
    assert "the same staging table, the same review" in result.output


DESKTOP_PINNED_ENV = (
    "IMAGE_NAME=mcfaddja/nl2sql-retail-postgres\n"
    "IMAGE_TAG=v1\n"
    "AGENT_IMAGE_NAME=mcfaddja/nl2sql-agent\n"
    "AGENT_IMAGE_TAG=v5_5\n"
    "DESKTOP_IMAGE_NAME=mcfaddja/nl2sql-desktop-build\n"
    "DESKTOP_IMAGE_TAG=v5_5\n"
    "RAG_ENABLED=true\n"
)


def test_a_pinned_image_that_is_here_is_copied_from_rather_than_rebuilt(run_launch):
    """`setup.sh --desktop` pulled it. Compose builds a service only when its
    image is missing, so this turns a Maven build into a copy."""
    result = run_launch("--desktop", env_file=DESKTOP_PINNED_ENV,
                        env={"FAKE_UNAME_S": "Darwin", "FAKE_UNAME_M": "arm64",
                             "FAKE_DESKTOP_IMAGE_PRESENT": "1"})

    assert "Taking it from mcfaddja/nl2sql-desktop-build:v5_5-mac-aarch64" in result.output
    assert "Building it for" not in result.output
    # And it never asks a registry: launch.sh is the fast path.
    assert not result.calls_matching("pull ")


def test_a_pinned_image_that_is_not_here_is_built_instead(run_launch):
    """A tag that is not published yet, a machine that never ran setup with
    --desktop, or one that is offline. The jar is still what was asked for."""
    result = run_launch("--desktop", env_file=DESKTOP_PINNED_ENV,
                        env={"FAKE_UNAME_S": "Linux", "FAKE_UNAME_M": "x86_64"})

    assert "Building it for linux" in result.output
    assert (result.workdir / "desktop/target/nl2sql-desktop.jar").is_file()


def test_an_unpinned_checkout_looks_for_a_local_tag(run_launch):
    """`nl2sql-desktop-build:local-linux` has nowhere to be pulled from, which
    is what .env naming no image means."""
    result = run_launch("--desktop", env={"FAKE_UNAME_S": "Linux", "FAKE_UNAME_M": "x86_64"})

    assert result.calls_matching("image inspect nl2sql-desktop-build:local-linux")
    assert "Building it for linux" in result.output



# ---------------------------------------------------------------------------
# Model routing
#
# The agent works its routing table out once at start-up. launch.sh asks the
# same image to work it out the same way, so a table that fell back to one
# model is seen before the first question rather than in a benchmark.
# ---------------------------------------------------------------------------


def test_the_routing_table_is_asked_of_the_agent_image_itself(run_launch):
    result = run_launch()
    # The probe is several lines of Python, so it is several lines of the
    # call log; the command in front of it is one.
    # Nothing it needs is in the databases, so none of them is started for it,
    # and there is no terminal to give it.
    probes = result.calls_matching("compose run --rm --no-deps -T --entrypoint python agent -c")
    assert len(probes) == 1
    assert result.called("build_table(s, catalog")
    assert "Checking model routing" in result.output


def test_a_table_with_several_models_names_them_anchor_first(run_launch):
    routing = "ROUTE on\nROUTE models chat:latest light:latest mid:latest"
    result = run_launch(env={"FAKE_ROUTING": routing})
    assert "model routing: 3 models -- chat:latest (OLLAMA_MODEL), light:latest, mid:latest" in result.output


def test_a_catalog_for_another_host_is_reported_with_how_to_build_one(run_launch):
    """Anyone's host but the maintainer's: the committed catalog is ignored,
    and the note the agent writes says what to run instead."""
    result = run_launch()
    assert "model routing: every call goes to qwen3.8-256k:latest" in result.output
    assert "the catalog describes http://192.168.10.82:11434" in result.output


def test_routing_switched_off_says_so(run_launch):
    result = run_launch(env={"FAKE_ROUTING": "ROUTE off\nROUTE models qwen3.8-256k:latest"})
    assert "model routing is off (MODEL_ROUTING_ENABLED)" in result.output


def test_a_table_that_names_no_models_falls_back_to_the_chat_model(run_launch):
    result = run_launch(env={"FAKE_ROUTING": "ROUTE on"})
    assert "model routing: every call goes to qwen3.8-256k" in result.output


def test_a_catalog_the_agent_cannot_read_is_a_warning_about_the_agent(run_launch):
    """MODEL_CATALOG naming a file that is not there stops the agent at
    start-up. That is worth a warning here, before anyone asks it anything."""
    routing = "ROUTE error MODEL_CATALOG names /app/models/catalog.json, which cannot be read"
    result = run_launch(env={"FAKE_ROUTING": routing})
    assert "the agent will not start with these settings: MODEL_CATALOG names" in result.output


def test_an_agent_image_older_than_routing_says_how_to_get_a_newer_one(run_launch):
    result = run_launch(env={"FAKE_ROUTING_OLD_IMAGE": "1"})
    assert result.returncode == 0
    assert "the pinned agent image predates model routing" in result.output
    assert "./start.sh does it for you" in result.output


def test_a_probe_that_would_not_run_says_what_docker_said(run_launch):
    result = run_launch(env={"FAKE_ROUTING_BROKEN": "no such image"})
    assert result.returncode == 0
    assert "could not ask the agent image which models it will route to" in result.output
    assert "no such image" in result.output


# ---------------------------------------------------------------------------
# MLflow (--mlflow)
# ---------------------------------------------------------------------------

TRACED_ENV_FILE = "IMAGE_NAME=x\nMLFLOW_TRACKING_URI=http://nl2sql-mlflow:5000\n"


def test_mlflow_is_not_started_unless_it_is_asked_for(run_launch):
    result = run_launch()
    assert not result.called("--profile mlflow")
    assert "MLflow" not in result.output


def test_the_mlflow_flag_starts_mlflow_and_says_where_traces_go(run_launch):
    result = run_launch("--mlflow", env_file=TRACED_ENV_FILE)

    assert result.returncode == 0
    assert result.called("--profile mlflow up -d mlflow")
    assert "MLflow is healthy at https://localhost:5001" in result.output
    assert "==> MLflow is up:" in result.output
    assert "open https://localhost:5001" in result.output
    output = " ".join(result.output.split())
    assert "is a trace in the experiment nl2sql-agent: one span per agent and per model call" in output
    assert "docker compose --profile mlflow logs -f mlflow" in output
    assert "WARNING" not in result.output


def test_mlflow_brings_the_api_for_its_front_doors_certificate_but_no_interface(run_launch):
    """A question asked from a terminal is traced too -- but MLflow's front
    door presents the certificate the API writes, and asks the auth service,
    which starts with the API, about every request."""
    result = run_launch("--mlflow", env_file=TRACED_ENV_FILE)
    assert result.called("--profile api up -d api")
    assert result.index_of("--profile api up -d api") < result.index_of("--profile mlflow up -d mlflowproxy")
    assert result.index_of("--profile mlflow up -d mlflow") < result.index_of("--profile mlflow up -d mlflowproxy")
    assert not result.called("up -d gui")


def test_mlflow_whose_front_door_does_not_come_up_is_still_tracing_and_says_so(run_launch):
    result = run_launch("--mlflow", env_file=TRACED_ENV_FILE, env={"FAKE_MLFLOW_PROXY_HEALTH": "unhealthy"})
    output = " ".join(result.output.split())
    assert result.returncode == 0
    assert "MLflow is up, and tracing, but its front door did not become healthy" in output
    assert "docker compose --profile mlflow logs mlflowproxy" in output
    assert "MLflow is healthy at" not in output


def test_mlflow_comes_up_after_the_api_it_does_not_hold_up(run_launch):
    """The agent asks for the server on its first question, so nothing
    waits on it -- and the API is not held back while MLflow's image pulls."""
    result = run_launch("--gui", "--mlflow", env_file=TRACED_ENV_FILE)
    assert result.index_of("--profile api up -d api") < result.index_of("--profile mlflow up -d mlflow")


def test_mlflows_port_and_experiment_follow_what_compose_will_use(run_launch):
    result = run_launch("--mlflow", env_file=TRACED_ENV_FILE + "MLFLOW_PORT=6001\nMLFLOW_EXPERIMENT_NAME=ablations\n")
    assert "MLflow is healthy at https://localhost:6001" in result.output
    assert "open https://localhost:6001" in result.output
    assert "the experiment ablations" in " ".join(result.output.split())


def test_mlflow_without_a_tracking_uri_in_env_is_a_server_nothing_traces_to(run_launch):
    """An .env from before 5.5 has no MLFLOW_TRACKING_URI, and compose
    forwards it empty -- which is tracing off."""
    result = run_launch("--mlflow")
    assert "MLFLOW_TRACKING_URI is not set, so the agent will not trace to it." in result.output
    assert "setup.sh writes one into .env; add it there or export it before starting." in result.output


def test_mlflow_that_never_comes_up_is_reported_and_the_rest_carries_on(run_launch):
    result = run_launch(
        "--mlflow", env_file=TRACED_ENV_FILE,
        env={"FAKE_MLFLOW_HEALTH": "starting", "FAKE_MLFLOW_RUNNING": "false"},
    )
    assert result.returncode == 0
    assert "MLflow did not become healthy, so questions are answered untraced." in result.output
    assert "Check what it said: docker compose --profile mlflow logs mlflow mlflowdb" in result.output
    assert "MLflow is healthy" not in result.output


@pytest.mark.parametrize("bind", ["127.0.0.1", "localhost", "::1"])
def test_mlflow_on_this_machine_only_is_not_warned_about(run_launch, bind):
    result = run_launch("--mlflow", env_file=TRACED_ENV_FILE + f"MLFLOW_BIND_ADDRESS={bind}\n")
    assert "with no login" not in result.output


def test_mlflow_published_beyond_this_machine_with_sign_in_on_is_not_warned_about(run_launch):
    """Its front door lets in reviewers and administrators, and nobody else."""
    result = run_launch("--mlflow", env_file=TRACED_ENV_FILE + "MLFLOW_BIND_ADDRESS=0.0.0.0\n")
    assert "MLflow is published" not in result.output


def test_mlflow_published_beyond_this_machine_is_warned_about(run_launch):
    result = run_launch("--mlflow", "--no-auth", env_file=TRACED_ENV_FILE + "MLFLOW_BIND_ADDRESS=0.0.0.0\n")
    output = " ".join(result.output.split())
    assert "MLflow is published on 0.0.0.0 with no login: anything that can" in result.output
    assert "reach it can read every question, query and result, and delete them." in result.output
    assert "WARNING: MLflow is published" in output



# ---------------------------------------------------------------------------
# The golden pairs: the stores against the document (--load-golden)
# ---------------------------------------------------------------------------
#
# The context store and the vectors are images, published holding the golden
# set as it was then; only a promotion reloads them. So a checkout whose
# document has grown is answered from fewer pairs than it holds until it is
# loaded -- which is what this flag does, and what the check below says.

LOAD = "--profile review run --rm --no-deps -T --user 10001:10001 --entrypoint sh review"


def _golden_document(tmp_path: Path, pairs: int) -> None:
    """A question document holding `pairs` pairs, where launch.sh reads it."""
    folder = tmp_path / "repo" / "context_questions"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "translated_questions.md").write_text(
        "# Golden pairs\n\n## How to read a pair\n\n# Suite 1\n\n"
        + "".join(f"## Q{number:02d} - pair {number}\n\nbody\n\n" for number in range(1, pairs + 1))
    )


def test_load_golden_runs_both_loaders_in_the_review_image_before_the_stores_are_counted(run_launch):
    result = run_launch("--load-golden")

    assert result.returncode == 0
    assert "==> Loading the golden pairs from context_questions/translated_questions.md" in result.output
    assert "48 rows written, 0 stale rows removed" in result.output
    assert "question         -> golden_pair_question_vectors: 0 embedded, 48 already current, 0 removed" in result.output
    assert result.index_of(LOAD) < result.index_of("nl2sql_ops report"), "counted after the load"
    # The script is passed to `sh -c` across several lines, which the call log
    # records as several entries.
    script = "\n".join(result.calls[result.index_of(LOAD):][:6])
    assert '05_load_golden_pairs.py "$REVIEW_DOCUMENT" &&' in script
    assert '06_embed_golden_pairs.py --ollama-url "$OLLAMA_URL"' in script
    assert "DB_URL" not in script, "a store's URL, password and all, is the loader's environment, not its argv"


def test_load_golden_starts_nothing_of_the_review_system(run_launch):
    """One container, run once and removed: --no-deps keeps the staging
    database and the fix stores, which the review service depends on, down."""
    result = run_launch("--load-golden")
    assert not result.called("up -d review")
    assert not result.called("up -d feedbackdb")


def test_without_the_flag_nothing_is_loaded(run_launch):
    assert not run_launch().called(LOAD)


def test_a_load_that_fails_is_a_warning_and_the_stack_comes_up_anyway(run_launch):
    result = run_launch("--load-golden", env={"FAKE_GOLDEN_LOAD_FAILS": "1"})

    assert result.returncode == 0
    assert "the golden pairs did not load completely; the stores keep what they had. It said:" in result.output
    assert "Could not reach Ollama" in result.output
    assert "==> Ready." in result.output


def test_load_golden_with_no_rag_says_there_is_nothing_to_load(run_launch):
    result = run_launch("--load-golden", "--no-rag")

    assert "--load-golden does nothing with --no-rag: there is no context store to load." in result.output
    assert not result.called(LOAD)


# ---------------------------------------------------------------------------
# The SQL snippets (v5.6): the store launch.sh fills from its document
# ---------------------------------------------------------------------------

SNIPPET_LOAD = "07_load_snippets.py"


def test_the_runtime_stores_are_started_and_waited_on_with_the_other_databases(run_launch):
    result = run_launch()
    assert result.called("up -d postgres stores vectordb chunkdb")
    assert result.called("inspect --format {{.State.Health.Status}} nl2sql-stores")
    assert "nl2sql-stores is healthy" in result.output


def test_a_store_that_holds_the_document_is_not_loaded(run_launch):
    result = run_launch(env={"FAKE_SNIPPETS_STATE": "current"})
    assert result.called("compose run --rm --no-deps -T dbprep python -m nl2sql_ops snippets")
    assert not result.called(SNIPPET_LOAD)
    assert "Loading the SQL snippets" not in result.output


def test_a_store_behind_its_document_is_loaded_in_the_review_image(run_launch):
    result = run_launch(env={"FAKE_SNIPPETS_STATE": "behind", "FAKE_SNIPPETS_EMBEDDED": "3"})

    assert result.returncode == 0
    assert "==> Loading the SQL snippets from context_questions/sql_snippets.md" in result.output
    assert "the snippet store is behind the document" in result.output
    assert "32 rows written, 0 stale rows removed" in result.output
    assert "vectors -> sql_snippet_vectors: 3 embedded, 29 already current" in result.output
    assert result.called(LOAD)
    script = "\n".join(result.calls[result.index_of(SNIPPET_LOAD) - 2:][:4])
    assert '07_load_snippets.py "$REVIEW_SNIPPETS_DOCUMENT"' in script
    assert "DB_URL" not in script, "a store's URL, password and all, is the loader's environment, not its argv"
    # Reported after the load had its chance, so the count is what the agent sees.
    assert result.index_of(SNIPPET_LOAD) < result.index_of("nl2sql_ops report")


def test_a_store_that_cannot_be_compared_loads_nothing(run_launch):
    """`unknown` -- no store to ask, or no document -- must not read as
    behind and reload on every start."""
    result = run_launch(env={"FAKE_SNIPPETS_STATE": "unknown"})
    assert not result.called(SNIPPET_LOAD)


def test_a_snippet_load_the_embedding_host_stopped_is_a_warning(run_launch):
    result = run_launch(env={"FAKE_SNIPPETS_STATE": "behind", "FAKE_SNIPPETS_LOAD_FAILS": "1"})

    assert result.returncode == 0
    assert (
        "the SQL snippets did not load completely; the agent is shown what the store holds. It said:"
        in result.output
    )
    assert "the rows are loaded and searchable by keyword" in result.output
    assert "==> Ready." in result.output


def test_a_review_image_from_before_snippets_is_named_as_the_reason(run_launch):
    result = run_launch(env={"FAKE_SNIPPETS_STATE": "behind", "FAKE_SNIPPETS_OLD_IMAGE": "1"})
    assert "the pinned review image predates SQL snippets, so it cannot load them." in result.output
    assert "./setup.sh pulls the image this checkout ships" in result.output


def test_without_the_document_nothing_is_compared_or_loaded(run_launch, tmp_path):
    (tmp_path / "repo" / "context_questions" / "sql_snippets.md").unlink()
    result = run_launch(env={"FAKE_SNIPPETS_STATE": "behind"})
    assert not result.called("nl2sql_ops snippets")
    assert not result.called(SNIPPET_LOAD)


def test_no_rag_starts_no_snippet_store_and_loads_nothing(run_launch):
    result = run_launch("--no-rag", env={"FAKE_SNIPPETS_STATE": "behind"})
    assert not result.called("nl2sql_ops snippets")
    assert not result.called(SNIPPET_LOAD)


def test_runtime_stores_that_never_come_up_stop_the_start(run_launch):
    result = run_launch(env={"FAKE_STORES_HEALTH": "starting"}, timeout=240)
    assert result.returncode != 0
    assert "nl2sql-stores did not become healthy" in result.output


# ---------------------------------------------------------------------------
# The curation interface (--curate)
# ---------------------------------------------------------------------------


def test_curate_starts_the_review_service_and_its_own_page_but_not_the_review_page(run_launch):
    result = run_launch("--curate")
    assert result.called("up -d postgres stores")
    assert result.called("--profile review up -d review")
    assert result.called("--profile review --profile curategui up -d curategui")
    assert not result.called("up -d reviewgui")
    assert "Curation interface is healthy at https://localhost:8083" in result.output


def test_curate_asks_for_the_api_whose_certificate_the_service_presents(run_launch):
    assert run_launch("--curate").called("--profile api up -d api")


def test_the_curation_page_is_not_started_unless_asked_for(run_launch):
    assert not run_launch("--review").called("up -d curategui")


def test_review_and_curate_together_start_one_service_and_both_pages(run_launch):
    result = run_launch("--review", "--curate")
    assert sum("up -d review" in call and "reviewgui" not in call for call in result.calls) == 1
    assert result.called("up -d reviewgui") and result.called("up -d curategui")


def test_the_curation_port_follows_what_compose_will_use(run_launch):
    result = run_launch("--curate", env_file="IMAGE_NAME=x\nCURATE_GUI_PORT=9083\n")
    assert "Curation interface is healthy at https://localhost:9083" in result.output
    assert result.called("localhost:9083/readyz")


def test_the_closing_lines_say_where_to_curate_and_what_is_written(run_launch):
    output = run_launch("--curate").output
    assert "==> The curation interface is up:" in output
    assert "open https://localhost:8083" in output
    assert "context_questions/sql_snippets.md" in output and "git diff" in output
    assert "curate/README.md" in output


def test_a_curation_page_that_never_comes_up_is_reported(run_launch):
    result = run_launch(
        "--curate", env={"FAKE_CURATE_GUI_HEALTH": "starting", "FAKE_CURATE_GUI_RUNNING": "false"}, timeout=60
    )
    assert "the curation interface did not become healthy." in result.output
    assert (
        "docker compose --profile review --profile curategui logs curategui"
        in result.output
    )


def test_a_curation_page_whose_proxy_is_stale_is_restarted(run_launch):
    result = run_launch("--curate", env={"FAKE_PROXY_BROKEN": "8083"})
    assert result.called("restart curategui")
    assert "Curation interface is healthy at https://localhost:8083" in result.output


def test_a_curation_proxy_a_restart_does_not_fix_is_reported(run_launch):
    result = run_launch("--curate", env={"FAKE_PROXY_DEAD": "8083"})
    assert "the curation interface is up but cannot reach the review service." in result.output
    assert "Curation interface is healthy" not in result.output


# ---------------------------------------------------------------------------
# Sign-in
# ---------------------------------------------------------------------------

SIGNIN_SECRETS = ("ldap_admin_password", "ldap_service_password", "auth_rolesync_password")


def test_with_the_api_the_directory_and_auth_service_start_after_it(run_launch):
    result = run_launch("--api")
    assert result.returncode == 0
    # The database's half -- the group roles, the sync's login, the pg_hba
    # lines -- is the dbprep service's, which the auth service waits for.
    assert result.index_of("compose run --rm --no-deps -T dbprep") < result.index_of("--profile api up -d api")
    assert result.index_of("--profile api up -d api") < result.index_of("--profile api --profile auth up -d auth")
    assert result.called("inspect --format {{.State.Health.Status}} nl2sql-ldap")
    assert result.called("inspect --format {{.State.Health.Status}} nl2sql-auth")
    assert result.called("--profile api --profile auth --profile directorygui up -d directorygui")
    assert "Auth service is healthy at https://localhost:8446, with a standalone directory" in result.output
    assert "Directory page is healthy at https://localhost:8084" in result.output


def test_compose_hands_the_one_shot_every_setting_it_reads(run_launch):
    """The fake `docker` takes whatever it is given, so a setting the
    one-shot needs and is never handed looked fine here -- 6.0.0's launch.sh
    left out the directory's host the same way, and nobody could sign in.
    Every name nl2sql_ops.settings reads is in the dbprep service's
    environment, as itself or as the secret file beside it."""
    import yaml

    read = set(re.findall(r'(?:env_str|env_int|env_bool|secret)\("([A-Z_]+)"',
                          (REPO_ROOT / "common" / "nl2sql_ops" / "settings.py").read_text()))
    assert {"POSTGRES_READER_PASSWORD", "AUTH_ROLESYNC_PASSWORD", "LDAP_BASE_DN", "AUTH_ENABLED"} <= read
    given = set(yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())["services"]["dbprep"]["environment"])
    # Fixed in the image or for tests alone: the sockets' and documents' paths,
    # and the directory's host and port and how it is reached -- the name its
    # certificate is issued for, nl2sql-ldap, on 389 with StartTLS.
    fixed = {"NL2SQL_SOCKETS_DIR", "NL2SQL_GOLDEN_DOCUMENT", "NL2SQL_SNIPPETS_DOCUMENT",
             "NL2SQL_LDAP_HOST", "NL2SQL_LDAP_PORT", "NL2SQL_LDAP_TLS"}
    missing = {name for name in read - fixed if name not in given and f"{name}_FILE" not in given}
    assert missing == set(), f"compose does not hand dbprep {sorted(missing)}"


def test_the_closing_lines_say_who_signs_in_first_and_where_people_are_added(run_launch):
    output = " ".join(run_launch("--gui").output.split())
    assert "==> Sign-in is on. Every page asks who you are. The first person is admin," in output
    assert "cat secrets/ldap_admin_password" in output
    assert "nl2sql-users ask questions, nl2sql-reviewers review and read MLflow" in output
    assert "open https://localhost:8084" in output
    assert "LDAP_SEED_FILE in .env, with ldap/seed/people.example.csv as the shape" in output
    # And the REST API's example signs in first.
    assert "https://localhost:8446/auth/token" in output
    assert '-H "Authorization: Bearer $TOKEN"' in output


def test_a_terminal_only_start_has_nobody_to_sign_in_and_leaves_pg_hba_alone(run_launch):
    result = run_launch()
    assert not result.called("--profile auth")
    assert "Sign-in is" not in result.output


def test_a_checkout_from_before_secrets_is_given_its_passwords_once_in_secrets(run_launch):
    """6.3 (V6-38): every password a file in secrets/, which only its owner
    can open, the files in it readable to the containers' own accounts."""
    result = run_launch("--api")
    secrets = result.secrets()
    for name in SIGNIN_SECRETS:
        assert re.fullmatch(r"[0-9a-f]{48}", secrets[name]), name
    assert len({secrets[name] for name in SIGNIN_SECRETS}) == 3
    assert "Generated 15 password(s) into secrets/, which only you can open" in result.output
    assert (result.workdir / "secrets").stat().st_mode & 0o777 == 0o700
    assert (result.workdir / ".env").stat().st_mode & 0o777 == 0o600


def test_a_password_in_env_is_moved_into_its_file_and_out_of_env_and_never_replaced(run_launch):
    """A store keeps the password it was given, so a .env's value from before
    6.3 is the one the file must hold -- and the file is then the one copy."""
    kept = "".join(f"{name.upper()}=kept-{index}\n" for index, name in enumerate(SIGNIN_SECRETS))
    result = run_launch("--api", env_file="IMAGE_NAME=x\n" + kept + "API_TOKEN=tok\n")
    assert [result.secrets()[name] for name in SIGNIN_SECRETS] == ["kept-0", "kept-1", "kept-2"]
    assert result.secrets()["api_token"] == "tok"
    assert not [key for key in result.env_file() if key.endswith(("_PASSWORD", "_TOKEN"))]
    again = run_launch("--api", env_file="IMAGE_NAME=x\n")
    assert [again.secrets()[name] for name in SIGNIN_SECRETS] == ["kept-0", "kept-1", "kept-2"]


@pytest.mark.parametrize("how", ["flag", "env"])
def test_with_sign_in_off_nothing_is_started_and_pg_hba_is_put_back(run_launch, how):
    if how == "flag":
        result = run_launch("--gui", "--no-auth")
    else:
        result = run_launch("--gui", env_file="IMAGE_NAME=x\nAUTH_ENABLED=false\n")
    assert result.returncode == 0
    assert not result.called("--profile auth")
    assert "==> Sign-in is off (AUTH_ENABLED=false)" in result.output
    assert "every page and port is open to whoever can reach it" in result.output
    assert "Sign-in is on" not in result.output


def test_no_auth_reaches_every_service_compose_starts(run_launch):
    """Exported, because compose reads the shell before .env: a service the
    script never asks about is still told sign-in is off."""
    result = run_launch("--gui", "--no-auth")
    told = (result.workdir.parent / "auth-enabled").read_text().split()
    assert told and set(told) == {"false"}


def test_a_replica_has_no_directory_page_and_says_where_people_are_edited(run_launch):
    result = run_launch("--api", env_file="IMAGE_NAME=x\nLDAP_MODE=replica\nLDAP_UPSTREAM_URI=ldaps://ad.example.com:636\n")
    assert "Auth service is healthy at https://localhost:8446, with a replica directory" in result.output
    assert "The directory copies ldaps://ad.example.com:636: people are added and changed there." in result.output
    assert not result.called("up -d directorygui")
    assert "open https://localhost:8084" not in result.output


def test_a_plain_auth_service_is_said_to_be_one(run_launch):
    result = run_launch("--api", env_file="IMAGE_NAME=x\nAUTH_TLS_ENABLED=false\n")
    assert "Auth service is healthy at http://localhost:8446" in result.output


@pytest.mark.parametrize("failing", ["FAKE_LDAP_HEALTH", "FAKE_AUTH_HEALTH"])
def test_a_directory_or_auth_service_that_does_not_come_up_is_reported_and_the_rest_carries_on(run_launch, failing):
    result = run_launch("--gui", env={failing: "unhealthy", "FAKE_AUTH_RUNNING": "false"})
    output = " ".join(result.output.split())
    assert result.returncode == 0
    assert "the directory or the auth service did not become healthy, so nobody can sign in." in output
    assert "Check what they said: docker compose --profile api --profile auth logs ldap auth" in output
    assert "GUI is healthy at https://localhost:8080" in output
    assert not result.called("up -d directorygui")


def test_a_directory_page_that_does_not_come_up_is_reported(run_launch):
    result = run_launch("--api", env={"FAKE_DIRECTORY_GUI_HEALTH": "unhealthy"})
    output = " ".join(result.output.split())
    assert "the directory page did not become healthy." in output
    assert "docker compose --profile api --profile auth --profile directorygui logs directorygui" in output
    assert "open https://localhost:8084" not in output


def test_plain_pages_are_probed_and_named_over_http(run_launch):
    result = run_launch("--gui", env_file="IMAGE_NAME=x\nGUI_TLS_ENABLED=false\n")
    assert "GUI is healthy at http://localhost:8080" in result.output
    assert result.called("curl readyz -s -k -o /dev/null -w %{http_code} --max-time 10 http://localhost:8080/readyz")


# ---------------------------------------------------------------------------
# Every password a file in secrets/ (6.1 V6-08, 6.3 V6-38)
# ---------------------------------------------------------------------------

STORE_SECRETS = (
    "postgres_password", "postgres_reader_password", "context_db_password", "vector_db_password",
    "stores_db_password", "snippets_db_password", "snippets_reader_password", "feedback_db_password",
    "feedback_writer_password", "corrections_db_password", "completions_db_password", "mlflow_db_password",
)


def test_every_store_password_is_generated_before_anything_starts(run_launch):
    """V6-08. A checkout from before 6.1 has none, and compose would fall
    back to the password every copy of this repository shares."""
    result = run_launch()
    secrets = result.secrets()
    assert all(re.fullmatch(r"[0-9a-f]{48}", secrets[name]) for name in STORE_SECRETS)
    assert len({secrets[name] for name in STORE_SECRETS}) == len(STORE_SECRETS)
    assert result.output.index("password(s) into secrets/") < result.output.index("==> Starting 4 service(s)")


def test_store_passwords_are_never_replaced_and_said_only_when_made(run_launch):
    first = run_launch().secrets()
    again = run_launch()
    assert [again.secrets()[name] for name in STORE_SECRETS] == [first[name] for name in STORE_SECRETS]
    assert "password(s) into secrets/" not in again.output


def test_the_optional_secrets_are_empty_files_until_someone_makes_them(run_launch):
    secrets = run_launch().secrets()
    for name in ("api_token", "review_token", "console_token", "ldap_upstream_bind_password"):
        assert secrets[name] == "", name


def test_mlflows_store_is_given_its_password_by_the_server_s_dependency_on_the_one_shot(run_launch):
    """No password is set by this script: the server waits for dbprep, which
    sets its store's from the secret (docker-compose.yml)."""
    result = run_launch("--mlflow")
    assert result.index_of("--profile mlflow up -d mlflowdb") < next(
        i for i, call in enumerate(result.calls) if call.endswith("--profile mlflow up -d mlflow"))
    assert not result.calls_matching("NL2SQL_PASSWORD")


# ---------------------------------------------------------------------------
# Published beyond this machine (V6-06)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bind", [None, "127.0.0.1", "localhost", "::1"])
def test_stores_on_this_machine_say_nothing(run_launch, bind):
    assert "DB_BIND_ADDRESS" not in run_launch(env={"DB_BIND_ADDRESS": bind} if bind else None).output


def test_an_address_that_only_looks_like_this_machine_is_said(run_launch):
    assert "published on 127.0.0.10 (DB_BIND_ADDRESS)" in run_launch(env={"DB_BIND_ADDRESS": "127.0.0.10"}).output


def test_stores_published_wider_are_said_and_so_is_every_default_password(run_launch):
    result = run_launch(
        env={"DB_BIND_ADDRESS": "0.0.0.0"},
        env_file="RAG_ENABLED=true\nPOSTGRES_PASSWORD=nl2sql\nFEEDBACK_DB_PASSWORD=feedback\n",
    )
    assert "the databases are published on 0.0.0.0 (DB_BIND_ADDRESS), not only on this machine." in result.output
    assert "secrets/postgres_password is still the default every copy of this repository knows." in result.output
    assert "secrets/feedback_db_password is still the default" in result.output
    assert "secrets/vector_db_password is still the default" not in result.output, "a generated one is not"


# ---------------------------------------------------------------------------
# Another instance beside this one (V6-67)
# ---------------------------------------------------------------------------

#: The default .env, as another stack's: the acceptance tier's runs beside
#: the usual one under a name of its own.
ANOTHER = (
    "IMAGE_NAME=mcfaddja/nl2sql-retail-postgres\nIMAGE_TAG=v1\n"
    "AGENT_IMAGE_NAME=mcfaddja/nl2sql-agent\nAGENT_IMAGE_TAG=v6_1\nNL2SQL_INSTANCE=nl2sql-accept\n"
)


def test_another_instance_waits_on_its_own_containers(run_launch):
    result = run_launch("--api", "--gui", env_file=ANOTHER)
    assert result.returncode == 0, result.output
    for name in ("postgres", "api", "ldap", "auth", "directory-gui", "gui"):
        assert result.called(f"{{{{.State.Health.Status}}}} nl2sql-accept-{name}"), name
    assert not [call for call in result.calls if call.rstrip().endswith(" nl2sql-postgres")], (
        "it asked about the other stack's database"
    )
    # Said by the names everyone knows them by.
    assert "nl2sql-postgres is healthy" in result.output


def test_the_shell_names_the_instance_over_dotenv(run_launch):
    result = run_launch(env={"NL2SQL_INSTANCE": "from-the-shell"}, env_file=ANOTHER)
    assert result.called("{{.State.Health.Status}} from-the-shell-postgres")


def test_a_database_that_never_comes_up_is_named_as_compose_knows_it(run_launch):
    result = run_launch(env={"FAKE_PG_HEALTH": "starting"}, env_file=ANOTHER)
    assert result.returncode != 0
    assert "nl2sql-postgres did not become healthy" in result.output
    assert "docker compose logs postgres" in result.output


# ---------------------------------------------------------------------------
# The runtime stores from before 6.3 (V6-40)
# ---------------------------------------------------------------------------


def test_no_old_store_volume_is_nothing_to_move(run_launch):
    result = run_launch()
    assert not result.called("storesmigrate")
    assert "from before 6.3" not in result.output


def test_each_old_store_is_moved_into_its_database_and_its_volume_kept(run_launch):
    result = run_launch(env={"FAKE_LEGACY_VOLUMES": "feedback completions snippets"})
    assert result.returncode == 0
    moves = result.calls_matching("storesmigrate")
    assert len(moves) == 2, "the snippets are loaded from their document, not moved"
    assert "--profile migrate run --rm --no-deps -T -v nl2sql_feedbackdata:/legacy:ro" in moves[0]
    assert "-e NL2SQL_DB=nl2sql_feedback -e NL2SQL_OWNER=feedback -e NL2SQL_VOLUME=nl2sql_feedbackdata" in moves[0]
    assert "moved nl2sql_feedback from nl2sql_feedbackdata into the runtime stores" in result.output
    assert "moved nl2sql_completions" in result.output
    assert ("the stores' volumes from before 6.3 are kept; once you have looked, docker volume rm "
            "nl2sql_feedbackdata nl2sql_completionsdata nl2sql_snippetsdata") in result.output
    # After the databases are made, before anything that writes them starts.
    assert result.index_of("compose run --rm --no-deps -T dbprep") < result.index_of("storesmigrate")


def test_a_store_moved_already_says_nothing_but_that_its_volume_is_kept(run_launch):
    result = run_launch(env={"FAKE_LEGACY_VOLUMES": "corrections", "FAKE_MIGRATED": "corrections"})
    assert "moved nl2sql_corrections" not in result.output
    assert "docker volume rm nl2sql_correctionsdata" in result.output


def test_a_move_that_fails_is_warned_about_and_the_rest_goes_on(run_launch):
    result = run_launch(env={"FAKE_LEGACY_VOLUMES": "feedback corrections", "FAKE_MIGRATE_FAILS": "feedback"})
    assert result.returncode == 0
    assert "the feedback store from before 6.3 was not moved into the runtime stores. It said:" in result.output
    assert "already has 2 tables, so it is not merged into it." in result.output
    assert "moved nl2sql_corrections" in result.output


def test_another_instance_moves_its_own_old_stores(run_launch):
    result = run_launch(env={"FAKE_LEGACY_VOLUMES": "feedback", "NL2SQL_INSTANCE": "nl2sql-accept"})
    assert result.called("volume inspect nl2sql-accept_feedbackdata")


# ---------------------------------------------------------------------------
# The stack's CA, where a client was told to trust it
# ---------------------------------------------------------------------------


def test_a_ca_copy_that_still_matches_is_left_alone(run_launch, tmp_path):
    (tmp_path / "repo" / "nl2sql-ca.crt").write_text("not really a certificate\n")
    result = run_launch("--api")
    assert result.called("cp api:/etc/nl2sql/tls/ca.crt ./nl2sql-ca.crt.new")
    assert "the stack's CA is a new one" not in result.output
    assert not (tmp_path / "repo" / "nl2sql-ca.crt.new").exists()


def test_a_ca_made_again_since_replaces_the_copy_and_says_to_trust_it_again(run_launch, tmp_path):
    """The daemon restart that took the CA's volume left a copy that verified
    nothing, and a plain start said nothing about it (found 2026-10-05)."""
    (tmp_path / "repo" / "nl2sql-ca.crt").write_text("the old CA\n")
    result = run_launch("--api", env={"FAKE_CA": "the new CA"})
    assert (tmp_path / "repo" / "nl2sql-ca.crt").read_text() == "the new CA\n"
    assert "the stack's CA is a new one, so ./nl2sql-ca.crt was replaced with it." in result.output
    assert "Trust it again wherever you trusted the old one" in result.output


def test_no_copy_is_made_where_nobody_asked_for_one(run_launch, tmp_path):
    result = run_launch("--api")
    assert not result.called("nl2sql-ca.crt.new")
    assert not (tmp_path / "repo" / "nl2sql-ca.crt").exists()
