"""`docker/migrate_store.sh`: a runtime store from before 6.3, moved into its database.

Driven against fake `psql`, `pg_ctl`, `pg_dump` and `pg_restore` that say
what they were asked, for each way a move can go; then, with --run-docker,
for real: a feedback store made the way 6.2 made one -- postgres:18, the
review service's own schema, row-level security, rows -- moved into a
stores server the dbprep code prepared, by the stores' image as its
postgres user, read-only, and checked row by row.
"""

from __future__ import annotations

import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "docker" / "migrate_store.sh"

FAKE_PSQL = r"""#!/usr/bin/env bash
printf 'psql %s\n' "$*" >> "$FAKE_LOG"
case "$*" in
    *shobj_description*) printf '%s\n' "${FAKE_MARKER-}" ;;
    *"FROM pg_tables"*) printf '%s\n' "${FAKE_TABLES-0}" ;;
esac
exit 0
"""
FAKE_PG_CTL = r"""#!/usr/bin/env bash
printf 'pg_ctl %s\n' "$*" >> "$FAKE_LOG"
[[ "$*" == *" start" && -n "${FAKE_START_FAILS:-}" ]] && exit 1
exit 0
"""
FAKE_PG_DUMP = r"""#!/usr/bin/env bash
printf 'pg_dump %s PGOPTIONS=%s\n' "$*" "${PGOPTIONS-}" >> "$FAKE_LOG"
while [ $# -gt 0 ]; do [ "$1" = -f ] && echo dump > "$2"; shift; done
"""
FAKE_PG_RESTORE = r"""#!/usr/bin/env bash
printf 'pg_restore %s\n' "$*" >> "$FAKE_LOG"
if [ "$1" = -l ]; then
    printf '%s\n' '1; 3079 1 EXTENSION - vector' '2; 0 0 COMMENT - EXTENSION vector' \
        '3; 1259 2 TABLE public feedback_submissions feedback' '4; 3256 3 POLICY public feedback_submissions writer_select feedback'
    exit 0
fi
[[ -n "${FAKE_RESTORE_FAILS:-}" ]] && { echo "pg_restore: error: something" >&2; exit 1; }
for argument in "$@"; do case "$argument" in */list) cat "$argument" > "${FAKE_LOG%/*}/restored-list" ;; esac; done
"""


@pytest.fixture
def run(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("psql", FAKE_PSQL), ("pg_ctl", FAKE_PG_CTL), ("pg_dump", FAKE_PG_DUMP),
                       ("pg_restore", FAKE_PG_RESTORE)):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    legacy = tmp_path / "legacy"
    (legacy / "pgdata").mkdir(parents=True)
    (legacy / "pgdata" / "PG_VERSION").write_text("18\n")
    (legacy / "pgdata" / "postmaster.pid").write_text("1\n")
    work = tmp_path / "work"
    work.mkdir()
    log = tmp_path / "log"

    def _run(**env: str) -> subprocess.CompletedProcess:
        environment = {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "FAKE_LOG": str(log),
            "NL2SQL_DB": "nl2sql_feedback",
            "NL2SQL_OWNER": "feedback",
            "NL2SQL_VOLUME": "nl2sql_feedbackdata",
            "NL2SQL_LEGACY": str(legacy),
            "NL2SQL_TARGET": str(tmp_path / "sockets"),
            "NL2SQL_WORK": str(work),
            **env,
        }
        command = ["bash", str(SCRIPT)]
        if os.environ.get("NL2SQL_SHELL_TRACE"):
            environment["PS4"] = "+@${BASH_SOURCE}@${LINENO}@ "
            command = ["bash", "-x", str(SCRIPT)]
        result = subprocess.run(command, capture_output=True, text=True, env=environment)
        directory = os.environ.get("NL2SQL_SHELL_TRACE")
        if directory:
            with open(os.path.join(directory, "trace.log"), "a") as handle:
                handle.write(result.stderr)
        return result

    _run.log = log
    _run.tmp = tmp_path
    _run.legacy = legacy
    _run.work = work
    return _run


