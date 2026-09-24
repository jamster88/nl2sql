"""The RAG pipeline's shell scripts.

Seven of them, 375 lines, and until now not one test. They are the only way
the knowledge base is built and published, they are almost entirely guard
clauses and warnings, and none of it is Python -- so nothing in the coverage
report could see any of it.

A sandbox for exercising the RAG pipeline's shell scripts.

Seven scripts in `rag/` drive Docker, publish images and push them to a
registry -- none of which should happen against the developer's real
environment during a test run. So each test gets a throwaway copy of them
plus a fake `docker` on PATH that records every call and returns scripted
results.

That makes the scripts' actual decision logic testable: which flags they
parse, which compose services they start, which image they pull and when,
whether a container is stopped before its volume is snapshotted and restarted
afterwards, and which of their many `die` paths a bad argument reaches.

The same arrangement `tests/docker/conftest.py` gives `setup.sh` and
`launch.sh`, for the same reason: these are almost entirely warnings and
guard clauses, and a guard nobody triggers looks exactly like one that works.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RAG_DIR = REPO_ROOT / "rag"

FAKE_DOCKER = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$FAKE_LOG"

case "$1" in
    info)
        [[ -n "${FAKE_NO_DAEMON:-}" ]] && exit 1
        exit 0 ;;
    compose)
        shift
        case "$1" in
            version)
                [[ -n "${FAKE_NO_COMPOSE:-}" ]] && exit 1
                echo "2.99.0"; exit 0 ;;
            *) exit 0 ;;
        esac ;;
    inspect)
        # Two shapes are asked for: is it running, and is it healthy.
        if [[ "$*" == *".State.Running"* ]]; then
            echo "${FAKE_RUNNING:-false}"
        else
            echo "${FAKE_HEALTH:-healthy}"
        fi
        exit 0 ;;
    pull)
        [[ -n "${FAKE_FAIL_PULL:-}" ]] && exit 1
        exit 0 ;;
    volume)
        [[ -n "${FAKE_NO_VOLUME:-}" ]] && exit 1
        exit 0 ;;
    image)
        [[ -n "${FAKE_NO_IMAGE:-}" ]] && exit 1
        exit 0 ;;
    run)
        # The volume snapshot: `docker run -v VOL:/src:ro -v DIR:/out alpine
        # tar ...`. The real one writes the tar; so does this, because the
        # script measures it with du on the next line.
        [[ -n "${FAKE_FAIL_SNAPSHOT:-}" ]] && exit 1
        out=""
        for arg in "$@"; do
            [[ "$arg" == *":/out" ]] && out="${arg%:/out}"
        done
        [[ -n "$out" ]] && printf 'not really a cluster' > "$out/pgdata.tar"
        exit 0 ;;
    stop|start)
        exit 0 ;;
    build)
        [[ -n "${FAKE_FAIL_BUILD:-}" ]] && exit 1
        exit 0 ;;
    push)
        [[ -n "${FAKE_FAIL_PUSH:-}" ]] && exit 1
        exit 0 ;;
    exec)
        # Only the listing query is made to fail. `CREATE EXTENSION` failing
        # is a different thing entirely -- the vector store is unusable
        # without pgvector, and that one is meant to be fatal.
        if [[ "$*" == *embedding_table* ]]; then
            [[ -n "${FAKE_PSQL_FAILS:-}" ]] && exit 1
            # What start_rag_db.sh prints as "what is inside".
            echo " document_embeddings | 53"
        fi
        exit 0 ;;
    *) exit 0 ;;
esac
"""

#: wait_healthy polls with `sleep 2`; a real one would make every unhealthy
#: test take two minutes.
FAKE_SLEEP = """#!/usr/bin/env bash
exit 0
"""

#: Stands in for both the repo venv and python3, so `py` is observable
#: without either being installed in the sandbox.
FAKE_PYTHON = r"""#!/usr/bin/env bash
printf 'python %s\n' "$*" >> "$FAKE_LOG"
exit ${FAKE_PYTHON_EXIT:-0}
"""


