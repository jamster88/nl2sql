"""The review GUI's own test suite, run from this one.

The same argument as `tests/gui/test_gui_suite.py`: a suite that is only
executed by whoever remembers to `cd review/gui && npm test` is a suite that
goes stale, and the first sign of it is an interface that stopped matching
the service three commits ago.

This application is the one that writes the golden question set, so its
suite going stale is not a cosmetic problem.

Behind `--run-node` rather than `--run-docker`, for the same reason as the
web GUI's: a clone with Docker but no npm should still run every container
test, and someone with npm and no Docker daemon should still run this.
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
GUI = REPO_ROOT / "review" / "gui"


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


def test_every_review_gui_test_passes(suite: subprocess.CompletedProcess):
    assert suite.returncode == 0, f"{suite.stdout}\n{suite.stderr}"


def _test_count(suite: subprocess.CompletedProcess) -> int:
    match = re.search(r"Tests\s+(\d+) passed", suite.stdout + suite.stderr)
    assert match, f"could not find a test count in:\n{suite.stdout}\n{suite.stderr}"
    return int(match.group(1))


def test_the_suite_is_not_empty(suite: subprocess.CompletedProcess):
    """A vitest run with no tests exits 0, so deleting the whole suite would
    otherwise look exactly like the suite passing."""
    assert _test_count(suite) > 50


@pytest.mark.parametrize("doc", ["README.md", "review/README.md"])
def test_the_documented_test_count_is_the_real_one(suite: subprocess.CompletedProcess, doc: str):
    counted = _test_count(suite)
    text = (REPO_ROOT / doc).read_text()
    quoted = [
        int(number)
        for pattern in (r"(\d+)-test review GUI suite", r"review GUI: (\d+) tests")
        for number in re.findall(pattern, text, re.MULTILINE)
    ]
    assert quoted, f"{doc} no longer quotes a review GUI test count"
    assert all(number == counted for number in quoted), (
        f"{doc} quotes {quoted} review GUI tests; there are {counted}"
    )


@pytest.mark.parametrize("metric", ["Statements", "Branches", "Functions", "Lines"])
def test_coverage_is_complete(suite: subprocess.CompletedProcess, metric: str):
    output = suite.stdout + suite.stderr
    match = re.search(rf"{metric}\s+:\s+([\d.]+)%", output)
    assert match, f"no {metric} line in the coverage summary:\n{output}"
    assert float(match.group(1)) == 100.0


def test_the_entry_point_is_the_only_thing_left_out(suite: subprocess.CompletedProcess):
    config = (GUI / "vitest.config.ts").read_text()
    excluded = re.findall(r'"(src/[^"]+)"', re.search(r"exclude: \[([^\]]*)\]", config).group(1))
    assert excluded == ["src/main.tsx"]
    assert suite.returncode == 0


def test_the_lockfile_matches_package_json(installed: str):
    package = json.loads((GUI / "package.json").read_text())
    lock = json.loads((GUI / "package-lock.json").read_text())
    assert lock["name"] == package["name"]
    assert lock["version"] == package["version"]


def test_the_review_gui_is_a_separate_project_from_the_web_gui():
    """Separate on purpose, and the separation is physical.

    Two Vite entry points in one project would share a build, and the public
    GUI's image would then serve the review interface to anyone who could
    reach it. A second package.json is a few more files and a boundary that
    cannot be crossed by forgetting something.
    """
    assert (GUI / "package.json").is_file()
    review = json.loads((GUI / "package.json").read_text())
    web = json.loads((REPO_ROOT / "gui" / "package.json").read_text())
    assert review["name"] != web["name"]
    assert not (REPO_ROOT / "gui" / "src" / "review").exists()
