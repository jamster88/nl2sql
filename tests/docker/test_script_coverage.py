"""The shell scripts, checked for things no test exercises.

`setup.sh` and `launch.sh` are the two commands almost everyone runs, and
neither is Python, so nothing in the coverage report can see them. What they
are *for* is noticing trouble -- a container up but empty, a chat host that
moved, a role that gained a grant -- and every one of those notices is a
branch that can rot silently, because a warning nobody triggers looks exactly
like a warning that works.

So these tests read the scripts and assert structurally: every flag is
parsed, every flag is documented, every message the user could see is
asserted by some test, and the two-command contract still holds. They are
cheap, they need no Docker, and they fail the moment a branch is added
without a test for it.
"""

from __future__ import annotations

import ast
import collections
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: The front door and the two commands it runs. All three carry a `usage()`
#: function, and all three are held to every rule below.
SCRIPTS = ("start.sh", "setup.sh", "launch.sh")

#: The two that do the work. start.sh delegates to them and creates nothing
#: itself, so the rules about what a start must guarantee apply to these.
WORKERS = ("setup.sh", "launch.sh")

#: The RAG pipeline's own scripts -- the only way the knowledge base is built
#: and published. Same shape of risk as the two above (flags, guard clauses
#: and warnings, none of it Python), so they are held to the same rules;
#: their sandbox is in tests/rag/test_rag_scripts.py.
RAG_SCRIPTS = (
    "rag/run_all.sh",
    "rag/run_update.sh",
    "rag/start_rag_db.sh",
    "rag/01_start_chunk_db.sh",
    "rag/03_start_vector_db.sh",
    "rag/publish_db_image.sh",
)

#: Sourced rather than executed, so it takes no flags -- but it holds the
#: helpers every script above dies through.
RAG_LIB = "rag/lib.sh"

#: Sourced by the nginx image's entrypoint. No flags, but it decides two
#: things and refuses to start over one of them.
GUI_ENVSH = "gui/10-nl2sql-config.envsh"

#: The review interface's equivalent. The same two decisions, and the token
#: it turns into a header is the one that can rewrite the golden question
#: set. Driven in tests/review/test_review_project.py.
REVIEW_GUI_ENVSH = "review/gui/10-nl2sql-review-config.envsh"

SMOKE = "docker/apitest/smoke.sh"

#: Runs once, inside `docker build`, to bake a populated cluster into the
#: postgres image. No flags -- it is handed build ARGs as environment -- and
#: it is driven against fake `initdb`, `pg_ctl` and `psql` in
#: tests/docker/test_init_db_script.py.
INIT_DB = "docker/init_db.sh"

#: Everything that parses a flag or prints a message a user reads.
COMMANDS = SCRIPTS + RAG_SCRIPTS

#: Everything written in shell, whatever its shape.
ALL_SHELL = COMMANDS + (RAG_LIB, SMOKE, GUI_ENVSH, REVIEW_GUI_ENVSH, INIT_DB)

#: The API's smoke script is driven from tests/api/, against a real server
#: rather than a fake Docker, so its assertions live there; the RAG scripts'
#: sandbox is in tests/rag/, the GUI's start-up script is driven from
#: tests/gui/, and the review interface's from tests/review/.
TEST_FILES = [
    path
    for directory in ("docker", "api", "rag", "gui", "review")
    for path in (REPO_ROOT / "tests" / directory).glob("test_*.py")
]
TEST_SOURCES = "".join(path.read_text() for path in TEST_FILES)


def _flags_actually_passed() -> set[str]:
    """Flags handed to a script by a test, as opposed to merely named in one.

    Read from the syntax tree rather than by searching the text, because
    searching cannot tell a flag that was *run* from one listed in an
    assertion about the help output -- and that distinction is the whole
    point. It hid the fact that `setup.sh --build` had never once been
    executed: the name appeared in a list of flags `--help` should mention,
    which a substring search counted as coverage.
    """
    passed: set[str] = set()
    for path in TEST_FILES:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else ""
            if not name.startswith("run_"):
                continue
            for argument in node.args:
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    passed.add(argument.value)
    return passed


def _source(name: str) -> str:
    return (REPO_ROOT / name).read_text()