@dataclass
class ScriptRun:
    returncode: int
    stdout: str
    stderr: str
    calls: list[str]
    workdir: Path

    @property
    def output(self) -> str:
        return self.stdout + self.stderr

    def called(self, fragment: str) -> bool:
        return any(fragment in call for call in self.calls)

    def calls_matching(self, fragment: str) -> list[str]:
        return [call for call in self.calls if fragment in call]

    def index_of(self, fragment: str) -> int:
        """Where a call appears, so ordering can be asserted."""
        for index, call in enumerate(self.calls):
            if fragment in call:
                return index
        raise AssertionError(f"no call matching {fragment!r} in:\n" + "\n".join(self.calls))


@pytest.fixture
def run_rag(tmp_path: Path):
    """Run one of the rag/ scripts in a sandbox.

    The layout matters: `lib.sh` derives `RAG_DIR` from its own location and
    `REPO_DIR` from its parent, and the scripts resolve `knowledge/` and the
    virtualenv through those. So the sandbox is a miniature repository rather
    than a flat directory of scripts.
    """
    repo = tmp_path / "repo"
    rag = repo / "rag"
    rag.mkdir(parents=True)
    (repo / "knowledge").mkdir()

    for script in RAG_DIR.glob("*.sh"):
        shutil.copy(script, rag / script.name)
        os.chmod(rag / script.name, 0o755)
    (rag / "docker").mkdir()
    shutil.copy(RAG_DIR / "docker" / "seeded.Dockerfile", rag / "docker" / "seeded.Dockerfile")
    shutil.copy(RAG_DIR / "docker-compose.yml", rag / "docker-compose.yml")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("docker", FAKE_DOCKER), ("sleep", FAKE_SLEEP), ("python3", FAKE_PYTHON)):
        path = bin_dir / name
        path.write_text(body)
        os.chmod(path, 0o755)

    log = tmp_path / "calls.log"
    log.touch()

    def _run(
        script: str,
        *args: str,
        env: dict | None = None,
        venv: bool = False,
        timeout: int = 60,
    ) -> ScriptRun:
        if venv:
            # `py` prefers the repo's own interpreter when there is one, which
            # is a branch of its own.
            venv_bin = repo / ".venv" / "bin"
            venv_bin.mkdir(parents=True, exist_ok=True)
            interpreter = venv_bin / "python"
            interpreter.write_text(FAKE_PYTHON.replace("python %s", "venv-python %s"))
            os.chmod(interpreter, 0o755)

        run_env = dict(os.environ)
        run_env["PATH"] = f"{bin_dir}:{run_env['PATH']}"
        run_env["FAKE_LOG"] = str(log)
        # Scripts read these for their connection strings; pinned so the
        # assertions do not depend on the developer's shell.
        for name in ("EMBED_MODEL", "OLLAMA_URL", "RAG_DB_USER", "RAG_DB_PASSWORD",
                     "CHUNK_DB_PORT", "VECTOR_DB_PORT", "CHUNK_DB_NAME",
                     "VECTOR_DB_NAME", "CHUNKDB_IMAGE", "CHUNKDB_TAG",
                     "VECTORDB_IMAGE", "VECTORDB_TAG"):
            run_env.pop(name, None)
        if env:
            run_env.update(env)

        command = ["bash", str(rag / script)]
        if os.environ.get("NL2SQL_SHELL_TRACE"):
            # See tests/shell_coverage.py -- the only way a shell script gets
            # a line-coverage number.
            run_env["PS4"] = "+@${BASH_SOURCE##*/}@${LINENO}@ "
            command = [command[0], "-x", *command[1:]]
        result = subprocess.run(
            command + list(args),
            cwd=rag, capture_output=True, text=True, timeout=timeout, env=run_env,
        )
        directory = os.environ.get("NL2SQL_SHELL_TRACE")
        if directory:
            with open(os.path.join(directory, "trace.log"), "a") as handle:
                handle.write(result.stderr)
        return ScriptRun(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            calls=log.read_text().splitlines(),
            workdir=repo,
        )

    return _run


# ---------------------------------------------------------------------------
# lib.sh -- sourced by all six others, so every one of them inherits its bugs
# ---------------------------------------------------------------------------


