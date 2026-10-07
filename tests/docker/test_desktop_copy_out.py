"""`desktop/copy-out.sh`: the jar copied out as whoever owns where it lands (V6-31).

Run rather than read, against fakes on PATH: `id` answers the uid the test
chooses, `stat` the owner of the destination, and `su-exec` writes down
whom it would have become before running the copy as the test's own user.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "desktop" / "copy-out.sh"

FAKES = {
    "id": '#!/bin/sh\necho "${FAKE_UID:-0}"\n',
    "stat": '#!/bin/sh\necho "${FAKE_OWNER:-501:20}"\n',
    "su-exec": '#!/bin/sh\necho "su-exec $1" >> "$FAKE_LOG"\nshift\nexec "$@"\n',
}


@pytest.fixture
def run(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in FAKES.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    jar = tmp_path / "nl2sql-desktop.jar"
    jar.write_text("the jar")
    out = tmp_path / "out"
    out.mkdir()
    log = tmp_path / "log"

    def go(*, uid: int, owner: str) -> list[str]:
        env = {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "FAKE_UID": str(uid),
            "FAKE_OWNER": owner,
            "FAKE_LOG": str(log),
            "NL2SQL_OUT": str(out),
            "NL2SQL_JAR": str(jar),
        }
        command = ["sh", str(SCRIPT)]
        trace = os.environ.get("NL2SQL_SHELL_TRACE")
        if trace:
            # What tests/shell_coverage.py reads: every line run, by script.
            env["PS4"] = "+@${BASH_SOURCE##*/}@${LINENO}@ "
            command = ["bash", "-x", *command[1:]]
        result = subprocess.run(command, env=env, capture_output=True, text=True)
        if trace:
            with open(os.path.join(trace, "trace.log"), "a") as handle:
                handle.write(result.stderr)
        assert result.returncode == 0, result.stderr
        assert (out / "nl2sql-desktop.jar").read_text() == "the jar"
        return log.read_text().splitlines() if log.exists() else []

    return go


def test_as_root_the_jar_is_copied_as_the_owner_of_the_checkout(run):
    assert run(uid=0, owner="501:20") == ["su-exec 501:20"]


def test_a_directory_root_owns_is_written_as_root(run):
    assert run(uid=0, owner="0:0") == []


def test_started_as_anyone_else_it_copies_as_them(run):
    assert run(uid=1000, owner="1000:1000") == []


def test_the_image_runs_it_and_carries_what_it_needs():
    dockerfile = (REPO_ROOT / "desktop" / "Dockerfile").read_text()
    assert 'CMD ["/usr/local/bin/nl2sql-copy-out"]' in dockerfile
    assert "apk add --no-cache su-exec~" in dockerfile
