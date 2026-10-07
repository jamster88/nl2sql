"""Documentation kept honest by the code it documents.

The README, the documents in docs/ and the components' own READMEs describe
flags, environment variables and published image tags. Every one of those is a fact about the code or the compose file
that can drift silently: a new setting is added, the table that lists it is
not, and the docs quietly become wrong. These tests pin the directions that
actually rot -- code gaining something the docs never mention, and docs
pointing at files a fresh clone does not have.
"""

from __future__ import annotations

import argparse
import configparser
import functools
import re
import subprocess
import sys
from pathlib import Path

import pytest
from tests.route_table import flattened

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
AGENT_DIR = REPO_ROOT / "agent"

#: The README is a front page -- a quick start, what is new, and an index --
#: and the rest of what it used to hold is a document a topic in docs/.
TOPICS = tuple(sorted(path.name for path in (REPO_ROOT / "docs").glob("*.md")))
DOCS = (
    "README.md",
    *(f"docs/{name}" for name in TOPICS),
    "agent/README.md",
    "agent/USAGE.md",
    "agent/API.md",
    "data_gen/README.md",
    "rag/README.md",
    "gui/README.md",
    "models/README.md",
    "console/README.md",
)


@functools.cache
def _doc(name: str) -> str:
    """One of the documents in docs/, by its file name."""
    return (REPO_ROOT / "docs" / name).read_text()


@pytest.fixture(scope="module")
def root_readme() -> str:
    return (REPO_ROOT / "README.md").read_text()


@pytest.fixture(scope="module")
def documentation(root_readme: str) -> str:
    """The README and every document in docs/: the surface a flag or a tag
    has to be mentioned on somewhere, as the one README was before."""
    return root_readme + "".join(_doc(name) for name in TOPICS)


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
    env_vars = set(re.findall(r'(?:_env(?:_str|_bool|_int|_float|_tuple|_url)?|_secret|os\.getenv)\(\s*"([A-Z_]+)"', config_py))
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
    names = set(re.findall(r'(?:_env(?:_str|_bool|_int|_float|_tuple|_url)?|_secret)\(\s*"([A-Z_]+)"', source))
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
    for route in flattened(app.routes):
        path = getattr(route, "path", "")
        if not path or path in internal:
            continue
        assert path in documented, f"the server serves {path}, which API.md never mentions"


def test_every_error_code_the_server_can_return_is_documented(agent_api_doc: str):
    """A client branches on these. One that is returned but undocumented is
    one nobody handles.
    """
    source = "".join((AGENT_DIR / "nl2sql_agent" / "api" / name).read_text() for name in ("app.py", "routes.py"))
    codes = set(re.findall(r'ApiHTTPError\(\s*\n?\s*[\w.]+,\s*\n?\s*"([a-z_]+)"', source))
    codes |= set(re.findall(r'_error_response\(\s*\n?\s*\d+,\s*\n?\s*"([a-z_]+)"', source))
    assert {"not_found", "queue_full", "job_running", "principal_not_allowed"} <= codes, (
        "no error codes found in app.py and routes.py -- the regex needs updating"
    )
    for code in sorted(codes):
        assert f"`{code}`" in agent_api_doc, f"the server returns {code!r}, which API.md never lists"


def test_the_api_flag_is_documented_where_someone_would_look(agent_usage: str):
    """It opens a port, which is the kind of thing that should not be
    discoverable only by reading the script.
    """
    for doc in (_doc("rest_api.md"), _doc("stack.md"), agent_usage):
        assert "./launch.sh --api" in doc


def test_the_switch_that_refuses_the_development_certificate_is_documented(agent_api_doc: str):
    """The whole point of shipping a dummy certificate is that it can be
    taken away. Someone deploying this has to be able to find out how.
    """
    assert "API_TLS_ALLOW_SELF_SIGNED=false" in agent_api_doc
    assert "API_TLS_ALLOW_SELF_SIGNED=false" in _doc("rest_api.md")


def test_the_documented_version_is_the_packaged_one(agent_readme: str, root_readme: str):
    from nl2sql_agent import __version__

    major, minor = __version__.split(".")[:2]
    assert f"(v{major}" in agent_readme.splitlines()[0], agent_readme.splitlines()[0]
    assert f"nl2sql-agent:v{major}" in _doc("images.md")
    # The front page says what is new in the release it describes.
    assert f"## What's new in {major}.{minor}" in root_readme


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


