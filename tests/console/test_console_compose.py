"""The SQL console as compose resolves it.

Behind `--run-docker` like the other compose tests: `docker compose config`
only parses and resolves, but it needs a working `docker` CLI.

What carries weight here is what no single file shows:

* **The console sees what the agent sees.** Its database URL is the agent's
  own -- the same role, the same host -- and so are the limits it runs every
  query under. A console on different settings would reproduce nothing.
* **It holds nothing else.** One credential, the reader's, on one database.
* **It presents the API's certificate.** `API_TLS_HOSTNAMES` has to cover
  `nl2sql-console`, and the interface's proxy has to verify a name it covers.
* **Its ports are this machine's.** Every other port in the file is opened
  the way Docker opens ports; a page that runs SQL is published on loopback
  unless someone says otherwise.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from nl2sql_agent.config import Settings
from nl2sql_agent.console.settings import AGENT_SETTINGS, SERVICE_HOSTNAME

pytestmark = pytest.mark.docker

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: Worked out by the start-up script -- the sign-in hop's TLS include and this
#: listener's -- rather than set by anyone.
SIGN_IN_COMPUTED = {"NGINX_AUTH_TLS_CONF", "NGINX_SERVER_TLS_CONF"}
CONSOLE = REPO_ROOT / "console"
PROFILES = ("agent", "api", "gui", "feedback", "review", "reviewgui", "console", "consolegui")


def _compose_config(tmp_path: Path, *profiles: str, env: dict | None = None) -> dict:
    empty_env_file = tmp_path / "empty.env"
    empty_env_file.write_text("")
    cmd = ["docker", "compose", "--env-file", str(empty_env_file)]
    for profile in profiles or PROFILES:
        cmd += ["--profile", profile]
    cmd += ["config", "--format", "json"]

    # The host's own environment would otherwise substitute into the file and
    # the test would be reading this machine's .env rather than the defaults.
    substitutable = set(
        re.findall(r"\$\{([A-Z_][A-Z0-9_]*)", (REPO_ROOT / "docker-compose.yml").read_text())
    )
    run_env = {k: v for k, v in os.environ.items() if k not in substitutable}
    run_env.update(env or {})
    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=60, env=run_env)
    if result.returncode != 0:
        pytest.fail(f"`docker compose config` failed:\n{result.stderr}")
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def config(tmp_path_factory) -> dict:
    return _compose_config(tmp_path_factory.mktemp("compose"))


@pytest.fixture(scope="module")
def console(config: dict) -> dict:
    return config["services"]["console"]


@pytest.fixture(scope="module")
def consolegui(config: dict) -> dict:
    return config["services"]["consolegui"]


# ---------------------------------------------------------------------------
# None of it unless it is asked for
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("service", ["console", "consolegui"])
def test_nothing_is_started_unless_it_is_asked_for(tmp_path_factory, service: str):
    tmp_path = tmp_path_factory.mktemp("noprofile")
    empty = tmp_path / "empty.env"
    empty.write_text("")
    result = subprocess.run(
        ["docker", "compose", "--env-file", str(empty), "config", "--services"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
    )
    assert service not in result.stdout.split()


@pytest.mark.parametrize(("service", "profile"), [("console", "console"), ("consolegui", "consolegui")])
def test_each_service_is_in_the_profile_it_is_named_for(config: dict, service: str, profile: str):
    assert config["services"][service]["profiles"] == [profile]


# ---------------------------------------------------------------------------
# What the agent sees
# ---------------------------------------------------------------------------


def test_it_runs_the_agents_image_as_a_third_way_in(console: dict, config: dict):
    agent = config["services"]["agent"]
    assert console["image"] == agent["image"]
    assert console["build"]["dockerfile"] == "agent/Dockerfile"
    assert console["command"] == ["python", "-m", "nl2sql_agent.console"]
    assert console["entrypoint"] == [], "the image's own entrypoint is the CLI"


def test_it_reads_the_agents_database_as_the_agents_role(console: dict, config: dict):
    url = console["environment"]["DATABASE_URL"]
    assert url == config["services"]["agent"]["environment"]["DATABASE_URL"]
    assert url.startswith("postgresql+psycopg://nl2sql_reader:")
    assert "@postgres:5432/nl2sql_retail" in url


def test_it_runs_under_the_agents_limits(console: dict, config: dict):
    agent = config["services"]["agent"]["environment"]
    for name in AGENT_SETTINGS:
        assert console["environment"][name] == agent[name], f"{name} differs from the agent's"


def test_a_limit_changed_on_the_host_changes_for_both(tmp_path_factory):
    changed = {
        "MAX_PLAN_COST": "5000", "STATEMENT_TIMEOUT_MS": "1500", "MAX_ROWS": "9",
        "SAMPLE_ROWS": "1", "DB_SCHEMA": "analytics", "POSTGRES_READER_USER": "someone",
    }
    config = _compose_config(tmp_path_factory.mktemp("limits"), env=changed)
    agent = config["services"]["agent"]["environment"]
    console = config["services"]["console"]["environment"]
    for name in AGENT_SETTINGS:
        assert console[name] == agent[name]
    assert console["MAX_PLAN_COST"] == "5000"
    assert console["DATABASE_URL"].startswith("postgresql+psycopg://someone:")


def test_it_holds_no_credential_but_the_readers(console: dict):
    """One database, one role. None of the stores the agent retrieves from,
    none of the feedback system's, and never the retail owner."""
    environment = json.dumps(console["environment"])
    for marker in ("vectordb", "chunkdb", "feedbackdb", "correctionsdb", "completionsdb", "nl2sql:nl2sql@"):
        assert marker not in environment
    assert [key for key in console["environment"] if key.endswith("_URL")] == ["DATABASE_URL"]


