"""Starting the server: the flags, the certificate policy, and the banner.

The banner is tested as carefully as the routes because it is the only place
three silent misconfigurations become visible: TLS off when someone thought
it was on, a self-signed certificate their HTTP library will refuse, and an
open port with no token. None of the three produces an error the operator
sees from the client side.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest
from nl2sql_agent import __version__
from nl2sql_agent.api.server import (
    api_settings_from_args,
    banner,
    build,
    main,
    parse_args,
)
from nl2sql_agent.api.settings import ApiSettings
from nl2sql_agent.api.tls import generate_self_signed


@pytest.fixture
def certs(tmp_path: Path) -> list[str]:
    """Flags pointing a run at a throwaway certificate location."""
    return ["--cert", str(tmp_path / "server.crt"), "--key", str(tmp_path / "server.key")]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    import os

    for name in list(os.environ):
        if name.startswith("API_"):
            monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------


def test_the_defaults_come_from_the_environment(monkeypatch):
    """The container is configured by compose, so the environment is the
    primary interface and the flags are the override.
    """
    monkeypatch.setenv("API_PORT", "9000")
    monkeypatch.setenv("API_TOKEN", "from-env")
    args = parse_args([])
    assert args.port == 9000 and args.token == "from-env"


def test_a_flag_beats_the_environment(monkeypatch):
    monkeypatch.setenv("API_PORT", "9000")
    assert parse_args(["--port", "1234"]).port == 1234


def test_tls_can_be_turned_off_from_the_command_line():
    assert api_settings_from_args(parse_args(["--no-tls"])).tls_enabled is False
    assert api_settings_from_args(parse_args(["--tls"])).tls_enabled is True


def test_self_signed_can_be_refused_from_the_command_line():
    """The same switch as API_TLS_ALLOW_SELF_SIGNED, for someone debugging a
    deployment by hand.
    """
    assert api_settings_from_args(parse_args(["--no-allow-self-signed"])).tls_allow_self_signed is False


def test_repeatable_flags_replace_the_defaults_rather_than_adding_to_them():
    settings = api_settings_from_args(
        parse_args(["--hostname", "api.internal", "--hostname", "10.0.0.5",
                    "--cors-origin", "https://gui.example.com"])
    )
    assert settings.tls_hostnames == ("api.internal", "10.0.0.5")
    assert settings.cors_origins == ("https://gui.example.com",)


def test_omitting_a_repeatable_flag_leaves_the_configured_value(monkeypatch):
    monkeypatch.setenv("API_CORS_ORIGINS", "https://from-env.example.com")
    assert api_settings_from_args(parse_args([])).cors_origins == ("https://from-env.example.com",)


def test_every_flag_names_the_environment_variable_it_overrides():
    """So `--help` is also the configuration reference, which is where
    someone reads it from inside a container.
    """
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), pytest.raises(SystemExit):
        parse_args(["--help"])
    text = buffer.getvalue()
    for name in ("API_HOST", "API_PORT", "API_TLS_ENABLED", "API_TOKEN",
                 "API_TLS_ALLOW_SELF_SIGNED", "API_ALLOW_PRINCIPAL"):
        assert name in text, f"--help never mentions {name}"


# ---------------------------------------------------------------------------
# Building a run
# ---------------------------------------------------------------------------


def test_building_generates_a_certificate_and_an_application(certs):
    app, api, certificate = build(certs)
    assert certificate is not None and certificate.generated is True
    assert api.scheme == "https"
    assert app.title == "NL2SQL agent" and app.version == __version__


def test_building_without_tls_presents_no_certificate(certs):
    _, api, certificate = build([*certs, "--no-tls"])
    assert certificate is None and api.scheme == "http"


def test_refusing_self_signed_stops_the_process_with_a_distinct_code(certs, capsys):
    """Exit 2, not 1: a certificate the operator has to fix is a different
    thing from a server that started and then failed.
    """
    assert main([*certs, "--no-allow-self-signed"]) == 2
    assert "API_TLS_ALLOW_SELF_SIGNED=false" in capsys.readouterr().err


def test_the_openapi_document_can_be_printed_without_serving_anything(certs, capsys):
    """How a GUI build step generates its client: no container, no port."""
    assert main([*certs, "--print-openapi"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert "/v1/questions" in document["paths"]
    assert document["info"]["version"] == __version__


# ---------------------------------------------------------------------------
# The banner
# ---------------------------------------------------------------------------


def test_the_banner_prints_a_url_a_client_can_use(tmp_path):
    info = generate_self_signed(tmp_path / "c", tmp_path / "k", hostnames=("localhost",))
    text = banner(ApiSettings(host="0.0.0.0", port=8443, token="t",
                              cors_origins=("https://g",)), info)
    assert "https://localhost:8443/openapi.json" in text
    assert "0.0.0.0:8443" in text, "the bind address is still worth showing"


def test_the_banner_names_the_certificate_and_its_fingerprint(tmp_path):
    """The fingerprint is how a client pins a development certificate rather
    than turning verification off.
    """
    info = generate_self_signed(tmp_path / "c", tmp_path / "k", hostnames=("nl2sql-api",))
    text = banner(ApiSettings(token="t", cors_origins=("https://g",)), info)
    assert "self-signed" in text and "nl2sql-api" in text
    assert info.fingerprint_sha256 in text


def test_the_banner_warns_about_plain_http():
    text = banner(ApiSettings(tls_enabled=False, token="t", cors_origins=("https://g",)), None)
    assert "WARNING" in text and "clear text" in text


def test_the_banner_warns_about_an_open_port():
    text = banner(ApiSettings(token=None), None)
    assert "auth        none" in text
    assert "No API_TOKEN" in text


def test_the_banner_warns_that_a_self_signed_certificate_must_be_trusted(tmp_path):
    info = generate_self_signed(tmp_path / "c", tmp_path / "k", hostnames=("localhost",))
    text = banner(ApiSettings(token="t", cors_origins=("https://g",)), info)
    assert "--cacert" in text


def test_a_properly_configured_server_prints_no_warnings(tmp_path):
    from nl2sql_agent.api.tls import CertificateInfo
    import datetime as dt

    real = CertificateInfo(
        path="/etc/nl2sql/tls/server.crt",
        subject="CN=nl2sql-api",
        issuer="CN=Example CA",
        not_before=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1),
        not_after=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=90),
        hostnames=["nl2sql-api"],
        self_signed=False,
        fingerprint_sha256="ab" * 32,
    )
    text = banner(ApiSettings(token="t", cors_origins=("https://gui.example.com",)), real)
    assert "WARNING" not in text
    assert "CA-issued" in text


def test_the_banner_follows_the_docs_switch():
    assert "/docs" in banner(ApiSettings(docs_enabled=True), None)
    assert "/docs" not in banner(ApiSettings(docs_enabled=False), None)


# ---------------------------------------------------------------------------
# Handing off to uvicorn
# ---------------------------------------------------------------------------


def test_the_server_is_started_with_the_certificate_it_decided_on(certs, monkeypatch, capsys):
    """The last line of wiring: a certificate chosen correctly and then not
    passed to uvicorn is a server that serves plain HTTP on the HTTPS port.
    """
    import uvicorn

    captured: dict = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: captured.update(kwargs, app=app))

    assert main([*certs, "--port", "8443"]) == 0
    assert captured["port"] == 8443
    assert captured["ssl_certfile"] == certs[1]
    assert captured["ssl_keyfile"] == certs[3]
    # One process: several would each hold their own job store, and a client
    # polling the job it just created would reach the wrong one.
    assert captured["workers"] is None
    assert "REST API" in capsys.readouterr().out


def test_without_tls_no_certificate_is_handed_over(certs, monkeypatch):
    import uvicorn

    captured: dict = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: captured.update(kwargs))
    assert main([*certs, "--no-tls"]) == 0
    assert captured["ssl_certfile"] is None and captured["ssl_keyfile"] is None


def test_the_module_can_be_run_as_a_module(certs, monkeypatch, capsys):
    """`python -m nl2sql_agent.api` is the container's command, so the entry
    point itself is worth executing rather than only importing.
    """
    import runpy
    import sys

    monkeypatch.setattr(sys, "argv", ["nl2sql_agent.api", *certs, "--print-openapi"])
    with pytest.raises(SystemExit) as raised:
        runpy.run_module("nl2sql_agent.api", run_name="__main__")
    assert raised.value.code == 0
    assert "/v1/questions" in capsys.readouterr().out
