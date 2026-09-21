"""A sandbox for exercising setup.sh without touching real Docker.

setup.sh drives Docker and probes Ollama over HTTP, and it writes a .env into
its own directory -- none of which should happen against the developer's real
environment during a test run. So each test gets a throwaway copy of the repo's
scripts plus fake `docker`, `curl` and `sleep` executables on PATH that record
every call and return scripted results.

That makes the script's actual decision logic testable: which images it pulls,
what it writes to .env, which services it starts, and which warnings it emits.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

FAKE_DOCKER = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$FAKE_LOG"

read_env() {  # read_env KEY DEFAULT -- reflects whatever setup.sh wrote to .env
    local key="$1" default="$2" value=""
    if [[ -f .env ]]; then
        value=$(grep -E "^${key}=" .env 2>/dev/null | head -1 | cut -d= -f2-)
    fi
    printf '%s' "${value:-$default}"
}

case "$1" in
    info) exit 0 ;;
    version) echo "99.9.9"; exit 0 ;;
    pull)
        if [[ -n "${FAKE_FAIL_PULL:-}" && "$*" == *"$FAKE_FAIL_PULL"* ]]; then
            echo "Error response from daemon: pull access denied" >&2
            exit 1
        fi
        exit 0 ;;
    stop) exit 0 ;;
    ps)
        [[ -n "${FAKE_LEGACY_CONTAINER:-}" ]] && echo "c0ffee123456"
        exit 0 ;;
    volume)
        [[ -n "${FAKE_VOLUME_EXISTS:-}" ]] && exit 0
        exit 1 ;;
    inspect)
        if [[ "$*" == *nl2sql-vectordb* ]]; then
            echo "${FAKE_VECTOR_HEALTH:-healthy}"
        elif [[ "$*" == *nl2sql-chunkdb* ]]; then
            echo "${FAKE_CONTEXT_HEALTH:-healthy}"
        elif [[ "$*" == *nl2sql-api* && "$*" == *Running* ]]; then
            echo "${FAKE_API_RUNNING:-true}"
        elif [[ "$*" == *nl2sql-api* ]]; then
            echo "${FAKE_API_HEALTH:-healthy}"
        else
            echo "${FAKE_PG_HEALTH:-healthy}"
        fi
        exit 0 ;;
    compose)
        shift
        # Matched on the whole argument list rather than $1: these are
        # invoked with a --profile in front of the subcommand.
        if [[ "$*" == *" logs "* || "$*" == *" logs" ]]; then
            # What the API container said, for the branch that tells an image
            # without the REST API apart from any other startup failure.
            printf '%s\n' "${FAKE_API_LOGS:-}"
            exit 0
        fi
        case "$1" in
            version) echo "2.99.0"; exit 0 ;;
            build|up|down) exit 0 ;;
            run)
                # The end-of-setup retrieval probe, run inside the agent image.
                if [[ -n "${FAKE_PROBE_FAILS:-}" ]]; then
                    echo "ModuleNotFoundError: No module named 'nl2sql_agent.retrieval'" >&2
                    exit 1
                fi
                printf 'PROBE {"chunks": %s, "collections": %s}\n' \
                    "${FAKE_PROBE_CHUNKS-1}" "${FAKE_PROBE_COLLECTIONS-3}"
                exit 0 ;;
            exec)
                # Ordered most specific first: several of these run against the
                # same service and are told apart only by the SQL.
                if [[ "$*" == *"pg_extension"* ]]; then
                    echo "${FAKE_TRGM_INSTALLED-1}"
                elif [[ "$*" == *"role_table_grants"* ]]; then
                    echo "${FAKE_READER_EXTRA_GRANTS-0}"
                elif [[ "$*" == *ddl_index_embeddings* ]]; then
                    echo "${FAKE_DDL_CHUNKS-20}"
                elif [[ "$*" == *reader=* ]]; then
                    # docker/reader_role.sql, piped in as the superuser.
                    [[ -n "${FAKE_READER_ROLE_FAILS:-}" ]] && exit 1
                    exit 0
                elif [[ "$*" == *golden_pair_question_vectors* ]]; then
                    echo "${FAKE_VECTOR_COUNT-45}"
                elif [[ "$*" == *golden_pairs* ]]; then
                    echo "${FAKE_PAIR_COUNT-45}"
                elif [[ "$*" == *vectordb* ]]; then
                    echo "${FAKE_CHUNK_COUNT-53}"
                else
                    echo "${FAKE_ROW_COUNT-194101}"
                fi
                exit 0 ;;
            config)
                # One write, like the real thing: a reader that stops early
                # must not be able to SIGPIPE us mid-stream.
                printf '    OLLAMA_BASE_URL: %s\n    OLLAMA_MODEL: %s\n    EMBED_BASE_URL: %s\n    EMBED_MODEL: %s\n' \
                    "$(read_env OLLAMA_BASE_URL http://192.168.10.82:11434)" \
                    "$(read_env OLLAMA_MODEL qwen3.8-256k)" \
                    "$(read_env EMBED_BASE_URL http://host.docker.internal:11434)" \
                    "$(read_env EMBED_MODEL bge-m3)"
                exit 0 ;;
            *) exit 0 ;;
        esac ;;
    *) exit 0 ;;
esac
"""

