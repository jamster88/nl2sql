"""Starting the console: flags, the certificate it presents, the banner.

The certificate is the API's -- the console never writes one -- so what is
tested is what it does with the one it finds: present it, say which names
it covers, warn when `nl2sql-console` is not one of them, and refuse to
start with TLS on and nothing to present.
"""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

import pytest

from nl2sql_agent.api.tls import generate_self_signed
from nl2sql_agent.config import Settings
from nl2sql_agent.console import server
from nl2sql_agent.console.settings import ConsoleSettings

CONSOLE_VARIABLES = (
    "CONSOLE_HOST", "CONSOLE_PORT", "CONSOLE_TLS_ENABLED", "CONSOLE_TLS_CERT_FILE",
    "CONSOLE_TLS_KEY_FILE", "CONSOLE_TOKEN", "CONSOLE_MAX_ROWS", "CONSOLE_DOCS_ENABLED",
    "CONSOLE_LOG_LEVEL",
)


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    for name in CONSOLE_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def _certificate(tmp_path: Path, *names: str, days: int = 365) -> list[str]:
    cert, key = tmp_path / "server.crt", tmp_path / "server.key"
    generate_self_signed(cert, key, hostnames=names or ("localhost", "nl2sql-console"), days=days)
    return ["--cert", str(cert), "--key", str(key)]


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------


def test_the_defaults_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("CONSOLE_PORT", "9001")
    monkeypatch.setenv("CONSOLE_TOKEN", "from-env")
    monkeypatch.setenv("CONSOLE_TLS_ENABLED", "false")
    settings = server.settings_from_args(server.parse_args([]))
    assert (settings.port, settings.token, settings.tls_enabled) == (9001, "from-env", False)


def test_a_flag_beats_the_environment(monkeypatch):
    monkeypatch.setenv("CONSOLE_PORT", "9001")
    args = server.parse_args(
        ["--port", "9002", "--host", "127.0.0.1", "--no-tls", "--cert", "/c", "--key", "/k",
         "--token", "t", "--max-rows", "5", "--no-docs", "--log-level", "debug"]
    )
    assert server.settings_from_args(args) == ConsoleSettings(
        host="127.0.0.1", port=9002, tls_enabled=False, tls_cert_file="/c", tls_key_file="/k",
        token="t", max_rows=5, docs_enabled=False, log_level="debug",
    )


def test_every_flag_names_the_variable_it_overrides(capsys):
    """A flag whose help does not say which variable it stands in for is a
    second, undocumented way to configure the same thing."""
    with pytest.raises(SystemExit):
        server.parse_args(["--help"])
    text = " ".join(capsys.readouterr().out.split())
    for variable in CONSOLE_VARIABLES:
        assert f"({variable})" in text, f"no flag names {variable}"


@pytest.mark.parametrize(
    ("url", "shown"),
    [
        ("postgresql+psycopg://nl2sql_reader:secret@postgres:5432/nl2sql_retail",
         "postgresql+psycopg://nl2sql_reader:***@postgres:5432/nl2sql_retail"),
        ("postgresql://:secret@postgres/db", "postgresql://postgres/db"),
        ("postgresql://postgres/db", "postgresql://postgres/db"),
    ],
)
def test_a_password_is_never_printed(url, shown):
    assert server.redacted(url) == shown


# ---------------------------------------------------------------------------
# The certificate it presents
# ---------------------------------------------------------------------------


def test_with_tls_off_there_is_nothing_to_present():
    assert server.load_certificate(ConsoleSettings(tls_enabled=False)) is None


def test_with_half_a_pair_there_is_nothing_to_present(tmp_path):
    (tmp_path / "server.crt").write_text("x")
    settings = ConsoleSettings(tls_cert_file=str(tmp_path / "server.crt"), tls_key_file=str(tmp_path / "server.key"))
    assert server.load_certificate(settings) is None


def test_the_certificate_is_described_and_its_names_checked(tmp_path):
    flags = _certificate(tmp_path, "localhost", "nl2sql-console")
    settings = ConsoleSettings(tls_cert_file=flags[1], tls_key_file=flags[3])
    info = server.load_certificate(settings)
    assert info.self_signed is True
    assert info.missing_hostnames == []


def test_a_certificate_that_does_not_cover_the_console_is_marked(tmp_path):
    flags = _certificate(tmp_path, "localhost", "nl2sql-api")
    info = server.load_certificate(ConsoleSettings(tls_cert_file=flags[1], tls_key_file=flags[3]))
    assert info.missing_hostnames == ["nl2sql-console"]


# ---------------------------------------------------------------------------
# The banner
# ---------------------------------------------------------------------------


def test_the_banner_says_what_it_reads_and_under_which_limits():
    agent = Settings(
        database_url="postgresql+psycopg://nl2sql_reader:secret@postgres:5432/nl2sql_retail",
        statement_timeout_ms=30000, max_rows=50, max_plan_cost=1_000_000.0,
    )
    text = server.banner(ConsoleSettings(token="t"), agent, None, version="9.9.9")
    assert text.splitlines()[0] == "nl2sql SQL console 9.9.9"
    assert "nl2sql_reader:***@postgres:5432/nl2sql_retail (schema public)" in text
    assert "secret" not in text
    assert "timeout 30000 ms, 50 rows, plan cost 1,000,000" in text
    assert "up to 1000 per query" in text
    assert "auth           bearer token" in text
    assert "!" not in text, "a careful configuration has nothing to warn about"


