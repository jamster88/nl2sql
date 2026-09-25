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
    info)
        [[ -n "${FAKE_NO_DAEMON:-}" ]] && exit 1
        exit 0 ;;
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
    image)
        # `docker image inspect <tag>` -- launch.sh asks whether the desktop
        # client's image is already here before deciding what to say about
        # where the jar is coming from.
        if [[ "$2" == "inspect" ]]; then
            [[ -n "${FAKE_DESKTOP_IMAGE_PRESENT:-}" ]] && exit 0
            exit 1
        fi
        exit 0 ;;
    inspect)
        if [[ "$*" == *nl2sql-vectordb* ]]; then
            echo "${FAKE_VECTOR_HEALTH:-healthy}"
        elif [[ "$*" == *nl2sql-chunkdb* ]]; then
            echo "${FAKE_CONTEXT_HEALTH:-healthy}"
        elif [[ "$*" == *nl2sql-api* && "$*" == *Running* ]]; then
            echo "${FAKE_API_RUNNING:-true}"
        elif [[ "$*" == *nl2sql-api* ]]; then
            echo "${FAKE_API_HEALTH:-healthy}"
        elif [[ "$*" == *nl2sql-gui* && "$*" == *Running* ]]; then
            echo "${FAKE_GUI_RUNNING:-true}"
        elif [[ "$*" == *nl2sql-gui* ]]; then
            echo "${FAKE_GUI_HEALTH:-healthy}"
        elif [[ "$*" == *nl2sql-feedbackdb* ]]; then
            echo "${FAKE_FEEDBACK_HEALTH:-healthy}"
        # The review GUI's container name contains the review service's, so
        # the longer name is matched first or every review-gui inspect would
        # be answered as the service.
        elif [[ "$*" == *nl2sql-review-gui* && "$*" == *Running* ]]; then
            echo "${FAKE_REVIEW_GUI_RUNNING:-true}"
        elif [[ "$*" == *nl2sql-review-gui* ]]; then
            echo "${FAKE_REVIEW_GUI_HEALTH:-healthy}"
        elif [[ "$*" == *nl2sql-review* && "$*" == *Running* ]]; then
            echo "${FAKE_REVIEW_RUNNING:-true}"
        elif [[ "$*" == *nl2sql-review* ]]; then
            echo "${FAKE_REVIEW_HEALTH:-healthy}"
        else
            echo "${FAKE_PG_HEALTH:-healthy}"
        fi
        exit 0 ;;
    compose)
        shift
        # Matched on the whole argument list rather than $1: these are
        # invoked with a --profile in front of the subcommand.
        if [[ "$*" == *"run --rm desktop"* ]]; then
            # Builds the desktop client's jar into a bind mount. The real one
            # takes minutes; this one writes the file that proves it ran.
            [[ -n "${FAKE_DESKTOP_BUILD_FAILS:-}" ]] && exit 1
            mkdir -p desktop/target
            printf 'not really a jar\n' > desktop/target/nl2sql-desktop.jar
            exit 0
        fi
        if [[ "$*" == *" cp api:"* ]]; then
            # Copies the API's certificate out of the volume it writes it to.
            [[ -n "${FAKE_CERT_COPY_FAILS:-}" ]] && exit 1
            printf 'not really a certificate\n' > "${@: -1}"
            exit 0
        fi
        if [[ "$*" == *" logs "* || "$*" == *" logs" ]]; then
            # What the API container said, for the branch that tells an image
            # without the REST API apart from any other startup failure.
            printf '%s\n' "${FAKE_API_LOGS:-}"
            exit 0
        fi
        case "$1" in
            version)
                [[ -n "${FAKE_NO_COMPOSE:-}" ]] && exit 1
                echo "2.99.0"; exit 0 ;;
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
# start.sh polls the web interface with curl before opening a browser at
# it; this is how a test makes that poll time out without also taking the
# Ollama probe down.
if [[ -n "${FAKE_GUI_DOWN:-}" && "$*" == *":${FAKE_GUI_PORT:-8080}"* ]]; then exit 7; fi
if [[ -n "${FAKE_OLLAMA_DOWN:-}" ]]; then exit 7; fi