def test_it_waits_for_the_database_and_nothing_else(console: dict):
    """Not the API: the certificate it presents is a file, not a service,
    and the retail database is the only thing it talks to."""
    assert set(console["depends_on"]) == {"postgres"}
    assert console["depends_on"]["postgres"]["condition"] == "service_healthy"


# ---------------------------------------------------------------------------
# TLS: the API's certificate, presented
# ---------------------------------------------------------------------------


def test_the_api_certificate_covers_the_console(config: dict):
    covered = config["services"]["api"]["environment"]["API_TLS_HOSTNAMES"].split(",")
    assert SERVICE_HOSTNAME in covered


def test_the_console_gui_verifies_a_name_that_certificate_covers(consolegui: dict, config: dict):
    covered = config["services"]["api"]["environment"]["API_TLS_HOSTNAMES"].split(",")
    assert consolegui["environment"]["CONSOLE_SSL_NAME"] in covered


def test_the_console_gui_proxies_the_name_it_verifies(consolegui: dict, console: dict):
    assert consolegui["environment"]["CONSOLE_SSL_NAME"] in consolegui["environment"]["CONSOLE_UPSTREAM"]
    assert console["container_name"] == SERVICE_HOSTNAME


@pytest.mark.parametrize("service", ["console", "consolegui"])
def test_the_certificate_is_mounted_read_only(config: dict, service: str):
    mounts = {mount["target"]: mount for mount in config["services"][service]["volumes"]}
    assert mounts["/etc/nl2sql/tls"]["source"] == "apitls"
    assert mounts["/etc/nl2sql/tls"]["read_only"] is True


def test_the_console_presents_the_file_the_api_writes(console: dict, config: dict):
    api = config["services"]["api"]["environment"]
    assert console["environment"]["CONSOLE_TLS_CERT_FILE"] == api["API_TLS_CERT_FILE"]
    assert console["environment"]["CONSOLE_TLS_KEY_FILE"] == api["API_TLS_KEY_FILE"]


# ---------------------------------------------------------------------------
# The interface
# ---------------------------------------------------------------------------


def test_the_console_gui_waits_for_the_console(consolegui: dict):
    assert consolegui["depends_on"]["console"]["condition"] == "service_healthy"


def test_the_token_never_reaches_the_browser(consolegui: dict, console: dict):
    """Held by nginx in the interface's container and by the console."""
    assert "CONSOLE_TOKEN" in consolegui["environment"]
    assert "CONSOLE_TOKEN" in console["environment"]


def test_the_proxy_outlasts_the_agents_statement_timeout(consolegui: dict):
    """A query can run until the database cancels it, and a proxy that gives
    up first turns the database's answer into a gateway error."""
    timeout = consolegui["environment"]["CONSOLE_READ_TIMEOUT"]
    assert timeout.endswith("s")
    assert int(timeout.rstrip("s")) * 1000 > Settings().statement_timeout_ms


def test_the_three_interfaces_are_three_images(config: dict):
    services = config["services"]
    images = {services[name]["image"] for name in ("gui", "reviewgui", "consolegui")}
    dockerfiles = {services[name]["build"]["dockerfile"] for name in ("gui", "reviewgui", "consolegui")}
    assert len(images) == len(dockerfiles) == 3