def test_every_image_tag_setup_defaults_to_is_documented(setup_sh: str, documentation: str):
    """setup.sh pins a tag per image; if the tag tables in docs/ do not list
    it, the default nobody passes is also the one nobody has read about.
    """
    for var in (
        "POSTGRES_IMAGE", "AGENT_IMAGE", "VECTOR_IMAGE", "REVIEW_IMAGE", "PROXY_IMAGE", "MLFLOW_IMAGE",
        "MLFLOW_DB_IMAGE", "LDAP_IMAGE", "AUTH_IMAGE",
    ):
        image = re.search(rf'^{var}="([^"]+)"', setup_sh, re.MULTILINE).group(1)
        tag = re.search(rf'^{var.replace("_IMAGE", "_TAG")}="([^"]+)"', setup_sh, re.MULTILINE).group(1)
        assert f"{image}:{tag}" in documentation, f"no document shows {image}:{tag}"


def test_the_pre_rag_agent_tag_is_documented():
    """v1 is published alongside v2 so the schema-only agent stays reachable
    (it is what the 107.5% / 21.5% comparison is measured against). A tag
    that exists on Docker Hub but in no document is a tag nobody will find.
    """
    images = _doc("images.md")
    assert "mcfaddja/nl2sql-agent:v1" in images or re.search(r"\|\s*`v1`\s*\|", images), (
        "docs/images.md does not document the published v1 agent tag"
    )


# ---------------------------------------------------------------------------
# The test counts docs/tests.md quotes
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


def test_the_tests_document_quotes_the_real_test_counts():
    """These numbers went stale twice before this test existed. They are the
    first thing a contributor checks a run against, so a wrong one reads as a
    broken checkout.
    """
    total = _collected("--run-docker", "--run-node", "--run-java", "--run-acceptance")
    docker_only = _collected("--run-docker", "-m", "docker")
    node_only = _collected("--run-node", "-m", "node")
    java_only = _collected("--run-java", "-m", "java")
    acceptance_only = _collected("--run-acceptance", "-m", "acceptance")
    offline = total - docker_only - node_only - java_only - acceptance_only

    quoted = _quoted_counts()

    assert quoted["offline"] == offline, f"docs/tests.md says {quoted['offline']} offline tests, there are {offline}"
    assert quoted["total"] == total, f"docs/tests.md says {quoted['total']} total, there are {total}"
    assert quoted["docker"] == docker_only, f"docs/tests.md says {quoted['docker']} docker tests, there are {docker_only}"
    assert quoted["node"] == node_only, f"docs/tests.md says {quoted['node']} node tests, there are {node_only}"
    assert quoted["java"] == java_only, f"docs/tests.md says {quoted['java']} java tests, there are {java_only}"
    assert quoted["acceptance"] == acceptance_only, (
        f"docs/tests.md says {quoted['acceptance']} acceptance tests, there are {acceptance_only}"
    )


def _quoted_counts() -> dict[str, int]:
    tests_doc = _doc("tests.md")
    return {
        "offline": int(re.search(r"pytest\s+#\s*(\d+) tests", tests_doc).group(1)),
        "total": int(re.search(r"--run-acceptance\s+#\s*all (\d+)", tests_doc).group(1)),
        "docker": int(re.search(r"The (\d+) tests behind `--run-docker`", tests_doc).group(1)),
        "node": int(re.search(r"The (\d+) behind `--run-node`", tests_doc).group(1)),
        "java": int(re.search(r"The (\d+) behind `--run-java`", tests_doc).group(1)),
        "acceptance": int(re.search(r"The (\d+) behind `--run-acceptance`", tests_doc).group(1)),
    }


def test_the_quoted_counts_are_internally_consistent():
    quoted = _quoted_counts()
    assert (quoted["offline"] + quoted["docker"] + quoted["node"] + quoted["java"]
            + quoted["acceptance"] == quoted["total"])


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