FAKE_CURL = r"""#!/usr/bin/env bash
# Log only the URL argument so tests can assert which host was probed.
for arg in "$@"; do
    case "$arg" in http*) printf 'curl %s\n' "$arg" >> "$FAKE_LOG" ;; esac
done
if [[ -n "${FAKE_OLLAMA_DOWN:-}" ]]; then exit 7; fi

# Assigned on its own line rather than inline as ${VAR:-default}: brace
# expansion stops at the first unescaped } and would truncate this JSON.
models="${FAKE_OLLAMA_MODELS:-}"
if [[ -z "$models" ]]; then
    models='{"name":"qwen3.8-256k"},{"name":"bge-m3:latest"}'
fi
printf '{"models":[%s]}\n' "$models"
"""

# setup.sh polls health with `sleep 2` up to 60 times; a real sleep would make
# the failure-path tests take minutes.
FAKE_SLEEP = "#!/usr/bin/env bash\nexit 0\n"


@dataclass
class SetupRun:
    returncode: int
    stdout: str
    stderr: str
    calls: list[str]
    workdir: Path

    @property
    def output(self) -> str:
        return self.stdout + self.stderr

    def env_file(self) -> dict[str, str]:
        path = self.workdir / ".env"
        if not path.exists():
            return {}
        values = {}
        for line in path.read_text().splitlines():
            if line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key] = value
        return values

    def called(self, fragment: str) -> bool:
        return any(fragment in call for call in self.calls)

    def calls_matching(self, fragment: str) -> list[str]:
        return [call for call in self.calls if fragment in call]


def _copy_reader_role_sql(workdir: Path) -> None:
    """Both scripts pipe docker/reader_role.sql into the (fake) container."""
    (workdir / "docker").mkdir()
    shutil.copy(REPO_ROOT / "docker" / "reader_role.sql", workdir / "docker" / "reader_role.sql")


@pytest.fixture
def run_setup(tmp_path: Path):
    """Run setup.sh in a sandbox. Returns a callable: run_setup(*args, env=...)."""

    workdir = tmp_path / "repo"
    workdir.mkdir()
    for name in ("setup.sh", "docker-compose.yml"):
        shutil.copy(REPO_ROOT / name, workdir / name)
    os.chmod(workdir / "setup.sh", 0o755)
    _copy_reader_role_sql(workdir)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("docker", FAKE_DOCKER), ("curl", FAKE_CURL), ("sleep", FAKE_SLEEP)):
        path = bin_dir / name
        path.write_text(body)
        os.chmod(path, 0o755)

    log = tmp_path / "calls.log"
    log.touch()

    def _run(*args: str, env: dict | None = None, timeout: int = 60) -> SetupRun:
        run_env = dict(os.environ)
        run_env["PATH"] = f"{bin_dir}:{run_env['PATH']}"
        run_env["FAKE_LOG"] = str(log)
        if env:
            run_env.update(env)

        result = subprocess.run(
            ["bash", str(workdir / "setup.sh"), *args],
            cwd=workdir, capture_output=True, text=True, timeout=timeout, env=run_env,
        )
        return SetupRun(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            calls=log.read_text().splitlines(),
            workdir=workdir,
        )

    return _run


@pytest.fixture
def run_launch(tmp_path: Path):
    """Run launch.sh in the same sandbox setup.sh gets.

    launch.sh expects a .env to already exist -- that is setup.sh's job -- so
    one is written by default. Pass `env_file=None` to exercise the handoff.
    """

    workdir = tmp_path / "repo"
    workdir.mkdir()
    for name in ("launch.sh", "setup.sh", "docker-compose.yml"):
        shutil.copy(REPO_ROOT / name, workdir / name)
        os.chmod(workdir / name, 0o755)
    _copy_reader_role_sql(workdir)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("docker", FAKE_DOCKER), ("curl", FAKE_CURL), ("sleep", FAKE_SLEEP)):
        path = bin_dir / name
        path.write_text(body)
        os.chmod(path, 0o755)

    log = tmp_path / "calls.log"
    log.touch()

    DEFAULT_ENV_FILE = (
        "IMAGE_NAME=mcfaddja/nl2sql-retail-postgres\n"
        "IMAGE_TAG=v1\n"
        "AGENT_IMAGE_NAME=mcfaddja/nl2sql-agent\n"
        "AGENT_IMAGE_TAG=v3\n"
        "VECTOR_IMAGE_NAME=mcfaddja/nl2sql-rag-vectordb\n"
        "VECTOR_IMAGE_TAG=v3\n"
        "CONTEXT_IMAGE_NAME=mcfaddja/nl2sql-rag-chunkdb\n"
        "CONTEXT_IMAGE_TAG=v3\n"
        "RAG_ENABLED=true\n"
    )

    def _run(*args: str, env: dict | None = None, env_file: str | None = DEFAULT_ENV_FILE,
             timeout: int = 60) -> SetupRun:
        dotenv = workdir / ".env"
        if env_file is None:
            dotenv.unlink(missing_ok=True)
        else:
            dotenv.write_text(env_file)

        run_env = dict(os.environ)
        run_env["PATH"] = f"{bin_dir}:{run_env['PATH']}"
        run_env["FAKE_LOG"] = str(log)
        if env:
            run_env.update(env)

        result = subprocess.run(
            ["bash", str(workdir / "launch.sh"), *args],
            cwd=workdir, capture_output=True, text=True, timeout=timeout, env=run_env,
        )
        return SetupRun(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            calls=log.read_text().splitlines(),
            workdir=workdir,
        )

    return _run
