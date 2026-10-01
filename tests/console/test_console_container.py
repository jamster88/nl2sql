"""The SQL console and its interface, as real containers.

Four on a private network, which is the arrangement compose builds: the
agent image serving the API (only so it writes the certificate it writes in
every deployment), a Postgres holding one table and a read-only role, the
agent image again serving the console with that certificate, and the
interface's nginx in front of it. The properties worth proving need all
four: the proxy *verifies* the console's certificate rather than trusting
whatever answers, the token is added by the proxy and never by the page,
and a query runs as a role whose writes the database itself refuses.

Everything is removed before and after, and a leak is a failure: a network
left behind eventually takes a subnet that shadows a real LAN address.
"""

from __future__ import annotations

import json
import subprocess
import urllib.request
import uuid

import pytest

from tests.docker.test_gui_container import (
    API_IMAGE,
    _build,
    _get,
    _names,
    _remove_network,
    _wait_for,
    _wait_for_api,
    free_port,
)

pytestmark = pytest.mark.docker

GUI_IMAGE = "nl2sql-console-gui:pytest"
POSTGRES_IMAGE = "postgres:18"
NAME_PREFIX = "nl2sql-console-test-"
NETWORK_PREFIX = "nl2sql-console-net-"
VOLUME_PREFIX = "nl2sql-console-tls-"

SCHEMA = """
CREATE TABLE dim_store (store_key int PRIMARY KEY, store_name text NOT NULL);
COMMENT ON TABLE dim_store IS 'One row per store.';
INSERT INTO dim_store SELECT g, 'Store ' || g FROM generate_series(1, 60) g;
CREATE ROLE reader LOGIN PASSWORD 'reader';
GRANT USAGE ON SCHEMA public TO reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO reader;
ANALYZE;
"""


def _sweep() -> tuple[list[str], list[str], list[str]]:
    containers = _names("container", NAME_PREFIX)
    for name in containers:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=60)
    networks = _names("network", NETWORK_PREFIX)
    for name in networks:
        _remove_network(name)
    volumes = _names("volume", VOLUME_PREFIX)
    for name in volumes:
        subprocess.run(["docker", "volume", "rm", "-f", name], capture_output=True, timeout=30)
    return containers, networks, volumes


@pytest.fixture(scope="module", autouse=True)
def _leaves_nothing_behind(docker_daemon_available: bool):
    if not docker_daemon_available:
        yield
        return
    _sweep()
    yield
    containers, networks, volumes = _sweep()
    assert not networks, f"these test networks were left behind: {networks}"
    assert not containers, f"these test containers were left behind: {containers}"
    assert not volumes, f"these test volumes were left behind: {volumes}"


@pytest.fixture(scope="module")
def gui_image(docker_daemon_available: bool) -> str:
    return _build("console/Dockerfile", GUI_IMAGE, docker_daemon_available)


@pytest.fixture(scope="module")
def api_image(docker_daemon_available: bool) -> str:
    return _build("agent/Dockerfile", API_IMAGE, docker_daemon_available)


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=180)
    if check:
        assert result.returncode == 0, result.stderr
    return result


def _wait_for_postgres(name: str) -> None:
    for _ in range(60):
        if _docker("exec", name, "pg_isready", "-U", "owner", "-d", "shop", check=False).returncode == 0:
            return
        subprocess.run(["sleep", "1"], timeout=5)
    pytest.fail(f"{name} never became ready")  # pragma: no cover - a broken daemon


def _wait_for_console(name: str) -> None:
    """From inside, as the compose health check does: the port accepting a
    connection is not the server having loaded its certificate."""
    probe = (
        "import ssl,urllib.request;"
        "urllib.request.urlopen('https://127.0.0.1:8445/healthz',"
        "context=ssl._create_unverified_context(),timeout=5).read()"
    )
    for _ in range(60):
        if _docker("exec", name, "python", "-c", probe, check=False).returncode == 0:
            return
        subprocess.run(["sleep", "1"], timeout=5)
    logs = _docker("logs", name, check=False)
    pytest.fail(f"the console never became ready:\n{logs.stdout}\n{logs.stderr}")


