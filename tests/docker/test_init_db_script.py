"""`docker/init_db.sh`, run rather than only read.

This is the script that makes the published postgres image what it is: it
initialises a cluster, applies the DDL, bulk-loads the CSVs, creates the
read-only role the agent connects as, and shuts down cleanly, so the image
ships a populated cluster instead of building one on first run.

It runs exactly once, inside `docker build`, which is why it was the one
shell script in the repository covered only by reading. Reading catches a
missing `CREATE EXTENSION`; it does not catch a variable that is unset at
run time, a redirection into a directory that does not exist, or an ordering
that only shows up once the thing executes. So `initdb`, `pg_ctl` and `psql`
are faked here -- they record their arguments and succeed -- and the script
itself runs end to end.

The structural assertions about how the Dockerfile *invokes* it stay in
test_dockerfiles.py; what it does once invoked is here.
"""

from __future__ import annotations

import pytest


def calls_to(result, program: str) -> list[str]:
    """Every invocation of one fake binary, in order, without its name."""
    prefix = f"{program} "
    return [line[len(prefix):] for line in result.calls if line.startswith(prefix)]


# ---------------------------------------------------------------------------
# It runs at all
# ---------------------------------------------------------------------------


def test_the_default_build_arguments_run_it_through_to_the_end(run_init_db):
    result = run_init_db()
    assert result.returncode == 0, result.output


def test_every_stage_runs_in_the_order_the_image_needs(run_init_db):
    """initdb, then a server, then the schema, then the data, then the role,
    then a clean stop. Any other order produces an image that is subtly wrong
    rather than one that fails to build.
    """
    result = run_init_db()
    programs = [line.split(" ", 1)[0] for line in result.calls]
    assert programs[0] == "initdb"
    assert programs[1] == "pg_ctl"
    assert programs[-1] == "pg_ctl"
    assert "psql" in programs


# ---------------------------------------------------------------------------
# The cluster
# ---------------------------------------------------------------------------


def test_the_cluster_is_initialised_with_scram_and_utf8(run_init_db):
    """`trust` for local so the build's own psql needs no password, and
    scram-sha-256 for host so the shipped image never accepts an unauthenticated
    connection over TCP.
    """
    [init] = calls_to(run_init_db(), "initdb")
    assert "--auth-local=trust" in init
    assert "--auth-host=scram-sha-256" in init
    assert "--encoding=UTF8" in init
    assert "--username=postgres" in init


def test_the_host_rule_the_runtime_entrypoint_would_have_added_is_written(run_init_db):
    """The stock entrypoint writes this when it initialises the cluster
    itself. This image initialises it at build time instead, so without this
    line nothing can connect over TCP to the published image.
    """
    result = run_init_db()
    hba = (result.workdir.parent / "pgdata" / "pg_hba.conf").read_text()
    assert "host all all all scram-sha-256" in hba


def test_durability_is_relaxed_only_as_command_line_overrides(run_init_db):
    """fsync=off makes the build fast, and must not survive into the image.
    Passing them with `-o` keeps them out of the shipped postgresql.conf.
    """
    start = calls_to(run_init_db(), "pg_ctl")[0]
    for setting in ("fsync=off", "full_page_writes=off", "synchronous_commit=off"):
        assert setting in start
    assert "-o " in start and "listen_addresses=''" in start


def test_the_build_time_server_listens_on_no_socket_at_all(run_init_db):
    """`listen_addresses=''` during the build: nothing outside the build
    container can reach the cluster while it has trust authentication on.
    """
    assert "listen_addresses=''" in calls_to(run_init_db(), "pg_ctl")[0]


def test_the_cluster_is_stopped_with_fast_mode_so_the_layer_is_clean(run_init_db):
    """An unclean stop commits a cluster that replays WAL on first start, and
    the image would then be slower and dirtier than one built at run time.
    """
    stop = calls_to(run_init_db(), "pg_ctl")[-1]
    assert "-m fast" in stop and "stop" in stop and "-w" in stop


# ---------------------------------------------------------------------------
# Roles, database and extension
# ---------------------------------------------------------------------------


def test_the_superuser_password_is_set_from_the_build_argument(run_init_db):
    joined = "\n".join(calls_to(run_init_db(), "psql"))
    assert "ALTER ROLE postgres PASSWORD 'owner-secret'" in joined