def test_the_daemon_is_checked_before_anything_is_attempted(run_rag):
    """Every script calls require_docker first. Without it the failure is
    whatever `docker compose up` prints, which names neither cause nor fix.
    """
    result = run_rag("01_start_chunk_db.sh", env={"FAKE_NO_DAEMON": "1"})
    assert result.returncode != 0
    assert "the Docker daemon is not running" in result.output
    assert not result.called("compose up")


def test_compose_v2_is_required_and_said_so(run_rag):
    result = run_rag("01_start_chunk_db.sh", env={"FAKE_NO_COMPOSE": "1"})
    assert result.returncode != 0
    assert "Docker Compose v2 is required" in result.output


def test_a_container_that_never_becomes_healthy_is_reported_with_its_status(run_rag):
    """The message has to carry the last status and where to look, because
    "did not become healthy" on its own is the least useful sentence a
    start-up script can end with.
    """
    result = run_rag("01_start_chunk_db.sh", env={"FAKE_HEALTH": "starting"})
    assert result.returncode != 0
    assert "did not become healthy" in result.output
    assert "last status: starting" in result.output
    assert "docker logs nl2sql-rag-chunkdb" in result.output


def test_the_repo_interpreter_is_preferred_over_the_system_one(run_rag):
    """`py` exists so the pipeline runs against the virtualenv the loaders
    were installed into, rather than whatever python3 happens to be first.
    """
    result = run_rag("run_all.sh", venv=True)
    assert result.returncode == 0
    assert any(call.startswith("venv-python") for call in result.calls)


def test_the_system_interpreter_is_the_fallback(run_rag):
    result = run_rag("run_all.sh")
    assert result.returncode == 0
    assert any(call.startswith("python ") for call in result.calls)
    assert not any(call.startswith("venv-python") for call in result.calls)


# ---------------------------------------------------------------------------
# 01_start_chunk_db.sh and 03_start_vector_db.sh
# ---------------------------------------------------------------------------

STARTERS = [
    ("01_start_chunk_db.sh", "chunkdb", "nl2sql-rag-chunkdb"),
    ("03_start_vector_db.sh", "vectordb", "nl2sql-rag-vectordb"),
]


@pytest.mark.parametrize(("script", "service", "container"), STARTERS)
def test_the_starter_builds_locally_by_default(run_rag, script, service, container):
    result = run_rag(script)
    assert result.returncode == 0
    assert result.called(f"compose up -d {service}")
    assert not result.called("--no-build")
    assert not result.called("pull")


@pytest.mark.parametrize(("script", "service", "container"), STARTERS)
def test_an_image_is_pulled_and_started_without_building(run_rag, script, service, container):
    """The published images ship their data inside them, so building over the
    top of one would throw away the thing that was pulled.
    """
    result = run_rag(script, "--image", "mcfaddja/example:v9")
    assert result.returncode == 0
    assert result.called("pull mcfaddja/example:v9")
    assert result.called(f"compose up -d --no-build {service}")


@pytest.mark.parametrize(("script", "service", "container"), STARTERS)
def test_a_failed_pull_is_fatal_rather_than_silently_building(run_rag, script, service, container):
    """Falling through to a local build would replace the published data with
    an empty database, which looks like retrieval simply finding nothing.
    """
    result = run_rag(script, "--image", "mcfaddja/example:v9", env={"FAKE_FAIL_PULL": "1"})
    assert result.returncode != 0
    assert "could not pull mcfaddja/example:v9" in result.output
    assert not result.called("compose up")


@pytest.mark.parametrize(("script", "service", "container"), STARTERS)
def test_the_starter_waits_for_health_before_claiming_readiness(run_rag, script, service, container):
    result = run_rag(script)
    assert result.called(f"inspect --format {{{{.State.Health.Status}}}} {container}")
    assert "ready on localhost:" in result.output


@pytest.mark.parametrize(("script", "_service", "_container"), STARTERS)
def test_the_starter_documents_its_own_flag(run_rag, script, _service, _container):
    result = run_rag(script, "--help")
    assert result.returncode == 0
    assert "--image REPO:TAG" in result.output
    assert not result.called("compose up")


@pytest.mark.parametrize(("script", "_service", "_container"), STARTERS)
def test_the_starter_refuses_an_option_it_does_not_know(run_rag, script, _service, _container):
    result = run_rag(script, "--wat")
    assert result.returncode != 0
    assert "unknown option: --wat" in result.output


