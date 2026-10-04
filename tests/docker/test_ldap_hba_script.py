"""`docker/ldap_hba.sh`, run against a pg_hba.conf of its own.

The script runs inside the retail container on every start and rewrites
the file Postgres decides who may connect by, so every way it can go wrong
is run here: the block it writes, rewriting it, taking it out, and the file
it puts back when Postgres says the new rules do not parse. `psql` is a fake
that records what it was asked and answers how many rules failed.

That the rules really let a directory user in, and keep the reader on its
password, is checked live in tests/auth/test_auth_live.py.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "docker" / "ldap_hba.sh"

ORIGINAL = """# PostgreSQL Client Authentication Configuration File
local   all             all                                     trust
host    all             all             127.0.0.1/32            scram-sha-256
host all all all scram-sha-256
"""

ON = {
    "NL2SQL_SIGNIN": "on",
    "NL2SQL_DB": "nl2sql_retail",
    "NL2SQL_SERVICE_ROLES": "nl2sql_reader,nl2sql_rolesync",
    "NL2SQL_LDAP_HOST": "nl2sql-ldap",
    "NL2SQL_LDAP_BASE_DN": "dc=nl2sql,dc=local",
}


def run(tmp_path: Path, *, errors: str = "0", hba: Path | None = None, **env: str) -> subprocess.CompletedProcess:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    psql = bin_dir / "psql"
    psql.write_text(f'#!/bin/sh\necho "$*" >> "{tmp_path}/psql.log"\ncase "$*" in *pg_hba_file_rules*) echo {errors} ;; esac\n')
    psql.chmod(0o755)
    if hba is None:
        hba = tmp_path / "pg_hba.conf"
        if not hba.exists():
            hba.write_text(ORIGINAL)
    environment = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HBA_FILE": str(hba), **env}
    command = ["sh", str(SCRIPT)]
    if os.environ.get("NL2SQL_SHELL_TRACE"):
        environment["PS4"] = "+@ldap_hba.sh@${LINENO}@ "
        command = ["sh", "-x", str(SCRIPT)]
    result = subprocess.run(command, capture_output=True, text=True, env=environment)
    directory = os.environ.get("NL2SQL_SHELL_TRACE")
    if directory:
        with open(os.path.join(directory, "trace.log"), "a") as handle:
            handle.write(result.stderr)
    return result


def psql_calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "psql.log"
    return log.read_text().splitlines() if log.exists() else []


def test_the_rules_go_first_and_the_files_own_follow_unchanged(tmp_path):
    result = run(tmp_path, **ON)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "sign-in rules written (on)"
    lines = (tmp_path / "pg_hba.conf").read_text().splitlines()
    assert lines[0].startswith("# BEGIN nl2sql sign-in")
    assert lines[1] == "host all nl2sql_reader,nl2sql_rolesync all scram-sha-256"
    assert lines[2] == (
        'host nl2sql_retail +nl2sql_ldap all ldap ldapserver=nl2sql-ldap ldapport=389 ldaptls=1 '
        'ldapprefix="uid=" ldapsuffix=",ou=people,dc=nl2sql,dc=local"'
    )
    assert lines[3] == "# END nl2sql sign-in"
    assert "\n".join(lines[4:]) + "\n" == ORIGINAL
    assert psql_calls(tmp_path)[-1].endswith("SELECT pg_reload_conf()")


def test_running_it_again_changes_nothing_and_reloads_nothing(tmp_path):
    run(tmp_path, **ON)
    before = (tmp_path / "pg_hba.conf").read_text()
    calls = len(psql_calls(tmp_path))
    result = run(tmp_path, **ON)
    assert result.stdout.strip() == "sign-in rules unchanged (on)"
    assert (tmp_path / "pg_hba.conf").read_text() == before
    assert len(psql_calls(tmp_path)) == calls


def test_a_changed_setting_replaces_the_block_rather_than_adding_one(tmp_path):
    run(tmp_path, **ON)
    run(tmp_path, **{**ON, "NL2SQL_LDAP_BASE_DN": "dc=corp,dc=example", "NL2SQL_LDAP_PORT": "1389"})
    text = (tmp_path / "pg_hba.conf").read_text()
    assert text.count("# BEGIN nl2sql sign-in") == 1
    assert "ldapport=1389" in text and "ou=people,dc=corp,dc=example" in text
    assert "dc=nl2sql,dc=local" not in text


@pytest.mark.parametrize(("tls", "words"), [("ldaps", "ldapscheme=ldaps"), ("none", "ldapport=389  ldapprefix")])
def test_the_directory_can_be_reached_by_ldaps_or_in_the_clear(tmp_path, tls, words):
    run(tmp_path, **{**ON, "NL2SQL_LDAP_TLS": tls})
    assert words in (tmp_path / "pg_hba.conf").read_text()


def test_turning_sign_in_off_takes_the_block_out(tmp_path):
    run(tmp_path, **ON)
    result = run(tmp_path, NL2SQL_SIGNIN="off", NL2SQL_DB="nl2sql_retail")
    assert result.stdout.strip() == "sign-in rules written (off)"
    assert (tmp_path / "pg_hba.conf").read_text() == ORIGINAL
    assert run(tmp_path, NL2SQL_DB="nl2sql_retail").stdout.strip() == "sign-in rules unchanged (off)", "off by default"


def test_rules_postgres_cannot_parse_are_put_back_as_they_were(tmp_path):
    result = run(tmp_path, errors="2", **ON)
    assert result.returncode != 0
    assert "the new rules did not parse (2 errors), so the old file is back" in result.stderr
    assert (tmp_path / "pg_hba.conf").read_text() == ORIGINAL
    assert not any("pg_reload_conf" in call for call in psql_calls(tmp_path))


def test_the_file_is_pgdata_s_own_by_default(tmp_path):
    data = tmp_path / "pgdata"
    data.mkdir()
    (data / "pg_hba.conf").write_text(ORIGINAL)
    environment = {**ON, "PGDATA": str(data)}
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "psql").write_text("#!/bin/sh\necho 0\n")
    (bin_dir / "psql").chmod(0o755)
    result = subprocess.run(
        ["sh", str(SCRIPT)], capture_output=True, text=True, env={"PATH": f"{bin_dir}:/usr/bin:/bin", **environment}
    )
    assert result.returncode == 0, result.stderr
    assert (data / "pg_hba.conf").read_text().startswith("# BEGIN nl2sql sign-in")


@pytest.mark.parametrize(
    ("env", "said"),
    [
        ({**ON, "NL2SQL_SIGNIN": "maybe"}, "NL2SQL_SIGNIN must be on or off, not maybe"),
        ({**ON, "NL2SQL_LDAP_TLS": "sometimes"}, "NL2SQL_LDAP_TLS must be starttls, ldaps or none, not sometimes"),
        ({**ON, "NL2SQL_LDAP_BASE_DN": 'dc="x"'}, "NL2SQL_LDAP_BASE_DN cannot contain a double quote"),
        ({k: v for k, v in ON.items() if k != "NL2SQL_DB"}, "NL2SQL_DB is not set"),
        ({k: v for k, v in ON.items() if k != "NL2SQL_SERVICE_ROLES"}, "NL2SQL_SERVICE_ROLES is not set"),
        ({k: v for k, v in ON.items() if k != "NL2SQL_LDAP_HOST"}, "NL2SQL_LDAP_HOST is not set"),
        ({k: v for k, v in ON.items() if k != "NL2SQL_LDAP_BASE_DN"}, "NL2SQL_LDAP_BASE_DN is not set"),
    ],
)
def test_a_setting_it_cannot_use_is_refused_before_the_file_is_touched(tmp_path, env, said):
    result = run(tmp_path, **env)
    assert result.returncode != 0 and said in result.stderr
    assert (tmp_path / "pg_hba.conf").read_text() == ORIGINAL


def test_a_missing_file_is_refused(tmp_path):
    result = run(tmp_path, hba=tmp_path / "nowhere" / "pg_hba.conf", **ON)
    assert result.returncode != 0 and "there is no" in result.stderr and "to write the rules into" in result.stderr


def test_without_pgdata_or_a_file_it_says_which(tmp_path):
    result = subprocess.run(["sh", str(SCRIPT)], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", **ON})
    assert result.returncode != 0 and "PGDATA is not set" in result.stderr