def test_an_old_store_is_dumped_from_a_copy_and_restored_as_its_owner(run):
    result = run()
    assert result.returncode == 0, result.stderr
    assert "migrate_store: moved nl2sql_feedback from nl2sql_feedbackdata into the runtime stores" in result.stdout
    log = run.log.read_text()
    assert "pg_ctl -D " in log and "listen_addresses=''" in log, "the old server listens on no port"
    assert f"-D {run.work}/legacy." in log, "on a copy: the volume itself is read-only"
    assert "PGOPTIONS=-c client_min_messages=error" in log
    assert "pg_dump -h" in log and "-U feedback -d nl2sql_feedback -Fc --no-owner --no-privileges" in log
    restore = next(line for line in log.splitlines() if line.startswith("pg_restore -h"))
    assert f"-h {run.tmp / 'sockets'} -U postgres -d nl2sql_feedback --role=feedback --no-owner --no-privileges" in restore
    assert "--exit-on-error" in restore
    restored = (run.tmp / "restored-list").read_text()
    assert "TABLE public feedback_submissions" in restored
    assert "POLICY" not in restored and "EXTENSION" not in restored, "made again by the review service and dbprep"
    assert "COMMENT ON DATABASE \"nl2sql_feedback\" IS 'nl2sql: migrated from nl2sql_feedbackdata'" in log
    assert "-m fast -w -s stop" in log, "the old server is stopped"
    assert list(run.work.iterdir()) == [], "and the copy taken away"
    assert (run.legacy / "pgdata" / "postmaster.pid").exists(), "the volume is left as it was"


def test_a_volume_with_no_database_has_nothing_to_move(run):
    (run.legacy / "pgdata" / "PG_VERSION").unlink()
    result = run()
    assert result.returncode == 0 and "holds no database, so there is nothing to move" in result.stdout
    assert not run.log.exists()


def test_a_store_moved_already_is_left_alone(run):
    result = run(FAKE_MARKER="nl2sql: migrated from nl2sql_feedbackdata")
    assert result.returncode == 0 and "was moved from nl2sql_feedbackdata already" in result.stdout
    assert "pg_dump" not in run.log.read_text()


def test_a_database_already_in_use_is_not_merged_into(run):
    result = run(FAKE_TABLES="3")
    assert result.returncode == 1
    assert "already has 3 tables, so nl2sql_feedbackdata is not merged into it" in result.stderr
    assert "docker volume rm nl2sql_feedbackdata" in result.stderr
    assert "pg_ctl" not in run.log.read_text()


def test_a_restore_that_fails_fails_the_move_and_still_stops_the_old_server(run):
    result = run(FAKE_RESTORE_FAILS="1")
    assert result.returncode != 0
    log = run.log.read_text()
    assert "COMMENT ON DATABASE" not in log, "not marked as moved"
    assert "-m fast -w -s stop" in log and list(run.work.iterdir()) == []


@pytest.mark.parametrize("missing", ["NL2SQL_DB", "NL2SQL_OWNER", "NL2SQL_VOLUME"])
def test_it_is_told_what_to_move(run, missing):
    result = run(**{missing: ""})
    assert result.returncode != 0 and f"{missing} is not set" in result.stderr


# --- for real --------------------------------------------------------------------------


def docker(*args: str, check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check, **kwargs)