def test_every_document_in_docs_is_indexed_by_the_readme(root_readme: str):
    """The README is the way in; a document it does not list is one nobody
    finds without browsing the folder."""
    index = root_readme.split("## Documentation", 1)[1]
    unlisted = [name for name in TOPICS if f"](docs/{name})" not in index]
    assert unlisted == [], f"README.md's index never links {unlisted}"


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
# The usage guide and the quick start
# ---------------------------------------------------------------------------
#
# Both are written for someone who has never seen the scripts, so a flag that
# does not exist, a port nothing publishes or a section link that goes nowhere
# is the one mistake they cannot recover from on their own.

GUIDES = ("docs/USAGE_GUIDE.md", "docs/QUICKSTART.md")
SCRIPTS = ("start.sh", "launch.sh", "setup.sh")


def _guide(name: str) -> str:
    return (REPO_ROOT / name).read_text()


def _script_flags(script: str) -> set[str]:
    """Every option a script's parser takes, short and long."""
    source = (REPO_ROOT / script).read_text()
    flags = set()
    for short, long in re.findall(r"^\s+(?:(-\w)\|)?(--[a-z-]+)\)", source, re.MULTILINE):
        flags.update(flag for flag in (short, long) if flag)
    assert "--help" in flags, f"no flags found in {script} -- the pattern needs updating"
    return flags


def _mentions(text: str, flag: str) -> bool:
    """`--tag` is not mentioned by `--agent-tag`."""
    return re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", text) is not None


@pytest.mark.parametrize("script", SCRIPTS)
def test_the_usage_guide_documents_every_script_flag(script: str):
    guide = _guide("docs/USAGE_GUIDE.md")
    for flag in sorted(_script_flags(script)):
        assert _mentions(guide, flag), f"{script} takes {flag}, which USAGE_GUIDE.md never mentions"


def test_the_usage_guide_documents_every_agent_flag():
    from nl2sql_agent.__main__ import parse_args

    guide = _guide("docs/USAGE_GUIDE.md")
    for action in _parser_from(parse_args)._actions:
        for flag in action.option_strings:
            if flag not in ("-h", "--help"):
                assert _mentions(guide, flag), f"the agent takes {flag}, which USAGE_GUIDE.md never mentions"


@pytest.mark.parametrize("guide", GUIDES)
def test_every_script_command_in_the_guides_uses_flags_the_script_takes(guide: str):
    text = _guide(guide)
    commands = re.findall(r"\./(start|launch|setup)\.sh((?:[ \t]+[^\s`#&|]+)*)", text)
    assert len(commands) > 3, f"no script commands found in {guide} -- the pattern needs updating"
    for script, arguments in commands:
        taken = _script_flags(f"{script}.sh")
        for flag in (word for word in arguments.split() if word.startswith("-")):
            assert flag in taken, f"{guide} runs ./{script}.sh {flag.strip()}, which it does not take"


@pytest.mark.parametrize("guide", GUIDES)
def test_every_agent_command_in_the_guides_uses_flags_the_agent_takes(guide: str):
    from nl2sql_agent.__main__ import parse_args

    taken = {flag for action in _parser_from(parse_args)._actions for flag in action.option_strings}
    commands = re.findall(r"docker compose run --rm(?:\s+-T)?\s+agent\b([^\"#|`\n]*)", _guide(guide))
    assert commands, f"no agent commands found in {guide} -- the pattern needs updating"
    for arguments in commands:
        for flag in (word for word in arguments.split() if word.startswith("-")):
            assert flag in taken, f"{guide} passes the agent {flag}, which it does not take"


def _published_ports() -> dict[str, str]:
    """Each port setting compose reads, and the host port it defaults to."""
    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    return dict(re.findall(r"\$\{([A-Z_]*PORT):-(\d+)\}", compose))


@pytest.mark.parametrize("guide", GUIDES)
def test_every_local_address_in_the_guides_is_one_compose_publishes(guide: str):
    published = set(_published_ports().values()) | {"11434"}  # and Ollama's own
    addresses = set(re.findall(r"localhost:(\d+)", _guide(guide)))
    assert addresses, f"no local addresses found in {guide}"
    assert addresses <= published, f"{guide} points at ports nothing publishes: {sorted(addresses - published)}"