# launch.sh probes /readyz *through* a proxy to find out whether nginx is
# still trusting a certificate that has since been reissued -- the container
# is healthy either way, so only a request the API owns tells them apart.
# FAKE_PROXY_BROKEN makes that probe fail once per port, which is what a
# stale certificate looks like: the restart is expected to cure it.
# FAKE_PROXY_DEAD makes it fail every time, which is the other case -- the
# proxy cannot reach the API for a reason a restart does not fix, and the
# script has to say so rather than report it as working.
if [[ "$*" == */readyz* ]]; then
    code=200
    for port in ${FAKE_PROXY_DEAD:-}; do
        if [[ "$*" == *":${port}/readyz"* ]]; then code=000; fi
    done
    for port in ${FAKE_PROXY_BROKEN:-}; do
        if [[ "$*" == *":${port}/readyz"* ]]; then
            marker="${FAKE_LOG%/*}/proxy-broken-${port}"
            if [[ -e "$marker" ]]; then
                # Already restarted once; the reissued certificate is loaded.
                code=200
            else
                touch "$marker"
                code=000
            fi
        fi
    done
    printf 'curl readyz %s -> %s\n' "$*" "$code" >> "$FAKE_LOG"
    for arg in "$@"; do
        case "$prev_w" in -w|--write-out) printf '%s' "$code" ;; esac
        prev_w="$arg"
    done
    [[ "$code" == "000" ]] && exit 7
    exit 0
fi

# Assigned on its own line rather than inline as ${VAR:-default}: brace
# expansion stops at the first unescaped } and would truncate this JSON.
models="${FAKE_OLLAMA_MODELS:-}"
if [[ -z "$models" ]]; then
    models='{"name":"qwen3.8-256k"},{"name":"bge-m3:latest"}'
fi

# Honour -o, as the real curl does. Without this a caller that asked for the
# body to go to a file still got it on stdout, which is invisible until
# something is checked for saying nothing.
out=""
prev=""
for arg in "$@"; do
    # Bundled short flags too: real curl takes the argument after -sfo.
    case "$prev" in -o|--output|-[a-zA-Z]*o) out="$arg" ;; esac
    prev="$arg"
done
if [[ -n "$out" ]]; then
    printf '{"models":[%s]}\n' "$models" > "$out"
else
    printf '{"models":[%s]}\n' "$models"
fi
"""

# setup.sh polls health with `sleep 2` up to 60 times; a real sleep would make
# the failure-path tests take minutes.
FAKE_SLEEP = "#!/usr/bin/env bash\nexit 0\n"

#: Stands in for whichever of `open`, `xdg-open`, `wslview` and friends the
#: machine running the tests would actually have. All of them are installed
#: into the sandbox, so the platform branch start.sh takes is the one that
#: gets exercised rather than one chosen by the test.
FAKE_BROWSER = r"""#!/usr/bin/env bash
printf 'browser %s %s\n' "$(basename "$0")" "$*" >> "$FAKE_LOG"
exit ${FAKE_BROWSER_EXIT:-0}
"""

#: `uname -s` decides which opener start.sh reaches for, so on a Mac the
#: Linux and Windows branches would never run. This defers to the real one
#: unless a test says otherwise, so nothing else in the sandbox changes.
FAKE_UNAME = r"""#!/usr/bin/env bash
if [[ -n "${FAKE_UNAME_S:-}" && "$*" == "-s" ]]; then
    printf '%s\n' "$FAKE_UNAME_S"
    exit 0
fi
# The machine, for the JavaFX classifier launch.sh works out. Answered
# separately from -s because the two vary independently: an arm64 Mac and an
# arm64 Linux box need different builds of the same client.
if [[ -n "${FAKE_UNAME_M:-}" && "$*" == "-m" ]]; then
    printf '%s\n' "$FAKE_UNAME_M"
    exit 0
