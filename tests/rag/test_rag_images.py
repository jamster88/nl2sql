"""The RAG images and the compose file that runs them.

Both halves of the knowledge base are published *with their data inside
them*, which is unusual and is what makes these files worth pinning. A
Postgres image normally ships empty and fills up at run time; these ship a
populated cluster -- `publish_db_image.sh` dumps a store and the build
restores it into the image, once per platform -- so someone else can pull
the embeddings rather than spend an hour recomputing them.

That arrangement rests on one invariant that is invisible unless you know to
look for it, and silently produces empty images when it is broken -- see
`test_the_cluster_lives_outside_the_base_image_s_declared_volume` below.

The compose file is checked in both directions like the repo-root one: the
variables the start-up scripts export are the ones it reads, and nothing it
reads is a variable nothing sets.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RAG_DIR = REPO_ROOT / "rag"
DOCKER_DIR = RAG_DIR / "docker"

#: Where the cluster is kept. Not a Docker convention -- a deliberate move
#: away from the base image's own path, for the reason tested below.
PGDATA = "/var/lib/pgdata"

#: The path the postgres and pgvector base images declare as a VOLUME.
BASE_VOLUME = "/var/lib/postgresql"

STORES = [
    ("chunkdb", "chunkdb.Dockerfile", "POSTGRES_IMAGE", "postgres:"),
    ("vectordb", "vectordb.Dockerfile", "PGVECTOR_IMAGE", "pgvector/pgvector:"),
]


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load((RAG_DIR / "docker-compose.yml").read_text())


def _dockerfile(name: str) -> str:
    return (DOCKER_DIR / name).read_text()


# ---------------------------------------------------------------------------
# The images
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("service", "dockerfile", "arg", "base"), STORES)
def test_the_cluster_lives_outside_the_base_image_s_declared_volume(
    service: str, dockerfile: str, arg: str, base: str
):
    """The invariant the whole publishing story rests on.

    Both base images declare `/var/lib/postgresql` as a VOLUME, and anything
    written to a volume path is invisible to `docker commit` and to a build
    that snapshots the cluster. Leave PGDATA at its default and the published
    image is a perfectly valid, completely empty database -- which looks like
    retrieval finding nothing rather than like a broken build.
    """
    source = _dockerfile(dockerfile)
    declared = re.search(r"^ENV PGDATA=(\S+)", source, re.MULTILINE)

    assert declared, f"{dockerfile} no longer sets PGDATA"
    assert declared.group(1) == PGDATA
    assert not declared.group(1).startswith(BASE_VOLUME), (
        f"{dockerfile} puts the cluster inside {BASE_VOLUME}, which the base "
        "image declares as a VOLUME -- the published image would ship empty"
    )


@pytest.mark.parametrize(("service", "dockerfile", "arg", "base"), STORES)
def test_the_base_image_is_a_build_argument(service: str, dockerfile: str, arg: str, base: str):
    """So a Postgres major version can be moved without editing the file."""
    source = _dockerfile(dockerfile)
    assert re.search(rf"^ARG {arg}={re.escape(base)}", source, re.MULTILINE)
    assert f"FROM ${{{arg}}}" in source


@pytest.mark.parametrize(("service", "dockerfile", "arg", "base"), STORES)
def test_the_cluster_directory_is_owned_and_locked_down(
    service: str, dockerfile: str, arg: str, base: str
):
    """Postgres refuses to start on a data directory it does not own or that
    is group- or world-readable. Both are set here because the directory is
    created by the build rather than by the entrypoint.
    """
    source = _dockerfile(dockerfile)
    assert 'chown -R postgres:postgres "$PGDATA"' in source
    assert 'chmod 700 "$PGDATA"' in source


def test_only_the_vector_store_is_built_on_pgvector():
    """The chunk store holds text and needs no extension; building it on
    pgvector would pull a larger image for nothing.
    """
    assert "pgvector" not in _dockerfile("chunkdb.Dockerfile")
    assert "pgvector" in _dockerfile("vectordb.Dockerfile")


@pytest.mark.parametrize(("service", "dockerfile", "arg", "base"), STORES)
def test_each_image_says_what_it_is(service: str, dockerfile: str, arg: str, base: str):
    source = _dockerfile(dockerfile)
    assert "org.opencontainers.image.title" in source
    assert "org.opencontainers.image.description" in source


# ---------------------------------------------------------------------------
# restore.Dockerfile -- the data, restored per platform
# ---------------------------------------------------------------------------


def _restore_run() -> str:
    """The restore's one RUN instruction, continuation lines joined."""
    source = _dockerfile("restore.Dockerfile")
    return re.search(r"^RUN (.*?)(?<!\\)$", source, re.MULTILINE | re.DOTALL).group(1)