@pytest.fixture(scope="module")
def stack(gui_image: str, api_image: str):
    """The database, the API that writes the certificate, the console, and a
    factory for interfaces in front of it -- each test says how its proxy is
    configured."""
    suffix = uuid.uuid4().hex[:8]
    network = f"{NETWORK_PREFIX}{suffix}"
    volume = f"{VOLUME_PREFIX}{suffix}"
    started: list[str] = []

    _docker("network", "create", network)
    _docker("volume", "create", volume)

    def run(name: str, *args: str) -> str:
        full = f"{NAME_PREFIX}{name}-{suffix}"
        _docker("run", "-d", "--name", full, "--network", network, *args)
        started.append(full)
        return full

    def run_console(alias: str, *env: str) -> str:
        environment = [
            "-e", "DATABASE_URL=postgresql+psycopg://reader:reader@nl2sql-postgres:5432/shop",
            *[flag for pair in env for flag in ("-e", pair)],
        ]
        return run(
            alias, "--network-alias", alias, "-v", f"{volume}:/etc/nl2sql/tls:ro", *environment,
            "--entrypoint", "python", api_image, "-m", "nl2sql_agent.console",
        )

    def run_gui(upstream: str = "https://nl2sql-console:8445", **env: str) -> int:
        port = free_port()
        flags = [flag for key, value in env.items() for flag in ("-e", f"{key}={value}")]
        name = run(
            f"gui-{uuid.uuid4().hex[:6]}", "-p", f"127.0.0.1:{port}:8082",
            "-v", f"{volume}:/etc/nl2sql/tls:ro", "-e", f"CONSOLE_UPSTREAM={upstream}", *flags, gui_image,
        )
        _wait_for(f"http://127.0.0.1:{port}/index.html", name)
        return port

    try:
        database = run("db", "--network-alias", "nl2sql-postgres", "-e", "POSTGRES_USER=owner",
                       "-e", "POSTGRES_PASSWORD=owner", "-e", "POSTGRES_DB=shop", POSTGRES_IMAGE)
        _wait_for_postgres(database)
        load = subprocess.run(
            ["docker", "exec", "-i", database, "psql", "-U", "owner", "-d", "shop", "-v", "ON_ERROR_STOP=1", "-q"],
            input=SCHEMA, capture_output=True, text=True, timeout=60,
        )
        assert load.returncode == 0, load.stderr

        api = run(
            "api", "--network-alias", "nl2sql-api", "-v", f"{volume}:/etc/nl2sql/tls",
            "-e", "DATABASE_URL=postgresql+psycopg://nobody:nobody@127.0.0.1:1/none",
            "-e", "OLLAMA_BASE_URL=http://127.0.0.1:1",
            "-e", "API_TLS_HOSTNAMES=localhost,nl2sql-api,nl2sql-console,127.0.0.1",
            "--entrypoint", "python", api_image, "-m", "nl2sql_agent.api",
        )
        _wait_for_api(api)
        console = run_console("nl2sql-console")
        _wait_for_console(console)
        yield {"gui": run_gui, "console": console, "run_console": run_console, "run": run}
    finally:
        for name in reversed(started):
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=60)
        assert _remove_network(network), f"could not remove the test network {network}"
        subprocess.run(["docker", "volume", "rm", "-f", volume], capture_output=True, timeout=30)


