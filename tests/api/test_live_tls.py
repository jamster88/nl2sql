"""The server on a real socket, over real TLS, driven by a real client.

Everything else in this directory goes through Starlette's test client,
which never opens a port. That is the right trade for route logic and the
wrong one for the claim this work exists to make: that a GUI written in
anything can reach the agent over an encrypted link.

So this binds uvicorn to a loopback port with the certificate the server
generated for itself, and talks to it with nothing but the standard library
-- `ssl` and `urllib` -- which is as close to "a client that knows nothing
about this project" as a Python test can get. No Docker and no network, so
it runs on every `pytest`.
"""

from __future__ import annotations

import json
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import uvicorn
from nl2sql_agent.api.app import create_app
from nl2sql_agent.api.jobs import JobStore
from nl2sql_agent.api.settings import ApiSettings
from nl2sql_agent.api.tls import ensure_certificate
from nl2sql_agent.config import Settings

from .conftest import make_runner


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Served:
    """A uvicorn server on a thread, with the URL and certificate to reach it."""

    def __init__(self, api: ApiSettings, certificate, server: uvicorn.Server, thread: threading.Thread):
        self.api, self.certificate, self._server, self._thread = api, certificate, server, thread

    @property
    def base(self) -> str:
        return f"{self.api.scheme}://127.0.0.1:{self.api.port}"

    def context(self, *, trust: bool = True) -> ssl.SSLContext | None:
        if not self.api.tls_enabled:
            return None
        if not trust:
            return ssl.create_default_context()
        # The development certificate is not in any CA store, so it is
        # trusted the way the smoke test trusts it: as its own CA file.
        return ssl.create_default_context(cafile=self.api.tls_cert_file)

    def get(self, path: str, *, trust: bool = True, timeout: float = 15.0, **kwargs):
        return self.request("GET", path, trust=trust, timeout=timeout, **kwargs)

    def request(self, method: str, path: str, *, body: dict | None = None,
                trust: bool = True, timeout: float = 15.0, headers: dict | None = None):
        request = urllib.request.Request(
            f"{self.base}{path}",
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        return urllib.request.urlopen(request, context=self.context(trust=trust), timeout=timeout)

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


@pytest.fixture
def serve(tmp_path: Path):
    running: list[Served] = []

    def _serve(*, tls: bool = True, token: str | None = None, runner=None, **kwargs) -> Served:
        api = ApiSettings(
            host="127.0.0.1",
            port=free_port(),
            tls_enabled=tls,
            tls_cert_file=str(tmp_path / "server.crt"),
            tls_key_file=str(tmp_path / "server.key"),
            tls_hostnames=("localhost", "127.0.0.1"),
            token=token,
            keepalive_seconds=0.2,
            event_stream_timeout_seconds=10.0,
            max_wait_seconds=20.0,
            **kwargs,
        )
        certificate = ensure_certificate(api)
        app = create_app(
            settings=Settings(),
            api_settings=api,
            store=JobStore(runner or make_runner()),
            certificate=certificate,
        )
        config = uvicorn.Config(
            app,
            host=api.host,
            port=api.port,
            log_level="warning",
            ssl_certfile=api.tls_cert_file if tls else None,
            ssl_keyfile=api.tls_key_file if tls else None,
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 20
        while not server.started:
            if time.monotonic() > deadline:  # pragma: no cover - a hung bind
                raise AssertionError("uvicorn never started")
            time.sleep(0.05)
        served = Served(api, certificate, server, thread)
        running.append(served)
        return served

    yield _serve
    for served in running:
        served.stop()


# ---------------------------------------------------------------------------
# The link itself
# ---------------------------------------------------------------------------


def test_the_server_answers_over_a_verified_tls_connection(serve):
    """The whole point: an encrypted link, verified against the certificate,
    by a client that imports nothing from this project.
    """
    served = serve()
    with served.get("/healthz") as response:
        assert response.status == 200
        assert json.load(response)["status"] == "ok"


def test_the_connection_really_is_encrypted_and_names_the_host(serve):
    """Checked at the socket rather than inferred from the URL: a server
    that quietly fell back to HTTP would still answer every request above.
    """
    served = serve()
    context = served.context()
    with socket.create_connection(("127.0.0.1", served.api.port), timeout=10) as raw:
        with context.wrap_socket(raw, server_hostname="localhost") as tls:
            assert tls.version().startswith("TLS")
            peer = tls.getpeercert()
    names = {value for kind, value in peer["subjectAltName"] if kind == "DNS"}
    assert "localhost" in names


def test_an_untrusting_client_is_refused_rather_than_quietly_served(serve):
    """Which is what makes the certificate worth anything: the default CA
    store does not contain it, and the handshake fails.
    """
    served = serve()
    with pytest.raises(urllib.error.URLError) as raised:
        served.get("/healthz", trust=False)
    assert isinstance(raised.value.reason, ssl.SSLCertVerificationError)


def test_plain_http_is_available_for_something_that_terminates_tls_in_front(serve):
    served = serve(tls=False)
    assert served.base.startswith("http://")
    with served.get("/healthz") as response:
        assert response.status == 200


# ---------------------------------------------------------------------------
# A question, end to end
# ---------------------------------------------------------------------------


def test_a_question_asked_over_tls_comes_back_answered(serve):
    served = serve()
    with served.request("POST", "/v1/questions?wait=20", body={"question": "how many stores?"}) as response:
        assert response.status == 200
        job = json.load(response)
    assert job["status"] == "succeeded"
    assert job["answer"]["result"]["rows"] == [[42]]
    assert job["answer"]["sql"].startswith("SELECT count(*)")


def test_the_progress_stream_arrives_as_it_happens_not_all_at_the_end(serve):
    """Read incrementally, the way `EventSource` and every other SSE client
    reads it: a first event before the job is finished.
    """
    gate = threading.Event()
    served = serve(runner=make_runner(gate=gate))
    with served.request("POST", "/v1/questions", body={"question": "q"}) as response:
        job = json.load(response)

    stream = served.get(job["links"]["events"], timeout=20)
    first = b""
    while b"\n\n" not in first:
        first += stream.read1(64)
    assert b"event: progress" in first
    assert gate.is_set() is False, "the stream waited for the job to finish"
    gate.set()
    stream.close()


def test_a_token_protected_server_refuses_an_anonymous_request_over_tls(serve):
    served = serve(token="s3cret")
    with pytest.raises(urllib.error.HTTPError) as raised:
        served.get("/v1/meta")
    assert raised.value.code == 401
    assert json.load(raised.value)["error"]["code"] == "unauthorized"

    with served.get("/v1/meta", headers={"Authorization": "Bearer s3cret"}) as response:
        assert response.status == 200


def test_the_openapi_document_can_be_fetched_over_the_wire(serve):
    """How a GUI build step generates its client when the server is already
    running somewhere.
    """
    served = serve()
    with served.get("/openapi.json") as response:
        document = json.load(response)
    assert "/v1/questions" in document["paths"]
