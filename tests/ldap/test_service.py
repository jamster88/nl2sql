"""Running the directory: the supervisor, the health check, import and sync.

slapd is a fake process and the directory an in-memory one; what is under
test is the order things happen in and what each failure turns into. The
real process, signals and all, runs in tests/ldap/test_ldap_image.py.
"""

from __future__ import annotations

import signal

import pytest
from ldap3.core.exceptions import LDAPException

from nl2sql_ldap import directory as directory_module
from nl2sql_ldap import service
from nl2sql_ldap.directory import DirectoryError
from nl2sql_ldap.settings import REPLICA, DirectorySettings, UpstreamSettings

from .conftest import BASE, mock_connection, store_password


class FakeProcess:
    def __init__(self, code: int = 0, alive: bool = True) -> None:
        self.code = code
        self.alive = alive
        self.terminated = 0

    def poll(self):
        return None if self.alive else self.code

    def terminate(self):
        self.terminated += 1

    def wait(self):
        return self.code


@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setattr(service, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(service, "CONFIG_FILE", str(tmp_path / "slapd.conf"))
    monkeypatch.setattr(directory_module, "_password_modify", store_password)
    return tmp_path


def _settings(paths, **overrides) -> DirectorySettings:
    values = dict(
        service_password="svc",
        admin_password="admin-password",
        tls_cert_file=str(paths / "tls" / "ldap.crt"),
        tls_key_file=str(paths / "tls" / "ldap.key"),
    )
    values.update(overrides)
    return DirectorySettings(**values)


def _replica(paths, **upstream) -> DirectorySettings:
    values = {"uri": "ldaps://dc1", "bind_dn": "x", "bind_password": "y", "base_dn": "z", **upstream}
    return _settings(paths, mode=REPLICA, admin_password=None, upstream=UpstreamSettings(**values))


class Harness:
    """`serve` with every collaborator recorded."""

    def __init__(self, process=None, conn="mock"):
        self.process = process or FakeProcess()
        self.spawned: list = []
        self.handlers: dict = {}
        self.replicators: list = []
        self.conn = mock_connection() if conn == "mock" else conn

    def spawn(self, command):
        self.spawned.append(command)
        return self.process

    def install(self, signum, handler):
        self.handlers[signum] = handler

    def replicator(self, settings, local):
        harness = self

        class Fake:
            def run_forever(self, stop):
                harness.replicators.append((settings, local, stop))

        return Fake()

    def wait(self, connect, alive):
        self.alive = alive
        return self.conn

    def serve(self, settings):
        return service.serve(
            settings,
            spawn=self.spawn,
            connect=lambda: mock_connection(),
            install=self.install,
            replicator=self.replicator,
            wait=self.wait,
        )


def test_a_directory_without_its_passwords_does_not_start(paths, capsys):
    assert Harness().serve(DirectorySettings()) == 2
    assert capsys.readouterr().err.count("error: ") == 2


def test_a_standalone_start_writes_the_config_starts_slapd_and_bootstraps(paths, capsys):
    harness = Harness()
    assert harness.serve(_settings(paths)) == 0
    [command] = harness.spawned
    assert command[:4] == ["slapd", "-d", "stats", "-f"] and command[-2] == "-h"
    assert (paths / "slapd.conf").read_text().startswith("# Written by nl2sql_ldap")
    assert (paths / "run").is_dir() and (paths / "data").is_dir()
    out = capsys.readouterr().out
    assert "wrote a development certificate" in out
    assert "first start: created admin" in out
    assert f"nl2sql directory (standalone) serving {BASE}" in out
    assert harness.replicators == []
    assert harness.alive() is True, "slapd is alive while it has not exited"


def test_stopping_the_container_stops_slapd(paths):
    harness = Harness()
    harness.serve(_settings(paths))
    assert set(harness.handlers) == {signal.SIGTERM, signal.SIGINT}
    harness.handlers[signal.SIGTERM](signal.SIGTERM, None)
    assert harness.process.terminated == 1


def test_a_replica_starts_its_copy_beside_slapd(paths, capsys):
    harness = Harness()
    assert harness.serve(_replica(paths, starttls=False)) == 0
    [(upstream, local, stop)] = harness.replicators
    assert upstream.uri == "ldaps://dc1"
    assert local().layout.base_dn == BASE
    assert stop.is_set(), "the copy is told to stop once slapd has exited"
    out = capsys.readouterr()
    assert "replica: people and groups come from the primary" in out.out


def test_warnings_are_printed_before_starting(paths, capsys):
    """A primary over clear text, allowed by name: it starts, and says what
    that costs before anything else."""
    harness = Harness()
    assert harness.serve(_replica(paths, uri="ldap://dc1", allow_cleartext=True)) == 0
    err = capsys.readouterr().err
    assert err.startswith("warning: LDAP_UPSTREAM_URI is ldap://dc1 without StartTLS")
    assert "clear text" in err


def test_a_clear_text_primary_not_allowed_by_name_stops_the_start(paths, capsys):
    """V6-57: the replica would pass every password through in clear."""
    harness = Harness()
    assert harness.serve(_replica(paths, uri="ldap://dc1")) == 2
    assert harness.spawned == [] and harness.replicators == []
    assert "error: " in capsys.readouterr().err


def test_a_certificate_that_cannot_be_had_stops_the_start(paths, capsys):
    harness = Harness()
    assert harness.serve(_settings(paths, tls_generate=False)) == 2
    assert harness.spawned == []
    assert "LDAP_TLS_GENERATE=false" in capsys.readouterr().err


@pytest.mark.parametrize("code", [0, 3])
def test_slapd_that_never_answers_is_an_error(paths, capsys, code):
    harness = Harness(process=FakeProcess(code=code), conn=None)
    assert harness.serve(_settings(paths)) == (code or 1)
    assert harness.process.terminated == 1
    assert "did not start answering" in capsys.readouterr().err


def test_a_bootstrap_the_directory_refuses_stops_slapd(paths, capsys, monkeypatch):
    def refuse(directory, settings):
        raise DirectoryError("insufficientAccessRights", "creating x: refused")

    monkeypatch.setattr(service, "bootstrap", refuse)
    harness = Harness(process=FakeProcess(code=0))
    assert harness.serve(_settings(paths)) == 1
    assert harness.process.terminated == 1
    assert "preparing the directory: creating x: refused" in capsys.readouterr().err


# --- waiting for slapd --------------------------------------------------------


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_wait_for_retries_until_slapd_answers():
    clock = Clock()
    attempts = []

    def connect():
        attempts.append(1)
        if len(attempts) < 3:
            raise LDAPException("socket not there yet")
        return "connection"

    assert service.wait_for(connect, lambda: True, sleep=clock.sleep, clock=clock) == "connection"
    assert len(attempts) == 3


def test_wait_for_gives_up_when_slapd_dies_or_time_runs_out():
    clock = Clock()

    def never():
        raise OSError("no socket")

    assert service.wait_for(never, lambda: False, sleep=clock.sleep, clock=clock) is None
    assert service.wait_for(never, lambda: True, seconds=1, sleep=clock.sleep, clock=clock) is None
    assert clock.now >= 1


def test_the_local_connection_is_root_on_the_socket(monkeypatch):
    seen = {}

    class FakeServer:
        def __init__(self, url, **options):
            seen["url"] = url

    class FakeConnection:
        def __init__(self, server, **options):
            seen.update(options)

    monkeypatch.setattr(service, "Server", FakeServer)
    monkeypatch.setattr(service, "Connection", FakeConnection)
    service.local_connection()
    assert seen["url"] == "ldapi:///var/lib/openldap/run/ldapi"
    assert seen["sasl_mechanism"] == service.EXTERNAL and seen["auto_bind"] is True


# --- the other commands ---------------------------------------------------------


def test_health_is_the_base_entry_answering(capsys):
    conn = mock_connection()
    assert service.health(connect=lambda: conn, base_dn=BASE) == 1
    assert "is not there yet" in capsys.readouterr().err
    conn = mock_connection()
    conn.add(BASE, ["top", "dcObject", "organization"], {"dc": "nl2sql", "o": "nl2sql"})
    assert service.health(connect=lambda: conn, base_dn=BASE) == 0

    def down():
        raise LDAPException("no socket")

    assert service.health(connect=down, base_dn=BASE) == 1
    assert "unhealthy: no socket" in capsys.readouterr().err


def _populated():
    conn = mock_connection()
    from nl2sql_ldap.directory import Directory
    from nl2sql_ldap.layout import Layout

    directory = Directory(conn, Layout(BASE), set_password=store_password)
    directory.ensure_base("nl2sql")
    directory.ensure_groups(["nl2sql-users"])
    return conn


def test_import_loads_a_file_into_a_standalone_directory(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(directory_module, "_password_modify", store_password)
    conn = _populated()
    good = tmp_path / "people.csv"
    good.write_text("uid,groups,password\nalice,nl2sql-users,pw\n")
    assert service.import_file(str(good), DirectorySettings(), connect=lambda: conn) == 0
    assert "people.csv: 1 created, 0 updated, 1 passwords set, 1 memberships added" in capsys.readouterr().out
    bad = tmp_path / "more.csv"
    bad.write_text("uid\nBad Name\n")
    assert service.import_file(str(bad), DirectorySettings(), connect=lambda: _populated()) == 1
    assert "not a usable login name" in capsys.readouterr().out


def test_import_refuses_a_replica_and_a_missing_file(tmp_path, capsys):
    upstream = UpstreamSettings(uri="ldaps://d", bind_dn="x", bind_password="y", base_dn="z")
    assert service.import_file("x.csv", DirectorySettings(mode=REPLICA, upstream=upstream)) == 2
    assert "added on the primary" in capsys.readouterr().err
    assert service.import_file(str(tmp_path / "missing.csv"), DirectorySettings()) == 2
    assert "is not a file" in capsys.readouterr().err


@pytest.mark.parametrize("succeeded", [True, False])
def test_sync_runs_one_copy_in_a_replica(succeeded):
    upstream = UpstreamSettings(uri="ldaps://d", bind_dn="x", bind_password="y", base_dn="z")
    made = []

    class Fake:
        def __init__(self, settings, local):
            made.append(local().layout.base_dn)

        def run_once(self):
            return succeeded

    settings = DirectorySettings(mode=REPLICA, upstream=upstream)
    assert service.sync(settings, replicator=Fake, connect=mock_connection) == (0 if succeeded else 1)
    assert made == [BASE]


def test_sync_refuses_a_standalone_directory(capsys):
    assert service.sync(DirectorySettings()) == 2
    assert "no primary to copy from" in capsys.readouterr().err


@pytest.fixture
def no_env(monkeypatch):
    for name in ("LDAP_MODE", "LDAP_UPSTREAM_URI"):
        monkeypatch.delenv(name, raising=False)


def test_main_dispatches_each_command(monkeypatch, no_env, capsys):
    calls = []
    became = []
    monkeypatch.setattr(service, "become", lambda **kwargs: became.append(kwargs["own"]))
    monkeypatch.setattr(service, "health", lambda **kwargs: calls.append(("health", kwargs)) or 0)
    monkeypatch.setattr(service, "import_file", lambda path, settings: calls.append(("import", path)) or 0)
    monkeypatch.setattr(service, "sync", lambda settings: calls.append(("sync",)) or 0)
    monkeypatch.setattr(service, "serve", lambda settings: calls.append(("serve",)) or 0)
    for argv in (["health"], ["import", "f.csv"], ["sync"], ["serve"], []):
        assert service.main(argv) == 0
    assert calls == [
        ("health", {"base_dn": BASE}),
        ("import", "f.csv"),
        ("sync",),
        ("serve",),
        ("serve",),
    ]
    assert service.main(["config"]) == 0
    assert capsys.readouterr().out.startswith("# Written by nl2sql_ldap")
    # Every command gives root up; only serving hands the account what it writes.
    serving = ("/var/lib/openldap/run", "/var/lib/openldap/openldap-data", "/etc/nl2sql/ldap-tls", "/etc/nl2sql/ldap-tls")
    assert became == [(), (), (), serving, serving, ()]


class _Entry:
    pw_uid, pw_gid, pw_dir = 100, 101, "/var/lib/openldap"


def _becoming(uid: int, *, refuse_chown: bool = False):
    done: list = []

    def chown(path, uid, gid):
        if refuse_chown and path == "/etc/certs":
            raise OSError(30, "Read-only file system")
        done.append(("chown", path, uid, gid))

    environ: dict = {}
    service.become(
        own=("/var/lib/openldap/run", "/etc/certs"),
        getuid=lambda: uid,
        lookup=lambda name: done.append(("lookup", name)) or _Entry,
        makedirs=lambda path, exist_ok: done.append(("makedirs", path, exist_ok)),
        chown=chown,
        setgroups=lambda groups: done.append(("setgroups", groups)),
        setgid=lambda gid: done.append(("setgid", gid)),
        setuid=lambda uid: done.append(("setuid", uid)),
        environ=environ,
    )
    return done, environ


def test_root_hands_the_account_its_directories_then_becomes_it():
    """A named volume another container mounted first is created owned by
    root -- the certificate's, when the retail database starts first -- and
    only root can hand it over. Then root is given up, groups first: the
    other way round, setuid would leave no right to change the rest."""
    done, environ = _becoming(0)
    assert done == [
        ("lookup", "ldap"),
        ("makedirs", "/var/lib/openldap/run", True), ("chown", "/var/lib/openldap/run", 100, 101),
        ("makedirs", "/etc/certs", True), ("chown", "/etc/certs", 100, 101),
        ("setgroups", []), ("setgid", 101), ("setuid", 100),
    ]
    assert environ == {"HOME": "/var/lib/openldap"}


def test_a_directory_that_cannot_be_handed_over_is_left_as_it_is():
    """A certificate mounted read-only is the operator's; it is only written
    when there is none, and `tls.ensure` says so when that fails."""
    done, _ = _becoming(0, refuse_chown=True)
    assert ("chown", "/etc/certs", 100, 101) not in done
    assert done[-1] == ("setuid", 100)


def test_anyone_but_root_is_left_as_they_are():
    assert _becoming(100) == ([], {})


def test_become_defaults_to_the_real_environment(monkeypatch):
    """Not root here, so nothing is done -- which is what the defaults are
    exercised for: the real calls, wired."""
    assert service.become() is False


def test_main_refuses_settings_it_cannot_use(monkeypatch, capsys):
    monkeypatch.setenv("LDAP_MODE", "sideways")
    assert service.main([]) == 2
    assert "LDAP_MODE=sideways" in capsys.readouterr().err


def test_the_module_runs_main(monkeypatch):
    import runpy

    monkeypatch.setattr(service, "main", lambda argv=None: 0)
    with pytest.raises(SystemExit) as exited:
        runpy.run_module("nl2sql_ldap", run_name="__main__")
    assert exited.value.code == 0
