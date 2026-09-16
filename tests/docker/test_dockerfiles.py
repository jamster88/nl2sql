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
    assert {"DB_NAME", "DB_USER", "DB_PASSWORD"} <= arg_names
    for name in ("DB_NAME", "DB_USER", "DB_PASSWORD"):
        assert f"${{{name}}}" in init_db_sh, f"{name} is declared as a build ARG but never used in init_db.sh"


# ---------------------------------------------------------------------------
# docker/init_db.sh
# ---------------------------------------------------------------------------


def test_init_db_sh_fails_fast(init_db_sh: str):
    assert "set -euo pipefail" in init_db_sh


def test_init_db_sh_shuts_the_cluster_down_cleanly_at_the_end(init_db_sh: str):
    non_empty_lines = [line for line in init_db_sh.splitlines() if line.strip() and not line.strip().startswith("#")]
    assert "pg_ctl" in non_empty_lines[-1]
    assert "stop" in non_empty_lines[-1]


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