def test_the_owner_role_and_its_database_are_created(run_init_db):
    joined = "\n".join(calls_to(run_init_db(), "psql"))
    assert "CREATE ROLE nl2sql LOGIN PASSWORD 'owner-secret'" in joined
    assert "CREATE DATABASE nl2sql_retail OWNER nl2sql" in joined
    assert "ALTER SCHEMA public OWNER TO nl2sql" in joined


def test_pg_trgm_is_installed_before_the_ddl_that_may_index_with_it(run_init_db):
    """Only a superuser can create an extension and the agent connects as a
    read-only role, so the image is the only place this can happen. The agent
    falls back to difflib without it, which makes matching worse silently.
    """
    calls = calls_to(run_init_db(), "psql")
    trgm = next(i for i, c in enumerate(calls) if "pg_trgm" in c)
    ddl = next(i for i, c in enumerate(calls) if c.endswith("ddl.sql"))
    assert trgm < ddl


def test_every_psql_call_stops_on_the_first_error(run_init_db):
    """Without ON_ERROR_STOP psql reports success after a failed statement,
    and the image ships with a half-applied schema.
    """
    for call in calls_to(run_init_db(), "psql"):
        assert "-v ON_ERROR_STOP=1" in call


# ---------------------------------------------------------------------------
# Schema, data, reader
# ---------------------------------------------------------------------------


def test_the_ddl_is_applied_as_the_owner_not_the_superuser(run_init_db):
    """Tables created by postgres would leave the owner unable to grant on
    them, and the reader role's grants would silently cover nothing.
    """
    [ddl] = [c for c in calls_to(run_init_db(), "psql") if c.endswith("ddl.sql")]
    assert "--username=nl2sql " in ddl
    assert "--username=postgres" not in ddl


def test_the_bulk_load_and_its_analyze_run_as_the_superuser(run_init_db):
    """Server-side COPY reads /csv directly, which an ordinary role may not do."""
    calls = calls_to(run_init_db(), "psql")
    [load] = [c for c in calls if c.endswith("_load.sql")]
    assert "--username=postgres" in load
    assert any("VACUUM ANALYZE" in c and "--username=postgres" in c for c in calls)


def test_the_reader_role_is_created_last_and_told_who_it_reads_for(run_init_db):
    """It is granted SELECT on tables that already exist, so it has to come
    after the load; the three -v values are what reader_role.sql substitutes.
    """
    calls = calls_to(run_init_db(), "psql")
    load = next(i for i, c in enumerate(calls) if c.endswith("_load.sql"))
    reader_index = next(i for i, c in enumerate(calls) if c.endswith("reader_role.sql"))
    assert load < reader_index
    reader = calls[reader_index]
    assert "-v reader=nl2sql_reader" in reader
    assert "-v reader_password=reader-secret" in reader
    assert "-v owner=nl2sql" in reader


# ---------------------------------------------------------------------------
# The paths it reads
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("variable", "suffix"),
    [("DDL_FILE", "other-ddl.sql"), ("LOAD_SQL", "other-load.sql"), ("READER_SQL", "other-reader.sql")],
)
def test_each_sql_path_can_be_overridden(run_init_db, variable: str, suffix: str):
    """They default to the paths the Dockerfile copies to; overridable means
    this script can be exercised without that layout.
    """
    result = run_init_db(env={variable: f"/tmp/{suffix}"})
    assert result.returncode == 0, result.output
    assert any(c.endswith(f"/tmp/{suffix}") for c in calls_to(result, "psql"))


# ---------------------------------------------------------------------------
# Failure
# ---------------------------------------------------------------------------


def test_a_failing_statement_aborts_the_build_rather_than_shipping_the_image(run_init_db):
    """`set -euo pipefail`, proved by running it: a psql that exits non-zero
    has to stop the script, or docker build succeeds and publishes a database
    missing whatever that statement was for.
    """
    result = run_init_db(env={"FAKE_PG_FAIL": "CREATE DATABASE"})
    assert result.returncode != 0
    assert not any(c.endswith("reader_role.sql") for c in calls_to(result, "psql"))


@pytest.mark.parametrize(
    "variable", ["DB_PASSWORD", "DB_USER", "DB_NAME", "DB_READER", "DB_READER_PASSWORD"]
)
def test_a_build_argument_the_dockerfile_forgot_to_pass_stops_the_build(
    run_init_db, variable: str
):
    """`set -u`, proved by running it. Every one of these is interpolated into
    SQL. Unset under `set -u` is a hard error; without it the role would be
    created with an empty name or a blank password and the image would ship
    that way.
    """
    result = run_init_db(env={variable: None})
    assert result.returncode != 0, f"{variable} unset did not stop the script"
    assert variable in result.output
