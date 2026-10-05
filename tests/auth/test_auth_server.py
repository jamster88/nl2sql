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
    assert "nl2sql-postgres:5432/nl2sql_retail (sslmode=verify-full, against /etc/nl2sql/pg-tls/server.crt)" in text
    assert "/directory/v1 on port 8447 only (AUTH_DIRECTORY_PORT)" in text
    assert "(sslmode=require)" in server.banner(AuthSettings(db_sslmode="require"))
    assert "/directory/v1, on the sign-in port" in server.banner(AuthSettings(directory_port=0))
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
    assert "names that include nl2sql-auth" in capsys.readouterr().err


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


def test_root_gives_the_account_both_key_directories_before_anything_is_read(capsys, monkeypatch, tmp_path):
    """V6-31: root only long enough to hand over what it writes -- with what
    an older release wrote there as root -- and the key is read as the account."""
    monkeypatch.setenv("AUTH_SIGNING_KEY_FILE", str(tmp_path / "data" / "session.key"))
    monkeypatch.setenv("AUTH_PUBLIC_KEY_FILE", str(tmp_path / "keys" / "session.pub"))
    asked = []

    def become(account, *, own, recursive):
        asked.append((account, tuple(own), recursive))
        return True

    assert server.main(["--no-tls"], run=lambda *a, **k: None, become=become) == 0
    assert asked == [("nl2sql", (str(tmp_path / "data"), str(tmp_path / "keys")), True)]
    assert "running as nl2sql" in capsys.readouterr().out


def test_the_real_server_is_uvicorn(monkeypatch, tmp_path):
    """With no directory port of its own, one socket, as before 6.1."""
    import uvicorn

    monkeypatch.setenv("AUTH_SIGNING_KEY_FILE", str(tmp_path / "session.key"))
    monkeypatch.setenv("AUTH_PUBLIC_KEY_FILE", str(tmp_path / "session.pub"))
    monkeypatch.setenv("AUTH_HOST", "127.0.0.1")
    monkeypatch.setenv("AUTH_PORT", "0")
    monkeypatch.setenv("AUTH_DIRECTORY_PORT", "0")
    ran = []

    class Server:
        def __init__(self, config):
            ran.append(config)

        def run(self, sockets=None):
            ran.append(sockets)

    monkeypatch.setattr(uvicorn, "Server", Server)
    assert server.main(["--no-tls"]) == 0
    config, sockets = ran
    assert (config.host, config.port) == ("127.0.0.1", 0) and sockets is None, "port 0: one socket"


def test_two_ports_are_two_bound_sockets_under_one_server():
    import socket as sockets_module

    ran = []

    class Server:
        def __init__(self, config):
            ran.append(config)

        def run(self, sockets=None):
            ran.append(sockets)

    server.serve(object(), host="127.0.0.1", port=0, directory_port=0, server_factory=Server)
    assert ran[1] is None
    ran.clear()
    with sockets_module.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
    server.serve(object(), host="127.0.0.1", port=0, directory_port=free, server_factory=Server, log_level="info")
    config, bound = ran
    assert config.log_level == "info"
    assert [sock.getsockname()[1] for sock in bound][1] == free
    for sock in bound:
        sock.close()


def test_an_ipv6_host_binds_an_ipv6_socket():
    sock = server._listening("::1", 0)
    assert sock.family.name == "AF_INET6"
    sock.close()


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
