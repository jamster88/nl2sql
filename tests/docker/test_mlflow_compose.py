"""MLflow as compose resolves it: the `mlflow` service and the database behind it.

Behind `--run-docker` like the other compose tests: `docker compose config`
only parses and resolves, but it needs a working `docker` CLI.

What carries weight is what joins files that never mention each other:

* **One version of MLflow.** The server is MLflow's own published image,
  pinned in docker/mlflow/Dockerfile and built under the release's tag; the
  agent's tracing client is pinned in agent/requirements.txt and the
  benchmark's runs client in tests/requirements.txt. A client ahead of its
  server is a trace format the server does not read, and nothing but a test
  holds the three together.
* **Both images are the release's.** They are built here, from
  docker/mlflow/Dockerfile and docker/mlflowdb/Dockerfile, and pinned by
  `setup.sh --mlflow` like every other image this project publishes.
* **The address the agent is given is this service's.** setup.sh writes
  `http://nl2sql-mlflow:5000` into .env; that is a container name and a port
  here, and the server's DNS-rebinding guard must accept it as a Host header.
* **Its interface is reached through its front door, and only that.** MLflow
  has no login, and a trace holds the rows every question returned -- so the
  server itself publishes nothing, and `mlflowproxy` publishes it over HTTPS,
  asking the auth service about every request, on loopback unless someone
  says otherwise.
* **The store behind it is not published at all.** Nothing reads it but the
  server.
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
#: What setup.sh writes into .env for the agent.
SETUP_TRACKING_URI = "http://nl2sql-mlflow:5000"


def _compose_config(tmp_path: Path, *profiles: str, env: dict | None = None) -> dict:
    empty_env_file = tmp_path / "empty.env"
    empty_env_file.write_text("")
    cmd = ["docker", "compose", "--env-file", str(empty_env_file)]
    for profile in profiles or ("agent", "api", "mlflow"):
        cmd += ["--profile", profile]
    cmd += ["config", "--format", "json"]
    # The host's own environment would otherwise substitute into the file.
    substitutable = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)", (REPO_ROOT / "docker-compose.yml").read_text()))
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
def mlflow(config: dict) -> dict:
    return config["services"]["mlflow"]


@pytest.fixture(scope="module")
def proxy(config: dict) -> dict:
    return config["services"]["mlflowproxy"]


@pytest.fixture(scope="module")
def mlflowdb(config: dict) -> dict:
    return config["services"]["mlflowdb"]


def _flags(service: dict) -> dict[str, str]:
    """`--name=value` arguments of the server's command, by name."""
    return dict(arg[2:].split("=", 1) for arg in service["command"] if arg.startswith("--") and "=" in arg)


def _base(dockerfile: str) -> str:
    """The image a Dockerfile is built from."""
    [base] = re.findall(r"^FROM (\S+)$", (REPO_ROOT / dockerfile).read_text(), re.MULTILINE)
    return base


def _pin(path: str, package: str) -> str:
    [version] = re.findall(rf"^{re.escape(package)}==([\d.]+)$", (REPO_ROOT / path).read_text(), re.MULTILINE)
    return version


# ---------------------------------------------------------------------------
# Only when asked for
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("service", ["mlflow", "mlflowdb"])
def test_mlflow_is_not_started_without_its_profile(tmp_path_factory, service: str):
    config = _compose_config(tmp_path_factory.mktemp("bare"), "agent", "api")
    assert service not in config["services"]


@pytest.mark.parametrize("service", ["mlflow", "mlflowdb"])
def test_both_are_behind_the_mlflow_profile(config: dict, service: str):
    assert config["services"][service]["profiles"] == ["mlflow"]


# ---------------------------------------------------------------------------
# One version
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("service", "dockerfile"),
    [("mlflow", "docker/mlflow/Dockerfile"), ("mlflowdb", "docker/mlflowdb/Dockerfile")],
)
def test_both_are_built_here_and_named_as_the_release_publishes_them(config: dict, service: str, dockerfile: str):
    built = config["services"][service]
    assert built["build"]["dockerfile"] == dockerfile
    # Unpinned, the local build; `setup.sh --mlflow` pins the published one.
    assert built["image"] == f"nl2sql-{service}:latest"


