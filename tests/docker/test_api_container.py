"""The API as a real container, reached from outside it over real TLS.

Everything in tests/api/ proves the server is correct. This proves it is
correctly *packaged*: that the published image carries the HTTP dependencies,
that it writes itself a certificate on a fresh volume, that its healthcheck
works, and that a container with nothing of this project in it can talk to
it with curl. Those are exactly the things a unit test cannot see and a GUI
author hits first.

No database is started. The pipeline is built on the first question, not at
startup, so the whole HTTP surface -- discovery, the schema document, the
readiness report -- answers without one, and that is worth pinning too: a
GUI must be able to render its own screen while Postgres is still coming up.

Opt-in (--run-docker): builds two images and runs containers on a private
network.
"""

from __future__ import annotations

import json
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.docker

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
API_IMAGE = "nl2sql-agent:pytest-api"
CLIENT_IMAGE = "nl2sql-apitest:pytest"


def _build(dockerfile: str, tag: str, daemon: bool) -> str:
    if not daemon:
        pytest.skip("no working docker daemon")
    result = subprocess.run(
        ["docker", "build", "-f", dockerfile, "-t", tag, "."],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=900,
    )
    if result.returncode != 0:
        pytest.skip(f"could not build {tag} (likely no network):\n{result.stderr[-2000:]}")
    return tag


@pytest.fixture(scope="module")
def api_image(docker_daemon_available: bool) -> str:
    return _build("agent/Dockerfile", API_IMAGE, docker_daemon_available)


@pytest.fixture(scope="module")
def client_image(docker_daemon_available: bool) -> str:
    return _build("docker/apitest/Dockerfile", CLIENT_IMAGE, docker_daemon_available)


def _compose_healthcheck() -> str:
    """The API healthcheck, exactly as compose resolves it."""
    result = subprocess.run(
        ["docker", "compose", "--profile", "api", "config", "--format", "json"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:  # pragma: no cover - compose is a fixture dependency
        pytest.skip(f"`docker compose config` failed:\n{result.stderr}")
    test = json.loads(result.stdout)["services"]["api"]["healthcheck"]["test"]
    assert test[0] == "CMD-SHELL", test
    return test[1]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Container:
    def __init__(self, name: str, port: int) -> None:
        self.name, self.port = name, port

    def logs(self) -> str:
        result = subprocess.run(
            ["docker", "logs", self.name], capture_output=True, text=True, timeout=30
        )
        return result.stdout + result.stderr

    def health(self) -> str:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Health.Status}}", self.name],
            capture_output=True, text=True, timeout=30,
        )
        return result.stdout.strip()

    def certificate(self, into: Path) -> Path:
        subprocess.run(
            ["docker", "cp", f"{self.name}:/etc/nl2sql/tls/server.crt", str(into)],
            check=True, capture_output=True, timeout=30,
        )
        return into

    def get(self, path: str, cafile: Path | None, **kwargs):
        context = ssl.create_default_context(cafile=str(cafile)) if cafile else None
        return urllib.request.urlopen(
            f"https://localhost:{self.port}{path}", context=context, timeout=20, **kwargs
        )


NETWORK_PREFIX = "nl2sql-test-"
CONTAINER_PREFIXES = ("nl2sql-api-test-", "nl2sql-apitest-")


def _remove_network(name: str) -> bool:
    """Remove a test network, taking anything still attached off it first.

    Fixture teardown runs in reverse order of setup, so this can be asked to
    clean up while a container another fixture made is still attached -- and
    `docker network rm` refuses that.

    Leaking one is not a tidiness problem. Docker allocates network subnets
    from 172.16/12 and then, once those sixteen /16s are gone, from
    192.168/16 in /20s. Enough leaked networks and a bridge is handed a range
    covering real LAN addresses, at which point every container on the
    machine routes those addresses into the bridge instead of out to the
    network -- and a host that is up and pingable from the desktop becomes
    unreachable from inside any container, with nothing to point at.
    """
    for _ in range(3):
        attached = subprocess.run(
            ["docker", "network", "inspect", name,
             "--format", "{{range .Containers}}{{.Name}} {{end}}"],
            capture_output=True, text=True, timeout=30,
        )
        if attached.returncode != 0:
            return True  # already gone
        for container in attached.stdout.split():
            subprocess.run(
                ["docker", "network", "disconnect", "-f", name, container],
                capture_output=True, timeout=30,
            )
        if subprocess.run(["docker", "network", "rm", name],
                          capture_output=True, timeout=60).returncode == 0:
            return True
        time.sleep(1)
    return False