def test_the_restore_continues_the_base_stage_rather_than_starting_one():
    """publish_db_image.sh appends it to the kind's own Dockerfile. A FROM
    here would start a second stage without the base's PGDATA and labels --
    and the image would be that stage."""
    assert not re.search(r"^FROM ", _dockerfile("restore.Dockerfile"), re.MULTILINE)


def test_the_files_the_publish_script_joins_are_the_ones_that_exist():
    script = (RAG_DIR / "publish_db_image.sh").read_text()
    assert '"$RAG_DIR/docker/$SERVICE.Dockerfile" "$RAG_DIR/docker/restore.Dockerfile"' in script
    for service, dockerfile, _, _ in STORES:
        assert dockerfile == f"{service}.Dockerfile" and (DOCKER_DIR / dockerfile).is_file()
    assert (DOCKER_DIR / "restore.Dockerfile").is_file()


def test_the_cluster_is_initialised_in_the_build_for_its_own_platform():
    """The point of the file: initdb runs under each platform of a buildx
    build, where the tar it replaced carried one machine's data directory."""
    run = _restore_run()
    assert 'gosu postgres initdb --username="$DB_USER"' in run
    assert "--encoding=UTF8 --locale=en_US.utf8" in run


def test_the_bootstrap_role_is_the_one_the_stores_are_created_with(compose: dict):
    """initdb makes it and the dump's CREATE of it is dropped, so they have to
    be the same role -- the one compose's POSTGRES_USER creates."""
    assert re.search(r"^ARG DB_USER=ragproc$", _dockerfile("restore.Dockerfile"), re.MULTILINE)
    for service in ("chunkdb", "vectordb"):
        assert compose["services"][service]["environment"]["POSTGRES_USER"] == "${RAG_DB_USER:-ragproc}"


def test_the_restore_stops_at_the_first_error():
    """psql carries on past a failed statement by default, and a restore that
    carried on would publish a store missing whatever failed."""
    assert "-v ON_ERROR_STOP=1" in _restore_run()


def test_the_stores_own_pg_hba_is_installed_owned_by_postgres():
    run = _restore_run()
    assert 'install -o postgres -g postgres -m 600 /tmp/restore/pg_hba.conf "$PGDATA/pg_hba.conf"' in run


def test_nothing_can_connect_while_the_restore_runs():
    assert """pg_ctl -o "-c listen_addresses=''" -w start""" in _restore_run()


def test_the_server_is_shut_down_cleanly_before_the_layer_is_taken():
    """A layer taken from a server that did not checkpoint is a cluster that
    replays WAL on its first start -- or does not start."""
    run = _restore_run()
    assert "pg_ctl -m fast -w stop" in run
    assert run.index("psql") < run.index("pg_ctl -m fast -w stop")


def test_the_dump_leaves_the_image_in_the_layer_that_used_it():
    """Removed in a later RUN, the dump would still be in the image, one
    layer down, doubling what everybody pulls."""
    run = _restore_run()
    assert run.rstrip().endswith("rm -rf /tmp/restore")
    assert len(re.findall(r"^RUN ", _dockerfile("restore.Dockerfile"), re.MULTILINE)) == 1


# ---------------------------------------------------------------------------
# The compose file
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("service", "dockerfile", "arg", "base"), STORES)
def test_each_service_builds_from_its_own_dockerfile(
    compose: dict, service: str, dockerfile: str, arg: str, base: str
):
    build = compose["services"][service]["build"]
    assert build["dockerfile"] == f"docker/{dockerfile}"
    assert (DOCKER_DIR / dockerfile).is_file()


@pytest.mark.parametrize(("service", "dockerfile", "arg", "base"), STORES)
def test_the_volume_is_mounted_where_the_dockerfile_put_the_cluster(
    compose: dict, service: str, dockerfile: str, arg: str, base: str
):
    """The two halves of the same decision, in two files. A volume mounted
    anywhere else leaves the image's data in place and unused, so the store
    looks empty on first start and full after a rebuild.
    """
    mounts = compose["services"][service]["volumes"]
    assert any(mount.endswith(f":{PGDATA}") for mount in mounts), mounts