def test_the_usage_guide_names_every_setting_that_moves_a_port():
    guide = _guide("docs/USAGE_GUIDE.md")
    for setting, port in sorted(_published_ports().items()):
        assert f"`{setting}`" in guide, f"{setting} moves port {port}, and USAGE_GUIDE.md never says so"


def _heading_slugs(markdown: str) -> set[str]:
    """The anchors GitHub gives a document's headings, outside code blocks."""
    slugs, in_code = set(), False
    for line in markdown.splitlines():
        if line.startswith("```"):
            in_code = not in_code
        elif not in_code and (heading := re.match(r"#{1,6}\s+(.+)", line)):
            slugs.add(re.sub(r"[^\w\- ]", "", heading.group(1).strip().lower()).replace(" ", "-"))
    return slugs


@pytest.mark.parametrize("doc", DOCS)
def test_every_section_link_in_the_documents_lands_on_a_heading(doc: str):
    """The file-link check above stops at the `#`; these documents send
    readers to sections, in themselves and in each other, by the dozen --
    and since the README was split into docs/, across files that used to be
    one."""
    text = _guide(doc)
    links = re.findall(r"\]\(([^)#\s]*)#([^)\s]+)\)", text)
    if doc in GUIDES:
        assert links, f"no section links found in {doc}"
    for path, anchor in links:
        target = (REPO_ROOT / doc).parent / path if path else REPO_ROOT / doc
        assert anchor in _heading_slugs(target.read_text()), f"{doc} links to {path}#{anchor}, which has no such heading"


def test_the_readme_and_the_quick_start_point_on_to_the_guides(root_readme: str):
    assert "[`docs/QUICKSTART.md`](docs/QUICKSTART.md)" in root_readme
    assert "[`docs/USAGE_GUIDE.md`](docs/USAGE_GUIDE.md)" in root_readme
    assert "](USAGE_GUIDE.md" in _guide("docs/QUICKSTART.md")


# ---------------------------------------------------------------------------
# MLflow's two services
# ---------------------------------------------------------------------------


def _compose_service(name: str) -> str:
    """One service's block of docker-compose.yml, as written."""
    text = (REPO_ROOT / "docker-compose.yml").read_text()
    match = re.search(rf"^  {name}:\n(.*?)(?=^  [a-z]+:\n|^[a-z]+:)", text, re.MULTILINE | re.DOTALL)
    assert match, f"docker-compose.yml has no {name} service"
    return match.group(1)


#: Settings every service's block reads that no service does: the stack's
#: own name, in each container's (docs/USAGE_GUIDE.md documents it once).
STACK_WIDE = {"NL2SQL_INSTANCE"}


def _compose_defaults(text: str) -> dict[str, str]:
    """Each `${NAME:-default}` in `text`, as compose resolves it with nothing
    set: a default may name another setting -- `https://nl2sql-auth:${AUTH_PORT:-8446}`,
    so moving the auth service's port moves every page that proxies to it --
    and then the documented default is that one's, `https://nl2sql-auth:8446`.
    Only the outer names: an inner one is the other service's setting."""
    found: dict[str, str] = {}
    index = 0
    while (start := text.find("${", index)) != -1:
        match = re.match(r"\$\{([A-Z_][A-Z0-9_]*):-", text[start:])
        if not match:
            index = start + 2
            continue
        depth, cursor = 1, start + match.end()
        while depth:
            if text.startswith("${", cursor):
                depth, cursor = depth + 1, cursor + 2
            else:
                depth -= text[cursor] == "}"
                cursor += 1
        default = text[start + match.end():cursor - 1]
        while (inner := re.search(r"\$\{[A-Z_][A-Z0-9_]*:-([^${}]*)\}", default)):
            default = default[:inner.start()] + inner.group(1) + default[inner.end():]
        found.setdefault(match.group(1), default)
        index = cursor
    return found