def _instructions(dockerfile: str) -> list[str]:
    return [
        line for line in (REPO_ROOT / dockerfile).read_text().splitlines()
        if line and not line.startswith(("#", " "))
    ]


def test_the_store_adds_nothing_to_what_it_is_built_from():
    """How a server is run is compose's command, which changes without a
    rebuild; an image that baked any of it in would be a second copy."""
    assert {line.split()[0] for line in _instructions("docker/mlflowdb/Dockerfile")} <= {"FROM", "EXPOSE", "LABEL"}


def test_the_server_adds_an_account_to_run_as_and_nothing_of_how_it_is_served():
    """6.2 (V6-31): the account `mlflow` and the entrypoint that drops to
    it. How it is served -- the store, the artifacts, the hosts -- is still
    compose's command alone."""
    added = [line for line in _instructions("docker/mlflow/Dockerfile") if line.split()[0] not in {"FROM", "EXPOSE", "LABEL"}]
    assert [line.split()[0] for line in added] == ["RUN", "COPY", "ENTRYPOINT"]
    assert added[0].startswith("RUN groupadd --system --gid 10002 mlflow")
    assert added[1] == "COPY --chmod=755 docker/mlflow/entrypoint.sh /usr/local/bin/nl2sql-mlflow-entrypoint"
    assert added[2] == 'ENTRYPOINT ["/usr/local/bin/nl2sql-mlflow-entrypoint"]'
    assert "CMD" not in {line.split()[0] for line in _instructions("docker/mlflow/Dockerfile")}


def test_the_server_is_mlflows_own_image_with_a_postgres_driver():
    image, _, tag = _base("docker/mlflow/Dockerfile").rpartition(":")
    assert image == "ghcr.io/mlflow/mlflow"
    # The plain image has no Postgres driver; `-full` does.
    assert tag.endswith("-full")


def test_the_store_is_stock_postgres():
    assert _base("docker/mlflowdb/Dockerfile").startswith("postgres:")


def test_the_agents_and_the_benchmarks_clients_are_the_servers_version():
    server = _base("docker/mlflow/Dockerfile").rpartition(":")[2].removeprefix("v").removesuffix("-full")
    assert _pin("agent/requirements.txt", "mlflow-tracing") == server
    assert _pin("tests/requirements.txt", "mlflow-skinny") == server


def test_the_pins_setup_writes_are_what_compose_runs(tmp_path_factory):
    config = _compose_config(
        tmp_path_factory.mktemp("pinned"),
        env={
            "MLFLOW_IMAGE_NAME": "mcfaddja/nl2sql-mlflow", "MLFLOW_IMAGE_TAG": "v9",
            "MLFLOW_DB_IMAGE_NAME": "mcfaddja/nl2sql-mlflowdb", "MLFLOW_DB_IMAGE_TAG": "v9",
        },
    )
    assert config["services"]["mlflow"]["image"] == "mcfaddja/nl2sql-mlflow:v9"
    assert config["services"]["mlflowdb"]["image"] == "mcfaddja/nl2sql-mlflowdb:v9"
    setup = (REPO_ROOT / "setup.sh").read_text()
    for key in ("MLFLOW_IMAGE_NAME", "MLFLOW_IMAGE_TAG", "MLFLOW_DB_IMAGE_NAME", "MLFLOW_DB_IMAGE_TAG"):
        assert f'echo "{key}=' in setup


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------


def test_the_store_is_a_database_of_its_own(mlflowdb: dict, config: dict):
    assert mlflowdb["container_name"] == "nl2sql-mlflowdb"
    others = {s["environment"].get("POSTGRES_DB") for n, s in config["services"].items()
              if n != "mlflowdb" and "environment" in s}
    assert mlflowdb["environment"]["POSTGRES_DB"] not in others


def test_the_store_is_not_published(mlflowdb: dict):
    assert "ports" not in mlflowdb