def test_the_two_stores_do_not_collide_with_each_other_or_the_retail_database(compose: dict):
    """5432 belongs to the repo-root compose file, and these run alongside
    it. Splitting on ":" is no good here -- the host side is a
    `${VAR:-default}`, which contains one.
    """
    defaults = {}
    for name, service in compose["services"].items():
        mapping = service["ports"][0]
        host = re.match(r"^\$\{([A-Z_]+):-(\d+)\}:(\d+)$", mapping)
        assert host, f"{name} publishes {mapping!r}, which is not an overridable host port"
        defaults[name] = int(host.group(2))
        # The container side is always Postgres's own port; only the host
        # side moves.
        assert host.group(3) == "5432"

    assert defaults == {"chunkdb": 5433, "vectordb": 5434}
    assert 5432 not in defaults.values()


def test_the_volumes_are_named_so_they_survive_a_project_rename(compose: dict):
    """An unnamed volume is prefixed with the compose project, and a project
    renamed is a dataset orphaned.
    """
    names = {key: value["name"] for key, value in compose["volumes"].items()}
    assert names == {
        "chunkdb-data": "nl2sql-rag-chunkdb-data",
        "vectordb-data": "nl2sql-rag-vectordb-data",
    }


def test_the_named_volumes_are_the_ones_the_publish_script_snapshots():
    """It addresses them by name, outside compose, so a rename here is a
    publish that cannot find the data.
    """
    script = (RAG_DIR / "publish_db_image.sh").read_text()
    for volume in ("nl2sql-rag-chunkdb-data", "nl2sql-rag-vectordb-data"):
        assert volume in script


@pytest.mark.parametrize(("service", "dockerfile", "arg", "base"), STORES)
def test_each_store_reports_when_it_is_ready(
    compose: dict, service: str, dockerfile: str, arg: str, base: str
):
    """`wait_healthy` in lib.sh polls this and gives up after two minutes;
    without a healthcheck the status is always `starting`.
    """
    health = compose["services"][service]["healthcheck"]
    assert health["test"][0] == "CMD-SHELL"
    assert "pg_isready" in health["test"][1]


@pytest.mark.parametrize(("service", "dockerfile", "arg", "base"), STORES)
def test_the_container_names_are_the_ones_every_script_uses(
    compose: dict, service: str, dockerfile: str, arg: str, base: str
):
    name = compose["services"][service]["container_name"]
    assert name == f"nl2sql-rag-{service}"
    for script in RAG_DIR.glob("*.sh"):
        source = script.read_text()
        if name in source:
            break
    else:  # pragma: no cover - every container is addressed by some script
        pytest.fail(f"no script ever addresses {name}")


def test_the_stores_get_a_shutdown_grace_period():
    """Postgres needs time to checkpoint. Killed mid-write, the cluster that
    gets published is the one that has to recover on someone else's machine.
    """
    compose = yaml.safe_load((RAG_DIR / "docker-compose.yml").read_text())
    for service in compose["services"].values():
        assert service["stop_grace_period"] == "1m"


# ---------------------------------------------------------------------------
# Both directions: what the scripts set, and what compose reads
# ---------------------------------------------------------------------------


def _compose_variables() -> set[str]:
    return set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)", (RAG_DIR / "docker-compose.yml").read_text()))


def test_the_image_override_the_starters_export_is_the_one_compose_reads():
    """`--image` works by exporting these two names before `compose up`. A
    variable compose does not read makes the flag a silent no-op -- it would
    pull the published image and then start a locally built one.
    """
    read = _compose_variables()
    for script, prefix in (
        ("01_start_chunk_db.sh", "CHUNKDB"),
        ("03_start_vector_db.sh", "VECTORDB"),
    ):
        source = (RAG_DIR / script).read_text()
        exported = set(re.findall(r"^\s*export ([A-Z_]+)=", source, re.MULTILINE))
        assert exported == {f"{prefix}_IMAGE", f"{prefix}_TAG"}, script
        assert exported <= read, f"{script} exports {exported - read}, which compose never reads"


def test_every_variable_compose_reads_is_one_something_sets():
    """The mirror image: a variable in the compose file that no script and no
    document ever mentions is a knob nobody can reach.
    """
    scripts = "".join(path.read_text() for path in RAG_DIR.glob("*.sh"))
    docs = (RAG_DIR / "README.md").read_text() if (RAG_DIR / "README.md").is_file() else ""
    unreachable = [name for name in sorted(_compose_variables()) if name not in scripts + docs]
    assert unreachable == [], f"nothing sets or documents {unreachable}"