@pytest.mark.docker
def test_a_feedback_store_made_by_6_2_moves_into_the_stores_with_its_rows(docker_daemon_available):
    if not docker_daemon_available:
        pytest.skip("no Docker daemon")
    from nl2sql_review import store as feedback_store

    psycopg = pytest.importorskip("psycopg")
    tag = f"nl2sql-migrate-{uuid.uuid4().hex[:8]}"
    legacy, socket, data = f"{tag}-legacy", f"{tag}-socket", f"{tag}-stores"
    stores_image = _stores_image()
    try:
        docker("run", "-d", "--name", f"{tag}-old", "-e", "POSTGRES_USER=feedback", "-e", "POSTGRES_PASSWORD=fb",
               "-e", "POSTGRES_DB=nl2sql_feedback", "-e", "PGDATA=/var/lib/postgresql/data/pgdata",
               "-v", f"{legacy}:/var/lib/postgresql/data", "-p", "127.0.0.1::5432", "postgres:18")
        port = _ready(f"{tag}-old")
        with psycopg.connect(host="127.0.0.1", port=port, user="feedback", password="fb", dbname="nl2sql_feedback") as conn:
            feedback_store.ensure_schema(conn)
            feedback_store.ensure_writer_role(conn, "writer")
            conn.execute("INSERT INTO feedback_submissions (id, job_id, verdict, question) "
                         "VALUES ('s1', 'j1', 'yes', 'How many stores?'), ('s2', 'j2', 'no', 'Sales by month?')")
            conn.commit()
        docker("stop", f"{tag}-old")

        docker("run", "-d", "--name", f"{tag}-new", "-e", "POSTGRES_PASSWORD=su",
               "-e", "PGDATA=/var/lib/postgresql/data/pgdata", "-v", f"{data}:/var/lib/postgresql/data",
               "-v", f"{socket}:/var/run/postgresql", stores_image)
        _ready(f"{tag}-new", publish=False)
        # The stores' databases, made as the dbprep service makes them.
        docker("exec", "-u", "postgres", f"{tag}-new", "psql", "-q", "-c",
               "CREATE ROLE feedback LOGIN PASSWORD 'fb2' CREATEROLE", "-c",
               "CREATE DATABASE nl2sql_feedback OWNER feedback")

        moved = docker("run", "--rm", "--user", "999:999", "--read-only", "--tmpfs", "/tmp", "--cap-drop", "ALL",
                       "-v", f"{legacy}:/legacy:ro", "-v", f"{socket}:/run/nl2sql/sockets/stores",
                       "-v", f"{SCRIPT}:/migrate/migrate_store.sh:ro",
                       "-e", "NL2SQL_DB=nl2sql_feedback", "-e", "NL2SQL_OWNER=feedback", "-e", f"NL2SQL_VOLUME={legacy}",
                       "--entrypoint", "bash", stores_image, "/migrate/migrate_store.sh")
        assert "moved nl2sql_feedback" in moved.stdout and moved.stderr == ""
        again = docker("run", "--rm", "--user", "999:999", "--read-only", "--tmpfs", "/tmp",
                       "-v", f"{legacy}:/legacy:ro", "-v", f"{socket}:/run/nl2sql/sockets/stores",
                       "-v", f"{SCRIPT}:/migrate/migrate_store.sh:ro",
                       "-e", "NL2SQL_DB=nl2sql_feedback", "-e", "NL2SQL_OWNER=feedback", "-e", f"NL2SQL_VOLUME={legacy}",
                       "--entrypoint", "bash", stores_image, "/migrate/migrate_store.sh")
        assert "was moved from" in again.stdout

        rows = docker("exec", "-u", "postgres", f"{tag}-new", "psql", "-d", "nl2sql_feedback", "-At", "-c",
                      "SELECT id || ':' || question || ':' || state FROM feedback_submissions ORDER BY id").stdout
        assert rows.split() == ["s1:How", "many", "stores?:pending", "s2:Sales", "by", "month?:pending"]
        owner = docker("exec", "-u", "postgres", f"{tag}-new", "psql", "-d", "nl2sql_feedback", "-At", "-c",
                       "SELECT string_agg(DISTINCT tableowner, ',') FROM pg_tables WHERE schemaname = 'public'").stdout
        assert owner.strip() == "feedback"
    finally:
        docker("rm", "-f", f"{tag}-old", f"{tag}-new", check=False)
        docker("volume", "rm", legacy, socket, data, check=False)


def _stores_image() -> str:
    import re

    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    return re.search(r"\$\{STORES_IMAGE:-([^}]+)\}", compose)[1]


def _ready(name: str, publish: bool = True) -> int:
    for _ in range(60):
        if docker("exec", name, "pg_isready", "-q", "-h", "127.0.0.1", check=False).returncode == 0:
            break
        time.sleep(1)
    if not publish:
        return 0
    return int(docker("port", name, "5432/tcp").stdout.strip().splitlines()[0].rsplit(":", 1)[1])
