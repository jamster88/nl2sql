"""Builds the real agent image from agent/Dockerfile and runs it as a real
container -- the one part of the agent that unit tests (fakes/mocks) can't
reach: that `docker compose run --rm agent "<question>"` actually works as
packaged, argv and all, and fails the way LlmUnavailableError promises
rather than with a raw traceback when Ollama is unreachable.

Opt-in (--run-docker): this builds an image, which needs network access for
pip installs and takes real time (tens of seconds to a couple of minutes on
a cold layer cache).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.docker

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
IMAGE_TAG = "nl2sql-agent:pytest"


@pytest.fixture(scope="module")
def agent_image(docker_daemon_available: bool) -> str:
    if not docker_daemon_available:
        pytest.skip("no working docker daemon")
    result = subprocess.run(
        ["docker", "build", "-f", "agent/Dockerfile", "-t", IMAGE_TAG, "."],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        pytest.skip(f"could not build agent image (likely no network for pip install):\n{result.stderr[-2000:]}")
    return IMAGE_TAG


def _run_agent(image: str, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "run", "--rm", image, *args],
        capture_output=True, text=True, timeout=timeout,
    )


def test_entrypoint_shows_help_without_needing_a_live_ollama_or_database(agent_image: str):
    # argparse's --help path exits before Settings/Nl2SqlAgent ever touch the
    # network -- proves the packaged ENTRYPOINT/argv wiring itself is sound.
    result = _run_agent(agent_image, "--help")
    assert result.returncode == 0
    assert "nl2sql-agent" in result.stdout
    assert "question" in result.stdout


def test_fails_fast_with_exit_two_when_ollama_is_unreachable(agent_image: str):
    # An address nothing listens on, so the connection fails immediately
    # instead of waiting out a real timeout.
    result = _run_agent(
        agent_image,
        "--base-url", "http://127.0.0.1:1",
        "--database-url", "postgresql+psycopg://nl2sql:nl2sql@127.0.0.1:1/nl2sql_retail",
        "some question",
    )
    assert result.returncode == 2
    assert "error:" in result.stderr
    assert "Cannot reach Ollama" in result.stderr
    # And it must be the clean wrapped message, not a raw traceback.
    assert "Traceback" not in result.stderr