def test_the_vector_store_gets_pgvector_before_anything_queries_it(run_rag):
    """`CREATE EXTENSION IF NOT EXISTS vector` is idempotent and cheap, and
    without it every `<=>` in the retriever is a syntax error.
    """
    result = run_rag("03_start_vector_db.sh")
    assert result.called("CREATE EXTENSION IF NOT EXISTS vector")
    assert "pgvector extension ready" in result.output


def test_the_short_image_flag_works_too(run_rag):
    result = run_rag("01_start_chunk_db.sh", "-i", "mcfaddja/example:v9")
    assert result.called("pull mcfaddja/example:v9")


# ---------------------------------------------------------------------------
# run_all.sh -- the whole pipeline
# ---------------------------------------------------------------------------


def test_the_pipeline_runs_its_four_steps_in_order(run_rag):
    result = run_rag("run_all.sh")
    assert result.returncode == 0

    chunk_db = result.index_of("compose up -d chunkdb")
    chunking = result.index_of("python 02_chunk_document.py")
    vector_db = result.index_of("compose up -d vectordb")
    embedding = result.index_of("python 04_embed_document.py")

    # Chunking needs its store, and embedding needs both the chunks and a
    # place to put the vectors.
    assert chunk_db < chunking < vector_db < embedding


def test_the_pipeline_passes_the_model_and_host_through_to_the_loaders(run_rag):
    result = run_rag(
        "run_all.sh", "--model", "nomic-embed-text", "--ollama-url", "http://elsewhere:11434"
    )
    assert result.called("--model nomic-embed-text")
    assert result.calls_matching("02_chunk_document.py")[0].endswith("--ollama-url http://elsewhere:11434")
    assert result.called("04_embed_document.py --all --model nomic-embed-text")


def test_the_pipeline_reads_its_defaults_from_the_environment(run_rag):
    result = run_rag("run_all.sh", env={"EMBED_MODEL": "bge-m3-custom"})
    assert result.called("--model bge-m3-custom")


def test_the_pipeline_chunks_the_directory_it_was_given(run_rag, tmp_path):
    docs = tmp_path / "other-docs"
    docs.mkdir()
    result = run_rag("run_all.sh", "--docs", str(docs))
    assert result.called(f"02_chunk_document.py --all {docs}")


def test_a_documents_directory_that_is_not_there_is_caught_before_docker(run_rag):
    """Named explicitly, because the alternative is chunking nothing
    successfully and wondering later why retrieval finds no context.
    """
    result = run_rag("run_all.sh", "--docs", "/no/such/place")
    assert result.returncode != 0
    assert "documents directory not found: /no/such/place" in result.output
    assert not result.called("compose up")


def test_the_pipeline_does_not_publish_unless_asked(run_rag):
    result = run_rag("run_all.sh")
    assert not result.called("push")


def test_publishing_snapshots_both_stores_under_the_given_account(run_rag):
    result = run_rag("run_all.sh", "--publish", "someone", "--tag", "v7")
    assert result.returncode == 0
    assert result.called("push someone/nl2sql-rag-chunkdb:v7")
    assert result.called("push someone/nl2sql-rag-vectordb:v7")


def test_publishing_defaults_to_v1(run_rag):
    result = run_rag("run_all.sh", "--publish", "someone")
    assert result.called("push someone/nl2sql-rag-chunkdb:v1")


def test_the_pipeline_documents_every_flag_it_parses(run_rag):
    result = run_rag("run_all.sh", "--help")
    assert result.returncode == 0
    for flag in ("--docs", "--model", "--ollama-url", "--publish", "--tag"):
        assert flag in result.output
    assert not result.called("compose up")


def test_the_pipeline_refuses_an_option_it_does_not_know(run_rag):
    result = run_rag("run_all.sh", "--wat")
    assert result.returncode != 0
    assert "unknown option: --wat" in result.output


def test_the_pipeline_says_where_both_stores_ended_up(run_rag):
    result = run_rag("run_all.sh")
    assert "postgresql://ragproc:ragproc@localhost:5433/nl2sql_chunks" in result.output
    assert "postgresql://ragproc:ragproc@localhost:5434/nl2sql_vectors" in result.output
    assert "./run_update.sh" in result.output


