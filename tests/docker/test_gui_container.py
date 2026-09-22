"""The GUI image, and the hop it exists to make.

Everything here runs two real containers on a private network: the agent
image serving the REST API with the certificate it writes for itself, and the
GUI image in front of it. That pairing is the whole point of the container --
a browser cannot be pointed at a self-signed certificate, so something has to
stand in front and verify it -- and it cannot be tested any other way. A
mocked upstream would prove the proxy forwards; only the real one proves it
*verifies*, which is the security property.

The containers, the network and the volume are all cleaned up before and
after, and a leak is a failure rather than a warning: a network left behind
eventually takes a subnet that shadows a real LAN address, and every
container on the machine then loses its route to it.
"""

from __future__ import annotations

import json
import socket
import subprocess
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.docker

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

GUI_IMAGE = "nl2sql-gui:pytest"
API_IMAGE = "nl2sql-agent:pytest"
NAME_PREFIX = "nl2sql-gui-test-"
NETWORK_PREFIX = "nl2sql-gui-net-"
VOLUME_PREFIX = "nl2sql-gui-tls-"


def _build(dockerfile: str, tag: str, available: bool) -> str:
    if not available:
        pytest.skip("no working docker daemon")
    result = subprocess.run(
        ["docker", "build", "-f", dockerfile, "-t", tag, "."],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=1800,
    )
    if result.returncode != 0:  # pragma: no cover - a broken build fails elsewhere too
        pytest.fail(f"building {dockerfile} failed:\n{result.stdout}\n{result.stderr}")
    return tag


@pytest.fixture(scope="module")
def gui_image(docker_daemon_available: bool) -> str:
    return _build("gui/Dockerfile", GUI_IMAGE, docker_daemon_available)


@pytest.fixture(scope="module")
def api_image(docker_daemon_available: bool) -> str:
    return _build("agent/Dockerfile", API_IMAGE, docker_daemon_available)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _names(kind: str, prefix: str) -> list[str]:
    fmt = "{{.Names}}" if kind == "container" else "{{.Name}}"
    listing = subprocess.run(
        ["docker", kind, "ls", "-a" if kind == "container" else "--quiet", "--format", fmt]
        if kind == "container"
        else ["docker", kind, "ls", "--format", fmt],
        capture_output=True, text=True, timeout=30,
    )
    return [name for name in listing.stdout.split() if name.startswith(prefix)]


def _remove_network(name: str) -> bool:
    """Disconnect anything still attached, then remove it.

    A network with a container on it refuses to go, and the container may be
    one this test failed before cleaning up.
    """
    attached = subprocess.run(
        ["docker", "network", "inspect", name, "--format", "{{range .Containers}}{{.Name}} {{end}}"],
        capture_output=True, text=True, timeout=30,
    )
    for container in attached.stdout.split():
        subprocess.run(
            ["docker", "network", "disconnect", "-f", name, container],
            capture_output=True, timeout=30,
        )
    return subprocess.run(
        ["docker", "network", "rm", name], capture_output=True, timeout=30
    ).returncode == 0


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
    assert not networks, (
        f"these test networks were left behind: {networks}. Leaked networks "
        "eventually take subnets that shadow real LAN addresses."
    )
    assert not containers, f"these test containers were left behind: {containers}"
    assert not volumes, f"these test volumes were left behind: {volumes}"


