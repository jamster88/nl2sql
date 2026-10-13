"""nl2sql_common.health: a health check that verifies the certificate served.

Against a real HTTPS server in this process, with certificates from the
stack's own CA (nl2sql_identity.pki): the check passes for the right one and
fails for a certificate from somewhere else, which is what the checks it
replaced -- `ssl._create_unverified_context()` -- could not tell apart.

And who it is while it asks: Docker runs a health check as the user the
container was started as, so one that finds itself root becomes the
service's account before anything else.
"""

from __future__ import annotations

import http.server
import runpy
import ssl
import sys
import threading
from pathlib import Path

import pytest

from nl2sql_common import health, privileges
from nl2sql_identity.pki import Identity, ensure_ca, ensure_identity


class Healthz(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - the stdlib's name
        self.send_response(200 if self.path == "/healthz" else 404)
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def log_message(self, *args):
        pass


def serve(directory: Path | None):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Healthz)
    if directory is not None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(directory / "server.crt", directory / "server.key")
        server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def issue(root: Path, name: str) -> Path:
    ca_cert, ca_key, _ = ensure_ca(root / f"{name}-ca")
    directory = root / name
    directory.mkdir()
    ensure_identity(Identity(name, directory, ("localhost", "127.0.0.1")), ca_cert, ca_key)
    return directory


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for name in ("API_PORT", "API_TLS_ENABLED", "API_TLS_CERT_FILE", "API_TLS_CA_FILE"):
        monkeypatch.delenv(name, raising=False)
    # Whoever runs the suite is who the checks below run as: there is no
    # account `nl2sql` to become on a machine that is not one of the images.
    monkeypatch.setattr(privileges, "become", lambda account: False)


def test_a_service_serving_its_own_certificate_is_healthy(tmp_path, monkeypatch):
    identity = issue(tmp_path, "api")
    server = serve(identity)
    monkeypatch.setenv("API_PORT", str(server.server_address[1]))
    monkeypatch.setenv("API_TLS_CERT_FILE", str(identity / "server.crt"))
    try:
        assert health.main(["api"]) == 0, "the prefix is case-blind"
    finally:
        server.shutdown()


def test_a_certificate_from_another_ca_is_unhealthy(tmp_path, monkeypatch, capsys):
    served, expected = issue(tmp_path, "served"), issue(tmp_path, "expected")
    server = serve(served)
    monkeypatch.setenv("API_PORT", str(server.server_address[1]))
    monkeypatch.setenv("API_TLS_CERT_FILE", str(expected / "server.crt"))
    try:
        assert health.main(["API"]) == 1
    finally:
        server.shutdown()
    assert "unhealthy: https://localhost:" in capsys.readouterr().err


def test_the_ca_can_be_named_and_a_self_issued_certificate_is_its_own(tmp_path, monkeypatch):
    identity = issue(tmp_path, "api")
    monkeypatch.setenv("API_TLS_CERT_FILE", str(identity / "server.crt"))
    monkeypatch.setenv("API_TLS_CA_FILE", str(tmp_path / "api-ca" / "ca.crt"))
    assert health.target("API")[0] == "https://localhost:8443/healthz"
    monkeypatch.delenv("API_TLS_CA_FILE")
    (identity / "ca.crt").unlink()
    loaded = []
    monkeypatch.setattr(ssl, "create_default_context", lambda cafile: loaded.append(cafile))
    health.target("API")
    assert loaded == [str(identity / "server.crt")]


def test_plain_http_has_nothing_to_verify(monkeypatch):
    server = serve(None)
    monkeypatch.setenv("API_TLS_ENABLED", "false")
    monkeypatch.setenv("API_PORT", str(server.server_address[1]))
    try:
        assert health.target("API")[1] is None
        assert health.main(["API"]) == 0
    finally:
        server.shutdown()


def test_each_service_has_its_own_port_by_default(monkeypatch):
    monkeypatch.setenv("REVIEW_TLS_ENABLED", "false")
    assert health.target("REVIEW")[0] == "http://127.0.0.1:8444/healthz"
    monkeypatch.setenv("SOMETHING_TLS_ENABLED", "false")
    assert health.target("SOMETHING")[0] == "http://127.0.0.1:8443/healthz"


def test_root_is_given_up_before_anything_is_read_or_asked(monkeypatch):
    """The auth and review services start as root to take what they write,
    so Docker runs their checks as root -- every ten seconds, for as long as
    the stack is up, until 7.0. The check becomes the account first: the
    settings, the certificate and the connection all come after."""
    order: list = []
    monkeypatch.setattr(health, "target", lambda prefix: order.append(("target", prefix)) or ("http://127.0.0.1:9/healthz", None))
    assert health.main(["auth"], become=lambda account: order.append(("become", account))) == 1
    assert order == [("become", "nl2sql"), ("target", "AUTH")]
    # Even a check asked wrongly has given root up before it says so.
    order.clear()
    assert health.main([], become=lambda account: order.append(("become", account))) == 2
    assert order == [("become", "nl2sql")]


def test_the_real_drop_is_the_one_it_uses(monkeypatch):
    """`privileges.become`, as every service that starts as root uses, with
    nothing named to own: the check writes nothing."""
    seen: list = []
    monkeypatch.setattr(privileges, "become", lambda *args, **kwargs: seen.append((args, kwargs)))
    assert health.main([]) == 2
    assert seen == [(("nl2sql",), {})]


@pytest.mark.parametrize("refusal", [KeyError("getpwnam(): name not found: 'nl2sql'"), PermissionError(1, "Operation not permitted")])
def test_a_check_that_cannot_give_root_up_is_unhealthy_and_asks_nothing(monkeypatch, capsys, refusal):
    """No such account in the image, or a container that was not given
    SETUID: either way it is root's check, and root's check is not run."""
    def become(account):
        raise refusal

    monkeypatch.setattr(health, "target", lambda prefix: pytest.fail("asked as root"))
    assert health.main(["AUTH"], become=become) == 1
    assert "unhealthy: started as root, and could not become nl2sql" in capsys.readouterr().err


def test_it_takes_one_prefix(capsys):
    assert health.main([]) == 2
    assert "usage: python -m nl2sql_common.health PREFIX" in capsys.readouterr().err


def test_the_module_runs_main(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["health"])
    with pytest.raises(SystemExit) as exit_:
        runpy.run_module("nl2sql_common.health", run_name="__main__", alter_sys=True)
    assert exit_.value.code == 2
