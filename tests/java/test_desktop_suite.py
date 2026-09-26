"""The desktop client's own test suite, run from this one.

The 354 tests in `desktop/src/test/java` are the real coverage of that
client, and they are written in the language it is written in -- a Python
test cannot press a JavaFX button. What this file does is make sure they are
*run*: a suite only executed by whoever remembers to `cd desktop && mvn test`
is a suite that goes stale, and the first sign of it is a client that stopped
matching the API three commits ago.

Behind `--run-java` rather than `--run-docker` for the same reason the GUI's
suite is behind `--run-node`: a clone with Docker and no JDK should still be
able to run every container test, and somebody working on the desktop client
should not need a Docker daemon to run its tests.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.java

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DESKTOP = REPO_ROOT / "desktop"


@pytest.fixture(scope="module")
def mvn() -> str:
    found = shutil.which("mvn")
    if found is None:
        pytest.skip("Maven is not on PATH")
    return found


@pytest.fixture(scope="module")
def suite(mvn: str) -> subprocess.CompletedProcess:
    """One `mvn test`, which compiles, runs and measures in that order.

    `verify` would also build the jar, which takes another minute and proves
    nothing these tests do not: the image that ships it is built from the
    same sources by `tests/docker/test_desktop_image.py`.
    """
    return subprocess.run(
        [mvn, "-B", "test"], cwd=DESKTOP, capture_output=True, text=True, timeout=1800
    )


def _output(suite: subprocess.CompletedProcess) -> str:
    return suite.stdout + suite.stderr


def test_every_desktop_test_passes(suite: subprocess.CompletedProcess):
    assert suite.returncode == 0, _output(suite)


def _test_count(suite: subprocess.CompletedProcess) -> int:
    counts = re.findall(r"Tests run: (\d+), Failures: \d+, Errors: \d+, Skipped: \d+$",
                        _output(suite), re.MULTILINE)
    assert counts, f"could not find a test count in:\n{_output(suite)}"
    # The last line is the run's total; the ones above it are per class.
    return int(counts[-1])


def test_the_suite_is_not_empty(suite: subprocess.CompletedProcess):
    """Surefire exits 0 with no tests at all. Without this, deleting the
    whole suite would look like the suite passing."""
    assert _test_count(suite) > 100


def test_nothing_was_skipped(suite: subprocess.CompletedProcess):
    """A skip here means the JavaFX toolkit would not start, which turns the
    whole interface half of the suite into a green run that tested none of
    it. Monocle is a dependency precisely so that does not happen."""
    skipped = re.findall(r"Tests run: \d+, Failures: \d+, Errors: \d+, Skipped: (\d+)$",
                         _output(suite), re.MULTILINE)
    assert skipped and int(skipped[-1]) == 0, _output(suite)


def test_coverage_is_complete(suite: subprocess.CompletedProcess):
    """JaCoCo's own rule already fails the build below 100%, so this is a
    second reading of the same fact -- worth having because the rule is a
    file someone can edit, and this names what was lost."""
    violations = re.findall(r"Rule violated for [^\n]+", _output(suite))
    assert violations == [], violations
    assert "All coverage checks have been met." in _output(suite)


@pytest.mark.parametrize("doc", ["README.md", "desktop/README.md"])
def test_the_documented_test_count_is_the_real_one(suite: subprocess.CompletedProcess, doc: str):
    """The Python and TypeScript counts both went stale before a test pinned
    them. This one is quoted in two places, which is twice as many chances."""
    counted = _test_count(suite)
    text = (REPO_ROOT / doc).read_text()
    # Anchored on phrasings that name this client, because the root README
    # quotes four other counts and the GUI's own test reads the same file
    # looking for "N-test suite". An interface-specific phrase is the only
    # thing that keeps two of these tests from failing each other.
    # `\s+` rather than a space: a count that has drifted can hide behind a
    # line break, and one did -- "365-test Java\nsuite" sat wrong in the
    # README for four releases because the pattern wanted them adjacent.
    quoted = [
        int(number)
        for pattern in (r"(\d+)-test\s+Java\s+suite",
                        r"^(\d+)\s+tests, 100% of lines and branches")
        for number in re.findall(pattern, text, re.MULTILINE)
    ]
    assert quoted, f"{doc} no longer quotes a desktop test count"
    assert all(number == counted for number in quoted), (
        f"{doc} quotes {quoted} desktop tests; there are {counted}"
    )