def _post(port: int, path: str, body: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.status, json.loads(response.read())


# ---------------------------------------------------------------------------
# The image
# ---------------------------------------------------------------------------


def test_the_toolchain_did_not_ship(gui_image: str):
    result = _docker("run", "--rm", "--entrypoint", "sh", gui_image, "-c", "command -v node npm || true")
    assert result.stdout.strip() == ""


def test_the_bundle_carries_no_hard_coded_address(gui_image: str):
    """Same origin, always: the page asks /v1 of whoever served it, which is
    the proxy. An address baked into the bundle is a page that only works
    on the machine it was built on."""
    result = _docker(
        "run", "--rm", "--entrypoint", "sh", gui_image, "-c",
        "cat /usr/share/nginx/html/assets/*.js",
    )
    for address in ("localhost:8445", "nl2sql-console:8445", "https://nl2sql", "127.0.0.1"):
        assert address not in result.stdout


# ---------------------------------------------------------------------------
# The hop
# ---------------------------------------------------------------------------


def test_it_serves_the_page_from_any_path(stack):
    port = stack["gui"]()
    for path in ("/", "/some/deep/link"):
        status, body = _get(port, path)
        assert status == 200
        assert "<title>NL2SQL SQL console</title>" in body


def test_it_reaches_the_console_over_tls_it_verified(stack):
    port = stack["gui"]()
    status, body = _get(port, "/readyz")
    assert status == 200
    checks = json.loads(body)["checks"]
    assert checks["role"] == {"ok": True, "detail": "reader, read-only"}


def test_a_query_through_the_proxy_runs_as_the_read_only_role(stack):
    port = stack["gui"]()
    status, body = _post(port, "/v1/query", {"sql": "SELECT count(*) AS n FROM dim_store"})
    assert status == 200
    assert body["rows"] == [[60]]
    assert body["agent"]["accepted"] is True


def test_a_write_the_validator_passes_is_refused_by_the_database(stack):
    port = stack["gui"]()
    _, body = _post(port, "/v1/query", {"sql": "SELECT lo_create(0)"})
    assert body["agent"]["stage"] == "runtime"
    assert body["error"] == "cannot execute lo_create() in a read-only transaction"


def test_a_certificate_that_does_not_match_the_upstream_is_refused(stack):
    """The proxy verifies the name, so pointing it at a name the certificate
    does not cover fails -- which is the difference between TLS and a
    TLS-shaped URL."""
    port = stack["gui"](CONSOLE_SSL_NAME="somebody-else")
    status, _ = _get(port, "/readyz")
    assert status == 502


def test_the_token_is_added_by_the_proxy_and_never_by_the_page(stack):
    """A second console with a token, behind two proxies: one holding the
    token and one not. Its name is not on the certificate, so both proxies
    verify it as `localhost`, which is."""
    _wait_for_console(stack["run_console"]("guarded-console", "CONSOLE_TOKEN=s3cret"))
    upstream = "https://guarded-console:8445"

    with_token = stack["gui"](upstream, CONSOLE_TOKEN="s3cret", CONSOLE_SSL_NAME="localhost")
    without = stack["gui"](upstream, CONSOLE_SSL_NAME="localhost")

    assert _get(with_token, "/v1/meta")[0] == 200
    assert _get(without, "/v1/meta")[0] == 401


# ---------------------------------------------------------------------------
# The console on its own
# ---------------------------------------------------------------------------


def test_the_console_refuses_to_start_without_a_certificate_to_present(stack, api_image: str):
    name = stack["run"](
        "no-cert", "-e", "DATABASE_URL=postgresql+psycopg://reader:reader@nl2sql-postgres:5432/shop",
        "--entrypoint", "python", api_image, "-m", "nl2sql_agent.console",
    )
    for _ in range(30):
        state = _docker("inspect", "--format", "{{.State.Running}} {{.State.ExitCode}}", name).stdout.split()
        if state[0] == "false":
            break
        subprocess.run(["sleep", "1"], timeout=5)
    assert state == ["false", "2"]
    said = " ".join(_docker("logs", name).stderr.split())
    assert "presents the certificate the agent API generates" in said


def test_the_console_says_what_it_reads_and_as_whom(stack):
    banner = _docker("logs", stack["console"]).stdout
    assert "postgresql+psycopg://reader:***@nl2sql-postgres:5432/shop" in banner
    assert "reader:reader" not in banner
    assert "written by the agent API, for localhost, nl2sql-api, nl2sql-console" in banner