def _strays(kind: str, prefixes: tuple[str, ...]) -> list[str]:
    listing = subprocess.run(
        ["docker", kind, "ls", "-a" if kind == "container" else "--no-trunc",
         "--format", "{{.Name}}" if kind == "network" else "{{.Names}}"],
        capture_output=True, text=True, timeout=30,
    )
    return [
        name for name in listing.stdout.split()
        if any(name.startswith(prefix) for prefix in prefixes)
    ]


@pytest.fixture(scope="module", autouse=True)
def _leaves_no_docker_objects_behind(docker_daemon_available: bool):
    """Clean up before, and fail loudly after.

    Before, because an interrupted run leaves containers and networks behind
    and the next run should not inherit them. After, because a leak here
    breaks LAN routing for every container on the machine -- it has happened,
    and the symptom looks like a host being down rather than like a test.
    """
    if not docker_daemon_available:
        yield
        return

    for name in _strays("container", CONTAINER_PREFIXES):
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=60)
    for name in _strays("network", (NETWORK_PREFIX,)):
        _remove_network(name)

    yield

    leaked_containers = _strays("container", CONTAINER_PREFIXES)
    for name in leaked_containers:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=60)
    leaked_networks = _strays("network", (NETWORK_PREFIX,))
    for name in leaked_networks:
        _remove_network(name)

    assert not leaked_networks, (
        f"these test networks were left behind: {leaked_networks}. Leaked networks "
        "eventually take subnets that shadow real LAN addresses, and every "
        "container on this machine then loses its route to them."
    )
    assert not leaked_containers, f"these test containers were left behind: {leaked_containers}"


@pytest.fixture
def docker_network(docker_daemon_available: bool):
    """A private network, so two containers can find each other by name."""
    if not docker_daemon_available:
        pytest.skip("no working docker daemon")
    created: list[str] = []

    def _make() -> str:
        name = f"{NETWORK_PREFIX}{uuid.uuid4().hex[:8]}"
        subprocess.run(["docker", "network", "create", name], check=True, capture_output=True, timeout=30)
        created.append(name)
        return name

    yield _make
    for name in created:
        assert _remove_network(name), f"could not remove the test network {name}"


@pytest.fixture
def run_api(api_image: str):
    started: list[str] = []

    def _run(*, env: dict | None = None, network: str | None = None, wait_healthy: bool = True) -> Container:
        name = f"nl2sql-api-test-{uuid.uuid4().hex[:8]}"
        port = free_port()
        cmd = [
            "docker", "run", "-d", "--name", name,
            "-p", f"127.0.0.1:{port}:8443",
            # Nothing is listening at either address; the point is that
            # neither has to be for the HTTP surface to come up. Pinned to a
            # dead local port rather than left at the defaults so the result
            # does not depend on what happens to be on the developer's LAN.
            "-e", "DATABASE_URL=postgresql+psycopg://nobody:nobody@127.0.0.1:1/none",
            "-e", "OLLAMA_BASE_URL=http://127.0.0.1:1",
            "-e", "API_TLS_HOSTNAMES=localhost,nl2sql-api,127.0.0.1",
        ]
        for key, value in (env or {}).items():
            cmd += ["-e", f"{key}={value}"]
        if network:
            cmd += ["--network", network, "--network-alias", "nl2sql-api"]
        cmd += ["--entrypoint", "python", api_image, "-m", "nl2sql_agent.api"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr
        started.append(name)
        container = Container(name, port)
        if wait_healthy:
            # Polled until /healthz actually answers, not until the port
            # accepts: Docker's port forwarder accepts a connection before
            # the server inside is listening, and a test that stopped there
            # went on to copy a certificate that had not been written yet.
            # Verification is deliberately skipped here and only here --
            # readiness is the question, and a separate test proves an
            # unverified client is refused.
            unverified = ssl._create_unverified_context()
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                try:
                    with urllib.request.urlopen(
                        f"https://localhost:{port}/healthz", context=unverified, timeout=5
                    ) as response:
                        if response.status == 200:
                            return container
                except Exception:
                    pass
                time.sleep(1)
            pytest.fail(f"the API container never answered:\n{container.logs()[-3000:]}")
        return container

    yield _run
    for name in started:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=60)


# ---------------------------------------------------------------------------
# The packaged server
# ---------------------------------------------------------------------------


