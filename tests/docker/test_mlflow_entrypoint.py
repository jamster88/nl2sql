"""`docker/mlflow/entrypoint.sh`: MLflow as its own account, not root (V6-31).

Run rather than read, against fakes on PATH that record what they were
asked: `id` answers the uid the test chooses, and `find` and `setpriv` only
write down what would have been handed over and whom the server would have
run as -- this machine has no `mlflow` account for the real ones to name.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "docker" / "mlflow" / "entrypoint.sh"

FAKES = {
    "id": '#!/bin/sh\necho "${FAKE_UID:-0}"\n',
    "find": '#!/bin/sh\necho "find $*" >> "$FAKE_LOG"\n',
    "setpriv": '#!/bin/sh\necho "setpriv $*" >> "$FAKE_LOG"\n',
}


@pytest.fixture
def run(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in FAKES.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    log = tmp_path / "log"
    artifacts = tmp_path / "artifacts"

    def go(*command: str, uid: int = 0) -> list[str]:
        env = {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "FAKE_UID": str(uid),
            "FAKE_LOG": str(log),
            "MLFLOW_ARTIFACTS_DIR": str(artifacts),
        }
        result = subprocess.run(["sh", str(SCRIPT), *command], env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return (log.read_text().splitlines() if log.exists() else []) + [result.stdout.strip()]

    go.artifacts = artifacts
    return go


def test_as_root_it_gives_the_account_the_volume_and_runs_mlflow_as_it(run):
    said = run("mlflow", "server", "--port", "5000")
    assert run.artifacts.is_dir(), "made when compose mounted nothing there"
    assert f"find {run.artifacts} ! -user mlflow -exec chown mlflow:mlflow {{}} +" in said, "only what is not its own"
    setpriv = [line for line in said if line.startswith("setpriv")]
    assert setpriv == ["setpriv --reuid=mlflow --regid=mlflow --init-groups --inh-caps=-all -- mlflow server --port 5000"]


def test_started_as_anyone_else_it_runs_as_given(run):
    said = run("echo", "served", uid=10002)
    assert said == ["served"], "no chown, no setpriv: the command itself"


def test_the_image_starts_through_it_as_an_account_of_its_own():
    dockerfile = (REPO_ROOT / "docker" / "mlflow" / "Dockerfile").read_text()
    assert "useradd --system --uid 10002" in dockerfile
    assert 'ENTRYPOINT ["/usr/local/bin/nl2sql-mlflow-entrypoint"]' in dockerfile
    assert "docker/mlflow/entrypoint.sh /usr/local/bin/nl2sql-mlflow-entrypoint" in dockerfile
