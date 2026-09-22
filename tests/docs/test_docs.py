"""Documentation kept honest by the code it documents.

The READMEs and USAGE.md describe flags, environment variables and published
image tags. Every one of those is a fact about the code or the compose file
that can drift silently: a new setting is added, the table that lists it is
not, and the docs quietly become wrong. These tests pin the directions that
actually rot -- code gaining something the docs never mention, and docs
pointing at files a fresh clone does not have.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
AGENT_DIR = REPO_ROOT / "agent"

DOCS = ("README.md", "agent/README.md", "agent/USAGE.md", "agent/API.md", "data_gen/README.md")


@pytest.fixture(scope="module")
def root_readme() -> str:
    return (REPO_ROOT / "README.md").read_text()


@pytest.fixture(scope="module")
def agent_readme() -> str:
    return (AGENT_DIR / "README.md").read_text()


@pytest.fixture(scope="module")
def agent_usage() -> str:
    return (AGENT_DIR / "USAGE.md").read_text()


@pytest.fixture(scope="module")
def agent_api_doc() -> str:
    return (AGENT_DIR / "API.md").read_text()


@pytest.fixture(scope="module")
def launch_sh() -> str:
    return (REPO_ROOT / "launch.sh").read_text()


@pytest.fixture(scope="module")
def setup_sh() -> str:
    return (REPO_ROOT / "setup.sh").read_text()


# ---------------------------------------------------------------------------
# The agent's own surface
# ---------------------------------------------------------------------------


def test_every_cli_flag_is_documented(agent_readme: str, agent_usage: str):
    """A flag nobody can discover may as well not exist."""
    from nl2sql_agent.__main__ import parse_args

    parser = _parser_from(parse_args)
    documented = agent_readme + agent_usage
    for action in parser._actions:
        for flag in action.option_strings:
            if flag in ("-h", "--help"):
                continue
            assert flag in documented, f"{flag} is not mentioned in agent/README.md or agent/USAGE.md"


def test_every_setting_the_agent_reads_is_documented(agent_readme: str):
    """config.py is the authority on what the environment can set; the README
    config table is how anyone finds out. This is the mirror image of the
    compose test that checks nothing is set that the agent never reads.
    """
    config_py = (AGENT_DIR / "nl2sql_agent" / "config.py").read_text()
    env_vars = set(re.findall(r'os\.getenv\(\s*"([A-Z_]+)"', config_py))
    env_vars |= set(re.findall(r'_env_(?:bool|int)\(\s*"([A-Z_]+)"', config_py))
    assert env_vars, "no environment variables found in config.py -- the regex needs updating"
    for name in sorted(env_vars):
        assert f"`{name}`" in agent_readme, f"{name} is read by config.py but absent from the README config table"


def test_the_documented_defaults_are_the_real_defaults(agent_readme: str):
    """The config table quotes default values; a bumped default that leaves
    the table behind is worse than no table at all.
    """
    from nl2sql_agent.config import Settings

    settings = Settings()
    for name, value in (
        ("OLLAMA_NUM_CTX", settings.num_ctx),
        ("MAX_ROWS", settings.max_rows),
        ("MAX_ATTEMPTS", settings.max_attempts),
        ("MAX_PLAN_COST", int(settings.max_plan_cost)),
        ("MAX_TABLES", settings.max_tables),
        ("SCHEMA_TOP_K", settings.schema_top_k),
        ("SAMPLE_ROWS", settings.sample_rows),
        ("STATEMENT_TIMEOUT_MS", settings.statement_timeout_ms),
        ("RAG_TOP_K", settings.rag_top_k),
        ("RAG_MAX_CONTEXT_CHARS", settings.rag_max_context_chars),
    ):
        row = _table_row(agent_readme, name)
        assert str(value) in row, f"README says {name} defaults to something other than {value}: {row!r}"

    for name, value in (
        ("EMBED_MODEL", settings.embed_model),
        ("OLLAMA_MODEL", settings.ollama_model),
        ("OLLAMA_BASE_URL", settings.ollama_base_url),
        ("EMBED_BASE_URL", settings.embed_base_url),
    ):
        row = _table_row(agent_readme, name)
        assert value in row, f"README says {name} defaults to something other than {value!r}: {row!r}"


# ---------------------------------------------------------------------------
# The REST API's surface
# ---------------------------------------------------------------------------


def test_every_api_setting_is_documented(agent_api_doc: str):
    """API.md is the contract a GUI is written against, and its settings
    table is where anyone finds out a knob exists. Same relationship as
    config.py and the agent README's table, checked the same way.
    """
    source = (AGENT_DIR / "nl2sql_agent" / "api" / "settings.py").read_text()
    names = set(re.findall(r'_env(?:_str|_bool|_int|_float|_tuple)?\(\s*"([A-Z_]+)"', source))
    assert names, "no environment variables found in api/settings.py -- the regex needs updating"
    for name in sorted(names):
        assert f"`{name}`" in agent_api_doc, f"{name} is read by the server but absent from API.md"


def test_the_documented_api_defaults_are_the_real_defaults(agent_api_doc: str):
    from nl2sql_agent.api.settings import ApiSettings

    def rows(name: str) -> list[str]:
        """Every table row mentioning the setting, not just the first.

        `API_JOB_TTL_SECONDS` is named in the error table as well as the
        settings one, and the first match is not the row with the default in
        it.
        """
        return [line for line in agent_api_doc.splitlines() if f"`{name}`" in line]

    settings = ApiSettings()
    for name, value in (
        ("API_PORT", settings.port),
        ("API_TLS_DAYS", settings.tls_days),
        ("API_MAX_CONCURRENCY", settings.max_concurrency),
        ("API_JOB_TTL_SECONDS", settings.job_ttl_seconds),
        ("API_MAX_JOBS", settings.max_jobs),
        ("API_KEEPALIVE_SECONDS", settings.keepalive_seconds),
        ("API_TLS_CERT_FILE", settings.tls_cert_file),
        ("API_TLS_KEY_FILE", settings.tls_key_file),
    ):
        found = rows(name)
        # `15` and `15.0` are the same default; the table reads better
        # without the trailing zero and the dataclass needs the float.
        spellings = {str(value)}
        if isinstance(value, float):
            spellings.add(f"{value:g}")
        assert any(any(s in row for s in spellings) for row in found), (
            f"API.md never says {name} defaults to {value}: {found!r}"
        )


def test_every_route_the_server_serves_is_documented(agent_api_doc: str):
    """A route absent from the table is a route nobody knows to call, however
    well it works.
    """
    from nl2sql_agent.api.app import create_app
    from nl2sql_agent.api.settings import ApiSettings
    from nl2sql_agent.config import Settings

    app = create_app(settings=Settings(), api_settings=ApiSettings(token=None))
    documented = agent_api_doc
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith(("/v1", "/healthz", "/readyz")):
            continue
        assert path in documented, f"the server serves {path}, which API.md never mentions"


def test_every_error_code_the_server_can_return_is_documented(agent_api_doc: str):
    """A client branches on these. One that is returned but undocumented is
    one nobody handles.
    """
    source = (AGENT_DIR / "nl2sql_agent" / "api" / "app.py").read_text()
    codes = set(re.findall(r'ApiHTTPError\(\s*\n?\s*[\w.]+,\s*\n?\s*"([a-z_]+)"', source))
    codes |= set(re.findall(r'_error_response\(\s*\n?\s*\d+,\s*\n?\s*"([a-z_]+)"', source))
    assert codes, "no error codes found in app.py -- the regex needs updating"
    for code in sorted(codes):
        assert f"`{code}`" in agent_api_doc, f"the server returns {code!r}, which API.md never lists"


def test_the_api_flag_is_documented_where_someone_would_look(root_readme: str, agent_usage: str):
    """It opens a port, which is the kind of thing that should not be
    discoverable only by reading the script.
    """
    for doc in (root_readme, agent_usage):
        assert "./launch.sh --api" in doc


def test_the_switch_that_refuses_the_development_certificate_is_documented(
    agent_api_doc: str, root_readme: str
):
    """The whole point of shipping a dummy certificate is that it can be
    taken away. Someone deploying this has to be able to find out how.
    """
    assert "API_TLS_ALLOW_SELF_SIGNED=false" in agent_api_doc
    assert "API_TLS_ALLOW_SELF_SIGNED=false" in root_readme


def test_the_documented_version_is_the_packaged_one(agent_readme: str, root_readme: str):
    from nl2sql_agent import __version__

    major = __version__.split(".")[0]
    assert f"(v{major}" in agent_readme.splitlines()[0], agent_readme.splitlines()[0]
    assert f"nl2sql-agent:v{major}" in root_readme


def test_the_documented_pipeline_matches_the_graph(agent_readme: str):
    """The README draws the node order and tabulates it. A node renamed or
    inserted in graph.py leaves that diagram describing a pipeline that no
    longer exists.
    """
    graph_py = (AGENT_DIR / "nl2sql_agent" / "graph.py").read_text()
    nodes = set(re.findall(r'add_node\(\s*"(\w+)"', graph_py))
    assert nodes, "no nodes found in graph.py -- the regex needs updating"

    diagram = agent_readme.split("## The pipeline")[1].split("## Configuration")[0]
    for node in sorted(nodes):
        assert node in diagram, f"graph.py defines the node {node!r}, which the README never mentions"

    for named in re.findall(r"`(\w+)`", diagram):
        if named.islower() and named.endswith(("_sql", "_tables", "_schema", "_query", "_knowledge")):
            assert named in nodes or named in _tool_names(), (
                f"the README pipeline section names {named!r}, which is neither a node nor a tool"
            )


def test_every_tool_is_documented(agent_readme: str):
    for name in sorted(_tool_names()):
        assert f"`{name}`" in agent_readme, f"build_tools() returns {name!r}, absent from the README"


def _tool_names() -> set[str]:
    """The keys build_tools() actually returns -- taken from the object rather
    than by pattern-matching the source, which would also pick up the dict
    keys the tools themselves return.
    """
    from nl2sql_agent.config import Settings
    from nl2sql_agent.tools import build_tools

    return set(build_tools(object(), object(), Settings()))


# ---------------------------------------------------------------------------
# Published images
# ---------------------------------------------------------------------------


def test_every_image_tag_setup_defaults_to_is_documented(setup_sh: str, root_readme: str):
    """setup.sh pins a tag per image; if the README's tag tables do not list
    it, the default nobody passes is also the one nobody has read about.
    """
    for var in ("POSTGRES_IMAGE", "AGENT_IMAGE", "VECTOR_IMAGE"):
        image = re.search(rf'^{var}="([^"]+)"', setup_sh, re.MULTILINE).group(1)
        tag = re.search(rf'^{var.replace("_IMAGE", "_TAG")}="([^"]+)"', setup_sh, re.MULTILINE).group(1)
        assert f"{image}:{tag}" in root_readme, f"README never shows {image}:{tag}"


def test_the_pre_rag_agent_tag_is_documented(root_readme: str):
    """v1 is published alongside v2 so the schema-only agent stays reachable
    (it is what the 107.5% / 21.5% comparison is measured against). A tag
    that exists on Docker Hub but in no document is a tag nobody will find.
    """
    assert "mcfaddja/nl2sql-agent:v1" in root_readme or re.search(
        r"\|\s*`v1`\s*\|", root_readme.split("Pulling the agent image")[1].split("##")[0]
    ), "README does not document the published v1 agent tag"


# ---------------------------------------------------------------------------
# The test counts the README quotes
# ---------------------------------------------------------------------------


def _collected(*args: str) -> int:
    """How many tests pytest selects for the given arguments.

    --collect-only, so nothing is executed and this cannot recurse.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *args],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=300,
    )
    # "389 tests collected", or "49/389 tests collected (340 deselected)"
    match = re.search(r"(\d+)(?:/\d+)? tests? collected", result.stdout)
    assert match, f"could not read a count from:\n{result.stdout[-800:]}"
    return int(match.group(1))