def test_a_loader_that_fails_stops_the_pipeline(run_rag):
    """`set -e` is what makes this true; a pipeline that carried on would
    embed a chunk store that was never rebuilt.
    """
    result = run_rag("run_all.sh", env={"FAKE_PYTHON_EXIT": "3"})
    assert result.returncode != 0
    assert not result.called("04_embed_document.py")


# ---------------------------------------------------------------------------
# run_update.sh -- the incremental path
# ---------------------------------------------------------------------------


def test_the_update_starts_only_what_is_not_already_running(run_rag):
    """The point of the incremental path is not restarting a database that
    is serving queries, so a running container is left alone.
    """
    result = run_rag("run_update.sh", env={"FAKE_RUNNING": "true"})
    assert result.returncode == 0
    assert not result.called("compose up")
    assert "is not running; starting it" not in result.output


def test_the_update_starts_a_store_that_is_down(run_rag):
    result = run_rag("run_update.sh", env={"FAKE_RUNNING": "false"})
    assert result.returncode == 0
    assert result.called("compose up -d chunkdb")
    assert result.called("compose up -d vectordb")
    assert "nl2sql-rag-chunkdb is not running; starting it" in result.output
    assert "nl2sql-rag-vectordb is not running; starting it" in result.output


def test_the_update_waits_for_what_it_started(run_rag):
    result = run_rag("run_update.sh", env={"FAKE_RUNNING": "false", "FAKE_HEALTH": "starting"})
    assert result.returncode != 0
    assert "did not become healthy" in result.output


def test_the_update_never_rebuilds(run_rag):
    """An edit to one document must not rebuild an image and lose the rest."""
    result = run_rag("run_update.sh", env={"FAKE_RUNNING": "false"})
    assert not result.called("compose build")
    assert not result.called("docker build")


def test_the_update_rechunks_and_reembeds(run_rag):
    result = run_rag("run_update.sh", env={"FAKE_RUNNING": "true"})
    assert result.called("02_chunk_document.py --all")
    assert result.called("04_embed_document.py --all")
    assert "Update complete" in result.output