def test_the_store_keeps_its_data_in_its_own_volume(mlflowdb: dict, config: dict):
    [mount] = mlflowdb["volumes"]
    assert mount["target"] == "/var/lib/postgresql/data"
    assert mount["source"] == "mlflowdata"
    assert mlflowdb["environment"]["PGDATA"].startswith(mount["target"] + "/")
    assert "mlflowdata" in config["volumes"]


def test_the_store_is_health_checked_and_the_server_waits_for_it(mlflowdb: dict, mlflow: dict):
    assert "pg_isready -U mlflow -d mlflow" in " ".join(mlflowdb["healthcheck"]["test"])
    assert mlflow["depends_on"]["mlflowdb"]["condition"] == "service_healthy"


def test_the_server_reaches_the_store_as_the_store_was_created(mlflow: dict, mlflowdb: dict):
    env = mlflowdb["environment"]
    assert _flags(mlflow)["backend-store-uri"] == (
        f"postgresql://{env['POSTGRES_USER']}:{env['POSTGRES_PASSWORD']}@"
        f"{mlflowdb['container_name']}:5432/{env['POSTGRES_DB']}"
    )


def test_the_stores_credentials_reach_both_sides_from_one_setting(tmp_path_factory):
    config = _compose_config(
        tmp_path_factory.mktemp("creds"),
        env={"MLFLOW_DB_USER": "tracer", "MLFLOW_DB_PASSWORD": "pw", "MLFLOW_DB_NAME": "traces"},
    )
    assert _flags(config["services"]["mlflow"])["backend-store-uri"] == (
        "postgresql://tracer:pw@nl2sql-mlflowdb:5432/traces"
    )
    assert "pg_isready -U tracer -d traces" in " ".join(config["services"]["mlflowdb"]["healthcheck"]["test"])


# ---------------------------------------------------------------------------
# The server
# ---------------------------------------------------------------------------


def test_artifacts_are_served_from_a_volume_of_their_own(mlflow: dict, config: dict):
    flags = _flags(mlflow)
    assert "--serve-artifacts" in mlflow["command"]
    [mount] = mlflow["volumes"]
    assert (mount["source"], mount["target"]) == ("mlflowartifacts", flags["artifacts-destination"])
    assert "mlflowartifacts" in config["volumes"]


def test_the_address_setup_writes_is_this_services(mlflow: dict):
    host, _, port = SETUP_TRACKING_URI.removeprefix("http://").partition(":")
    assert mlflow["container_name"] == host
    assert _flags(mlflow)["port"] == port
    assert _flags(mlflow)["host"] == "0.0.0.0"
    setup = (REPO_ROOT / "setup.sh").read_text()
    assert f'echo "MLFLOW_TRACKING_URI={SETUP_TRACKING_URI}"' in setup


@pytest.mark.parametrize("host", ["nl2sql-mlflow:5000", "mlflow:5000", "localhost:5001", "127.0.0.1:5001"])
def test_the_rebinding_guard_lets_the_agent_and_this_machine_in(mlflow: dict, host: str):
    import fnmatch

    allowed = _flags(mlflow)["allowed-hosts"].split(",")
    assert any(fnmatch.fnmatch(host, pattern) if "*" in pattern else host == pattern for pattern in allowed)


def test_the_interface_is_this_machines_unless_someone_says_otherwise(mlflow: dict, proxy: dict):
    assert "ports" not in mlflow, "MLflow has no login of its own: it is reached through its front door"
    [port] = proxy["ports"]
    assert (port["host_ip"], port["published"], port["target"]) == ("127.0.0.1", "5001", 5001)


def test_the_front_door_asks_about_every_request_over_https(proxy: dict):
    env = proxy["environment"]
    assert (env["AUTH_ENABLED"], env["MLFLOW_PROXY_TLS_ENABLED"]) == ("true", "true")
    assert env["MLFLOW_UPSTREAM"] == "http://nl2sql-mlflow:5000"
    assert env["AUTH_UPSTREAM"] == "https://nl2sql-auth:8446"
    # It waits for MLflow, and for its own certificate from the pki service
    # (V6-36), which it also verifies the auth service with -- so it waits
    # on nothing of the API's, and `--profile mlflow` alone is a project
    # compose accepts.
    assert set(proxy["depends_on"]) == {"pki", "mlflow"}
    [volume] = proxy["volumes"]
    assert (volume["source"], volume["target"], volume["read_only"]) == ("mlflowproxytls", "/etc/nl2sql/tls", True)
    assert env["AUTH_CACERT"] == "/etc/nl2sql/tls/ca.crt"
    assert proxy["profiles"] == ["mlflow"]


