"""The GUI's own test suite, run from this one.

The 248 tests in `gui/test/` are the real coverage of the interface, and they
are written in the language the interface is written in -- a Python test
cannot render a React component. What this file does is make sure they are
*run*: a suite that is only executed by whoever remembers to `cd gui && npm
test` is a suite that goes stale, and the first sign of it is a GUI that
stopped matching the API three commits ago.

Behind `--run-node` rather than `--run-docker` because the needs are
different. A clone with Docker but no npm should still be able to run every
container test, and a GUI developer with npm and no Docker daemon should
still be able to run this.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.node

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GUI = REPO_ROOT / "gui"


@pytest.fixture(scope="module")
def npm() -> str:
    found = shutil.which("npm")
    if found is None:
        pytest.skip("npm is not on PATH")
    return found


@pytest.fixture(scope="module")
def installed(npm: str) -> str:
    """The dependencies, installed from the lockfile if they are not already.

    `npm ci` rather than `npm install`: it installs exactly what the lockfile
    says and fails if the lockfile and package.json disagree, which is the
    behaviour a test wants.
    """
    if not (GUI / "node_modules").is_dir():
        result = subprocess.run(
            [npm, "ci", "--no-audit", "--no-fund"],
            cwd=GUI, capture_output=True, text=True, timeout=900,
        )
        if result.returncode != 0:  # pragma: no cover - a failed install fails below too
            pytest.fail(f"npm ci failed:\n{result.stdout}\n{result.stderr}")
    return npm


def _run(npm: str, *script: str, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(
        [npm, "run", *script], cwd=GUI, capture_output=True, text=True, timeout=timeout
    )


def test_the_typescript_compiles(installed: str):
    """`vite build` strips types without checking them, so this is the only
    thing that would catch the GUI drifting away from its own types."""
    result = _run(installed, "typecheck")
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


@pytest.fixture(scope="module")
def suite(installed: str) -> subprocess.CompletedProcess:
    return _run(installed, "test")


def test_every_gui_test_passes(suite: subprocess.CompletedProcess):
    assert suite.returncode == 0, f"{suite.stdout}\n{suite.stderr}"


def _test_count(suite: subprocess.CompletedProcess) -> int:
    match = re.search(r"Tests\s+(\d+) passed", suite.stdout + suite.stderr)
    assert match, f"could not find a test count in:\n{suite.stdout}\n{suite.stderr}"
    return int(match.group(1))


def test_the_suite_is_not_empty(suite: subprocess.CompletedProcess):
    """A vitest run with no tests exits 0. Without this, deleting the whole
    suite would look like the suite passing."""
    assert _test_count(suite) > 100


@pytest.mark.parametrize("doc", ["README.md", "gui/README.md"])
def test_the_documented_test_count_is_the_real_one(suite: subprocess.CompletedProcess, doc: str):
    """The Python counts went stale twice before a test pinned them. This one
    is quoted in two places, which is twice as many chances."""
    counted = _test_count(suite)
    text = (REPO_ROOT / doc).read_text()
    # Whitespace rather than spaces between the words: a count that has
    # drifted can otherwise hide behind a line break, and one did -- the
    # desktop client's was wrong in the README for four releases because its
    # pattern wanted the words adjacent.
    #
    # Anchored on the three phrasings rather than on any three-digit number:
    # the root README also quotes the Python counts, and "356 tests behind
    # --run-docker" is not this number.
    quoted = [
        int(number)
        for pattern in (r"(\d+)-test\s+suite", r"across\s+(\d+)\s+tests",
                        r"^(\d+)\s+tests, 100%")
        for number in re.findall(pattern, text, re.MULTILINE)
    ]
    assert quoted, f"{doc} no longer quotes a GUI test count"
    assert all(number == counted for number in quoted), (
        f"{doc} quotes {quoted} GUI tests; there are {counted}"
    )


@pytest.mark.parametrize("metric", ["Statements", "Branches", "Functions", "Lines"])
def test_coverage_is_complete(suite: subprocess.CompletedProcess, metric: str):
    """The thresholds in vitest.config.ts already fail the run below 100, so
    this is a second reading of the same fact -- worth having because the
    thresholds are a file someone can edit, and this names what was lost.
    """
    output = suite.stdout + suite.stderr
    match = re.search(rf"{metric}\s+:\s+([\d.]+)%", output)
    assert match, f"no {metric} line in the coverage summary:\n{output}"
    assert float(match.group(1)) == 100.0


# The coverage exclusion list is pinned by
# `tests/gui/test_gui_project.py::test_only_the_entry_point_is_left_out_of_coverage`,
# which reads the same file and needs no npm to do it. A copy here asserted
# the same thing behind --run-node, and re-asserted the suite's exit code,
# which `test_every_gui_test_passes` above already is.


def test_the_lockfile_matches_package_json(installed: str):
    """`npm ci` fails outright when they disagree, which is what makes the
    installed fixture above a check as well as a setup step."""
    package = json.loads((GUI / "package.json").read_text())
    lock = json.loads((GUI / "package-lock.json").read_text())
    assert lock["name"] == package["name"]
    assert lock["version"] == package["version"]