def test_every_setting_the_mlflow_services_read_is_documented_with_its_default():
    """Image pins aside, which `setup.sh --mlflow` writes, every setting the
    three -- the store, the server and its front door -- take from `.env` has
    a row in docs/tracing.md's table -- with
    the default compose really falls back to, where that is one value."""
    tracing = _doc("tracing.md")
    block = _compose_service("mlflowdb") + _compose_service("mlflow") + _compose_service("mlflowproxy")
    settings = {
        name: default
        for name, default in _compose_defaults(block).items()
        if not name.endswith(("_IMAGE_NAME", "_IMAGE_TAG")) and name not in STACK_WIDE
    }
    assert "MLFLOW_PORT" in settings, "no settings found in the MLflow services -- the regex needs updating"
    for name, default in sorted(settings.items()):
        rows = _rows(tracing, name)
        assert rows, f"compose's MLflow services read {name}, which docs/tracing.md never lists"
        if "," not in default:
            assert any(f"`{default}`" in row for row in rows), f"docs/tracing.md never says {name} defaults to {default}"


def _documented_with_defaults(service: str, readme: str, document: str) -> None:
    """Every setting a compose service takes from `.env`, image pins aside,
    has a row in `document` -- with the default compose falls back to, where
    that is one value. An image pinned by digest (6.3, V6-35) is documented
    by name and tag: the digest is tools/pin_images.py's to keep, and a row
    quoting it would be one more place to move each time it does."""
    settings = {
        name: default
        for name, default in _compose_defaults(_compose_service(service)).items()
        if not name.endswith(("_IMAGE_NAME", "_IMAGE_TAG")) and name not in STACK_WIDE
    }
    assert settings, f"no settings found in {service} -- the regex needs updating"
    for name, default in sorted(settings.items()):
        rows = _rows(readme, name)
        assert rows, f"compose's {service} reads {name}, which {document} never lists"
        if "@sha256:" in default:
            image = default.split("@", 1)[0]
            assert any(f"`{image}`" in row and "digest" in row for row in rows), (
                f"{document} never says {name} is {image}, pinned by digest"
            )
        elif default and "," not in default:
            assert any(f"`{default}`" in row for row in rows), f"{document} never says {name} defaults to {default}"


def test_every_setting_the_runtime_stores_read_is_documented_with_their_default():
    """The four runtime stores, one server since 6.3 (V6-40)."""
    _documented_with_defaults("stores", _doc("snippets.md"), "docs/snippets.md")


@pytest.mark.parametrize(("service", "document"), [
    ("gui", "gui/README.md"), ("reviewgui", "review/README.md"), ("curategui", "curate/README.md"),
    ("directorygui", "auth/README.md"), ("apitest", "agent/API.md"),
])
def test_every_setting_a_page_reads_is_documented_with_its_default(service: str, document: str):
    """The console's page has its own test, with the rest of the console's
    surface; MLflow's front door is in docs/tracing.md's table."""
    _documented_with_defaults(service, (REPO_ROOT / document).read_text(), document)


# ---------------------------------------------------------------------------
# The SQL console's surface
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def console_readme() -> str:
    return (REPO_ROOT / "console" / "README.md").read_text()


def _rows(doc: str, name: str) -> list[str]:
    return [line for line in doc.splitlines() if line.startswith("|") and f"`{name}`" in line]


@pytest.mark.parametrize(("sources", "document"), [
    (("auth/nl2sql_auth/settings.py",), "auth/README.md"),
    (("ldap/nl2sql_ldap/settings.py", "ldap/nl2sql_ldap/replica.py"), "ldap/README.md"),
    (("common/nl2sql_ops/settings.py",), "common/README.md"),
    (("rag/ragproc/config.py", "rag/07_load_snippets.py"), "rag/README.md"),
])
def test_every_setting_these_services_read_has_a_row_in_their_readme(sources: tuple, document: str):
    """The rule the agent, the API, the console and the review service are
    held to, for the four whose settings nothing read: two of the RAG
    loaders' and all thirty-five of dbprep's had no row until it asked."""
    from tests.settings_names import _CALL

    names = {name for path in sources for _, name in _CALL.findall((REPO_ROOT / path).read_text())}
    assert len(names) > 3, "no settings found -- the pattern needs updating"
    readme = (REPO_ROOT / document).read_text()
    missing = sorted(name for name in names if not _rows(readme, name))
    assert missing == [], f"{document} has no row for {missing}"


