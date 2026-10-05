"""`docker/entrypoint.sh`, the retail image's start since v1_2, run rather than read.

It is what makes the published image safe to start: a certificate of its
own, the transport rules at the top of pg_hba.conf, and passwords from the
environment instead of baked into the data. Here the stock entrypoint it
sources, and `openssl`, `psql`, `gosu` and `postgres`, are fakes that record
what they were asked; the script runs end to end. The real image is started
and connected to by the `--run-docker` tests and by the live stack.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "docker" / "entrypoint.sh"

#: The stock script's functions, as the entrypoint uses them -- and, run
#: rather than sourced, a stand-in for the stock entrypoint itself.
FAKE_STOCK = r"""#!/usr/bin/env bash
docker_setup_env() {
    : "${POSTGRES_USER:=postgres}"
    DATABASE_ALREADY_EXISTS="${FAKE_DB_EXISTS-true}"
}
docker_create_db_directories() { echo "create-db-directories" >> "$FAKE_LOG"; }
docker_temp_server_start() { echo "temp-server-start $*" >> "$FAKE_LOG"; }
docker_temp_server_stop() { echo "temp-server-stop" >> "$FAKE_LOG"; }
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    echo "stock $*" >> "$FAKE_LOG"
fi
"""

FAKE_OPENSSL = r"""#!/usr/bin/env bash
echo "openssl $*" >> "$FAKE_LOG"
if [ "$1" = x509 ]; then
    exit "${FAKE_EXPIRING:-0}"
fi
while [ $# -gt 0 ]; do
    case "$1" in
        -keyout) echo "key" > "$2"; shift ;;
        -out) echo "certificate" > "$2"; shift ;;
    esac
    shift