def test_the_update_takes_the_same_flags_as_the_full_run(run_rag, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    result = run_rag(
        "run_update.sh", "--docs", str(docs), "--model", "m", "--ollama-url", "http://h:1",
        env={"FAKE_RUNNING": "true"},
    )
    assert result.called(f"02_chunk_document.py --all {docs} --ollama-url http://h:1")
    assert result.called("04_embed_document.py --all --model m")


def test_the_update_checks_its_documents_directory_too(run_rag):
    result = run_rag("run_update.sh", "--docs", "/no/such/place")
    assert result.returncode != 0
    assert "documents directory not found" in result.output


def test_the_update_can_publish(run_rag):
    result = run_rag("run_update.sh", "--publish", "someone", "--tag", "v2",
                     env={"FAKE_RUNNING": "true"})
    assert result.called("push someone/nl2sql-rag-chunkdb:v2")
    assert result.called("push someone/nl2sql-rag-vectordb:v2")


def test_the_update_prints_its_own_usage(run_rag):
    """Its help is the header comment, read back with sed, so a rewrite of
    the comment is a rewrite of the help.
    """
    result = run_rag("run_update.sh", "--help")
    assert result.returncode == 0
    assert "./run_update.sh [--docs DIR]" in result.output


def test_the_update_refuses_an_option_it_does_not_know(run_rag):
    result = run_rag("run_update.sh", "--wat")
    assert result.returncode != 0
    assert "unknown option: --wat" in result.output


# ---------------------------------------------------------------------------
# start_rag_db.sh -- the one script the agent's retriever needs
# ---------------------------------------------------------------------------


def test_starting_the_rag_store_delegates_to_the_vector_starter(run_rag):
    result = run_rag("start_rag_db.sh")
    assert result.returncode == 0
    assert result.called("compose up -d vectordb")
    assert result.called("CREATE EXTENSION IF NOT EXISTS vector")


def test_starting_the_rag_store_passes_an_image_through(run_rag):
    result = run_rag("start_rag_db.sh", "--image", "mcfaddja/example:v9")
    assert result.called("pull mcfaddja/example:v9")
    assert result.called("compose up -d --no-build vectordb")


def test_starting_the_rag_store_shows_what_is_inside_it(run_rag):
    """Printing the counts is the point: an empty store and a working one
    look identical from the outside until something queries them.
    """
    result = run_rag("start_rag_db.sh")
    assert "Vector store contents" in result.output
    assert "document_embeddings | 53" in result.output


def test_a_store_that_cannot_be_listed_is_a_warning_not_a_failure(run_rag):
    """The store is up either way, and refusing to print the connection
    string because a `SELECT` failed would help nobody.
    """
    result = run_rag("start_rag_db.sh", env={"FAKE_PSQL_FAILS": "1"})
    assert result.returncode == 0
    assert "could not list embedding tables" in result.output
    assert "Ready for retrieval" in result.output


def test_starting_the_rag_store_prints_the_query_shape_it_implements(run_rag):
    result = run_rag("start_rag_db.sh")
    assert "embedding <=>" in result.output
    assert "ragproc.vector_store.search()" in result.output


# ---------------------------------------------------------------------------
# publish_db_image.sh -- a volume is not captured by a build
# ---------------------------------------------------------------------------


def test_publishing_needs_both_a_service_and_a_tagged_image(run_rag):
    for args in ([], ["chunkdb"]):
        result = run_rag("publish_db_image.sh", *args)
        assert result.returncode != 0
        assert "usage: ./publish_db_image.sh" in result.output


def test_publishing_refuses_an_image_reference_with_no_tag(run_rag):
    """`docker push name` without a tag pushes `latest`, which is the one
    thing a snapshot of a specific dataset should never silently become.
    """
    result = run_rag("publish_db_image.sh", "chunkdb", "someone/no-tag")
    assert result.returncode != 0
    assert "needs an explicit tag" in result.output


def test_publishing_refuses_a_service_it_does_not_know(run_rag):
    result = run_rag("publish_db_image.sh", "retaildb", "someone/x:v1")
    assert result.returncode != 0
    assert "service must be 'chunkdb' or 'vectordb'" in result.output


def test_publishing_refuses_an_option_it_does_not_know(run_rag):
    result = run_rag("publish_db_image.sh", "chunkdb", "someone/x:v1", "--wat")
    assert result.returncode != 0
    assert "unknown option: --wat" in result.output


def test_publishing_checks_the_volume_exists_before_touching_the_container(run_rag):
    result = run_rag("publish_db_image.sh", "chunkdb", "someone/x:v1", env={"FAKE_NO_VOLUME": "1"})
    assert result.returncode != 0
    assert "volume nl2sql-rag-chunkdb-data does not exist" in result.output
    assert "has the pipeline run?" in result.output
    assert not result.called("stop")


def test_publishing_checks_the_base_image_exists(run_rag):
    result = run_rag("publish_db_image.sh", "vectordb", "someone/x:v1", env={"FAKE_NO_IMAGE": "1"})
    assert result.returncode != 0
    assert "not found -- start the service first" in result.output


def test_a_running_container_is_stopped_before_its_volume_is_read_and_restarted_after(run_rag):
    """The whole reason this script exists. A Postgres volume copied while
    the server is writing to it is a cluster that may not start, so the
    snapshot is taken across a clean shutdown -- and the container has to
    come back, because it was running a moment ago.
    """
    result = run_rag(
        "publish_db_image.sh", "chunkdb", "someone/x:v1", env={"FAKE_RUNNING": "true"}
    )
    assert result.returncode == 0

    stop = result.index_of("stop nl2sql-rag-chunkdb")
    snapshot = result.index_of("tar -C /src")
    start = result.index_of("start nl2sql-rag-chunkdb")
    assert stop < snapshot < start
    assert "Restarting nl2sql-rag-chunkdb" in result.output


def test_a_container_that_was_not_running_is_left_that_way(run_rag):
    """Starting it would be a side effect nobody asked for, and the volume
    is already at rest."""
    result = run_rag(
        "publish_db_image.sh", "chunkdb", "someone/x:v1", env={"FAKE_RUNNING": "false"}
    )
    assert result.returncode == 0
    assert not result.called("stop nl2sql-rag-chunkdb")
    assert not result.called("start nl2sql-rag-chunkdb")


def test_a_volume_that_cannot_be_read_is_fatal(run_rag):
    result = run_rag(
        "publish_db_image.sh", "vectordb", "someone/x:v1", env={"FAKE_FAIL_SNAPSHOT": "1"}
    )
    assert result.returncode != 0
    assert "could not read volume nl2sql-rag-vectordb-data" in result.output


def test_publishing_builds_from_the_seeded_dockerfile_over_the_running_base(run_rag):
    result = run_rag("publish_db_image.sh", "chunkdb", "someone/x:v1")
    build = result.calls_matching("build --build-arg")
    assert build, "no image was built"
    assert "BASE_IMAGE=nl2sql-rag-chunkdb:latest" in build[0]
    assert "-t someone/x:v1" in build[0]


def test_the_base_image_follows_a_pulled_one(run_rag):
    """After `--image`, the running container is the published image, so the
    snapshot has to be layered onto that rather than onto a local build.
    """
    result = run_rag(
        "publish_db_image.sh", "vectordb", "someone/x:v1",
        env={"VECTORDB_IMAGE": "mcfaddja/nl2sql-rag-vectordb", "VECTORDB_TAG": "v3"},
    )
    assert "BASE_IMAGE=mcfaddja/nl2sql-rag-vectordb:v3" in result.calls_matching("build --build-arg")[0]


def test_no_push_builds_and_stops(run_rag):
    result = run_rag("publish_db_image.sh", "chunkdb", "someone/x:v1", "--no-push")
    assert result.returncode == 0
    assert result.called("build --build-arg")
    assert not result.called("push someone/x:v1")
    assert "built but not pushed" in result.output


def test_a_failed_push_names_the_likely_cause(run_rag):
    result = run_rag("publish_db_image.sh", "chunkdb", "someone/x:v1", env={"FAKE_FAIL_PUSH": "1"})
    assert result.returncode != 0
    assert "is 'docker login' done for this account?" in result.output


def test_a_successful_push_warns_that_new_repositories_are_public(run_rag):
    """Docker Hub's default, and the one that turns publishing a dataset
    into publishing it to everybody.
    """
    result = run_rag("publish_db_image.sh", "chunkdb", "someone/x:v1")
    assert result.returncode == 0
    assert "creates new repositories as PUBLIC by default" in result.output


# ---------------------------------------------------------------------------
# Regressions these tests found
# ---------------------------------------------------------------------------


def test_docker_missing_entirely_is_named_as_such(run_rag, tmp_path):
    """The third of lib.sh's three start-up refusals, and the only one that
    needs docker off PATH rather than misbehaving on it.
    """
    result = run_rag("01_start_chunk_db.sh", env={"PATH": "/usr/bin:/bin"})
    assert result.returncode != 0
    assert "docker is not installed or not on PATH" in result.output


def test_forgetting_the_image_reference_shows_the_usage_line(run_rag):
    """Regression. `shift 2` fails with one argument and leaves it in place,
    so the option loop used to report the service name as an unknown option
    -- for the likeliest mistake there is.
    """
    result = run_rag("publish_db_image.sh", "chunkdb")
    assert result.returncode != 0
    assert "usage: ./publish_db_image.sh" in result.output
    assert "unknown option" not in result.output


def test_the_update_help_shows_no_shell_source(run_rag):
    """Regression. Its help is the header comment read back with `sed`, and
    the line range ran two lines past the end of it.
    """
    result = run_rag("run_update.sh", "--help")
    assert result.returncode == 0
    for line in result.stdout.splitlines():
        assert not line.strip() or line.startswith("#"), f"--help printed code: {line!r}"


def test_the_short_and_long_image_flags_agree(run_rag):
    """Both are parsed; both are now documented. A short form nobody
    documents is a short form nobody uses.
    """
    short = run_rag("03_start_vector_db.sh", "-i", "mcfaddja/example:v9")
    long = run_rag("03_start_vector_db.sh", "--image", "mcfaddja/example:v9")
    assert short.called("pull mcfaddja/example:v9")
    assert long.called("pull mcfaddja/example:v9")
    assert short.returncode == long.returncode == 0