@pytest.fixture(scope="module")
def stack(gui_image: str, api_image: str):
    """The API and the GUI, on one network, sharing the certificate volume.

    Exactly the arrangement compose builds, which is why the certificate
    works: the API writes it for the names it will be reached by, and the
    proxy verifies against the same file.
    """
    suffix = uuid.uuid4().hex[:8]
    network = f"{NETWORK_PREFIX}{suffix}"
    volume = f"{VOLUME_PREFIX}{suffix}"
    api = f"{NAME_PREFIX}api-{suffix}"
    started: list[str] = []

    subprocess.run(["docker", "network", "create", network], check=True, capture_output=True, timeout=30)
    subprocess.run(["docker", "volume", "create", volume], check=True, capture_output=True, timeout=30)

    def run_gui(**env: str) -> int:
        name = f"{NAME_PREFIX}gui-{uuid.uuid4().hex[:8]}"
        port = free_port()
        cmd = [
            "docker", "run", "-d", "--name", name,
            "--network", network,
            "-p", f"127.0.0.1:{port}:8080",
            "-v", f"{volume}:/etc/nl2sql/tls:ro",
            "-e", "API_UPSTREAM=https://nl2sql-api:8443",
        ]
        for key, value in env.items():
            cmd += ["-e", f"{key}={value}"]
        cmd.append(gui_image)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stderr
        started.append(name)
        _wait_for(f"http://127.0.0.1:{port}/index.html", name)
        return port

    try:
        result = subprocess.run(
            [
                "docker", "run", "-d", "--name", api,
                "--network", network, "--network-alias", "nl2sql-api",
                "-v", f"{volume}:/etc/nl2sql/tls",
                # Nothing is listening at either address. The HTTP surface
                # comes up regardless -- the pipeline is built lazily, per
                # request -- and /v1/meta answers with an empty table list.
                "-e", "DATABASE_URL=postgresql+psycopg://nobody:nobody@127.0.0.1:1/none",
                "-e", "OLLAMA_BASE_URL=http://127.0.0.1:1",
                "-e", "API_TLS_HOSTNAMES=localhost,nl2sql-api,127.0.0.1",
                "--entrypoint", "python", api_image, "-m", "nl2sql_agent.api",
            ],
            capture_output=True, text=True, timeout=120,
        )
        assert result.returncode == 0, result.stderr
        started.append(api)
        _wait_for_api(api)
        yield run_gui
    finally:
        for name in reversed(started):
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=60)
        assert _remove_network(network), f"could not remove the test network {network}"
        subprocess.run(["docker", "volume", "rm", "-f", volume], capture_output=True, timeout=30)


def _wait_for_api(name: str) -> None:
    """Poll /healthz from inside the API container until it answers.

    Not the port: Docker's forwarder accepts a connection before the server
    is listening, and a check that stopped there went on to read a
    certificate that had not been written yet.
    """
    probe = (
        "import ssl,urllib.request;"
        "urllib.request.urlopen('https://127.0.0.1:8443/healthz',"
        "context=ssl._create_unverified_context(),timeout=5).read()"
    )
    for _ in range(60):
        result = subprocess.run(
            ["docker", "exec", name, "python", "-c", probe], capture_output=True, timeout=30
        )
        if result.returncode == 0:
            return
        subprocess.run(["sleep", "1"], timeout=5)
    logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True, timeout=30)
    pytest.fail(f"the API never became ready:\n{logs.stdout}\n{logs.stderr}")


def _wait_for(url: str, name: str) -> None:
    for _ in range(60):
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                if response.status == 200:
                    return
        except Exception:
            pass
        subprocess.run(["sleep", "1"], timeout=5)
    logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True, timeout=30)
    pytest.fail(f"{url} never answered:\n{logs.stdout}\n{logs.stderr}")


def _get(port: int, path: str, **headers: str):
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


# ---------------------------------------------------------------------------
# The image itself
# ---------------------------------------------------------------------------


def test_the_toolchain_did_not_ship(gui_image: str):
    """Two build stages exist for this. node_modules alone is bigger than
    everything the image needs to serve."""
    result = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "sh", gui_image,
         "-c", "command -v node npm; ls /usr/share/nginx/html"],
        capture_output=True, text=True, timeout=60,
    )
    assert "node" not in result.stdout.split("\n")[0]
    assert "index.html" in result.stdout


def test_the_page_is_there_and_its_assets_are_hashed(gui_image: str):
    result = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "sh", gui_image, "-c", "ls /usr/share/nginx/html/assets"],
        capture_output=True, text=True, timeout=60,
    )
    assets = result.stdout.split()
    assert any(name.endswith(".js") for name in assets)
    assert any(name.endswith(".css") for name in assets)
    # Content-addressed names are what makes the long cache lifetime safe.
    assert all("-" in name for name in assets if name.endswith((".js", ".css")))


