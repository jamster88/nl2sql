"""The console interface's own test suite, run from this one.

The same argument as the other two interfaces': a suite only executed by
whoever remembers to `cd console && npm test` goes stale, and the first sign
is a page that stopped matching the console three commits ago.

Behind `--run-node` rather than `--run-docker`: a clone with Docker and no
npm should still run every container test, and one with npm and no Docker
daemon should still run this.
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
GUI = REPO_ROOT / "console"


@pytest.fixture(scope="module")
def npm() -> str:
    found = shutil.which("npm")
    if found is None:
        pytest.skip("npm is not on PATH")
    return found


@pytest.fixture(scope="module")
def installed(npm: str) -> str:
    if not (GUI / "node_modules").is_dir():
        result = subprocess.run(
            [npm, "ci", "--no-audit", "--no-fund"],
            cwd=GUI, capture_output=True, text=True, timeout=900,
        )
        if result.returncode != 0:  # pragma: no cover - a failed install fails below too
            pytest.fail(f"npm ci failed:\n{result.stdout}\n{result.stderr}")
    return npm


def _run(npm: str, *script: str, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run([npm, "run", *script], cwd=GUI, capture_output=True, text=True, timeout=timeout)


def test_the_typescript_compiles(installed: str):
    """`vite build` strips types without checking them."""
    result = _run(installed, "typecheck")
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


@pytest.fixture(scope="module")
def suite(installed: str) -> subprocess.CompletedProcess:
    return _run(installed, "test")


def test_every_console_gui_test_passes(suite: subprocess.CompletedProcess):
    assert suite.returncode == 0, f"{suite.stdout}\n{suite.stderr}"


def _test_count(suite: subprocess.CompletedProcess) -> int:
    match = re.search(r"Tests\s+(\d+) passed", suite.stdout + suite.stderr)
    assert match, f"could not find a test count in:\n{suite.stdout}\n{suite.stderr}"
    return int(match.group(1))


def test_the_suite_is_not_empty(suite: subprocess.CompletedProcess):
    """A vitest run with no tests exits 0."""
    assert _test_count(suite) > 50


@pytest.mark.parametrize("doc", ["README.md", "console/README.md"])
def test_the_documented_test_count_is_the_real_one(suite: subprocess.CompletedProcess, doc: str):
    counted = _test_count(suite)
    text = (REPO_ROOT / doc).read_text()
    quoted = [
        int(number)
        for pattern in (r"(\d+)-test\s+console\s+GUI\s+suite", r"console\s+GUI:\s+(\d+)\s+tests")
        for number in re.findall(pattern, text, re.MULTILINE)
    ]
    assert quoted, f"{doc} no longer quotes a console GUI test count"
    assert all(number == counted for number in quoted), (
        f"{doc} quotes {quoted} console GUI tests; there are {counted}"
    )


@pytest.mark.parametrize("metric", ["Statements", "Branches", "Functions", "Lines"])
def test_coverage_is_complete(suite: subprocess.CompletedProcess, metric: str):
    output = suite.stdout + suite.stderr
    match = re.search(rf"{metric}\s+:\s+([\d.]+)%", output)
    assert match, f"no {metric} line in the coverage summary:\n{output}"
    assert float(match.group(1)) == 100.0


def test_the_lockfile_matches_package_json(installed: str):
    package = json.loads((GUI / "package.json").read_text())
    lock = json.loads((GUI / "package-lock.json").read_text())
    assert lock["name"] == package["name"]
    assert lock["version"] == package["version"]