def _help_text(source: str) -> str:
    """Whatever this script shows when asked what it takes.

    Three shapes are in use and all three count as documentation: a `usage()`
    function, a heredoc inside the `--help` case, and the header comment read
    back out with `sed`. The last one is worth resolving rather than skipping
    -- it means the line range in that `sed` is checked against the flags the
    script actually parses.
    """
    if "usage() {" in source:
        return source.split("usage() {")[1].split("EOF\n}")[0]

    lines = source.splitlines()
    text = "".join(re.findall(r"-h\|--help\)(.*?);;", source, re.DOTALL))
    selected = re.search(r"sed -n '(\d+),(\d+)p'", text)
    if selected:
        # The help *is* those lines. Returning the case body as well would
        # offer sed's own `-n` as a flag of the script's.
        start, end = int(selected.group(1)), int(selected.group(2))
        return "\n".join(lines[start - 1 : end])
    if text.strip():
        return text

    # No --help at all: the header comment is the documentation.
    header = []
    for line in lines[1:]:
        if not line.startswith("#"):
            break
        header.append(line)
    return "\n".join(header)


def _parsed_flags(name: str) -> set[str]:
    """Every flag this script accepts, including ones it only forwards.

    `start_rag_db.sh` documents `--image` and parses nothing: it passes `"$@"`
    straight to `03_start_vector_db.sh`. Resolving that is better than
    exempting it, because it means the delegate dropping the flag shows up
    here as the wrapper documenting something that no longer exists.
    """
    source = _source(name)
    flags = {
        flag
        for group in re.findall(r"^\s*(-[-\w|]+)\)", source, re.MULTILINE)
        for flag in group.split("|")
    }
    for delegate in re.findall(r'\$RAG_DIR/(\S+\.sh)" "\$@"', source):
        flags |= _parsed_flags(f"rag/{delegate}")
    return flags


def _documented_flags(help_text: str) -> set[str]:
    """Flags named in a help text, however it is punctuated.

    `[--no-push]` and `--image REPO:TAG` both count; the lookbehind is what
    keeps a bracket or a bar from hiding one.
    """
    return set(re.findall(r"(?<![\w-])(--?[a-z][-a-z]*)", help_text))


def _messages(source: str, function: str) -> list[str]:
    """The literal text of every `warn`/`die`/`info` call in a script.

    Only single-line, single-quoted-free literals: a message built from a
    variable cannot be matched against a test, and one spanning lines is
    matched on its first line.
    """
    found = []
    for match in re.finditer(rf'^\s*{function} "([^"$]+)"', source, re.MULTILINE):
        text = match.group(1).strip()
        if len(text) > 12:  # skip fragments too short to identify anything
            found.append(text)
    return found


def _messages_in(script: str) -> list[str]:
    """Every `warn` and `die` message in one script."""
    source = _source(script)
    return [
        message
        for function in ("warn", "die")
        for message in _messages(source, function)
    ]


def _asserted(message: str, script: str) -> bool:
    """Whether some test quotes enough of this message to be checking it.

    A test rarely quotes a whole warning -- the script wraps them across
    several `warn` calls and the test asserts the distinctive middle. So a
    run of consecutive words counts; but only a run that appears in *this*
    message and no other.

    That qualification is the whole test. Without it, "could not pull" in a
    test about the vector store marked the postgres and context-store
    failures asserted too, and neither had ever been run. Three messages
    were hiding behind phrasing they shared with two others.

    Uniqueness is judged within the one script, not across all of them: two
    scripts warning about the same thing in the same words is deliberate --
    `setup.sh` and `launch.sh` both create the reader role and both say so --
    and one test's assertion genuinely covers that phrase wherever it
    appears.
    """
    words = message.split()
    others = [other for other in _messages_in(script) if other != message]

    # Length is not what makes a quotation specific -- uniqueness is. Two
    # words are plenty when no other message in the script contains them,
    # and six are not enough when another does.
    for width in (6, 5, 4, 3, 2):
        for start in range(len(words) - width + 1):
            run = " ".join(words[start : start + width])
            if any(run in other for other in others):
                continue
            if run in TEST_SOURCES:
                return True
    return False