def test_every_setting_the_front_door_reads_can_be_set_through_compose_and_nothing_else_is(proxy: dict):
    root = REPO_ROOT / "docker" / "mlflow-proxy"
    sources = (root / "nginx.conf.template").read_text() + (root / "10-nl2sql-mlflow-proxy.envsh").read_text()
    computed = {"MLFLOW_AUTH_HEADER", "MLFLOW_PROXY_LISTEN_TLS", "NGINX_AUTH_TLS_CONF", "NGINX_SERVER_TLS_CONF",
                "NGINX_SIGNIN_CONF", "NGINX_UPSTREAM_TLS_CONF"}
    read = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)", sources)) - computed
    assert sorted(read - set(proxy["environment"])) == []
    assert sorted(set(proxy["environment"]) - read) == []


def test_the_published_port_and_address_can_be_changed(tmp_path_factory):
    config = _compose_config(
        tmp_path_factory.mktemp("port"), env={"MLFLOW_PORT": "6001", "MLFLOW_BIND_ADDRESS": "0.0.0.0"}
    )
    [port] = config["services"]["mlflowproxy"]["ports"]
    assert (port["host_ip"], port["published"]) == ("0.0.0.0", "6001")


def test_the_servers_workers_and_guard_can_be_changed(mlflow: dict, tmp_path_factory):
    """Two of the server's flags come from .env: its worker count, and the
    guard -- replaced whole, as MLflow's own flag is, for a name such as the
    one a proxy in front of it answers to."""
    assert _flags(mlflow)["workers"] == "2"
    config = _compose_config(
        tmp_path_factory.mktemp("server"), env={"MLFLOW_WORKERS": "4", "MLFLOW_ALLOWED_HOSTS": "mlflow.example.org"}
    )
    flags = _flags(config["services"]["mlflow"])
    assert (flags["workers"], flags["allowed-hosts"]) == ("4", "mlflow.example.org")


def test_the_benchmark_looks_for_mlflow_on_the_published_port(proxy: dict):
    from benchmarks.run_benchmark import HOST_DEFAULTS

    [port] = proxy["ports"]
    assert HOST_DEFAULTS["mlflow_tracking_uri"] == ("MLFLOW_TRACKING_URI", f"https://localhost:{port['published']}")


def test_the_server_is_health_checked_without_curl(mlflow: dict):
    """MLflow's image has no curl; the check is Python's."""
    test = " ".join(mlflow["healthcheck"]["test"])
    assert test.startswith("CMD-SHELL python -c")
    assert f"127.0.0.1:{_flags(mlflow)['port']}/health" in test


def test_mlflow_does_not_collide_with_another_service(config: dict, proxy: dict):
    published = {
        port["published"]
        for name, service in config["services"].items()
        if name != "mlflowproxy"
        for port in service.get("ports", [])
    }
    assert proxy["ports"][0]["published"] not in published


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("service", ["agent", "api"])
def test_the_agent_traces_where_dotenv_says_and_nowhere_by_default(config: dict, service: str):
    env = config["services"][service]["environment"]
    assert env["MLFLOW_TRACKING_URI"] == ""
    assert env["MLFLOW_EXPERIMENT_NAME"] == ""


def test_the_tracking_uri_setup_writes_reaches_the_agent(tmp_path_factory):
    config = _compose_config(tmp_path_factory.mktemp("traced"), env={"MLFLOW_TRACKING_URI": SETUP_TRACKING_URI})
    assert config["services"]["api"]["environment"]["MLFLOW_TRACKING_URI"] == SETUP_TRACKING_URI


def test_the_console_runs_no_pipeline_and_so_traces_nothing(tmp_path_factory):
    config = _compose_config(tmp_path_factory.mktemp("console"), "console")
    assert "MLFLOW_TRACKING_URI" not in config["services"]["console"]["environment"]