def test_the_readme_quotes_the_real_test_counts(root_readme: str):
    """These numbers went stale twice before this test existed. They are the
    first thing a contributor checks a run against, so a wrong one reads as a
    broken checkout.
    """
    total = _collected("--run-docker")
    docker_only = _collected("--run-docker", "-m", "docker")
    offline = total - docker_only

    quoted_offline = int(re.search(r"pytest\s+#\s*(\d+) tests", root_readme).group(1))
    quoted_total = int(re.search(r"pytest --run-docker\s+#\s*all (\d+)", root_readme).group(1))
    quoted_docker = int(re.search(r"The (\d+) tests behind `--run-docker`", root_readme).group(1))

    assert quoted_offline == offline, f"README says {quoted_offline} offline tests, there are {offline}"
    assert quoted_total == total, f"README says {quoted_total} total, there are {total}"
    assert quoted_docker == docker_only, f"README says {quoted_docker} docker tests, there are {docker_only}"


def test_the_quoted_counts_are_internally_consistent(root_readme: str):
    offline = int(re.search(r"pytest\s+#\s*(\d+) tests", root_readme).group(1))
    total = int(re.search(r"pytest --run-docker\s+#\s*all (\d+)", root_readme).group(1))
    docker_only = int(re.search(r"The (\d+) tests behind `--run-docker`", root_readme).group(1))
    assert offline + docker_only == total


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("doc", DOCS)
def test_relative_links_point_at_files_that_are_actually_committed(doc: str):
    """A link can resolve on the author's machine and 404 in a fresh clone:
    the target exists locally but is untracked or ignored.
    """
    text = (REPO_ROOT / doc).read_text()
    base = (REPO_ROOT / doc).parent
    missing = []
    for link in re.findall(r"\]\(([^)]+)\)", text):
        if link.startswith(("http://", "https://", "mailto:", "#")):
            continue
        target = (base / link.split("#")[0]).resolve()
        if not _is_tracked(target):
            missing.append(link)
    assert not missing, f"{doc} links to files not committed to the repo: {missing}"