done
"""

FAKE_PSQL = r"""#!/usr/bin/env bash
echo "psql $* | owner=${POSTGRES_PASSWORD-<unset>} reader=${POSTGRES_READER_PASSWORD-<unset>}" >> "$FAKE_LOG"
cat > "$FAKE_STDIN"
"""

FAKE_ID = r"""#!/usr/bin/env bash
echo "${FAKE_UID:-999}"
"""

#: Drops to "postgres" by changing what the fake `id` says, then runs the
#: rest of its command line, as gosu does.
FAKE_GOSU = r"""#!/usr/bin/env bash
echo "gosu $*" >> "$FAKE_LOG"
shift
FAKE_UID=999 exec "$@"
"""

FAKE_LOGGER = r"""#!/usr/bin/env bash
echo "$(basename "$0") $*" >> "$FAKE_LOG"
"""

ORIGINAL_HBA = (
    "# PostgreSQL Client Authentication Configuration File\n"
    "local all all trust\n"
    "host all all 127.0.0.1/32 scram-sha-256\n"
    "host all all all scram-sha-256\n"
)


@dataclass
class Run:
    returncode: int
    stdout: str
    stderr: str
    calls: list[str]
    sql: str
    hba: str
    tls: Path


@pytest.fixture
def run(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in {
        "openssl": FAKE_OPENSSL,
        "psql": FAKE_PSQL,
        "id": FAKE_ID,
        "gosu": FAKE_GOSU,
        "chown": FAKE_LOGGER,
        "postgres": FAKE_LOGGER,
    }.items():
        (bin_dir / name).write_text(body)
        os.chmod(bin_dir / name, 0o755)
    stock = tmp_path / "docker-entrypoint.sh"
    stock.write_text(FAKE_STOCK)
    os.chmod(stock, 0o755)
    pgdata = tmp_path / "pgdata"
    pgdata.mkdir()
    (pgdata / "pg_hba.conf").write_text(ORIGINAL_HBA)
    tls = tmp_path / "tls"
    log = tmp_path / "calls.log"
    stdin = tmp_path / "psql.stdin"

    def _run(*args: str, env: dict[str, str | None] | None = None) -> Run:
        run_env = {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "FAKE_LOG": str(log),
            "FAKE_STDIN": str(stdin),
            "NL2SQL_STOCK_ENTRYPOINT": str(stock),
            "PGDATA": str(pgdata),
            "POSTGRES_TLS_DIR": str(tls),
            "POSTGRES_USER": "nl2sql",
            "POSTGRES_READER_USER": "nl2sql_reader",
            "POSTGRES_PASSWORD": "owner-secret",
            "POSTGRES_READER_PASSWORD": "reader-secret",
        }
        for key, value in (env or {}).items():
            if value is None:
                run_env.pop(key, None)
            else:
                run_env[key] = value
        log.write_text("")
        command = ["bash", str(SCRIPT), *(args or ("postgres",))]
        trace = os.environ.get("NL2SQL_SHELL_TRACE")
        if trace:
            # What tests/shell_coverage.py reads: every line run, by script.
            run_env["PS4"] = "+@${BASH_SOURCE##*/}@${LINENO}@ "
            command = ["bash", "-x", *command[1:]]
        result = subprocess.run(command, env=run_env, capture_output=True, text=True, timeout=30)
        if trace:
            with open(os.path.join(trace, "trace.log"), "a") as handle:
                handle.write(result.stderr)
        return Run(
            result.returncode,
            result.stdout,
            result.stderr,
            log.read_text().splitlines(),
            stdin.read_text() if stdin.exists() else "",
            (pgdata / "pg_hba.conf").read_text(),
            tls,
        )

    return _run


def _called(run: Run, program: str) -> list[str]:
    return [line[len(program) + 1:] for line in run.calls if line.startswith(program + " ")]


# --- the order of a start -------------------------------------------------------


def test_a_start_writes_the_certificate_the_rules_and_the_passwords_then_serves_tls(run):
    result = run()
    assert result.returncode == 0, result.stderr
    programs = [line.split(" ", 1)[0] for line in result.calls]
    assert programs.index("openssl") < programs.index("temp-server-start") < programs.index("psql")
    assert programs[-2:] == ["temp-server-stop", "postgres"]
    [served] = _called(result, "postgres")
    tls = result.tls
    assert served == f"-c ssl=on -c ssl_cert_file={tls}/server.crt -c ssl_key_file={tls}/server.key"
    assert "nl2sql-postgres: wrote a certificate for nl2sql-postgres,postgres,localhost,127.0.0.1,::1" in result.stdout


def test_started_as_root_it_prepares_its_directories_then_drops_to_postgres(run):
    result = run(env={"FAKE_UID": "0"})
    assert result.returncode == 0, result.stderr
    assert "create-db-directories" in result.calls
    assert f"postgres:postgres {result.tls}" in _called(result, "chown")
    [dropped] = _called(result, "gosu")
    assert dropped.startswith("postgres ") and dropped.endswith(" postgres")
    assert _called(result, "postgres"), "and then it served"


@pytest.mark.parametrize("args", [("bash",), ("psql", "--version")])
def test_anything_but_a_server_start_is_the_stock_entrypoints(run, args):
    result = run(*args)
    assert result.returncode == 0
    assert result.calls == [f"stock {' '.join(args)}"]


def test_an_empty_data_directory_is_initialised_by_the_stock_image(run):
    result = run(env={"FAKE_DB_EXISTS": ""})
    assert result.returncode == 0
    assert "stock postgres" in result.calls and "openssl" not in " ".join(result.calls)
    assert "the stock image initialises one" in result.stdout


# --- 1. the certificate ------------------------------------------------------------


def test_the_certificate_names_every_host_as_dns_or_ip(run):
    result = run(env={"POSTGRES_TLS_HOSTNAMES": "db.lan,10.0.0.5,::1,4tune.example"})
    [request] = _called(result, "openssl")
    assert "-addext subjectAltName=DNS:db.lan,IP:10.0.0.5,IP:::1,DNS:4tune.example" in request
    assert "-subj /CN=db.lan/O=nl2sql (development)" in request
    assert "basicConstraints=critical,CA:TRUE,pathlen:0" in request
    assert (result.tls / "generated-for").read_text() == "db.lan,10.0.0.5,::1,4tune.example\n"


def test_its_key_is_the_servers_alone(run):
    result = run()
    assert oct((result.tls / "server.key").stat().st_mode & 0o777) == "0o600"


def test_a_certificate_it_made_is_kept_on_the_next_start(run):
    run()
    again = run()
    assert "using the certificate at" in again.stdout
    assert all(not call.startswith("req") for call in _called(again, "openssl"))


def test_new_names_mean_a_new_certificate(run):
    run()
    again = run(env={"POSTGRES_TLS_HOSTNAMES": "nl2sql-postgres,db.lan"})
    assert "reissued the certificate for nl2sql-postgres,db.lan" in again.stdout


def test_a_certificate_near_its_end_is_reissued(run):
    run()
    again = run(env={"FAKE_EXPIRING": "1"})
    assert "had less than 30 days left" in again.stdout


def test_a_mounted_certificate_is_used_as_it_is(run, tmp_path):
    tls = tmp_path / "tls"
    tls.mkdir()
    (tls / "server.crt").write_text("theirs")
    (tls / "server.key").write_text("theirs")
    result = run(env={"FAKE_EXPIRING": "1"})
    assert "using the certificate" in result.stdout
    assert (tls / "server.crt").read_text() == "theirs"


def test_a_mounted_certificate_with_no_key_is_an_error_not_a_replacement(run, tmp_path):
    tls = tmp_path / "tls"
    tls.mkdir()
    (tls / "server.crt").write_text("theirs")
    result = run()
    assert result.returncode != 0
    assert "no key at" in result.stderr
    assert (tls / "server.crt").read_text() == "theirs"


# --- 2. the transport rules ----------------------------------------------------------


def test_the_rules_refuse_clear_text_and_the_superuser_and_keep_the_rest(run):
    result = run()
    lines = result.hba.splitlines()
    assert lines[0].startswith("# BEGIN nl2sql transport")
    assert lines[1:4] == [
        "hostnossl all all all reject",
        "host all postgres all reject",
        "# END nl2sql transport",
    ]
    assert "\n".join(lines[4:]) + "\n" == ORIGINAL_HBA


def test_the_rules_are_written_once_however_often_it_starts(run):
    run()
    again = run()
    assert again.hba.count("# BEGIN nl2sql transport") == 1
    assert again.hba.endswith(ORIGINAL_HBA)


def test_clear_text_can_be_allowed_by_name_and_the_superuser_still_cannot(run):
    result = run(env={"POSTGRES_REQUIRE_TLS": "false"})
    assert "hostnossl" not in result.hba
    assert "host all postgres all reject" in result.hba
    assert "passwords may cross the network in clear" in result.hba


# --- 3. the passwords -------------------------------------------------------------------


def test_the_passwords_come_from_the_environment_and_never_a_command_line(run):
    result = run()
    [call] = _called(result, "psql")
    assert "owner-secret" not in call.split(" | ")[0] and "reader-secret" not in call.split(" | ")[0]
    assert "owner=owner-secret reader=reader-secret" in call
    assert "ALTER ROLE postgres PASSWORD NULL;" in result.sql
    assert "\\getenv owner_password POSTGRES_PASSWORD" in result.sql
    assert "\\getenv reader_password POSTGRES_READER_PASSWORD" in result.sql
    assert "passwords from the environment: nl2sql and nl2sql_reader" in result.stdout


def test_an_empty_password_is_unset_rather_than_cleared(run):
    """Compose passes a variable nobody set as "", and Postgres takes an empty
    password as no password at all."""
    result = run(env={"POSTGRES_PASSWORD": "", "POSTGRES_READER_PASSWORD": ""})
    [call] = _called(result, "psql")
    assert call.endswith("owner=<unset> reader=<unset>")
    assert "passwords from the environment: none" in result.stdout


def test_the_reader_alone_can_be_given_one(run):
    result = run(env={"POSTGRES_PASSWORD": None})
    assert "passwords from the environment: nl2sql_reader" in result.stdout


def test_the_server_gets_none_of_the_postgres_variables(run, tmp_path):
    """As the stock entrypoint does: the server process has no use for them,
    and a password in its environment is one more place to read it from."""
    server = tmp_path / "bin" / "postgres"
    server.write_text('#!/usr/bin/env bash\necho "postgres env:${POSTGRES_PASSWORD-gone}" >> "$FAKE_LOG"\n')
    result = run()
    assert "postgres env:gone" in result.calls
