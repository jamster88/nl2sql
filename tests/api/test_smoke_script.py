"""`docker/apitest/smoke.sh`, run for real against a live server.

The smoke script is the outside client: the thing that proves this API can be
driven by something holding no code of ours. It is also a shell script, so no
Python coverage report can see it, and until it ran somewhere other than a
container its branches were only ever read.

So it is run here the way it runs in compose -- bash, curl, jq, a real HTTPS
socket -- against `create_app` with a scripted pipeline. No Docker and no
model, which is what lets every one of its paths be exercised on an ordinary
`pytest`: the happy path, each of the three ways it decides to trust the
server, a server that is up but not ready, a question that fails, and the
refusals that make its exit codes mean something.

It found two things worth having. The `AUTH=()` array it started with aborted
under `set -u` on bash 3.2, which is what macOS ships -- invisible inside the
Alpine container. And the `data:` prefix has to come off an event frame
before jq sees it, which no assertion had covered.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from nl2sql_agent.api.app import create_app
from nl2sql_agent.api.jobs import JobStore
from nl2sql_agent.api.settings import ApiSettings
from nl2sql_agent.api.tls import ensure_certificate
from nl2sql_agent.config import Settings

from .conftest import FakeAgent, make_runner

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SMOKE = REPO_ROOT / "docker" / "apitest" / "smoke.sh"

MISSING = [tool for tool in ("bash", "curl", "jq") if shutil.which(tool) is None]
pytestmark = pytest.mark.skipif(
    bool(MISSING), reason=f"the smoke script needs {', '.join(MISSING)} on PATH"
)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Api:
    def __init__(self, settings: ApiSettings, server: uvicorn.Server, thread: threading.Thread):
        self.settings, self._server, self._thread = settings, server, thread

    @property
    def base(self) -> str:
        return f"{self.settings.scheme}://127.0.0.1:{self.settings.port}"

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=10)


@pytest.fixture
def api(tmp_path: Path):
    """A real HTTPS server on loopback, with a scripted pipeline behind it."""
    running: list[Api] = []

    def _serve(
        *,
        runner=None,
        token: str | None = None,
        tls: bool = True,
        agent_factory=None,
        **kwargs,
    ) -> Api:
        settings = ApiSettings(
            host="127.0.0.1",
            port=free_port(),
            tls_enabled=tls,
            tls_cert_file=str(tmp_path / "server.crt"),
            tls_key_file=str(tmp_path / "server.key"),
            tls_hostnames=("localhost", "127.0.0.1"),
            token=token,
            keepalive_seconds=0.5,
            event_stream_timeout_seconds=30.0,
            max_wait_seconds=60.0,
            **kwargs,
        )
        certificate = ensure_certificate(settings)
        app = create_app(
            settings=Settings(),
            api_settings=settings,
            store=JobStore(runner or make_runner()),
            # Readiness and /v1/meta ask the agent, not the job store, and
            # building a real one here would reach for the compose database.
            agent_factory=agent_factory or FakeAgent,
            certificate=certificate,
        )
        config = uvicorn.Config(
            app, host=settings.host, port=settings.port, log_level="warning",
            ssl_certfile=settings.tls_cert_file if tls else None,
            ssl_keyfile=settings.tls_key_file if tls else None,
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 20
        while not server.started:
            if time.monotonic() > deadline:  # pragma: no cover - a hung bind
                raise AssertionError("uvicorn never started")
            time.sleep(0.05)
        served = Api(settings, server, thread)
        running.append(served)
        return served

    yield _serve
    for served in running:
        served.stop()


def smoke(base: str, *args: str, env: dict | None = None, timeout: int = 120):
    run_env = dict(os.environ, API_BASE_URL=base, APITEST_WAIT_SECONDS="60")
    run_env.pop("API_TOKEN", None)
    run_env.pop("API_CACERT", None)
    run_env.pop("API_INSECURE", None)
    run_env.update(env or {})
    return subprocess.run(
        ["bash", str(SMOKE), *args],
        capture_output=True, text=True, timeout=timeout, env=run_env, cwd=REPO_ROOT,
    )


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


def test_it_drives_the_whole_api_and_reports_every_check(api, tmp_path):
    served = api()
    result = smoke(served.base, env={"API_CACERT": str(tmp_path / "server.crt")})
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert "0 failed" in output
    for expected in (
        "TLS handshake completed",
        "GET /healthz ->",
        "GET /readyz -> ready",
        "GET /openapi.json -> 200",
        "GET /v1/meta ->",
        "the limits a client has to respect are published",
        "an invalid question returns the documented error shape",
        "an unknown job -> 404",
        "POST /v1/questions -> job",
        "the stream ended with a done event",
        "the answer carries the SQL that produced it",
        "-> 204",
    ):
        assert expected in output, f"the smoke test never reported {expected!r}\n{output}"


def test_the_event_stream_it_prints_is_the_real_pipeline(api, tmp_path):
    """The `data:` prefix has to come off an SSE frame before jq sees it --
    a frame is not JSON, the field it carries is. Getting that wrong printed
    nothing and still passed, because the count came from grep.
    """
    served = api()
    output = smoke(served.base, env={"API_CACERT": str(tmp_path / "server.crt")}).stdout
    assert "the stream carried 5 pipeline step(s)" in output
    assert "1. screen -- supervise for" in output
    assert "5. answer -- finish for" in output


def test_a_question_on_the_command_line_beats_the_environment(api, tmp_path):
    served = api()
    output = smoke(
        served.base, "how many stores?",
        env={"API_CACERT": str(tmp_path / "server.crt"), "APITEST_QUESTION": "ignored"},
    ).stdout
    assert "Asking: how many stores?" in output
    assert "question: how many stores?" in output


# ---------------------------------------------------------------------------
# How it decides to trust the server
# ---------------------------------------------------------------------------


def test_a_ca_file_is_preferred_when_one_is_given(api, tmp_path):
    served = api()
    output = smoke(served.base, env={"API_CACERT": str(tmp_path / "server.crt")}).stdout
    assert "trust   the CA file at" in output


def test_the_servers_own_certificate_is_pinned_when_it_is_mounted(api, tmp_path, monkeypatch):
    """What compose does: the API writes its certificate into a volume and the
    client mounts that volume read-only. Better than --insecure even in
    development, because it still proves the connection reached the server
    holding that key.
    """
    served = api()
    mounted = tmp_path / "mount" / "etc" / "nl2sql" / "tls"
    mounted.mkdir(parents=True)
    shutil.copy(tmp_path / "server.crt", mounted / "server.crt")

    # The script looks at the absolute path the container has; point the
    # lookup at the same layout under a temporary root.
    script = SMOKE.read_text().replace("/etc/nl2sql/tls/", str(mounted) + "/")
    local = tmp_path / "smoke.sh"
    local.write_text(script)
    result = subprocess.run(
        ["bash", str(local)],
        capture_output=True, text=True, timeout=120, cwd=REPO_ROOT,
        env=dict(os.environ, API_BASE_URL=served.base, APITEST_WAIT_SECONDS="60",
                 API_INSECURE="false"),
    )
    assert "pinned as a CA" in result.stdout
    assert result.returncode == 0, result.stdout + result.stderr


def test_verification_can_be_turned_off_when_someone_says_so(api):
    served = api()
    result = smoke(served.base, env={"API_INSECURE": "true"})
    assert "certificate verification is off" in result.stdout
    assert result.returncode == 0, result.stdout + result.stderr


def test_it_refuses_to_run_blind(api):
    """With nothing to verify against and no explicit permission it stops,
    rather than quietly reaching for --insecure.
    """
    served = api()
    result = smoke(served.base, env={"API_INSECURE": "false"})
    assert result.returncode == 2
    assert "no way to verify the server" in result.stdout + result.stderr


def test_an_untrusted_certificate_is_a_failure_not_a_pass(api):
    """The check that makes every other check worth something: pointed at a
    server it cannot verify, it must not report success.
    """
    served = api()
    result = smoke(served.base, env={"API_CACERT": "/dev/null", "API_INSECURE": "false"})
    assert result.returncode != 0
    assert "0 failed" not in result.stdout


# ---------------------------------------------------------------------------
# A token
# ---------------------------------------------------------------------------


def test_it_presents_the_token_when_one_is_configured(api, tmp_path):
    served = api(token="s3cret")
    result = smoke(
        served.base,
        env={"API_CACERT": str(tmp_path / "server.crt"), "API_TOKEN": "s3cret"},
    )
    assert "auth    bearer token" in result.stdout
    assert result.returncode == 0, result.stdout + result.stderr


def test_an_unauthorised_client_is_a_failed_check_not_an_unreachable_service(api, tmp_path):
    """The distinction the exit codes exist for. A 401 exited 2 -- "the API
    was never there, retry" -- which would have a CI job retrying a token
    that is never going to appear.
    """
    served = api(token="s3cret")
    result = smoke(served.base, env={"API_CACERT": str(tmp_path / "server.crt")})
    output = result.stdout + result.stderr

    assert result.returncode == 1, "an unauthorised client looked like an outage"
    assert "unauthorized" in output
    # It still got far enough to prove the service is up and speaking TLS.
    assert "TLS handshake completed" in output
    assert "GET /healthz ->" in output


# ---------------------------------------------------------------------------
# Things going wrong
# ---------------------------------------------------------------------------


def test_a_server_that_is_up_but_not_ready_is_reported_not_ignored(api, tmp_path):
    """Readiness is the check that actually fails in real life: the process
    is fine and the database behind it is not. The script has to say which,
    because "it answered /healthz" is not an answer anyone can act on.
    """

    def unbuildable():
        raise RuntimeError("connection refused")

    served = api(agent_factory=unbuildable)
    result = smoke(served.base, env={"API_CACERT": str(tmp_path / "server.crt")})

    assert result.returncode == 1
    output = result.stdout + result.stderr
    assert "not ready" in output
    assert "connection refused" in output, "it did not say what was wrong"


def test_a_question_the_pipeline_fails_is_reported_as_a_failed_check(api, tmp_path):
    served = api(runner=make_runner(raises=RuntimeError("ollama went away")))
    result = smoke(served.base, env={"API_CACERT": str(tmp_path / "server.crt")})
    assert result.returncode == 1
    assert "the job failed" in result.stdout + result.stderr


def test_an_answer_with_no_rows_behind_it_is_reported(api, tmp_path):
    """A refusal comes back succeeded and carries no result, which is correct
    for the API and not what this script asked for.
    """
    refusal = {"verdict": "out_of_domain", "answer": "I can't answer that", "error": None}
    served = api(runner=make_runner(state=refusal))
    result = smoke(served.base, env={"API_CACERT": str(tmp_path / "server.crt")})
    assert result.returncode == 1
    output = result.stdout + result.stderr
    assert "the answer has no SQL" in output
    assert "the answer has no rows" in output


def test_an_api_that_is_not_there_at_all_is_exit_two(tmp_path):
    """Told apart from a failed check on purpose: a CI job retries the first
    and does not retry the second.
    """
    unused = free_port()
    result = smoke(f"https://127.0.0.1:{unused}", env={"API_INSECURE": "true"}, timeout=180)
    assert result.returncode == 2
    assert "no response from" in result.stdout + result.stderr


def test_it_works_against_a_server_with_tls_switched_off(api):
    """The reverse-proxy arrangement: something else terminates TLS, and the
    script must not insist on a handshake it is not making.
    """
    served = api(tls=False)
    result = smoke(served.base, env={"API_INSECURE": "true"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "TLS handshake completed" not in result.stdout


def test_a_service_description_with_nothing_in_it_is_a_failure(api, tmp_path):
    """jq counts a missing `.tables` as zero, so the check used to announce
    an error body as "ok -- 0 tables". A client cannot ask a question of a
    server with no tables in scope, whatever the reason.
    """

    class NoTables(FakeAgent):
        def __init__(self) -> None:
            super().__init__()
            self.db.tables = []

    served = api(agent_factory=NoTables)
    result = smoke(served.base, env={"API_CACERT": str(tmp_path / "server.crt")})
    output = result.stdout + result.stderr

    assert result.returncode == 1
    assert "did not return a description" in output
    assert "0 tables" not in output, "an empty description was reported as a pass"


def test_a_stream_that_carries_no_progress_is_a_failure(api, tmp_path):
    """The progress stream is the reason a GUI can show anything during the
    minute a question takes. A silent one is a regression the count has to
    catch, so the counting itself needs proving it can fail.
    """
    served = api(runner=make_runner(steps=()))
    result = smoke(served.base, env={"API_CACERT": str(tmp_path / "server.crt")})
    output = result.stdout + result.stderr

    assert result.returncode == 1
    assert "the event stream carried no progress events" in output
    # And the rest of the run still happened: the answer is there.
    assert "the job succeeded" in output


def test_a_question_that_outlasts_the_wait_is_reported_rather_than_hung_on(api, tmp_path):
    """The script has to end. A question still running when the budget is
    gone is a result -- "it is still running" -- not a reason to block a CI
    job until something kills it.
    """
    gate = threading.Event()
    served = api(runner=make_runner(steps=("supervise",), gate=gate))
    try:
        result = smoke(
            served.base,
            env={"API_CACERT": str(tmp_path / "server.crt"), "APITEST_WAIT_SECONDS": "1"},
        )
    finally:
        gate.set()
    output = result.stdout + result.stderr

    assert result.returncode == 1
    assert "the job is still running after 1s" in output