def test_the_image_can_serve_the_api_at_all(api_image: str):
    """The dependency check: an image built before fastapi was added would
    fail here with ModuleNotFoundError, which is exactly the failure
    launch.sh has a branch for.
    """
    result = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "python", api_image,
         "-m", "nl2sql_agent.api", "--help"],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "--no-allow-self-signed" in result.stdout


def test_the_container_writes_itself_a_certificate_and_serves_https(run_api, tmp_path):
    """No certificate is mounted and none is baked into the image, so a
    fresh volume has to produce a working HTTPS server on its own.
    """
    api = run_api()
    cert = api.certificate(tmp_path / "server.crt")
    assert "BEGIN CERTIFICATE" in cert.read_text()
    with api.get("/healthz", cert) as response:
        assert json.load(response)["status"] == "ok"


def test_the_certificate_names_the_service_other_containers_use(run_api, tmp_path):
    from cryptography import x509

    api = run_api()
    loaded = x509.load_pem_x509_certificate(api.certificate(tmp_path / "c.crt").read_bytes())
    san = loaded.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "nl2sql-api" in [str(n) for n in san.get_values_for_type(x509.DNSName)]


def test_a_client_that_does_not_trust_the_certificate_is_refused(run_api):
    api = run_api()
    with pytest.raises(urllib.error.URLError) as raised:
        api.get("/healthz", None)
    assert isinstance(raised.value.reason, ssl.SSLCertVerificationError)


def test_the_healthcheck_compose_uses_actually_probes_the_server(run_api):
    """`depends_on: service_healthy` and launch.sh both wait on this command,
    and a healthcheck that cannot fail is worse than none: every container
    would report healthy, including an empty one.

    Run here as compose resolves it, against the real container, both ways
    round -- succeeding on the port it is serving and failing on one it is not.
    """
    probe = _compose_healthcheck()
    api = run_api()
    good = subprocess.run(
        ["docker", "exec", "-e", "API_PORT=8443", api.name, "sh", "-c", probe],
        capture_output=True, text=True, timeout=60,
    )
    assert good.returncode == 0, good.stderr

    bad = subprocess.run(
        ["docker", "exec", "-e", "API_PORT=9", api.name, "sh", "-c", probe],
        capture_output=True, text=True, timeout=60,
    )
    assert bad.returncode != 0, "the healthcheck passes against a port nothing serves"


def test_the_banner_tells_the_operator_what_they_are_running(run_api):
    api = run_api()
    logs = api.logs()
    assert "REST API" in logs
    assert "fingerprint sha256:" in logs
    assert "self-signed" in logs
    assert "No API_TOKEN" in logs


def test_refusing_the_development_certificate_stops_the_container(run_api):
    """The switch, in the place it matters: a deployment told to require a
    real certificate must fail at startup rather than serve the throwaway one.
    """
    api = run_api(env={"API_TLS_ALLOW_SELF_SIGNED": "false"}, wait_healthy=False)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        logs = api.logs()
        if "API_TLS_ALLOW_SELF_SIGNED=false" in logs:
            break
        time.sleep(1)
    else:  # pragma: no cover - a container that kept running
        pytest.fail(f"it started anyway:\n{api.logs()[-2000:]}")

    result = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.ExitCode}}", api.name],
        capture_output=True, text=True, timeout=30,
    )
    assert result.stdout.strip() == "2", "a certificate the operator must fix exits 2"


# ---------------------------------------------------------------------------
# What answers before the database does
# ---------------------------------------------------------------------------


def test_the_schema_document_is_served_without_a_database(run_api, tmp_path):
    """How a GUI's build step generates its client: it needs the document,
    not a running stack.
    """
    api = run_api()
    cert = api.certificate(tmp_path / "c.crt")
    with api.get("/openapi.json", cert) as response:
        document = json.load(response)
    assert "/v1/questions" in document["paths"]


def test_metadata_answers_while_postgres_is_still_coming_up(run_api, tmp_path):
    api = run_api()
    cert = api.certificate(tmp_path / "c.crt")
    with api.get("/v1/meta", cert) as response:
        body = json.load(response)
    assert body["tables"] == []
    assert body["limits"]["max_rows"] > 0
    assert body["tls"]["self_signed"] is True