fi
exec /usr/bin/uname "$@"
"""

#: start.sh tells WSL apart by reading /proc/version, which does not exist
#: on a Mac and cannot be created there. This answers for that one path and
#: hands everything else to the real grep -- which matters, because both
#: setup.sh and launch.sh grep .env on every run.
FAKE_GREP = r"""#!/usr/bin/env bash
for arg in "$@"; do
    if [[ "$arg" == "/proc/version" ]]; then
        [[ -n "${FAKE_WSL:-}" ]] && exit 0
        exit 1
    fi
done
for candidate in /usr/bin/grep /bin/grep; do
    [[ -x "$candidate" ]] && exec "$candidate" "$@"
done
exit 127
"""

BROWSER_OPENERS = (
    "open", "xdg-open", "wslview", "explorer.exe", "gio", "x-www-browser",
    "sensible-browser", "start",
)


#: Set to a directory to have every sandboxed script run under `bash -x`,
#: with its trace appended there. That is what `python -m tests.shell_coverage`
#: does, and it is the only way these scripts get a line-coverage number --
#: nothing in coverage.py can see a shell script. Off unless asked for: the
#: traces are large and the assertions do not want them on stderr.
SHELL_TRACE = "NL2SQL_SHELL_TRACE"


def _traced(command: list[str], run_env: dict) -> list[str]:
    """Add `-x` and a line-numbering PS4 when tracing is on."""
    if not os.environ.get(SHELL_TRACE):
        return command
    # The basename only: bash 3.2 truncates a long PS4, and a temporary
    # directory path is long.
    run_env["PS4"] = "+@${BASH_SOURCE##*/}@${LINENO}@ "
    return [command[0], "-x", *command[1:]]


def _record(result: subprocess.CompletedProcess) -> None:
    directory = os.environ.get(SHELL_TRACE)
    if directory:
        with open(os.path.join(directory, "trace.log"), "a") as handle:
            handle.write(result.stderr)


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

    def index_of(self, fragment: str) -> int:
        """Where a call appears, so ordering can be asserted."""
        for index, call in enumerate(self.calls):
            if fragment in call:
                return index
        raise AssertionError(f"no call matching {fragment!r} in:\n" + "\n".join(self.calls))


def _copy_reader_role_sql(workdir: Path) -> None:
    """Both scripts pipe docker/reader_role.sql into the (fake) container."""
    (workdir / "docker").mkdir()
    shutil.copy(REPO_ROOT / "docker" / "reader_role.sql", workdir / "docker" / "reader_role.sql")


def _make_desktop_sources(workdir: Path) -> None:
    """Enough of `desktop/` for launch.sh to decide whether the jar is stale.

    It compares the jar's timestamp against everything under `desktop/src`
    and `desktop/pom.xml`. With neither present `find` reports nothing, which
    reads as "no source is newer" -- so a sandbox without them would answer
    "already built" for a jar that was never built from anything.
    """
    (workdir / "desktop" / "src").mkdir(parents=True)
    (workdir / "desktop" / "src" / "Main.java").write_text("// a source file\n")
    (workdir / "desktop" / "pom.xml").write_text("<project/>\n")


#: Stands in for a Java runtime. `start.sh` asks for its version before it
#: runs anything, and the answer decides whether it runs anything at all.
FAKE_JAVA = r"""#!/usr/bin/env bash
if [[ "$1" == "-version" ]]; then
    printf 'openjdk version "%s" 2026-09-25\n' "${FAKE_JAVA_VERSION:-21.0.12}" >&2
    exit 0
fi
printf 'java %s\n' "$*" >> "$FAKE_LOG"
if [[ -n "${FAKE_JAVA_DIES:-}" ]]; then
    printf 'Exception in Application start method\n' >&2
    exit 1
