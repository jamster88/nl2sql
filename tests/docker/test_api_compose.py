"""The REST API as compose resolves it, plus the outside client that tests it.

Grouped with the other compose tests and behind `--run-docker` for the same
reason: `docker compose config` only parses and resolves, but it needs a
working `docker` CLI, and a clone without one should still pass the offline
suite.

Two properties carry the weight here. The API service has to be given the
same pipeline configuration as the `agent` service -- a GUI asking a question
must get the same answer the CLI would -- and the `apitest` service has to
have nothing of this project in it, or it is not testing an API.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.docker

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
AGENT_DIR = REPO_ROOT / "agent"


def _compose_config(tmp_path: Path, *, env: dict | None = None) -> dict:
    empty_env_file = tmp_path / "empty.env"
    empty_env_file.write_text("")
    cmd = [
        "docker", "compose", "--env-file", str(empty_env_file),
        "--profile", "api", "--profile", "agent", "config", "--format", "json",
    ]
    substitutable = set(
        re.findall(r"\$\{([A-Z_][A-Z0-9_]*)", (REPO_ROOT / "docker-compose.yml").read_text())
    )
    run_env = {k: v for k, v in os.environ.items() if k not in substitutable}
    if env:
        run_env.update(env)
    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=30, env=run_env)
    if result.returncode != 0:
        pytest.fail(f"`docker compose config` failed:\n{result.stderr}")
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def config(tmp_path_factory) -> dict:
    return _compose_config(tmp_path_factory.mktemp("compose"))


@pytest.fixture(scope="module")
def api(config: dict) -> dict:
    return config["services"]["api"]


# ---------------------------------------------------------------------------
# The API service
# ---------------------------------------------------------------------------


def test_the_api_is_hidden_unless_it_is_asked_for(tmp_path_factory):
    """A port nobody asked to have opened should not be opened. `up` starts
    the databases; the API is a profile.
    """
    empty = tmp_path_factory.mktemp("compose") / "empty.env"
    empty.write_text("")
    result = subprocess.run(
        ["docker", "compose", "--env-file", str(empty), "config", "--format", "json"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
    )
    assert "api" not in json.loads(result.stdout)["services"]


def test_the_api_runs_the_same_image_as_the_agent(api: dict, config: dict):
    """One image, two entrypoints: the thing serving a GUI is the thing that
    was benchmarked, not a second build that can drift from it.
    """
    assert api["image"] == config["services"]["agent"]["image"]
    assert api["build"]["dockerfile"] == "agent/Dockerfile"
    assert api["command"] == ["python", "-m", "nl2sql_agent.api"]


def test_the_api_gets_the_agents_whole_pipeline_configuration(api: dict, config: dict):
    """A question asked over HTTP must be answered by the same pipeline as
    one asked in a terminal. The compose anchor is what guarantees it; this
    is what catches the anchor being dropped.
    """
    agent_env = config["services"]["agent"]["environment"]
    for name, value in agent_env.items():
        assert api["environment"].get(name) == value, f"the API is missing {name}"


def test_the_api_reads_the_database_as_the_reader_role_like_everything_else(api: dict):
    assert "nl2sql_reader:nl2sql_reader@postgres:5432" in api["environment"]["DATABASE_URL"]
    assert "nl2sql:nl2sql@" not in api["environment"]["DATABASE_URL"]


def test_the_api_waits_for_every_database(api: dict):
    assert set(api["depends_on"]) == {"postgres", "vectordb", "chunkdb"}
    assert all(d["condition"] == "service_healthy" for d in api["depends_on"].values())


def test_tls_is_on_and_the_certificate_is_generated_by_default(api: dict):
    env = api["environment"]
    assert env["API_TLS_ENABLED"] == "true"
    assert env["API_TLS_GENERATE"] == "true"
    assert env["API_TLS_ALLOW_SELF_SIGNED"] == "true"
    assert env["API_TLS_CERT_FILE"] == "/etc/nl2sql/tls/server.crt"


def test_the_generated_certificate_names_the_service_other_containers_reach(api: dict):
    """`apitest` connects to https://nl2sql-api:8443, so a certificate that
    does not carry that name fails verification for every container client.
    """
    assert "nl2sql-api" in api["environment"]["API_TLS_HOSTNAMES"]
    assert api["container_name"] == "nl2sql-api"


def test_the_certificate_survives_a_restart(api: dict, config: dict):
    """A regenerated certificate is a new fingerprint, and every client that
    pinned the old one stops working on a `docker compose up`.
    """
    mounts = {volume["target"]: volume["source"] for volume in api["volumes"]}
    assert mounts["/etc/nl2sql/tls"] == "apitls"
    assert "apitls" in config["volumes"]


def test_the_published_port_follows_the_configured_one(tmp_path_factory):
    """Publishing 8443 while the server listens on 9000 is a container that
    is up and unreachable.
    """
    config = _compose_config(tmp_path_factory.mktemp("compose"), env={"API_PORT": "9443"})
    api = config["services"]["api"]
    [port] = api["ports"]
    assert port["target"] == 9443 and port["published"] == "9443"
    assert api["environment"]["API_PORT"] == "9443"


def test_the_healthcheck_never_needs_a_token(api: dict):
    """/healthz is the one route that answers unauthenticated, which is what
    lets `depends_on: service_healthy` work once API_TOKEN is set.
    """
    test = " ".join(api["healthcheck"]["test"])
    assert "/healthz" in test
    assert "API_TOKEN" not in test


def test_every_api_setting_the_server_reads_can_be_set_through_compose(api: dict):
    """The mirror of the agent's own test. A knob documented but never
    forwarded cannot be set on the container, which is how everyone runs it.
    """
    source = (AGENT_DIR / "nl2sql_agent" / "api" / "settings.py").read_text()
    read = set(re.findall(r'_env(?:_str|_bool|_int|_float|_tuple)?\(\s*"([A-Z_]+)"', source))
    assert read, "no environment variables found in api/settings.py -- the regex needs updating"
    missing = sorted(read - set(api["environment"]))
    assert missing == [], f"api/settings.py reads these, but compose never passes them: {missing}"


def test_every_api_variable_compose_sets_is_one_the_server_reads(api: dict):
    source = (AGENT_DIR / "nl2sql_agent" / "api" / "settings.py").read_text()
    for name in api["environment"]:
        if not name.startswith("API_"):
            continue  # the pipeline's own settings, checked against config.py
        assert f'"{name}"' in source, f"compose sets {name}, but the server never reads it"


def test_an_unset_api_setting_arrives_empty_so_the_default_stands(api: dict):
    for name in ("API_TOKEN", "API_MAX_CONCURRENCY", "API_CORS_ORIGINS"):
        assert api["environment"][name] == "", f"{name} is pinned in compose rather than forwarded"


def test_a_token_and_an_origin_set_on_the_host_reach_the_container(tmp_path_factory):
    config = _compose_config(
        tmp_path_factory.mktemp("compose"),
        env={"API_TOKEN": "s3cret", "API_CORS_ORIGINS": "https://gui.example.com"},
    )
    env = config["services"]["api"]["environment"]
    assert env["API_TOKEN"] == "s3cret"
    assert env["API_CORS_ORIGINS"] == "https://gui.example.com"


def test_refusing_the_development_certificate_is_one_variable(tmp_path_factory):
    """The switch this work exists to provide, reachable without a rebuild."""
    config = _compose_config(
        tmp_path_factory.mktemp("compose"), env={"API_TLS_ALLOW_SELF_SIGNED": "false"}
    )
    assert config["services"]["api"]["environment"]["API_TLS_ALLOW_SELF_SIGNED"] == "false"


# ---------------------------------------------------------------------------
# The outside client
# ---------------------------------------------------------------------------


def test_the_test_client_shares_nothing_with_the_agent(config: dict):
    """If the smoke test needed this project installed, it would not be
    testing an API. It is a curl image on the same network.
    """
    apitest = config["services"]["apitest"]
    assert apitest["build"]["dockerfile"] == "docker/apitest/Dockerfile"
    assert apitest["image"] != config["services"]["api"]["image"]
    dockerfile = (REPO_ROOT / "docker" / "apitest" / "Dockerfile").read_text()
    assert "COPY agent/" not in dockerfile, "it copies the agent package in"
    assert "requirements" not in dockerfile and "pip install" not in dockerfile
    assert not re.search(r"^FROM .*python", dockerfile, re.MULTILINE), (
        "it shares a runtime with the thing it is testing"
    )


def test_the_test_client_waits_for_the_api_to_be_healthy(config: dict):
    apitest = config["services"]["apitest"]
    assert apitest["depends_on"]["api"]["condition"] == "service_healthy"


def test_the_test_client_reaches_the_api_by_its_service_name_over_tls(config: dict):
    env = config["services"]["apitest"]["environment"]
    assert env["API_BASE_URL"] == "https://nl2sql-api:8443"


def test_the_test_client_can_read_the_certificate_it_has_to_trust(config: dict):
    """Better than --insecure even in development: it still proves the
    connection reached the server holding that key.
    """
    [mount] = [v for v in config["services"]["apitest"]["volumes"] if v["source"] == "apitls"]
    assert mount["target"] == "/etc/nl2sql/tls"
    assert mount.get("read_only") is True


def test_the_test_client_is_given_the_same_token_as_the_server(tmp_path_factory):
    config = _compose_config(tmp_path_factory.mktemp("compose"), env={"API_TOKEN": "s3cret"})
    assert config["services"]["apitest"]["environment"]["API_TOKEN"] == "s3cret"


# ---------------------------------------------------------------------------
# The smoke script itself
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def smoke_sh() -> str:
    return (REPO_ROOT / "docker" / "apitest" / "smoke.sh").read_text()


def test_the_smoke_script_is_syntactically_valid():
    result = subprocess.run(
        ["bash", "-n", str(REPO_ROOT / "docker" / "apitest" / "smoke.sh")],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_the_smoke_script_exercises_every_route_the_api_serves(smoke_sh: str):
    """A route nothing calls from outside has never been proved reachable
    from outside, which is the only claim this container makes.
    """
    for path in ("/healthz", "/readyz", "/openapi.json", "/v1/meta", "/v1/questions"):
        assert path in smoke_sh, f"the smoke test never calls {path}"
    assert "/events" in smoke_sh
    assert "DELETE" in smoke_sh


def test_the_smoke_script_uses_nothing_but_curl_and_jq(smoke_sh: str):
    """The reason it is credible as an outside client, and the reason a GUI
    author can copy from it line for line.
    """
    assert "python" not in smoke_sh
    assert "nl2sql_agent" not in smoke_sh


def test_the_smoke_script_verifies_the_certificate_when_it_can(smoke_sh: str):
    """--insecure is the last resort, not the first: a test that never
    verifies would pass against a server presenting anything at all.
    """
    assert smoke_sh.index("CURL_TLS=(--cacert") < smoke_sh.index("CURL_TLS=(--insecure)"), (
        "--insecure is reached for before the certificate is tried"
    )


def test_the_smoke_script_tells_an_unreachable_api_apart_from_a_failing_one(smoke_sh: str):
    """Exit 2 is retryable (the API was never there), exit 1 is not (it was
    there and wrong). A CI job needs to know which.
    """
    assert "exit 2" in smoke_sh
    assert "exit 1" in smoke_sh