def test_readiness_says_which_dependency_is_missing(run_api, tmp_path):
    api = run_api()
    cert = api.certificate(tmp_path / "c.crt")
    with pytest.raises(urllib.error.HTTPError) as raised:
        api.get("/readyz", cert)
    assert raised.value.code == 503
    body = json.load(raised.value)
    assert body["ready"] is False
    assert body["checks"]["agent"]["ok"] is False
    assert body["checks"]["database"]["ok"] is False


def test_readiness_answers_promptly_even_when_the_chat_host_is_a_black_hole(run_api, tmp_path):
    """The failure that made `/readyz` unusable. A refused connection answers
    at once; a host that is routed and silent does not, and the model
    validation had no timeout of its own -- so the probe blocked for just
    under three minutes and an orchestrator gave up on the container instead
    of on the dependency.

    192.0.2.1 is TEST-NET-1 (RFC 5737): routable, reserved, answered by
    nothing, which is the shape of a machine that is off.
    """
    api = run_api(env={"OLLAMA_BASE_URL": "http://192.0.2.1:11434",
                       "OLLAMA_CONNECT_TIMEOUT": "3"})
    cert = api.certificate(tmp_path / "c.crt")

    started = time.monotonic()
    with pytest.raises(urllib.error.HTTPError) as raised:
        api.get("/readyz", cert)
    elapsed = time.monotonic() - started

    assert raised.value.code == 503
    assert elapsed < 30, f"/readyz took {elapsed:.0f}s; the timeout is not reaching the container"
    detail = json.load(raised.value)["checks"]["agent"]["detail"]
    assert "192.0.2.1" in detail and "timeout" in detail.lower()


def test_a_token_locks_the_api_down_inside_the_container(run_api, tmp_path):
    api = run_api(env={"API_TOKEN": "s3cret"})
    cert = api.certificate(tmp_path / "c.crt")
    with pytest.raises(urllib.error.HTTPError) as raised:
        api.get("/v1/meta", cert)
    assert raised.value.code == 401

    request = urllib.request.Request(
        f"https://localhost:{api.port}/v1/meta",
        headers={"Authorization": "Bearer s3cret"},
    )
    with urllib.request.urlopen(
        request, context=ssl.create_default_context(cafile=str(cert)), timeout=20
    ) as response:
        assert response.status == 200


# ---------------------------------------------------------------------------
# An outside container, with nothing but curl
# ---------------------------------------------------------------------------


def test_a_curl_only_container_reaches_the_api_over_verified_tls(
    run_api, client_image, docker_network, tmp_path
):
    """The claim the whole thing rests on: something with no shared code and
    no shared runtime can talk to this, and can verify the certificate while
    doing it -- `API_INSECURE=false`, so a handshake it cannot verify is a
    failure rather than a shrug.

    The databases are not up, so the smoke test gets as far as readiness and
    then reports a failure. That is the right answer; its earlier checks are
    the ones under test here.
    """
    network = docker_network()
    api = run_api(network=network)
    cert = api.certificate(tmp_path / "server.crt")

    client = f"nl2sql-apitest-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        ["docker", "create", "--name", client, "--network", network,
         "-e", "API_BASE_URL=https://nl2sql-api:8443",
         "-e", "API_INSECURE=false",
         client_image],
        check=True, capture_output=True, timeout=60,
    )
    try:
        # Copied in rather than bind-mounted: a host path is not shareable
        # with the daemon on every machine, and the certificate has to be
        # something the client trusts rather than something it skips.
        subprocess.run(
            ["docker", "cp", str(cert), f"{client}:/etc/nl2sql/tls/server.crt"],
            check=True, capture_output=True, timeout=30,
        )
        result = subprocess.run(
            ["docker", "start", "-a", client], capture_output=True, text=True, timeout=300
        )
    finally:
        subprocess.run(["docker", "rm", "-f", client], capture_output=True, timeout=60)
    output = result.stdout + result.stderr
    assert "TLS handshake completed" in output, output[-3000:]
    assert "GET /healthz ->" in output
    assert "GET /openapi.json -> 200" in output
    # And it says what it could not do, rather than passing quietly.
    assert "not ready" in output
    assert result.returncode == 1, "a reachable but unusable API is a failure, not a retry"


def test_the_outside_container_refuses_to_run_blind(client_image):
    """With no certificate to trust and no explicit permission, it stops
    rather than falling back to --insecure on its own.
    """
    result = subprocess.run(
        ["docker", "run", "--rm", "-e", "API_BASE_URL=https://nowhere:8443",
         "-e", "API_INSECURE=false", client_image],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 2
    assert "no way to verify the server" in result.stdout + result.stderr