fi
# start.sh asks whether the window is still there a moment after opening it,
# so this has to still be there. The real `sleep`, because the fake one on
# PATH returns instantly -- which is what keeps the test itself quick.
exec /bin/sleep 5
"""


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
    # `uname` again: setup.sh reads the system and the machine to decide
    # which of the desktop client's five published tags to pull.
    for name, body in (
        ("docker", FAKE_DOCKER), ("curl", FAKE_CURL), ("sleep", FAKE_SLEEP),
        ("uname", FAKE_UNAME),
    ):
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
            _traced(["bash", str(workdir / "setup.sh")], run_env) + list(args),
            cwd=workdir, capture_output=True, text=True, timeout=timeout, env=run_env,
        )
        _record(result)
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
    _make_desktop_sources(workdir)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # `uname` is here for the desktop client: launch.sh reads the system and
    # the machine to decide which of OpenJFX's five native builds to ask for,
    # and a test that could not answer for a machine other than this one
    # would only ever exercise one of the five.
    for name, body in (
        ("docker", FAKE_DOCKER), ("curl", FAKE_CURL), ("sleep", FAKE_SLEEP),
        ("uname", FAKE_UNAME),
    ):
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
            _traced(["bash", str(workdir / "launch.sh")], run_env) + list(args),
            cwd=workdir, capture_output=True, text=True, timeout=timeout, env=run_env,
        )
        _record(result)
        return SetupRun(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            calls=log.read_text().splitlines(),
            workdir=workdir,
        )

    return _run


@pytest.fixture
def run_start(tmp_path: Path):
    """Run start.sh in the sandbox the other two scripts get, plus a browser.

    start.sh runs the real `setup.sh` and `launch.sh` rather than stubs of
    them: its whole job is orchestrating those two and the browser, and
    against stubs there would be nothing left to be wrong. The fake `docker`
    underneath is what keeps that affordable.

    The browser openers are fakes installed ahead of everything else on
    PATH, because the real ones are there too: `/usr/bin/open` exists on
    every Mac, and a fixture that does not shadow it opens tabs on the
    developer's desktop every time the suite runs.
    """

    workdir = tmp_path / "repo"
    workdir.mkdir()
    for name in ("start.sh", "launch.sh", "setup.sh", "docker-compose.yml"):
        shutil.copy(REPO_ROOT / name, workdir / name)
        os.chmod(workdir / name, 0o755)
    _copy_reader_role_sql(workdir)
    _make_desktop_sources(workdir)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (
        ("docker", FAKE_DOCKER), ("curl", FAKE_CURL), ("sleep", FAKE_SLEEP),
        ("uname", FAKE_UNAME), ("grep", FAKE_GREP), ("java", FAKE_JAVA),
    ):
        path = bin_dir / name
        path.write_text(body)
        os.chmod(path, 0o755)

    browser_dir = tmp_path / "browsers"
    browser_dir.mkdir()
    for name in BROWSER_OPENERS:
        path = browser_dir / name
        path.write_text(FAKE_BROWSER)
        os.chmod(path, 0o755)

    log = tmp_path / "calls.log"
    log.touch()

    DEFAULT_ENV_FILE = (
        "IMAGE_NAME=mcfaddja/nl2sql-retail-postgres\n"
        "IMAGE_TAG=v1\n"
        "AGENT_IMAGE_NAME=mcfaddja/nl2sql-agent\n"
        "AGENT_IMAGE_TAG=v4_2\n"
        "GUI_IMAGE_NAME=mcfaddja/nl2sql-gui\n"
        "GUI_IMAGE_TAG=v4_2\n"
        "VECTOR_IMAGE_NAME=mcfaddja/nl2sql-rag-vectordb\n"
        "VECTOR_IMAGE_TAG=v3\n"
        "CONTEXT_IMAGE_NAME=mcfaddja/nl2sql-rag-chunkdb\n"
        "CONTEXT_IMAGE_TAG=v3\n"
        "RAG_ENABLED=true\n"
    )

    def _run(*args: str, env: dict | None = None, env_file: str | None = DEFAULT_ENV_FILE,
             timeout: int = 120) -> SetupRun:
        dotenv = workdir / ".env"
        if env_file is None:
            dotenv.unlink(missing_ok=True)
        else:
            dotenv.write_text(env_file)

        run_env = dict(os.environ)
        # The browser fakes go *first* so they shadow the real ones. Without
        # that, /usr/bin/open exists on every Mac and a test run opens tabs
        # on the developer's desktop -- which is exactly what happened the
        # first time this fixture was written.
        run_env["PATH"] = f"{browser_dir}:{bin_dir}:{run_env['PATH']}"
        run_env["FAKE_LOG"] = str(log)
        # Otherwise the developer's own setting picks the opener and the
        # platform branch under test never runs.
        run_env.pop("BROWSER", None)
        if env:
            run_env.update(env)

        result = subprocess.run(
            _traced(["bash", str(workdir / "start.sh")], run_env) + list(args),
            cwd=workdir, capture_output=True, text=True, timeout=timeout, env=run_env,
        )
        _record(result)
        return SetupRun(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            calls=log.read_text().splitlines(),
            workdir=workdir,
        )

    return _run


#: One binary standing in for `initdb`, `pg_ctl` and `psql`. `init_db.sh`
#: runs only inside the postgres image build, where running it for real would
#: mean building the whole dataset image; these record their arguments so the
#: script itself still executes end to end rather than only being read.
FAKE_PG_TOOL = r"""#!/usr/bin/env bash
printf '%s %s\n' "${0##*/}" "$*" >> "$FAKE_LOG"
if [[ -n "${FAKE_PG_FAIL:-}" && "$*" == *"$FAKE_PG_FAIL"* ]]; then
    echo "fake ${0##*/}: refused ${FAKE_PG_FAIL}" >&2
    exit 1
