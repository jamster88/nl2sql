"""Shared fixtures and configuration for the whole test suite.

Puts data_gen/ and agent/ on sys.path so `import datagen` and
`import nl2sql_agent` work without either package being installed, and wires
up the --run-docker and --run-node opt-ins for tests that build/run real
containers, talk to a live service, or need a JavaScript toolchain.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT / "data_gen", ROOT / "agent"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-docker",
        action="store_true",
        default=False,
        help="also run tests marked 'docker' (builds/runs real containers, or "
        "connects to a live service; slower and environment-dependent)",
    )
    parser.addoption(
        "--run-node",
        action="store_true",
        default=False,
        help="also run tests marked 'node' (runs the GUI's own test suite, "
        "which needs npm and a populated gui/node_modules)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    # Two opt-ins rather than one because the two needs are different: a
    # clone with Docker but no npm should still be able to run every
    # container test, and a GUI developer with npm and no Docker daemon
    # should still be able to run the GUI's suite.
    for name in ("docker", "node"):
        if config.getoption(f"--run-{name}"):
            continue
        skip = pytest.mark.skip(reason=f"needs --run-{name}")
        for item in items:
            # Note: `name in item.keywords` would also match anything merely
            # collected under a directory/module with that word in its name
            # (pytest keywords include path components for -k matching) -- we
            # only want items carrying the actual marker.
            if item.get_closest_marker(name) is not None:
                item.add_marker(skip)


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def docker_cli() -> str | None:
    """Path to the docker binary, or None if it isn't on PATH."""
    return shutil.which("docker")


@pytest.fixture(scope="session")
def docker_daemon_available(docker_cli: str | None) -> bool:
    if docker_cli is None:
        return False
    try:
        subprocess.run([docker_cli, "info"], capture_output=True, timeout=10, check=True)
        return True
    except Exception:
        return False
