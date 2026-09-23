"""Static assertions against the Dockerfiles and the build-time scripts they
run. Pure text/regex checks -- no docker CLI or daemon needed, so these run
in every environment, not just --run-docker ones.

These exist to pin the non-obvious properties the build depends on: PGDATA
living outside the base image's declared VOLUME (or writes during build are
silently discarded), the generated CSVs staying bind-mounted rather than
COPYed (or they'd become a layer in the published image), and the ARG names
in docker/Dockerfile actually being consumed by init_db.sh.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

DOCKER_DIR = Path(__file__).resolve().parent.parent.parent / "docker"
AGENT_DIR = Path(__file__).resolve().parent.parent.parent / "agent"


@pytest.fixture(scope="module")
def postgres_dockerfile() -> str:
    return (DOCKER_DIR / "Dockerfile").read_text()


@pytest.fixture(scope="module")
def agent_dockerfile() -> str:
    return (AGENT_DIR / "Dockerfile").read_text()


@pytest.fixture(scope="module")
def init_db_sh() -> str:
    return (DOCKER_DIR / "init_db.sh").read_text()


@pytest.fixture(scope="module")
def emit_load_sql_py() -> str:
    return (DOCKER_DIR / "emit_load_sql.py").read_text()


@pytest.fixture(scope="module")
def reader_role_sql() -> str:
    return (DOCKER_DIR / "reader_role.sql").read_text()


# ---------------------------------------------------------------------------
# docker/Dockerfile (the seeded Postgres image)
# ---------------------------------------------------------------------------


def test_has_two_named_stages(postgres_dockerfile: str):
    assert re.search(r"FROM .*\bAS generator\b", postgres_dockerfile)
    assert re.search(r"FROM .*\bAS db\b", postgres_dockerfile)


def test_generator_stage_is_pinned_to_the_build_platform(postgres_dockerfile: str):
    # So a multi-arch build generates the dataset once, not once per target
    # arch -- otherwise cross-arch float/SIMD divergence would make the two
    # published architectures' data disagree.
    assert re.search(r"FROM --platform=\$BUILDPLATFORM .*\bAS generator\b", postgres_dockerfile)


def test_pgdata_is_set_outside_the_base_images_declared_volume(postgres_dockerfile: str):
    match = re.search(r"ENV PGDATA=(\S+)", postgres_dockerfile)
    assert match, "PGDATA must be set explicitly"
    pgdata = match.group(1)
    # postgres:18 declares /var/lib/postgresql as a VOLUME; anything written
    # there during `docker build` is discarded before the layer is committed.
    assert not pgdata.startswith("/var/lib/postgresql")


def test_csvs_are_bind_mounted_not_copied_into_the_final_image(postgres_dockerfile: str):
    assert re.search(r"RUN --mount=type=bind,from=generator,source=/out,target=/csv", postgres_dockerfile)
    # A COPY --from=generator would defeat the point: the CSVs would become a
    # permanent layer in the published image instead of only living in the
    # loaded Postgres cluster.
    assert not re.search(r"COPY\s+--from=generator", postgres_dockerfile)


def test_db_stage_drops_to_postgres_user_before_running_init_db(postgres_dockerfile: str):
    user_lines = [
        line.strip() for line in postgres_dockerfile.splitlines()
        if line.strip().startswith("USER") or "init_db.sh" in line
    ]
    joined = "\n".join(user_lines)
    assert re.search(r"USER postgres\n.*init_db\.sh", joined, re.DOTALL)
    assert re.search(r"init_db\.sh.*\nUSER root", joined, re.DOTALL)


def test_declares_oci_image_labels_for_the_published_image(postgres_dockerfile: str):
    assert "org.opencontainers.image.title=" in postgres_dockerfile
    assert "org.opencontainers.image.description=" in postgres_dockerfile


def test_build_args_are_all_consumed_by_init_db_sh(postgres_dockerfile: str, init_db_sh: str):
    arg_names = set(re.findall(r"^ARG (\w+)=", postgres_dockerfile, re.MULTILINE))
    assert {"DB_NAME", "DB_USER", "DB_PASSWORD", "DB_READER", "DB_READER_PASSWORD"} <= arg_names
    for name in ("DB_NAME", "DB_USER", "DB_PASSWORD", "DB_READER", "DB_READER_PASSWORD"):
        assert f"${{{name}}}" in init_db_sh, f"{name} is declared as a build ARG but never used in init_db.sh"


def test_the_reader_role_sql_is_shipped_in_the_image(postgres_dockerfile: str):
    assert "COPY docker/reader_role.sql /opt/nl2sql/reader_role.sql" in postgres_dockerfile


# ---------------------------------------------------------------------------
# docker/init_db.sh
# ---------------------------------------------------------------------------


def test_init_db_sh_fails_fast(init_db_sh: str):
    assert "set -euo pipefail" in init_db_sh


def test_init_db_sh_installs_pg_trgm_before_the_ddl_that_may_index_with_it(init_db_sh: str):
    """Only a superuser can create an extension, and the agent connects as a
    read-only role, so the image is the only place this can happen.
    """
    assert "CREATE EXTENSION IF NOT EXISTS pg_trgm" in init_db_sh
    assert init_db_sh.index("pg_trgm") < init_db_sh.index('--file="$DDL_FILE"')


def test_init_db_sh_loads_as_the_owner_and_only_then_creates_the_reader(init_db_sh: str):
    """The DDL and the COPY need the owner; the agent's role is created after
    the data is in, so it can be granted SELECT on tables that already exist.
    """
    ddl = init_db_sh.index('--file="$DDL_FILE"')
    load = init_db_sh.index('--file="$LOAD_SQL"')
    reader = init_db_sh.index('--file="$READER_SQL"')
    assert ddl < load < reader
    assert re.search(r'-v reader="\$\{DB_READER\}".*-v owner="\$\{DB_USER\}"', init_db_sh)


def test_init_db_sh_shuts_the_cluster_down_cleanly_at_the_end(init_db_sh: str):
    non_empty_lines = [line for line in init_db_sh.splitlines() if line.strip() and not line.strip().startswith("#")]
    assert "pg_ctl" in non_empty_lines[-1]
    assert "stop" in non_empty_lines[-1]


# ---------------------------------------------------------------------------
# docker/reader_role.sql
# ---------------------------------------------------------------------------


def test_reader_role_sql_grants_select_and_nothing_else(reader_role_sql: str):
    grants = re.findall(r"GRANT\s+(.+?)\s+(?:ON|TO)\b", reader_role_sql, re.IGNORECASE)
    assert grants, "no GRANT statements found"
    for privilege in grants:
        assert privilege.upper() in {"SELECT", "CONNECT", "USAGE"}, privilege
    for forbidden in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "ALL PRIVILEGES", "ALL TABLES TO"):
        assert forbidden not in reader_role_sql.upper().replace("ALL TABLES IN SCHEMA", ""), forbidden


def test_reader_role_sql_covers_tables_added_later(reader_role_sql: str):
    assert re.search(r"ALTER DEFAULT PRIVILEGES FOR ROLE :\"owner\" IN SCHEMA public\s+GRANT SELECT ON TABLES",
                     reader_role_sql)


def test_reader_role_sql_is_safe_to_run_on_every_start(reader_role_sql: str):
    """launch.sh runs it each time: creation is guarded, everything after
    it is an ALTER or a GRANT, and nothing is dropped or revoked.
    """
    assert "WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'reader') \\gexec" in reader_role_sql
    assert "\\set ON_ERROR_STOP on" in reader_role_sql
    for forbidden in ("DROP ", "REVOKE "):
        assert forbidden not in reader_role_sql.upper()


def test_reader_role_is_a_plain_login_role_that_starts_read_only(reader_role_sql: str):
    assert re.search(r"ALTER ROLE :\"reader\" WITH LOGIN PASSWORD :'reader_password'", reader_role_sql)
    for attribute in ("NOSUPERUSER", "NOCREATEDB", "NOCREATEROLE", "NOBYPASSRLS"):
        assert attribute in reader_role_sql
    assert "SET default_transaction_read_only = on" in reader_role_sql


# ---------------------------------------------------------------------------
# docker/emit_load_sql.py
# ---------------------------------------------------------------------------


def test_emit_load_sql_uses_the_fk_safe_table_order(emit_load_sql_py: str):
    assert "from datagen.schema_columns import TABLE_ORDER" in emit_load_sql_py


def test_emit_load_sql_copy_statements_match_the_csv_writer_format(emit_load_sql_py: str):
    # writer.write_csvs() writes headers and no index column; the COPY
    # statement must agree, or the build-time load silently misparses.
    assert "HEADER true" in emit_load_sql_py
    assert "FORMAT csv" in emit_load_sql_py


@pytest.fixture(scope="module")
def emitted_load_sql(tmp_path_factory) -> str:
    """Run the script the way docker/Dockerfile does and return what it wrote.

    The static checks above only read the source; this is the script actually
    executing, which is the part the image build depends on.
    """
    import os
    import subprocess
    import sys

    data_gen = DOCKER_DIR.parent / "data_gen"
    out = tmp_path_factory.mktemp("load") / "_load.sql"
    result = subprocess.run(
        [sys.executable, str(DOCKER_DIR / "emit_load_sql.py"), "/csv", str(out)],
        # The image sets ENV PYTHONPATH=/app/data_gen for exactly this import.
        env={**os.environ, "PYTHONPATH": str(data_gen)},
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return out.read_text()


def test_emit_load_sql_writes_one_copy_per_table_in_fk_safe_order(emitted_load_sql: str):
    from datagen.schema_columns import TABLE_ORDER

    statements = emitted_load_sql.strip().splitlines()
    assert len(statements) == len(TABLE_ORDER)
    emitted_tables = [re.match(r"COPY (\w+) FROM", line).group(1) for line in statements]
    # Order is load order: test_validate.py separately pins that TABLE_ORDER
    # itself puts every referenced table before the table referencing it.
    assert emitted_tables == list(TABLE_ORDER)


def test_emit_load_sql_points_every_copy_at_the_directory_it_was_given(emitted_load_sql: str):
    for line in emitted_load_sql.strip().splitlines():
        assert "FROM '/csv/" in line, line


def test_emitted_statements_name_the_csv_files_the_writer_actually_produces(emitted_load_sql: str):
    """The COPY path and writer.write_csvs()'s filename have to agree; nothing
    at build time would notice a mismatch until the load fails.
    """
    from datagen.schema_columns import TABLE_ORDER

    filenames = set(re.findall(r"FROM '/csv/([^']+)'", emitted_load_sql))
    assert filenames == {f"{name}.csv" for name in TABLE_ORDER}


def test_emitted_sql_ends_with_a_newline(emitted_load_sql: str):
    # psql -f on a file whose last statement has no terminating newline still
    # works, but the trailing newline is what keeps `cat`-ing it readable.
    assert emitted_load_sql.endswith("\n")


# ---------------------------------------------------------------------------
# agent/Dockerfile
# ---------------------------------------------------------------------------


def test_agent_entrypoint_matches_the_cli_module(agent_dockerfile: str):
    assert 'ENTRYPOINT ["python", "-m", "nl2sql_agent"]' in agent_dockerfile


def test_agent_requirements_are_installed_before_source_is_copied(agent_dockerfile: str):
    lines = agent_dockerfile.splitlines()
    req_idx = next(i for i, l in enumerate(lines) if "COPY agent/requirements.txt" in l)
    src_idx = next(i for i, l in enumerate(lines) if "COPY agent/nl2sql_agent" in l)
    assert req_idx < src_idx, "requirements should be copied (and installed) before source, for layer caching"


def test_agent_image_has_no_baked_in_database_credentials(agent_dockerfile: str):
    # Unlike docker/Dockerfile, the agent gets DATABASE_URL at runtime -- it
    # must not bake a password into the image.
    assert "PASSWORD" not in agent_dockerfile.upper()


def test_agent_and_postgres_images_track_the_same_python_base(postgres_dockerfile: str, agent_dockerfile: str):
    match = re.search(r"ARG PYTHON_IMAGE=(\S+)", postgres_dockerfile)
    assert match
    assert f"FROM {match.group(1)}" in agent_dockerfile


# ---------------------------------------------------------------------------
# agent/Dockerfile: v2 publishing metadata
# ---------------------------------------------------------------------------


def test_agent_image_declares_a_version_arg_and_env(agent_dockerfile: str):
    assert re.search(r"^ARG AGENT_VERSION=\d+\.\d+\.\d+", agent_dockerfile, re.MULTILINE)
    assert "ENV AGENT_VERSION=${AGENT_VERSION}" in agent_dockerfile


def test_agent_image_declares_oci_labels_for_publishing(agent_dockerfile: str):
    assert "org.opencontainers.image.title=" in agent_dockerfile
    assert "org.opencontainers.image.description=" in agent_dockerfile
    assert 'org.opencontainers.image.version="${AGENT_VERSION}"' in agent_dockerfile


def test_agent_image_description_mentions_retrieval(agent_dockerfile: str):
    # The published description is what someone browsing Docker Hub reads.
    assert "retrieval" in agent_dockerfile.lower() or "knowledge" in agent_dockerfile.lower()


# ---------------------------------------------------------------------------
# Version consistency across the files that declare it
# ---------------------------------------------------------------------------


def _agent_version_from_dockerfile(text: str) -> str:
    match = re.search(r"^ARG AGENT_VERSION=(\S+)", text, re.MULTILINE)
    assert match
    return match.group(1)


def test_package_version_matches_the_dockerfile(agent_dockerfile: str):
    """__version__, the image label, and the published tag all say "v2"; a
    bump that misses one of them ships an image that lies about itself.
    """
    from nl2sql_agent import __version__

    assert _agent_version_from_dockerfile(agent_dockerfile) == __version__


def test_published_tag_in_setup_names_a_version_this_package_actually_is(agent_dockerfile: str):
    """The tag is `v` and as many version components as the release needed to
    be told apart: `v4` while there was one 4.x, `v4_1` once 4.1 shipped
    something 4.0 could not do. Whatever its depth, it has to be a prefix of
    `__version__`, or `setup.sh` pulls an image that is not this checkout.
    """
    from nl2sql_agent import __version__

    setup_sh = (DOCKER_DIR.parent / "setup.sh").read_text()
    match = re.search(r'^AGENT_TAG="v([\d_]+)"', setup_sh, re.MULTILINE)
    assert match, "setup.sh no longer pins an agent tag of the form vN or vN_M"
    tagged = match.group(1).split("_")
    assert tagged == __version__.split(".")[: len(tagged)], (
        f"setup.sh pulls v{match.group(1)}, but this package is {__version__}"
    )


def test_setup_defaults_point_at_the_published_repositories(agent_dockerfile: str):
    setup_sh = (DOCKER_DIR.parent / "setup.sh").read_text()
    assert 'AGENT_IMAGE="mcfaddja/nl2sql-agent"' in setup_sh
    assert 'VECTOR_IMAGE="mcfaddja/nl2sql-rag-vectordb"' in setup_sh


def test_retrieval_module_is_shipped_in_the_image(agent_dockerfile: str):
    """The agent image copies the package directory wholesale, so retrieval.py
    travels with it -- this pins that the copy is still directory-wide rather
    than a list of files that could omit the new module.
    """
    assert "COPY agent/nl2sql_agent/ ./nl2sql_agent/" in agent_dockerfile
    assert (DOCKER_DIR.parent / "agent" / "nl2sql_agent" / "retrieval.py").exists()


# ---------------------------------------------------------------------------
# The REST API in the agent image, and the outside client
# ---------------------------------------------------------------------------


def test_the_api_package_travels_with_the_image(agent_dockerfile: str):
    """One image, two entrypoints. The directory-wide COPY is what carries
    the api package too, so the thing serving a GUI is the thing that was
    benchmarked rather than a second build.
    """
    assert "COPY agent/nl2sql_agent/ ./nl2sql_agent/" in agent_dockerfile
    api_dir = DOCKER_DIR.parent / "agent" / "nl2sql_agent" / "api"
    assert (api_dir / "__main__.py").exists(), "the api package has no module entry point"
    assert (api_dir / "app.py").exists()


def test_the_image_installs_what_the_api_needs(agent_dockerfile: str):
    """An image built before these were added starts and then fails on the
    first import, which is the failure launch.sh has a branch for.
    """
    requirements = (DOCKER_DIR.parent / "agent" / "requirements.txt").read_text()
    for package in ("fastapi", "uvicorn", "cryptography"):
        assert package in requirements, f"{package} is not installed in the image"
    assert "COPY agent/requirements.txt" in agent_dockerfile


def test_the_image_prepares_somewhere_to_keep_the_certificate(agent_dockerfile: str):
    """The server writes itself one on first start, and compose mounts a
    volume over this path so it survives a restart.
    """
    from nl2sql_agent.api.settings import DEFAULT_TLS_DIR

    assert f"mkdir -p {DEFAULT_TLS_DIR}" in agent_dockerfile
    assert DEFAULT_TLS_DIR in agent_dockerfile


def test_the_exposed_port_is_the_one_the_server_defaults_to(agent_dockerfile: str):
    from nl2sql_agent.api.settings import DEFAULT_PORT

    assert f"EXPOSE {DEFAULT_PORT}" in agent_dockerfile


def test_the_cli_is_still_the_default_way_in(agent_dockerfile: str):
    """Adding a server must not change what `docker compose run --rm agent
    "<question>"` does; the api service overrides the command instead.
    """
    assert 'ENTRYPOINT ["python", "-m", "nl2sql_agent"]' in agent_dockerfile
    directives = [
        line for line in agent_dockerfile.splitlines()
        if line.startswith(("ENTRYPOINT", "CMD"))
    ]
    assert directives and not any("nl2sql_agent.api" in line for line in directives), (
        f"the image starts a server by default: {directives}"
    )


def test_the_outside_client_image_carries_only_curl_and_jq():
    """It is credible as an outside client precisely because it shares no
    runtime with what it is testing.
    """
    dockerfile = (DOCKER_DIR / "apitest" / "Dockerfile").read_text()
    assert re.search(r"^FROM alpine", dockerfile, re.MULTILINE)
    assert "curl" in dockerfile and "jq" in dockerfile
    assert "COPY agent/" not in dockerfile
    assert "pip install" not in dockerfile
    assert 'ENTRYPOINT ["/usr/local/bin/smoke.sh"]' in dockerfile


def test_the_outside_client_can_be_handed_a_certificate_to_trust():
    """compose mounts the API's volume here; the test suite copies a file in
    with `docker cp`, which needs the directory to already exist.
    """
    from nl2sql_agent.api.settings import DEFAULT_TLS_DIR

    dockerfile = (DOCKER_DIR / "apitest" / "Dockerfile").read_text()
    assert f"mkdir -p {DEFAULT_TLS_DIR}" in dockerfile


# ---------------------------------------------------------------------------
# docker/init_db.sh, actually run
# ---------------------------------------------------------------------------


@pytest.mark.docker
def test_init_db_sh_builds_a_working_cluster(tmp_path, docker_daemon_available: bool):
    """The one script in this repository that was only ever read.

    Everything above asserts its *shape* -- that pg_trgm comes before the
    DDL, that the reader role is created last, that it shuts down cleanly.
    None of that proves it runs, and it cannot be run in the sandbox the
    other scripts use, because faking `initdb`, `pg_ctl` and `psql` would
    leave nothing real being tested.

    It can be run for real, though, against the stock image it is built on:
    every path it takes is exercised with a two-row dataset instead of a
    million, in about the time the fakes would have taken. What that proves
    is what the structural tests cannot -- that the cluster it leaves behind
    starts, holds the data, and answers as the read-only role.
    """
    if not docker_daemon_available:
        pytest.skip("no working docker daemon")

    base = re.search(r"^ARG POSTGRES_IMAGE=(\S+)", (DOCKER_DIR / "Dockerfile").read_text(), re.MULTILINE)
    assert base, "docker/Dockerfile no longer pins a Postgres base image"

    work = tmp_path / "bootstrap"
    work.mkdir()
    (work / "init_db.sh").write_text((DOCKER_DIR / "init_db.sh").read_text())
    (work / "reader_role.sql").write_text((DOCKER_DIR / "reader_role.sql").read_text())
    (work / "ddl.sql").write_text("CREATE TABLE dim_store (store_key int primary key, name text);\n")
    (work / "load.sql").write_text(
        "COPY dim_store FROM '/tmp/work/stores.csv' WITH (FORMAT csv, HEADER true);\n"
    )
    (work / "stores.csv").write_text("store_key,name\n1,Corner Fresh Grocers\n2,Thrift & Table\n")

    # The script runs, the server stops, and then the cluster is started
    # again from scratch -- which is what the image does on first run, and
    # the only way to show the shutdown left something usable behind.
    # `load.sql` does a server-side COPY, so the CSV has to be readable at
    # the path it names from inside the container.
    script = (
        "set -e\n"
        "cp -r /work /tmp/work\n"
        "export PGDATA=/tmp/pgdata DDL_FILE=/tmp/work/ddl.sql"
        " LOAD_SQL=/tmp/work/load.sql READER_SQL=/tmp/work/reader_role.sql\n"
        "bash /tmp/work/init_db.sh\n"
        # Start it again from the cluster the script shut down. This is what
        # the image does on first run, and the only way to show the
        # shutdown left something usable behind.
        "pg_ctl -D $PGDATA -w -o \"-c listen_addresses=''\" start\n"
        "psql -U nl2sql_reader -d testdb -tAc \"SELECT 'rows=' || count(*) FROM dim_store\"\n"
        "psql -U nl2sql_reader -d testdb -tAc \"SELECT name FROM dim_store WHERE store_key = 2\"\n"
        "if psql -U nl2sql_reader -d testdb -tAc 'CREATE TABLE nope (x int)' 2>/dev/null; then\n"
        "  echo 'READER COULD WRITE'\n"
        "else\n"
        "  echo 'reader refused a write'\n"
        "fi\n"
        "psql -U postgres -d testdb -tAc "
        "\"SELECT 'pg_trgm=' || count(*) FROM pg_extension WHERE extname = 'pg_trgm'\"\n"
        "pg_ctl -D $PGDATA -m fast -w stop >/dev/null\n"
    )

    result = subprocess.run(
        [
            "docker", "run", "--rm",
            "-v", f"{work}:/work:ro",
            "-e", "DB_NAME=testdb", "-e", "DB_USER=nl2sql", "-e", "DB_PASSWORD=secret",
            "-e", "DB_READER=nl2sql_reader", "-e", "DB_READER_PASSWORD=reader_secret",
            "--user", "postgres",
            base.group(1), "bash", "-c", script,
        ],
        capture_output=True, text=True, timeout=600,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    assert "rows=2" in lines, f"the two rows did not survive the shutdown:\n{result.stdout}"
    assert "Thrift & Table" in lines
    assert "reader refused a write" in lines, "the read-only role could create a table"
    assert "READER COULD WRITE" not in result.stdout
    # Installed by the script, not by the base image.
    assert "pg_trgm=1" in lines, f"pg_trgm was not installed:\n{result.stdout}"
