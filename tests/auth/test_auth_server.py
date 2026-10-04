"""Starting the auth service: flags, the banner, the key, the certificate."""

from __future__ import annotations

import pytest

from nl2sql_auth import server
from nl2sql_auth.settings import AuthSettings


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for name in ("AUTH_HOST", "AUTH_PORT", "AUTH_TLS_ENABLED", "AUTH_ROLESYNC_DB_URL", "LDAP_SERVICE_PASSWORD"):
        monkeypatch.delenv(name, raising=False)


def test_flags_override_the_environment_only_when_given(monkeypatch):
    monkeypatch.setenv("AUTH_PORT", "9000")
    assert server.settings_from_args(server.parse_args([])).port == 9000
    settings = server.settings_from_args(server.parse_args(["--host", "127.0.0.1", "--port", "9001", "--no-tls"]))
    assert (settings.host, settings.port, settings.tls_enabled) == ("127.0.0.1", 9001, False)
    assert server.settings_from_args(server.parse_args(["--tls"])).tls_enabled


def test_the_banner_says_where_passwords_go_and_hides_the_sync_password():
    text = server.banner(AuthSettings(rolesync_url="postgresql://sync:secret@db/retail", ldap_service_password="x"))
    assert "secret" not in text and "postgresql://sync:***@db/retail" in text
    assert "nl2sql-postgres:5432/nl2sql_retail (sslmode=prefer)" in text
    assert "/directory/v1" in text
    replica = server.banner(AuthSettings(ldap_mode="replica"))
    assert "off: the directory is a replica" in replica
    assert "(not set)" in replica and "! AUTH_ROLESYNC_DB_URL is not set" in replica


@pytest.mark.parametrize(("url", "shown"), [(None, "(not set)"), ("postgresql://db/retail", "postgresql://db/retail")])
def test_redaction_leaves_a_url_without_credentials_alone(url, shown):
    assert server._redacted(url) == shown


def test_print_settings_prints_and_exits(capsys):
    assert server.main(["--print-settings"]) == 0
    assert "nl2sql auth service" in capsys.readouterr().out


def test_without_the_apis_certificate_it_will_not_start_with_tls(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_TLS_CERT_FILE", str(tmp_path / "missing.crt"))
    assert server.main([], run=lambda *a, **k: None) == 2
    assert "covers nl2sql-auth" in capsys.readouterr().err


def test_a_key_that_cannot_be_read_stops_the_start(capsys, monkeypatch, tmp_path):
    key = tmp_path / "session.key"
    key.write_text("not a key")
    monkeypatch.setenv("AUTH_SIGNING_KEY_FILE", str(key))
    assert server.main(["--no-tls"], run=lambda *a, **k: None) == 2
    assert "error: the signing key" in capsys.readouterr().err


def test_a_start_makes_the_key_builds_the_app_and_serves_it(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_SIGNING_KEY_FILE", str(tmp_path / "data" / "session.key"))
    monkeypatch.setenv("AUTH_PUBLIC_KEY_FILE", str(tmp_path / "keys" / "session.pub"))
    cert, key = tmp_path / "server.crt", tmp_path / "server.key"
    cert.write_text("c")
    key.write_text("k")
    monkeypatch.setenv("AUTH_TLS_CERT_FILE", str(cert))
    monkeypatch.setenv("AUTH_TLS_KEY_FILE", str(key))
    served = {}

    def run(app, **options):
        served.update(options, app=app)

    assert server.main([], run=run) == 0
    assert served["ssl_certfile"] == str(cert) and served["ssl_keyfile"] == str(key)
    assert served["port"] == 8446 and served["app"].title == "nl2sql auth"
    assert "made a signing key" in capsys.readouterr().out
    served.clear()
    assert server.main(["--no-tls"], run=run) == 0
    assert served["ssl_certfile"] is None and served["ssl_keyfile"] is None


def test_the_real_server_is_uvicorn(monkeypatch, tmp_path):
    import uvicorn

    monkeypatch.setenv("AUTH_SIGNING_KEY_FILE", str(tmp_path / "session.key"))
    monkeypatch.setenv("AUTH_PUBLIC_KEY_FILE", str(tmp_path / "session.pub"))
    called = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: called.append(options["host"]))
    assert server.main(["--no-tls"]) == 0
    assert called == ["0.0.0.0"]


def test_the_database_check_names_the_role_it_reached():
    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, query):
            assert query == "SELECT current_user"
            return self

        def fetchone(self):
            return ("nl2sql_rolesync",)

    opened = []
    check = server.database_check("postgresql://x", connect=lambda url, **k: opened.append(url) or Conn())
    assert check() == "reachable as nl2sql_rolesync" and opened == ["postgresql://x"]
    with pytest.raises(RuntimeError, match="AUTH_ROLESYNC_DB_URL is not set"):
        server.database_check(None)()


def test_the_module_runs_main(monkeypatch):
    import runpy

    monkeypatch.setattr(server, "main", lambda argv=None: 0)
    with pytest.raises(SystemExit) as exited:
        runpy.run_module("nl2sql_auth", run_name="__main__")
    assert exited.value.code == 0