def test_the_banner_warns_about_an_open_port():
    text = server.banner(ConsoleSettings(), Settings(), None)
    assert "auth           NONE" in text
    assert "! No CONSOLE_TOKEN is set" in text


def test_the_banner_names_the_certificate(tmp_path):
    flags = _certificate(tmp_path, "localhost", "nl2sql-console")
    info = server.load_certificate(ConsoleSettings(tls_cert_file=flags[1], tls_key_file=flags[3]))
    text = server.banner(ConsoleSettings(token="t"), Settings(), info)
    assert "self-signed, written by the agent API, for localhost, nl2sql-console" in text
    # Trusting a self-signed certificate is the API's clients' business, and
    # said in the API's own banner; here it would only be noise.
    assert "!" not in text


def test_the_banner_says_what_to_do_about_a_missing_name(tmp_path):
    flags = _certificate(tmp_path, "localhost")
    info = server.load_certificate(ConsoleSettings(tls_cert_file=flags[1], tls_key_file=flags[3]))
    text = " ".join(server.banner(ConsoleSettings(token="t"), Settings(), info).split())
    assert "does not cover nl2sql-console" in text
    assert "Add it to API_TLS_HOSTNAMES and restart the API" in text


def test_the_banner_passes_on_a_certificate_about_to_expire(tmp_path):
    flags = _certificate(tmp_path, "nl2sql-console", days=5)
    info = server.load_certificate(ConsoleSettings(tls_cert_file=flags[1], tls_key_file=flags[3]))
    assert "expires in" in server.banner(ConsoleSettings(token="t"), Settings(), info)


def test_a_certificate_the_ca_issued_is_named_as_such(tmp_path):
    flags = _certificate(tmp_path, "nl2sql-console")
    info = server.load_certificate(ConsoleSettings(tls_cert_file=flags[1], tls_key_file=flags[3]))
    info.self_signed = False
    assert "CA-issued, written by the agent API" in server.banner(ConsoleSettings(token="t"), Settings(), info)


def test_a_certificate_with_no_names_says_so(tmp_path):
    flags = _certificate(tmp_path, "nl2sql-console")
    info = server.load_certificate(ConsoleSettings(tls_cert_file=flags[1], tls_key_file=flags[3]))
    info.hostnames = []
    assert "for no names" in server.banner(ConsoleSettings(token="t"), Settings(), info)


# ---------------------------------------------------------------------------
# Starting, and refusing to
# ---------------------------------------------------------------------------


def test_building_gives_the_app_its_settings_and_its_certificate(tmp_path):
    app, settings, certificate = server.build(_certificate(tmp_path))
    assert settings.tls_enabled is True
    assert certificate is not None
    assert app.state.console_settings is settings


def test_with_tls_on_and_no_certificate_it_refuses_to_start(tmp_path, capsys):
    code = server.main(["--cert", str(tmp_path / "none.crt"), "--key", str(tmp_path / "none.key")])
    assert code == 2
    said = " ".join(capsys.readouterr().err.split())
    assert "presents the certificate the agent API generates" in said
    assert "API_TLS_HOSTNAMES covers nl2sql-console" in said


def test_the_openapi_document_can_be_printed_without_serving_anything(capsys):
    assert server.main(["--no-tls", "--print-openapi"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert "/v1/query" in document["paths"]


def test_the_server_is_started_with_the_certificate_it_presents(tmp_path, monkeypatch, capsys):
    import uvicorn

    captured: dict = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: captured.update(kwargs, app=app))
    flags = _certificate(tmp_path)
    assert server.main([*flags, "--port", "9443", "--log-level", "warning"]) == 0

    assert captured["ssl_certfile"] == flags[1]
    assert captured["ssl_keyfile"] == flags[3]
    assert (captured["port"], captured["log_level"]) == (9443, "warning")
    assert capsys.readouterr().out.startswith("nl2sql SQL console")


def test_without_tls_no_certificate_is_handed_over(monkeypatch, capsys):
    import uvicorn

    captured: dict = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: captured.update(kwargs))
    assert server.main(["--no-tls"]) == 0
    assert captured["ssl_certfile"] is None and captured["ssl_keyfile"] is None


def test_it_runs_as_a_module(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["nl2sql_agent.console", "--no-tls", "--print-openapi"])
    with pytest.raises(SystemExit) as exited:
        runpy.run_module("nl2sql_agent.console", run_name="__main__")
    assert exited.value.code == 0
    assert '"openapi"' in capsys.readouterr().out


def test_importing_the_entry_point_starts_nothing(monkeypatch):
    monkeypatch.setattr(server, "main", lambda *a, **k: pytest.fail("importing started the server"))
    namespace = runpy.run_module(
        "nl2sql_agent.console.__main__", run_name="nl2sql_agent.console.__main__"
    )
    assert "main" in namespace
