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
import configparser
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
AGENT_DIR = REPO_ROOT / "agent"

DOCS = (
    "README.md",
    "agent/README.md",
    "agent/USAGE.md",
    "agent/API.md",
    "data_gen/README.md",
    "rag/README.md",
    "gui/README.md",
    "models/README.md",
    "console/README.md",
)


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
    config table is how anyone finds out.

    Every reader config.py has, not two of them: this once matched only
    `_env_bool` and `_env_int`, and so checked 32 of the 60 settings --
    `OLLAMA_MODEL`, `DATABASE_URL` and every model route among the 28 it
    never looked for.
    """
    config_py = (AGENT_DIR / "nl2sql_agent" / "config.py").read_text()
    env_vars = set(re.findall(r'(?:_env(?:_str|_bool|_int|_float|_tuple)?|os\.getenv)\(\s*"([A-Z_]+)"', config_py))
    # Any call handed an upper-case name: a reader added under a new name is
    # caught here rather than silently left out of the check.
    named = set(re.findall(r'\w\(\s*"([A-Z][A-Z0-9_]+)"', config_py))
    assert env_vars and env_vars == named, f"config.py reads {sorted(named - env_vars)} through a reader this regex misses"
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
        ("MODEL_MAX_LOADED", settings.model_max_loaded),
        ("MODEL_NUM_CTX", settings.model_num_ctx),
        ("OLLAMA_NUM_PREDICT", settings.num_predict),
        ("OLLAMA_TIMEOUT", settings.ollama_timeout),
    ):
        row = _table_row(agent_readme, name)
        assert str(value) in row, f"README says {name} defaults to something other than {value}: {row!r}"

    for name, value in (
        ("EMBED_MODEL", settings.embed_model),
        ("OLLAMA_MODEL", settings.ollama_model),
        ("OLLAMA_BASE_URL", settings.ollama_base_url),
        ("EMBED_BASE_URL", settings.embed_base_url),
        ("OLLAMA_KEEP_ALIVE", settings.ollama_keep_alive),
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
    #: FastAPI's own OAuth2 redirect helper. Plumbing for the docs page, not
    #: a route a client calls, and nothing here serves OAuth2 anyway.
    internal = {"/docs/oauth2-redirect"}
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path or path in internal:
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
    for var in ("POSTGRES_IMAGE", "AGENT_IMAGE", "VECTOR_IMAGE", "GUI_IMAGE", "CONSOLE_GUI_IMAGE", "MLFLOW_IMAGE", "MLFLOW_DB_IMAGE"):
        image = re.search(rf'^{var}="([^"]+)"', setup_sh, re.MULTILINE).group(1)
        tag = re.search(rf'^{var.replace("_IMAGE", "_TAG")}="([^"]+)"', setup_sh, re.MULTILINE).group(1)
        assert f"{image}:{tag}" in root_readme, f"README never shows {image}:{tag}"


def test_the_pre_rag_agent_tag_is_documented(root_readme: str):
    """v1 is published alongside v2 so the schema-only agent stays reachable
    (it is what the 107.5% / 21.5% comparison is measured against). A tag
    that exists on Docker Hub but in no document is a tag nobody will find.
    """
    assert "mcfaddja/nl2sql-agent:v1" in root_readme or re.search(
        r"\|\s*`v1`\s*\|", root_readme.split("### Pulling the images")[1].split("\n## ")[0]
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
    total = _collected("--run-docker", "--run-node", "--run-java")
    docker_only = _collected("--run-docker", "-m", "docker")
    node_only = _collected("--run-node", "-m", "node")
    java_only = _collected("--run-java", "-m", "java")
    offline = total - docker_only - node_only - java_only

    quoted = _quoted_counts(root_readme)

    assert quoted["offline"] == offline, f"README says {quoted['offline']} offline tests, there are {offline}"
    assert quoted["total"] == total, f"README says {quoted['total']} total, there are {total}"
    assert quoted["docker"] == docker_only, f"README says {quoted['docker']} docker tests, there are {docker_only}"
    assert quoted["node"] == node_only, f"README says {quoted['node']} node tests, there are {node_only}"
    assert quoted["java"] == java_only, f"README says {quoted['java']} java tests, there are {java_only}"


def _quoted_counts(root_readme: str) -> dict[str, int]:
    return {
        "offline": int(re.search(r"pytest\s+#\s*(\d+) tests", root_readme).group(1)),
        "total": int(re.search(r"--run-java\s+#\s*all (\d+)", root_readme).group(1)),
        "docker": int(re.search(r"The (\d+) tests behind `--run-docker`", root_readme).group(1)),
        "node": int(re.search(r"The (\d+) behind `--run-node`", root_readme).group(1)),
        "java": int(re.search(r"The (\d+) behind `--run-java`", root_readme).group(1)),
    }


def test_the_quoted_counts_are_internally_consistent(root_readme: str):
    quoted = _quoted_counts(root_readme)
    assert (quoted["offline"] + quoted["docker"] + quoted["node"] + quoted["java"]
            == quoted["total"])


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
# The SQL console's surface
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def console_readme() -> str:
    return (REPO_ROOT / "console" / "README.md").read_text()


def _rows(doc: str, name: str) -> list[str]:
    return [line for line in doc.splitlines() if line.startswith("|") and f"`{name}`" in line]


def test_every_console_setting_is_documented(console_readme: str):
    """Its own `CONSOLE_*`, and the agent settings it runs under -- the ones a
    person has to know are shared before changing one on the host."""
    from nl2sql_agent.console.settings import AGENT_SETTINGS

    source = (AGENT_DIR / "nl2sql_agent" / "console" / "settings.py").read_text()
    names = set(re.findall(r'_env(?:_str|_bool|_int|_float|_tuple)?\(\s*"([A-Z_]+)"', source))
    assert names, "no environment variables found in console/settings.py -- the regex needs updating"
    for name in sorted(names | set(AGENT_SETTINGS)):
        assert _rows(console_readme, name), f"{name} is read by the console but has no row in console/README.md"


def test_the_documented_console_defaults_are_the_real_defaults(console_readme: str):
    from nl2sql_agent.config import Settings
    from nl2sql_agent.console.settings import ConsoleSettings

    console, agent = ConsoleSettings(), Settings()
    for name, value in (
        ("CONSOLE_HOST", console.host),
        ("CONSOLE_PORT", console.port),
        ("CONSOLE_TLS_CERT_FILE", console.tls_cert_file),
        ("CONSOLE_TLS_KEY_FILE", console.tls_key_file),
        ("CONSOLE_MAX_ROWS", console.max_rows),
        ("CONSOLE_LOG_LEVEL", console.log_level),
        ("DB_SCHEMA", agent.db_schema),
        ("STATEMENT_TIMEOUT_MS", agent.statement_timeout_ms),
        ("MAX_PLAN_COST", f"{agent.max_plan_cost:.0f}"),
        ("MAX_ROWS", agent.max_rows),
        ("SAMPLE_ROWS", agent.sample_rows),
    ):
        rows = _rows(console_readme, name)
        assert any(f"`{value}`" in row for row in rows), (
            f"console/README.md never says {name} defaults to {value}: {rows!r}"
        )


def test_every_setting_the_console_proxy_reads_is_documented(console_readme: str):
    sources = "".join(
        (REPO_ROOT / "console" / name).read_text()
        for name in ("nginx.conf.template", "10-nl2sql-console-config.envsh")
    )
    # Worked out by the start-up script rather than set by anyone.
    names = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)", sources)) - {
        "CONSOLE_AUTH_HEADER", "NGINX_CONSOLE_UPSTREAM_TLS_CONF"
    }
    for name in sorted(names):
        assert _rows(console_readme, name), f"the console's proxy reads {name}, which console/README.md never lists"
    assert "`CONSOLE_BIND_ADDRESS`" in console_readme


def test_every_route_the_console_serves_is_documented(console_readme: str):
    from nl2sql_agent.config import Settings
    from nl2sql_agent.console.app import create_app
    from nl2sql_agent.console.settings import ConsoleSettings

    app = create_app(settings=Settings(), console_settings=ConsoleSettings(), inspector_factory=lambda: None)
    internal = {"/docs/oauth2-redirect"}
    for route in app.routes:
        path = getattr(route, "path", "")
        if path and path not in internal:
            assert path in console_readme, f"the console serves {path}, which console/README.md never mentions"


def test_every_error_code_the_console_can_return_is_documented(console_readme: str):
    source = (AGENT_DIR / "nl2sql_agent" / "console" / "app.py").read_text()
    codes = set(re.findall(r'ApiHTTPError\(\s*\n?\s*[\w.]+,\s*\n?\s*"([a-z_]+)"', source))
    codes |= set(re.findall(r'_error_response\(\s*\n?\s*[\w.]+,\s*\n?\s*"([a-z_]+)"', source))
    assert {"unauthorized", "unknown_table", "database_unavailable", "invalid_request"} <= codes, (
        "the error codes were not all found in console/app.py -- the regexes need updating"
    )
    for code in sorted(codes | {"not_found"}):
        assert f"`{code}`" in console_readme, f"the console returns {code!r}, which console/README.md never lists"


def test_every_console_flag_is_documented(console_readme: str):
    from nl2sql_agent.console import server

    source = (AGENT_DIR / "nl2sql_agent" / "console" / "server.py").read_text()
    flags = set(re.findall(r'p\.add_argument\(\s*"(--[a-z-]+)"', source))
    assert flags, "no flags found in console/server.py -- the pattern needs updating"
    for flag in sorted(flags):
        assert f"`{flag}" in console_readme, f"{flag} is not documented in console/README.md"
    assert server.parse_args(["--no-tls"]).tls is False, "--no-tls is documented, so it must parse"


def test_the_console_is_documented_where_someone_would_look(root_readme: str, agent_usage: str):
    for doc in (root_readme, agent_usage):
        assert "./launch.sh --console" in doc
    assert "./start.sh --console" in root_readme
    assert "[`console/README.md`](console/README.md)" in root_readme


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



def test_the_three_scripts_are_documented_with_when_to_use_each(root_readme: str):
    """They look interchangeable and are not: one pulls images, one checks the
    databases are populated, one runs both and opens a browser. Someone who
    reaches for the wrong one either waits minutes for nothing or misses the
    problem they came to find.
    """
    assert "./start.sh" in root_readme
    assert "./setup.sh" in root_readme
    assert "./launch.sh" in root_readme
    assert "First run" in root_readme


@pytest.fixture(scope="module")
def start_sh() -> str:
    return (REPO_ROOT / "start.sh").read_text()


def test_every_start_flag_is_documented(start_sh: str, root_readme: str):
    """Same rule as launch.sh: the parser is the source of truth, and a flag
    nobody has read about is a flag nobody uses.
    """
    flags = set(re.findall(r"^\s+(?:-\w\|)?(--[a-z-]+)\)", start_sh, re.MULTILINE))
    assert flags, "no flags found in start.sh -- the pattern needs updating"
    for flag in flags - {"--help"}:
        assert flag in root_readme, f"{flag} is not mentioned in README.md"


def test_the_front_door_is_what_the_readme_opens_with(root_readme: str):
    """It is the first thing someone new runs, so it is the first thing the
    document should say. A quick start that begins with the three-step
    version is a quick start nobody finishes.
    """
    quick_start = root_readme.split("## Quick start")[1].split("\n## ")[0]
    assert "./start.sh" in quick_start
    assert quick_start.index("./start.sh") < quick_start.index("./setup.sh")


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


def _coverage_config() -> configparser.ConfigParser:
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


def test_no_test_file_defines_the_same_test_name_twice():
    """Python keeps the last definition and discards the first silently, so a
    duplicated name is a test that exists, reads correctly, is counted by the
    collector -- and never runs.

    Not hypothetical: test_schema_retrieval.py had two different tests called
    `test_two_tables_with_no_join_path_between_them_need_no_bridge`, and the
    one covering `close_and_cap` had been dead for as long as both existed.
    Nothing reported it, because a shadowed test does not fail; it is absent.
    """
    import ast

    shadowed = []
    for path in sorted((REPO_ROOT / "tests").rglob("test_*.py")):
        seen: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test"):
                continue
            if node.name in seen:
                shadowed.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} {node.name}")
            seen.add(node.name)

    assert shadowed == [], "these tests are shadowed and never run: " + ", ".join(shadowed)


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


def test_the_rag_readme_quotes_the_real_number_of_rag_tests():
    """The count under its Tests heading said 265 while the suite grew to 281,
    because nothing read it -- the same drift as the one above, a page over.
    """
    text = (REPO_ROOT / "rag" / "README.md").read_text()
    quoted = int(re.search(r"pytest tests/rag --run-docker\n```\n\n(\d+) tests:", text).group(1))
    assert quoted == _collected("--run-docker", "tests/rag")
