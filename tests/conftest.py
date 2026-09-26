"""Shared fixtures and configuration for the whole test suite.

Puts data_gen/, agent/, review/ and rag/ on sys.path so `import datagen`,
`import nl2sql_agent`, `import nl2sql_review` and `import ragproc` work
without any of them being installed, and wires up the --run-docker,
--run-node and --run-java opt-ins for tests that build/run real containers,
talk to a live service, or need a JavaScript or Java toolchain.

`rag/` is there because the review service's promotion path validates a
rendered golden pair by parsing it with the loader's own parser -- the whole
point being that it is the real one and not a copy of the rules.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for _p in (ROOT / "data_gen", ROOT / "agent", ROOT / "review", ROOT / "rag"):
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
    parser.addoption(
        "--run-java",
        action="store_true",
        default=False,
        help="also run tests marked 'java' (runs the desktop client's own "
        "test suite, which needs Maven and a JDK of 21 or later)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    # Three opt-ins rather than one because the three needs are different: a
    # clone with Docker but no npm should still be able to run every
    # container test, a GUI developer with npm and no Docker daemon should
    # still be able to run the GUI's suite, and neither of them should be
    # asked for a JDK to run the Python ones.
    for name in ("docker", "node", "java"):
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