# ---------------------------------------------------------------------------
# Ports
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("service", ["console", "consolegui"])
def test_its_ports_are_published_on_this_machine_only(config: dict, service: str):
    for port in config["services"][service]["ports"]:
        assert port["host_ip"] == "127.0.0.1"


def test_the_address_can_be_opened_up_when_asked(tmp_path_factory):
    config = _compose_config(tmp_path_factory.mktemp("bind"), env={"CONSOLE_BIND_ADDRESS": "0.0.0.0"})
    for service in ("console", "consolegui"):
        assert {port["host_ip"] for port in config["services"][service]["ports"]} == {"0.0.0.0"}


def test_no_port_is_shared_with_another_service(tmp_path_factory):
    everything = _compose_config(
        tmp_path_factory.mktemp("all"), *PROFILES, "desktop"
    )
    published: dict[str, set[str]] = {
        name: {port["published"] for port in spec.get("ports", [])}
        for name, spec in everything["services"].items()
    }
    for service in ("console", "consolegui"):
        for other, ports in published.items():
            if other != service:
                assert published[service].isdisjoint(ports), f"{service} shares a port with {other}"


def test_the_published_port_follows_the_configured_one(tmp_path_factory):
    config = _compose_config(
        tmp_path_factory.mktemp("port"), env={"CONSOLE_PORT": "9445", "CONSOLE_GUI_PORT": "9082"}
    )
    console, gui = config["services"]["console"], config["services"]["consolegui"]
    assert console["ports"][0]["published"] == "9445" and console["ports"][0]["target"] == 9445
    assert gui["ports"][0]["published"] == "9082" and gui["ports"][0]["target"] == 9082


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def test_the_console_is_health_checked_on_the_scheme_it_serves(console: dict):
    test = " ".join(console["healthcheck"]["test"])
    assert "/healthz" in test
    assert "CONSOLE_TLS_ENABLED" in test
    assert "CONSOLE_TOKEN" not in test


@pytest.mark.parametrize("service", ["console", "consolegui"])
def test_both_restart_unless_stopped(config: dict, service: str):
    assert config["services"][service]["restart"] == "unless-stopped"


# ---------------------------------------------------------------------------
# Nothing set that is not read, nothing read that cannot be set
# ---------------------------------------------------------------------------


def _console_settings() -> set[str]:
    source = (REPO_ROOT / "agent" / "nl2sql_agent" / "console" / "settings.py").read_text()
    read = set(re.findall(r'_env(?:_str|_bool|_int|_float|_tuple)?\(\s*"([A-Z_]+)"', source))
    assert read, "no environment variables found in console/settings.py -- the regex needs updating"
    return read | set(AGENT_SETTINGS)


def test_every_setting_the_console_reads_can_be_set_through_compose(console: dict):
    missing = sorted(_console_settings() - set(console["environment"]))
    assert missing == [], f"the console reads these, but compose never passes them: {missing}"


def test_every_variable_compose_sets_on_the_console_is_one_it_reads(console: dict):
    unread = sorted(set(console["environment"]) - _console_settings())
    assert unread == [], f"compose sets {unread} on the console, which nothing in it reads"


def test_an_unset_console_setting_arrives_empty_so_the_default_stands(console: dict):
    for name in ("CONSOLE_TOKEN", "CONSOLE_MAX_ROWS", "CONSOLE_CORS_ORIGINS", "MAX_ROWS"):
        assert console["environment"][name] == "", f"{name} is pinned in compose rather than forwarded"


def _proxy_variables() -> set[str]:
    """Every ${NAME} the interface's nginx template and start-up script substitute."""
    sources = (CONSOLE / "nginx.conf.template").read_text() + (CONSOLE / "10-nl2sql-console-config.envsh").read_text()
    names = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)", sources))
    # Worked out by the start-up script rather than set by anyone.
    return names - {"CONSOLE_AUTH_HEADER", "NGINX_CONSOLE_UPSTREAM_TLS_CONF", *SIGN_IN_COMPUTED, "CONSOLE_GUI_LISTEN_TLS"}


def test_every_setting_the_console_proxy_reads_can_be_set_through_compose(consolegui: dict):
    unsettable = sorted(_proxy_variables() - set(consolegui["environment"]))
    assert unsettable == [], f"the console's proxy reads {unsettable}, which compose never passes"


def test_every_variable_compose_sets_is_one_the_console_proxy_reads(consolegui: dict):
    unread = sorted(set(consolegui["environment"]) - _proxy_variables())
    assert unread == [], f"compose sets {unread} on the console interface, which nothing in it reads"