fi
exit 0
"""


@pytest.fixture
def run_init_db(tmp_path: Path):
    """Run docker/init_db.sh against fake Postgres binaries.

    Returns a callable: run_init_db(env=...). The build ARGs the Dockerfile
    passes are supplied as defaults, so a test overrides only what it is
    about.
    """
    workdir = tmp_path / "build"
    workdir.mkdir()
    shutil.copy(REPO_ROOT / "docker" / "init_db.sh", workdir / "init_db.sh")
    os.chmod(workdir / "init_db.sh", 0o755)

    pgdata = tmp_path / "pgdata"
    pgdata.mkdir()
    for name in ("ddl.sql", "_load.sql", "reader_role.sql"):
        (workdir / name).write_text("-- fixture\n")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("initdb", "pg_ctl", "psql"):
        path = bin_dir / name
        path.write_text(FAKE_PG_TOOL)
        os.chmod(path, 0o755)

    log = tmp_path / "calls.log"
    log.touch()

    def _run(env: dict | None = None, timeout: int = 60) -> SetupRun:
        run_env = dict(os.environ)
        run_env["PATH"] = f"{bin_dir}:{run_env['PATH']}"
        run_env["FAKE_LOG"] = str(log)
        run_env.update(
            {
                "PGDATA": str(pgdata),
                "DB_NAME": "nl2sql_retail",
                "DB_USER": "nl2sql",
                "DB_PASSWORD": "owner-secret",
                "DB_READER": "nl2sql_reader",
                "DB_READER_PASSWORD": "reader-secret",
                "DDL_FILE": str(workdir / "ddl.sql"),
                "LOAD_SQL": str(workdir / "_load.sql"),
                "READER_SQL": str(workdir / "reader_role.sql"),
            }
        )
        for key, value in (env or {}).items():
            # None removes the variable, which is the only way to exercise
            # `set -u` -- an ARG the Dockerfile forgot to pass is absent, not
            # empty.
            if value is None:
                run_env.pop(key, None)
            else:
                run_env[key] = value

        result = subprocess.run(
            _traced(["bash", str(workdir / "init_db.sh")], run_env),
            cwd=workdir, capture_output=True, text=True, timeout=timeout, env=run_env,
        )
        _record(result)
        return SetupRun(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            calls=log.read_text().splitlines(),
            workdir=workdir,
        )

    return _run
