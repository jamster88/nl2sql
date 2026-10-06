"""`docker/mlflow/entrypoint.sh`: MLflow as its own account, not root (V6-31).

Run rather than read, against fakes on PATH that record what they were
asked: `id` answers the uid the test chooses, and `find` and `setpriv` only
write down what would have been handed over and whom the server would have
run as -- this machine has no `mlflow` account for the real ones to name.
"""

from __future__ import annotations

import os
import subprocess
import sys
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
    # The image's Python, which encodes the store's password for its URL.
    (bin_dir / "python").symlink_to(sys.executable)
    log = tmp_path / "log"
    artifacts = tmp_path / "artifacts"

    def go(*command: str, uid: int = 0, **extra: str) -> list[str]:
        env = {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "FAKE_UID": str(uid),
            "FAKE_LOG": str(log),
            "MLFLOW_ARTIFACTS_DIR": str(artifacts),
            **extra,
        }
        command = ["sh", str(SCRIPT), *command]
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


# --- the tracking store's URL (6.3, V6-38) -------------------------------------------------

STORE = {"MLFLOW_DB_USER": "tracer", "MLFLOW_DB_HOST": "store", "MLFLOW_DB_NAME": "traces"}


def test_the_store_url_is_built_from_the_password_file(run, tmp_path):
    """Handed to the server in its environment, which `mlflow server` reads
    for --backend-store-uri: on the command line, as it was until 6.3, the
    password was in `ps` and `docker inspect`. Encoded, for a password
    chosen by hand."""
    secret = tmp_path / "mlflow_db_password"
    secret.write_text("p@ss/word\n")
    said = run("sh", "-c", 'echo "$MLFLOW_BACKEND_STORE_URI"', uid=10002,
               MLFLOW_DB_PASSWORD_FILE=str(secret), **STORE)
    assert said == ["postgresql://tracer:p%40ss%2Fword@store:5432/traces"]


def test_a_store_url_given_outright_is_used_as_it_is(run, tmp_path):
    secret = tmp_path / "mlflow_db_password"
    secret.write_text("unused")
    said = run("sh", "-c", 'echo "$MLFLOW_BACKEND_STORE_URI"', uid=10002,
               MLFLOW_BACKEND_STORE_URI="sqlite:////tmp/mlflow.db", MLFLOW_DB_PASSWORD_FILE=str(secret), **STORE)
    assert said == ["sqlite:////tmp/mlflow.db"]


def test_compose_gives_the_server_no_url_with_a_password_in_it():
    import yaml

    server = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())["services"]["mlflow"]
    assert not any("backend-store-uri" in part for part in server["command"])
    assert server["environment"]["MLFLOW_DB_PASSWORD_FILE"] == "/run/secrets/mlflow_db_password"