def test_every_review_setting_is_documented():
    """The console's rule, for the service that can rewrite the golden set and
    the snippets: two of its settings had no row until this test asked."""
    source = (REPO_ROOT / "review" / "nl2sql_review" / "settings.py").read_text()
    names = set(re.findall(r'(?:_env(?:_str|_bool|_int|_float|_tuple|_url)?|_secret)\(\s*"([A-Z_]+)"', source))
    assert len(names) > 20, "the review service's settings were not found -- the regex needs updating"
    readme = (REPO_ROOT / "review" / "README.md").read_text()
    for name in sorted(names):
        assert _rows(readme, name), f"{name} is read by the review service but has no row in review/README.md"


def test_every_console_setting_is_documented(console_readme: str):
    """Its own `CONSOLE_*`, and the agent settings it runs under -- the ones a
    person has to know are shared before changing one on the host."""
    from nl2sql_agent.console.settings import AGENT_SETTINGS

    source = (AGENT_DIR / "nl2sql_agent" / "console" / "settings.py").read_text()
    names = set(re.findall(r'(?:_env(?:_str|_bool|_int|_float|_tuple|_url)?|_secret)\(\s*"([A-Z_]+)"', source))
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


def test_every_setting_the_console_page_reads_is_documented(console_readme: str):
    """What its compose service takes from `.env`: the page is the proxy
    image's since 6.3 (V6-37), whose own settings proxy/README.md lists."""
    _documented_with_defaults("consolegui", console_readme, "console/README.md")
    assert "`CONSOLE_BIND_ADDRESS`" in console_readme


#: Worked out by the proxy's start-up script rather than set by anyone.
PROXY_COMPUTED = {"PAGE_ROOT", "PROXY_LISTEN_TLS", "UPSTREAM_AUTH_HEADER"}


def test_every_setting_the_proxy_image_reads_is_documented():
    """One image serves every page and MLflow's front door (V6-37); what it
    reads is its own README's to list, once, rather than each page's."""
    proxy = REPO_ROOT / "proxy"
    sources = "".join(path.read_text() for path in [
        proxy / "10-nl2sql-proxy.envsh", *sorted(proxy.glob("**/*.template")),
    ])
    names = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)", sources)) - PROXY_COMPUTED
    assert {"NL2SQL_PAGE", "UPSTREAM", "PROXY_PORT"} <= names, "the regex needs updating"
    readme = (proxy / "README.md").read_text()
    for name in sorted(names):
        assert _rows(readme, name), f"the proxy reads {name}, which proxy/README.md never lists"


def test_every_route_the_console_serves_is_documented(console_readme: str):
    from nl2sql_agent.config import Settings
    from nl2sql_agent.console.app import create_app
    from nl2sql_agent.console.settings import ConsoleSettings

    app = create_app(settings=Settings(), console_settings=ConsoleSettings(), inspector_factory=lambda: None)
    internal = {"/docs/oauth2-redirect"}
    for route in flattened(app.routes):
        path = getattr(route, "path", "")
        if path and path not in internal:
            assert path in console_readme, f"the console serves {path}, which console/README.md never mentions"


def _identity_codes() -> set[str]:
    """What the shared guard answers a caller it cannot let in with."""
    identity = REPO_ROOT / "common" / "nl2sql_identity"
    codes = set(re.findall(r'IdentityError\(\s*\n?\s*[\w.]+,\s*\n?\s*"([a-z_]+)"', (identity / "guard.py").read_text()))
    codes |= set(re.findall(r'TokenError\(\s*"([a-z_]+)"', (identity / "tokens.py").read_text()))
    return codes