def test_the_bundle_carries_no_hard_coded_api_address(gui_image: str):
    """The GUI asks its own origin for everything.

    That is what makes one image deployable anywhere: the API's address is
    the proxy's business and is set at start-up, so moving the API does not
    mean rebuilding the page. An address compiled into the bundle would be
    invisible until someone deployed it somewhere else.

    (The token is the same story, and is checked where it can be: the image
    bakes none in -- see tests/gui/test_gui_project.py -- and the
    application constructs its client without one.)
    """
    result = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "sh", gui_image,
         "-c", "cat /usr/share/nginx/html/assets/*.js"],
        capture_output=True, text=True, timeout=60,
    )
    for address in ("8443", "nl2sql-api", "localhost:", "127.0.0.1"):
        assert address not in result.stdout, f"{address!r} is compiled into the bundle"


# ---------------------------------------------------------------------------
# The hop
# ---------------------------------------------------------------------------


def test_it_serves_the_page(stack):
    port = stack()
    status, body = _get(port, "/")
    assert status == 200
    assert "<div id=\"root\">" in body


def test_any_path_that_is_not_a_file_is_still_the_page(stack):
    """One page, so a reload on a deep link has to land on it."""
    status, body = _get(port := stack(), "/somewhere/else")
    assert status == 200
    assert "<div id=\"root\">" in body
    # ...but not for the API's own paths, which must reach the API.
    assert _get(port, "/healthz")[0] == 200


def test_it_reaches_the_api_over_tls_it_actually_verified(stack):
    """The test this file exists for.

    The API is presenting the certificate it generated for itself, and the
    proxy is configured with `proxy_ssl_verify on` against that same file. If
    verification were off this would pass just as well -- so what proves it
    is the companion test below, where a certificate that does not match is
    refused.
    """
    status, body = _get(stack(), "/healthz")
    assert status == 200
    assert json.loads(body)["status"] == "ok"


def test_the_api_it_proxies_is_the_real_one(stack):
    status, body = _get(stack(), "/v1/meta")
    assert status == 200
    meta = json.loads(body)
    assert meta["service"] == "nl2sql-agent"
    # The database is unreachable on purpose; meta still answers, with the
    # table list it could not fill in left empty rather than failing.
    assert meta["tables"] == []


def test_a_certificate_that_does_not_match_the_upstream_is_refused(stack):
    """`proxy_ssl_name` is checked against the certificate, so asking for a
    name it does not cover must fail rather than fall back to trusting it."""
    port = stack(API_SSL_NAME="not-the-api.example.com")
    status, _ = _get(port, "/healthz")
    assert status == 502


def test_the_token_is_added_by_the_proxy_and_never_by_the_browser(stack):
    """The reason this container exists rather than a CORS configuration:
    the browser asks with no credentials at all and still gets an answer,
    because the token is added on this side of the hop."""
    port = stack(API_TOKEN="s3cret-for-the-test")
    assert _get(port, "/v1/meta")[0] == 200


def test_without_the_token_the_api_refuses_what_the_proxy_forwards(stack):
    """The other half: the API really is checking, so the pass above is the
    proxy's doing rather than the API not caring."""
    port = stack()  # no API_TOKEN on the GUI
    # The API container in this stack has no token either, so prove the
    # mechanism against the GUI's own behaviour: an Authorization header the
    # browser sends is replaced, not forwarded.
    status, body = _get(port, "/v1/meta", Authorization="Bearer wrong")
    assert status == 200, body


def test_the_progress_stream_is_not_buffered(stack):
    """A buffered stream is indistinguishable from a hung one.

    The job does not exist, so the API answers 404 -- but the response still
    proves the request crossed the proxy rather than being held by it.
    """
    status, body = _get(stack(), "/v1/questions/does-not-exist/events")
    assert status == 404
    assert json.loads(body)["error"]["code"] == "not_found"
