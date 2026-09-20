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

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS = ("setup.sh", "launch.sh")
TEST_SOURCES = "".join(
    p.read_text() for p in (REPO_ROOT / "tests" / "docker").glob("test_*.py")
)


def _source(name: str) -> str:
    return (REPO_ROOT / name).read_text()


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


def _asserted(message: str) -> bool:
    """Whether some test quotes enough of this message to be checking it.

    A test rarely quotes a whole warning -- the script wraps them across
    several `warn` calls and the test asserts the distinctive middle. So any
    run of four consecutive words counts, which is specific enough that an
    accidental match between two different messages does not happen here.
    """
    words = message.split()
    for width in (4, 3):
        for start in range(len(words) - width + 1):
            if " ".join(words[start : start + width]) in TEST_SOURCES:
                return True
    return False


# ---------------------------------------------------------------------------
# Every flag is parsed, and every parsed flag is documented
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("script", SCRIPTS)
def test_every_flag_the_usage_text_offers_is_actually_parsed(script: str):
    """A documented flag that falls through to the unknown-option branch is
    worse than an undocumented one: the user is told to use it and then told
    it does not exist.
    """
    source = _source(script)
    usage = source.split("usage() {")[1].split("EOF\n}")[0]
    parsed = set(re.findall(r"^\s*(-[-\w|]+)\)", source, re.MULTILINE))
    parsed = {flag for group in parsed for flag in group.split("|")}
    for flag in sorted(set(re.findall(r"\s(--?[a-z][-a-z]*)", usage))):
        assert flag in parsed, f"{script} documents {flag} but never parses it"


@pytest.mark.parametrize("script", SCRIPTS)
def test_every_parsed_flag_appears_in_the_usage_text(script: str):
    source = _source(script)
    usage = source.split("usage() {")[1].split("EOF\n}")[0]
    parsed = set(re.findall(r"^\s*(-[-\w|]+)\)", source, re.MULTILINE))
    for group in sorted(parsed):
        for flag in group.split("|"):
            if flag in ("-h", "--help", "*"):
                continue
            assert flag in usage, f"{script} parses {flag} but never documents it"


@pytest.mark.parametrize("script", SCRIPTS)
def test_every_flag_is_exercised_by_a_test(script: str):
    """The point of the sandbox is that a flag can be run for real against
    fake Docker. One nothing runs has never been executed at all.
    """
    source = _source(script)
    parsed = set(re.findall(r"^\s*(-[-\w|]+)\)", source, re.MULTILINE))
    for group in sorted(parsed):
        flags = group.split("|")
        if "*" in flags:
            continue
        assert any(f'"{flag}"' in TEST_SOURCES for flag in flags), (
            f"{script} parses {group} but no test ever passes it"
        )


# ---------------------------------------------------------------------------
# Every warning and every fatal message is asserted somewhere
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("script", SCRIPTS)
def test_every_warning_the_user_could_see_is_asserted_by_a_test(script: str):
    """These scripts exist to warn. A warning no test triggers is a branch
    that has never run, and it will be discovered by the person it was
    written to help.
    """
    unasserted = [
        message
        for message in _messages(_source(script), "warn")
        if not _asserted(message)
    ]
    assert unasserted == [], f"{script} warnings no test checks: {unasserted}"


@pytest.mark.parametrize("script", SCRIPTS)
def test_every_fatal_error_is_asserted_by_a_test(script: str):
    """A `die` is the strongest thing either script does, and the one whose
    message a user reads most carefully.
    """
    unasserted = [
        message
        for message in _messages(_source(script), "die")
        if not _asserted(message)
    ]
    assert unasserted == [], f"{script} fatal messages no test checks: {unasserted}"


# ---------------------------------------------------------------------------
# The scripts themselves
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("script", SCRIPTS)
def test_the_script_is_syntactically_valid(script: str):
    result = subprocess.run(
        ["bash", "-n", str(REPO_ROOT / script)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("script", SCRIPTS)
def test_the_script_fails_fast_rather_than_limping_on(script: str):
    assert "set -euo pipefail" in _source(script)


@pytest.mark.parametrize("script", SCRIPTS)
def test_the_script_runs_from_its_own_directory(script: str):
    """Both are documented as `./setup.sh` from anywhere, and both read
    files relative to the repository.
    """
    assert 'cd "$(dirname "$0")"' in _source(script)


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
    create both, every start.
    """
    for script in SCRIPTS:
        source = _source(script)
        assert "reader_role.sql" in source, f"{script} never creates the agent's role"
        assert "pg_trgm" in source, f"{script} never creates the trigram extension"


def test_the_helper_that_reads_compose_values_is_shared_by_both_scripts():
    """`compose_env` decides which database the role is created in and which
    role name the agent will use. The two scripts disagreeing about that
    would create a role the agent never connects as.
    """
    for script in SCRIPTS:
        assert "compose_env()" in _source(script), f"{script} lost compose_env"