def _is_tracked(path: Path) -> bool:
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(path)],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    return result.returncode == 0


# ---------------------------------------------------------------------------
# The test suite documents itself
# ---------------------------------------------------------------------------


def test_the_docker_opt_in_flag_is_documented_wherever_tests_are():
    """Roughly a seventh of the suite is skipped without --run-docker. A
    contributor who does not know that will read a green run as full coverage.
    """
    docs_mentioning_pytest = [
        doc for doc in DOCS if "pytest" in (REPO_ROOT / doc).read_text()
    ]
    assert docs_mentioning_pytest, "no document explains how to run the test suite"
    for doc in docs_mentioning_pytest:
        assert "--run-docker" in (REPO_ROOT / doc).read_text(), (
            f"{doc} explains how to run the tests but not the --run-docker opt-in, "
            "so the docker-marked tests would silently be skipped"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parser_from(parse_args) -> argparse.ArgumentParser:
    """Recover the configured parser by letting parse_args build it and
    intercepting the instance, so the test never duplicates the flag list.
    """
    captured: list[argparse.ArgumentParser] = []
    original_parse = argparse.ArgumentParser.parse_args

    def spy(self, *args, **kwargs):
        captured.append(self)
        return original_parse(self, *args, **kwargs)

    argparse.ArgumentParser.parse_args = spy
    try:
        parse_args([])
    finally:
        argparse.ArgumentParser.parse_args = original_parse
    return captured[0]


def _table_row(text: str, name: str) -> str:
    for line in text.splitlines():
        if line.startswith("|") and f"`{name}`" in line:
            return line
    raise AssertionError(f"{name} has no row in the README config table")


# ---------------------------------------------------------------------------
# launch.sh
# ---------------------------------------------------------------------------


def test_every_launch_flag_is_documented(launch_sh: str, root_readme: str, agent_usage: str):
    """A flag nobody has read about is a flag nobody uses. The parser is the
    source of truth, so the docs are checked against it rather than the reverse.
    """
    flags = set(re.findall(r"^\s+(--[a-z-]+)\)", launch_sh, re.MULTILINE))
    assert flags, "no flags found in launch.sh -- the pattern needs updating"
    documented = root_readme + agent_usage
    for flag in flags - {"--help"}:
        assert flag in documented, f"{flag} is not mentioned in README.md or agent/USAGE.md"


def test_the_two_scripts_are_both_documented_with_when_to_use_each(root_readme: str):
    """They look interchangeable and are not: one pulls images, the other checks
    the databases are populated. Someone who reaches for the wrong one either
    waits minutes for nothing or misses the problem they came to find.
    """
    assert "./setup.sh" in root_readme
    assert "./launch.sh" in root_readme
    assert "First run" in root_readme


def test_the_quick_start_is_still_two_commands(root_readme: str):
    """The contract the scripts exist to keep: run one, then ask a question."""
    assert 'docker compose run --rm agent "How many stores are there?"' in root_readme


def test_launch_does_not_pull_images(launch_sh: str):
    """Documented as the fast path, so it has to stay fast. A `docker pull` here
    would make every start a download.
    """
    assert "docker pull" not in launch_sh


# ---------------------------------------------------------------------------
# Coverage measures what is actually here
# ---------------------------------------------------------------------------


def _coverage_config() -> "configparser.ConfigParser":
    import configparser

    parser = configparser.ConfigParser()
    parser.read(REPO_ROOT / ".coveragerc")
    return parser


def test_coverage_is_configured_to_follow_scripts_into_their_own_process():
    """Several things here are tested the way they are used -- as scripts,
    with `subprocess.run`. Without this, coverage reports 0% for files with
    nine tests on them, which is worse than no number: it sends someone off
    to write tests that already exist.
    """
    assert _coverage_config().getboolean("run", "parallel") is True


def test_the_documented_coverage_command_turns_subprocess_measurement_on(root_readme: str):
    """The configuration alone does nothing -- the hook only fires when
    `COVERAGE_PROCESS_START` names the file. A documented command without it
    quietly measures less than it claims to.
    """
    block = root_readme.split("### Coverage")[1].split("```")[1]
    assert "COVERAGE_PROCESS_START=$PWD/.coveragerc" in block
    # Absolute, because a subprocess with a different working directory
    # would otherwise scatter its data files through the tree.
    assert "COVERAGE_FILE=$PWD/.coverage" in block
    assert "coverage combine" in root_readme.split("### Coverage")[1]


def test_every_directory_that_holds_code_is_measured():
    """The guard against the failure this section exists for: a top-level
    directory full of Python that no coverage run ever looks at. It is not
    hypothetical -- four of the eight here were outside the reported set
    until they were added, and two of them had no tests at all.
    """
    include = _coverage_config().get("report", "include").split()
    measured = {pattern.split("/")[0] for pattern in include}

    holds_code = set()
    for path in REPO_ROOT.rglob("*.py"):
        relative = path.relative_to(REPO_ROOT)
        top = relative.parts[0]
        if top in {"tests", ".venv", ".git"} or "__pycache__" in relative.parts:
            continue
        holds_code.add(top)

    missing = sorted(holds_code - measured)
    assert missing == [], f"these hold Python that no coverage run measures: {missing}"


def test_the_coverage_data_files_cannot_be_committed_by_accident():
    """A subprocess-measuring run writes one data file per process. They are
    noise, and a `git add -A` after a coverage run would sweep them in.
    """
    ignored = (REPO_ROOT / ".gitignore").read_text()
    assert ".coverage.*" in ignored


def test_the_readme_quotes_the_real_number_of_database_backed_rag_tests(root_readme: str):
    """It said 148 for a while, having been written when that was true. The
    three headline counts above are pinned; this one was not, and drifted.
    """
    quoted = int(re.search(r"The (\d+) database-backed tests", root_readme).group(1))
    assert quoted == _collected("--run-docker", "-m", "docker", "tests/rag")