# ---------------------------------------------------------------------------
# Every flag is parsed, and every parsed flag is documented
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("script", COMMANDS)
def test_every_flag_the_usage_text_offers_is_actually_parsed(script: str):
    """A documented flag that falls through to the unknown-option branch is
    worse than an undocumented one: the user is told to use it and then told
    it does not exist.
    """
    parsed = _parsed_flags(script)
    for flag in sorted(_documented_flags(_help_text(_source(script)))):
        assert flag in parsed, f"{script} documents {flag} but never parses it"


@pytest.mark.parametrize("script", COMMANDS)
def test_every_parsed_flag_appears_in_the_usage_text(script: str):
    documented = _documented_flags(_help_text(_source(script)))
    for flag in sorted(_parsed_flags(script)):
        if flag in ("-h", "--help", "*"):
            continue
        assert flag in documented, f"{script} parses {flag} but never documents it"


@pytest.mark.parametrize("script", COMMANDS)
def test_every_flag_is_exercised_by_a_test(script: str):
    """The point of the sandbox is that a flag can be run for real against
    fake Docker. One nothing runs has never been executed at all.
    """
    exercised = _flags_actually_passed()
    source = _source(script)
    for group in sorted(re.findall(r"^\s*(-[-\w|]+)\)", source, re.MULTILINE)):
        flags = group.split("|")
        if "*" in flags:
            continue
        assert any(flag in exercised for flag in flags), (
            f"{script} parses {group} but no test ever passes it"
        )


# ---------------------------------------------------------------------------
# Every warning and every fatal message is asserted somewhere
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("script", COMMANDS + (RAG_LIB,))
def test_every_warning_the_user_could_see_is_asserted_by_a_test(script: str):
    """These scripts exist to warn. A warning no test triggers is a branch
    that has never run, and it will be discovered by the person it was
    written to help.
    """
    unasserted = [
        message
        for message in _messages(_source(script), "warn")
        if not _asserted(message, script)
    ]
    assert unasserted == [], f"{script} warnings no test checks: {unasserted}"


@pytest.mark.parametrize("script", COMMANDS + (RAG_LIB,))
def test_every_fatal_error_is_asserted_by_a_test(script: str):
    """A `die` is the strongest thing either script does, and the one whose
    message a user reads most carefully.
    """
    unasserted = [
        message
        for message in _messages(_source(script), "die")
        if not _asserted(message, script)
    ]
    assert unasserted == [], f"{script} fatal messages no test checks: {unasserted}"