def test_every_error_code_the_console_can_return_is_documented(console_readme: str):
    source = "".join((AGENT_DIR / "nl2sql_agent" / "console" / name).read_text() for name in ("app.py", "routes.py"))
    codes = set(re.findall(r'ApiHTTPError\(\s*\n?\s*[\w.]+,\s*\n?\s*"([a-z_]+)"', source))
    codes |= set(re.findall(r'_error_response\(\s*\n?\s*[\w.]+,\s*\n?\s*"([a-z_]+)"', source))
    # Who may call is decided by the guard every service shares, and a
    # session it cannot read is refused in the session format's own words.
    codes |= _identity_codes()
    assert {"unauthorized", "sign_in_required", "forbidden", "expired", "unknown_table", "database_unavailable",
            "invalid_request"} <= codes, (
        "the error codes were not all found in console/app.py, routes.py and the guard -- the regexes need updating"
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
    for doc in (_doc("stack.md"), agent_usage):
        assert "./launch.sh --console" in doc
    for doc in (root_readme, _doc("stack.md")):
        assert "./start.sh --console" in doc
    assert "[`console/README.md`](../console/README.md)" in _doc("sql_console.md")


# ---------------------------------------------------------------------------
# launch.sh
# ---------------------------------------------------------------------------


def test_every_launch_flag_is_documented(launch_sh: str, documentation: str, agent_usage: str):
    """A flag nobody has read about is a flag nobody uses. The parser is the
    source of truth, so the docs are checked against it rather than the reverse.
    """
    flags = set(re.findall(r"^\s+(--[a-z-]+)\)", launch_sh, re.MULTILINE))
    assert flags, "no flags found in launch.sh -- the pattern needs updating"
    documented = documentation + agent_usage
    for flag in flags - {"--help"}:
        assert flag in documented, f"{flag} is not mentioned in README.md, docs/ or agent/USAGE.md"


def test_the_three_scripts_are_documented_with_when_to_use_each():
    """They look interchangeable and are not: one pulls images, one checks the
    databases are populated, one runs both and opens a browser. Someone who
    reaches for the wrong one either waits minutes for nothing or misses the
    problem they came to find.
    """
    stack = _doc("stack.md")
    assert "./start.sh" in stack
    assert "./setup.sh" in stack
    assert "./launch.sh" in stack
    assert "First run" in stack


@pytest.fixture(scope="module")
def start_sh() -> str:
    return (REPO_ROOT / "start.sh").read_text()


def test_every_start_flag_is_documented(start_sh: str):
    """Same rule as launch.sh: the parser is the source of truth, and a flag
    nobody has read about is a flag nobody uses. docs/stack.md is where the
    flags are listed, so that is where each has to be.
    """
    flags = set(re.findall(r"^\s+(?:-\w\|)?(--[a-z-]+)\)", start_sh, re.MULTILINE))
    assert flags, "no flags found in start.sh -- the pattern needs updating"
    for flag in flags - {"--help"}:
        assert flag in _doc("stack.md"), f"{flag} is not mentioned in docs/stack.md"


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


def test_the_documented_coverage_command_turns_subprocess_measurement_on():
    """The configuration alone does nothing -- the hook only fires when
    `COVERAGE_PROCESS_START` names the file. A documented command without it
    quietly measures less than it claims to.
    """
    coverage = _doc("tests.md").split("## Coverage")[1]
    block = coverage.split("```")[1]
    assert "COVERAGE_PROCESS_START=$PWD/.coveragerc" in block
    # Absolute, because a subprocess with a different working directory
    # would otherwise scatter its data files through the tree.
    assert "COVERAGE_FILE=$PWD/.coverage" in block
    assert "coverage combine" in coverage


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


def test_the_tests_document_quotes_the_real_number_of_database_backed_rag_tests():
    """It said 148 for a while, having been written when that was true. The
    three headline counts above are pinned; this one was not, and drifted.
    """
    quoted = int(re.search(r"The (\d+) database-backed tests", _doc("tests.md")).group(1))
    assert quoted == _collected("--run-docker", "-m", "docker", "tests/rag")


def test_the_rag_readme_quotes_the_real_number_of_rag_tests():
    """The count under its Tests heading said 265 while the suite grew to 281,
    because nothing read it -- the same drift as the one above, a page over.
    """
    text = (REPO_ROOT / "rag" / "README.md").read_text()
    quoted = int(re.search(r"pytest tests/rag --run-docker\n```\n\n(\d+) tests:", text).group(1))
    assert quoted == _collected("--run-docker", "tests/rag")
    behind = int(re.search(r"(\d+) of them need a database", text).group(1))
    assert behind == _collected("--run-docker", "-m", "docker", "tests/rag")


def test_a_nested_default_is_read_as_compose_resolves_it():
    text = "A: ${GUI_AUTH_UPSTREAM:-https://nl2sql-auth:${AUTH_PORT:-8446}}\nB: ${PLAIN:-x}\nC: $${NOT_ONE}\n"
    assert _compose_defaults(text) == {"GUI_AUTH_UPSTREAM": "https://nl2sql-auth:8446", "PLAIN": "x"}