# ---------------------------------------------------------------------------
# The scripts themselves
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("script", ALL_SHELL)
def test_the_script_is_syntactically_valid(script: str):
    result = subprocess.run(
        ["bash", "-n", str(REPO_ROOT / script)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("script", COMMANDS)
def test_the_script_fails_fast_rather_than_limping_on(script: str):
    assert "set -euo pipefail" in _source(script)


def test_the_sourced_library_does_not_change_its_callers_shell():
    """`set -e` in a sourced file applies to whatever sourced it. lib.sh
    leaves that decision to each script, and every one of them makes it.
    """
    assert not re.search(r"^set -", _source(RAG_LIB), re.MULTILINE)


@pytest.mark.parametrize("script", SCRIPTS)
def test_the_script_runs_from_its_own_directory(script: str):
    """All three are documented as `./name.sh` from anywhere, and all three
    read files relative to the repository.
    """
    assert 'cd "$(dirname "$0")"' in _source(script)


def test_the_front_door_only_delegates():
    """start.sh is the one command someone new runs, and its value is that
    there is nothing in it to go wrong separately: every decision it makes
    is one of the other two scripts', which have their own tests. A
    `docker compose up` appearing here would be a third place for the stack
    to be started slightly differently.
    """
    source = _source("start.sh")
    assert "./setup.sh" in source and "./launch.sh" in source
    assert "docker compose up" not in source
    assert "docker compose run" not in source


def test_launch_ends_by_showing_the_command_the_user_runs_next():
    """The whole contract is two commands. If the closing lines stop naming
    the second one, the script has stopped being the thing it is for.
    """
    assert 'docker compose run --rm agent "' in _source("launch.sh")


def test_setup_ends_by_showing_the_command_the_user_runs_next():
    assert 'docker compose run --rm agent "' in _source("setup.sh")


def test_both_scripts_create_what_the_v4_agent_needs_that_the_image_may_not_have():
    """An existing volume outlives the image that made it, so neither the
    reader role nor the trigram extension can be assumed. Both scripts
    create both, every start -- start.sh inherits it by running them.
    """
    for script in WORKERS:
        source = _source(script)
        assert "reader_role.sql" in source, f"{script} never creates the agent's role"
        assert "pg_trgm" in source, f"{script} never creates the trigram extension"


def test_a_help_text_never_prints_the_script_itself(script=None):
    """`run_update.sh` shows its own header comment with `sed`, which is a
    neat trick with one failure mode: a line range that runs past the end of
    the comment prints shell source as documentation. It did.
    """
    for name in COMMANDS:
        source = _source(name)
        selected = re.search(r"sed -n '(\d+),(\d+)p'", source)
        if not selected:
            continue
        start, end = int(selected.group(1)), int(selected.group(2))
        shown = _source(name).splitlines()[start - 1 : end]
        code = [line for line in shown if line.strip() and not line.startswith("#")]
        assert code == [], f"{name} --help prints these lines of code: {code}"


# ---------------------------------------------------------------------------
# The GUI's start-up script
# ---------------------------------------------------------------------------


def test_the_gui_start_up_script_refuses_rather_than_warning():
    """It runs before nginx and decides whether there is a certificate to
    verify. Carrying on without one would mean proxying to an upstream
    nobody checked, so the only outcome it has is to stop.
    """
    source = _source(GUI_ENVSH)
    assert "exit 1" in source
    assert "warn" not in source


def test_every_message_the_gui_script_prints_is_asserted_by_a_test():
    """Its messages go straight to stderr rather than through a `die`
    helper, so they need their own sweep -- same rule, different shape.
    """
    printed = re.findall(r'^\s*echo "([^"$]{12,})" >&2', _source(GUI_ENVSH), re.MULTILINE)
    assert printed, "no messages found in the GUI start-up script -- has it moved?"
    unasserted = [message for message in printed if not _asserted(message, GUI_ENVSH)]
    assert unasserted == [], f"{GUI_ENVSH} messages no test checks: {unasserted}"


# ---------------------------------------------------------------------------
# The API's smoke script
# ---------------------------------------------------------------------------
#
# Not in SCRIPTS above: it takes no flags and has no usage text, because it is
# not run by hand -- compose runs it, and its whole interface is the six
# environment variables compose sets. What it shares with the other two is
# that it is a script full of checks, and a check nothing has ever seen fail
# is a check that might not be able to.


def test_every_check_the_smoke_script_can_fail_is_exercised_by_a_test():
    """A `fail` that has never fired is a branch that has never run, and the
    whole point of this container is that its failures are trustworthy.
    """
    unasserted = [m for m in _messages(_source(SMOKE), "fail") if not _asserted(m, SMOKE)]
    assert unasserted == [], f"smoke.sh failures no test triggers: {unasserted}"


def test_every_fatal_error_in_the_smoke_script_is_exercised_by_a_test():
    unasserted = [m for m in _messages(_source(SMOKE), "die") if not _asserted(m, SMOKE)]
    assert unasserted == [], f"smoke.sh fatal messages no test triggers: {unasserted}"


def test_the_smoke_script_is_syntactically_valid_and_fails_fast():
    result = subprocess.run(
        ["bash", "-n", str(REPO_ROOT / SMOKE)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "set -uo pipefail" in _source(SMOKE)


def test_the_smoke_script_expands_no_array_that_could_be_empty():
    """Under `set -u`, bash 3.2 -- which is what macOS ships -- aborts on
    "${arr[@]}" when `arr` is empty. The container has bash 5 and would never
    have shown it, so this is checked by reading rather than by running.
    """
    source = _source(SMOKE)
    for name in re.findall(r"^(\w+)=\(\)$", source, re.MULTILINE):
        assert f'"${{{name}[@]}}"' not in source, (
            f"{name} is initialised empty and expanded unguarded; bash 3.2 aborts on that"
        )


def test_every_environment_variable_the_smoke_script_reads_is_documented():
    """Its interface is entirely environmental, so an undocumented variable
    is a setting nobody can discover.
    """
    source = _source(SMOKE)
    read = set(re.findall(r"\$\{(API[A-Z_]*|APITEST[A-Z_]*)[:\-}]", source))
    assert read, "no environment variables found in smoke.sh -- the regex needs updating"
    api_doc = (REPO_ROOT / "agent" / "API.md").read_text()
    for name in sorted(read):
        assert f"`{name}`" in api_doc, f"smoke.sh reads {name}, which API.md never mentions"


def test_the_helper_that_reads_compose_values_is_shared_by_both_scripts():
    """`compose_env` decides which database the role is created in and which
    role name the agent will use. The two scripts disagreeing about that
    would create a role the agent never connects as.
    """
    for script in SCRIPTS:
        assert "compose_env()" in _source(script), f"{script} lost compose_env"


# ---------------------------------------------------------------------------
# Nothing escapes the net
#
# Every list above is written by hand, and a file added beside one of them
# joins no list, fails no test and appears in no measurement -- it is simply
# absent, which looks exactly like a file that is covered. These compare the
# hand-written inventories against what git actually tracks, so adding a
# script, a Dockerfile or a compose file fails here until it is given tests.
# ---------------------------------------------------------------------------


def _tracked(*patterns: str) -> set[str]:
    """Repository files git tracks matching these pathspecs, at any depth."""
    result = subprocess.run(
        ["git", "ls-files", "-z", *patterns],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return {path for path in result.stdout.split("\0") if path}


#: Dockerfile -> the test files that assert on its contents. A Dockerfile is
#: not executed by the suite the way a script is; what is checked is what it
#: says, so the mapping records where that checking lives.
DOCKERFILES = {
    "docker/Dockerfile": ("tests/docker/test_dockerfiles.py",),
    "agent/Dockerfile": ("tests/docker/test_dockerfiles.py",),
    "docker/apitest/Dockerfile": ("tests/docker/test_dockerfiles.py",),
    "gui/Dockerfile": ("tests/gui/test_gui_project.py",),
    "review/Dockerfile": ("tests/review/test_review_image.py",),
    "review/gui/Dockerfile": ("tests/review/test_review_project.py",),
    "rag/docker/chunkdb.Dockerfile": ("tests/rag/test_rag_images.py",),
    "rag/docker/vectordb.Dockerfile": ("tests/rag/test_rag_images.py",),
    "rag/docker/seeded.Dockerfile": ("tests/rag/test_rag_images.py",),
}

#: Compose file -> the test files that resolve and assert on it.
COMPOSE_FILES = {
    "docker-compose.yml": (
        "tests/docker/test_compose_config.py",
        "tests/docker/test_api_compose.py",
        "tests/docker/test_gui_compose.py",
        "tests/review/test_review_compose.py",
    ),
    "rag/docker-compose.yml": ("tests/rag/test_rag_images.py",),
}


def test_every_shell_file_in_the_repository_is_measured():
    """`docker/init_db.sh` was tracked, was shell, and was in none of the
    lists above -- so the measurement reported 100% of eleven scripts while a
    twelfth went entirely unrun. This is the test that would have said so.
    """
    assert _tracked("*.sh", "*.envsh") == set(ALL_SHELL)


@pytest.mark.parametrize("inventory", [DOCKERFILES, COMPOSE_FILES], ids=["dockerfiles", "compose"])
def test_the_inventory_lists_every_file_git_tracks(inventory: dict):
    patterns = ("*Dockerfile",) if inventory is DOCKERFILES else ("*docker-compose.yml",)
    assert _tracked(*patterns) == set(inventory)


@pytest.mark.parametrize("inventory", [DOCKERFILES, COMPOSE_FILES], ids=["dockerfiles", "compose"])
def test_each_file_is_named_by_the_tests_said_to_cover_it(inventory: dict):
    """A mapping nobody checks rots: a test file renamed away, or one that
    stopped mentioning the file it is recorded against, both leave the entry
    looking like coverage that is no longer there.
    """
    for path, drivers in inventory.items():
        assert (REPO_ROOT / path).is_file(), path
        for driver in drivers:
            source = (REPO_ROOT / driver)
            assert source.is_file(), driver
            basename = path.rsplit("/", 1)[-1]
            assert basename in source.read_text(), f"{driver} never mentions {basename}"


# ---------------------------------------------------------------------------
# The two file kinds no inventory named
# ---------------------------------------------------------------------------
#
# `docker/init_db.sh` taught the lesson: a hand-written list is a place for a
# file to fall through, and a file that joins no list is not reported as
# uncovered -- it is simply absent, which reads exactly like one that passes.
#
# These two are driven straight off `git ls-files`, with no list to keep in
# step. A sixth requirements file or a third nginx template is held to the
# same rules the moment it is committed.


def _dependencies(path: str) -> list[str]:
    """The requirement lines of a file, without comments, blanks or `-r`."""
    lines = (REPO_ROOT / path).read_text().splitlines()
    return [
        stripped
        for line in lines
        if (stripped := line.split("#")[0].strip()) and not stripped.startswith("-r")
    ]


def test_there_are_requirements_files_to_check():
    """A pathspec that stops matching would make every test below vacuous."""
    assert _tracked("*requirements.txt")


@pytest.mark.parametrize("path", sorted(_tracked("*requirements.txt")))
def test_every_dependency_is_version_bounded(path: str):
    """An unbounded requirement is a build that stops reproducing.

    It was asserted for `review/requirements.txt` alone, which left the other
    four free to drift -- including the one the agent image is built from.
    """
    for line in _dependencies(path):
        assert re.search(r"[=<>~]", line), f"{path}: {line!r} has no version bound"


def test_a_package_pinned_exactly_in_two_places_is_pinned_to_one_version():
    """The agent and the review service install four of the same packages.

    They are separate images built from one checkout, and they answer with
    the same error envelope against types generated from the same models --
    so a bump applied to one and not the other is two services that were only
    ever tested as one.
    """
    pinned: dict[str, dict[str, str]] = collections.defaultdict(dict)
    for path in _tracked("*requirements.txt"):
        for line in _dependencies(path):
            match = re.fullmatch(r"([A-Za-z0-9_.\-]+(?:\[[^\]]+\])?)==([^\s,]+)", line)
            if match:
                pinned[match.group(1).lower()][path] = match.group(2)

    shared = {name: where for name, where in pinned.items() if len(where) > 1}
    assert shared, "no package is pinned in two files -- this test is checking nothing"
    for name, where in sorted(shared.items()):
        assert len(set(where.values())) == 1, f"{name} is pinned differently: {where}"


def test_there_are_nginx_templates_to_check():
    assert _tracked("*.template")


@pytest.mark.parametrize("path", sorted(_tracked("*.template")))
def test_every_nginx_template_verifies_its_upstream(path: str):
    """A proxy that trusts anything at the far end is a proxy that will one
    day trust something else. The directives live in an included file, which
    the start-up script writes from the upstream's own scheme."""
    template = (REPO_ROOT / path).read_text()
    assert "-upstream-tls.conf;" in template, f"{path} includes no TLS block"
    assert "proxy_ssl_verify off" not in template


@pytest.mark.parametrize("path", sorted(_tracked("*.template")))
def test_no_nginx_template_carries_a_token(path: str):
    """The tokens these proxies hold are the whole reason a browser does not
    have to. One written into a template is one in the image."""
    template = (REPO_ROOT / path).read_text()
    assert not re.search(r'Authorization\s+"Bearer\s+\S', template), (
        f"{path} appears to hard-code a token rather than substituting one"
    )
    assert "AUTH_HEADER}" in template, f"{path} does not take its token from a variable"


@pytest.mark.parametrize("path", sorted(_tracked("*.template")))
def test_every_nginx_template_resolves_its_upstream_per_request(path: str):
    """nginx resolves a literal upstream once, while it parses its config,
    then caches it for the life of the process: it refuses to start before
    the service is up, and talks to a stale address after it restarts."""
    template = (REPO_ROOT / path).read_text()
    assert "resolver " in template
    assert "proxy_pass $upstream$request_uri;" in template
