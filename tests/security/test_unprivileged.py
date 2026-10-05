"""Nothing in the stack runs as root (V6-31, the second review's K2 and M-01).

Each image either names an unprivileged account in its last stage (`USER`),
or starts as root for one reason -- a volume or a bind mount whose owner is
not known until it is mounted -- and drops to an account by a route this
test can point at. A new image is a failing test until it says which.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent.parent

#: Images that run as an account of their own from the first instruction.
USERS = {
    "agent/Dockerfile": "10001:10001",
    "docker/apitest/Dockerfile": "nobody",
    "gui/Dockerfile": "101:101",
    "review/gui/Dockerfile": "101:101",
    "curate/Dockerfile": "101:101",
    "console/Dockerfile": "101:101",
    "auth/gui/Dockerfile": "101:101",
    "docker/mlflow-proxy/Dockerfile": "101:101",
}

#: Images that start as root and drop it themselves: the file that does it,
#: and what in it does.
DROPS = {
    # Becomes the owner of the checkout it writes (V6-28).
    "review/Dockerfile": ("review/nl2sql_review/server.py", "privileges.become_owner_of"),
    # Gives the account its key directories, then becomes it.
    "auth/Dockerfile": ("auth/nl2sql_auth/server.py", "privileges.become"),
    # The pattern's origin (6.0.1): slapd's database and certificates.
    "ldap/Dockerfile": ("ldap/nl2sql_ldap/service.py", "become("),
    # The artifact volume, then MLflow as `mlflow`.
    "docker/mlflow/Dockerfile": ("docker/mlflow/entrypoint.sh", "setpriv --reuid=mlflow"),
    # The jar copied out as the owner of where it lands.
    "desktop/Dockerfile": ("desktop/copy-out.sh", "su-exec"),
    # Postgres's own entrypoint drops to `postgres` before the server starts;
    # the retail image's wraps it and does the same.
    "docker/Dockerfile": ("docker/entrypoint.sh", 'gosu}" postgres'),
    "docker/mlflowdb/Dockerfile": ("docker/mlflowdb/Dockerfile", "FROM postgres:"),
    "rag/docker/chunkdb.Dockerfile": ("rag/docker/chunkdb.Dockerfile", "POSTGRES_IMAGE"),
    "rag/docker/vectordb.Dockerfile": ("rag/docker/vectordb.Dockerfile", "PGVECTOR_IMAGE"),
    "rag/docker/restore.Dockerfile": ("rag/docker/restore.Dockerfile", "gosu postgres"),
}


def _tracked_dockerfiles() -> set[str]:
    listed = subprocess.run(
        ["git", "ls-files", "-z", "*Dockerfile", "*.Dockerfile"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return {name for name in listed.stdout.split("\0") if name}


def _last_stage(text: str) -> str:
    return re.split(r"^FROM ", text, flags=re.MULTILINE)[-1]


def test_every_image_is_accounted_for():
    assert _tracked_dockerfiles() == set(USERS) | set(DROPS)


@pytest.mark.parametrize("dockerfile,user", sorted(USERS.items()))
def test_the_image_runs_as_its_account(dockerfile: str, user: str):
    users = re.findall(r"^USER (\S+)", _last_stage((ROOT / dockerfile).read_text()), re.MULTILINE)
    assert users and users[-1] == user, f"{dockerfile}: USER {users}"
    assert users[-1] not in ("root", "0", "0:0")


@pytest.mark.parametrize("dockerfile,drop", sorted(DROPS.items()))
def test_the_image_that_starts_as_root_drops_it(dockerfile: str, drop: tuple[str, str]):
    """Starting as root is the point here -- the retail image even says
    `USER root` again after its build steps -- so what is held is the drop."""
    source, does = drop
    assert does in (ROOT / source).read_text(), f"{dockerfile} drops root in {source} by {does!r}"


def test_only_the_pki_service_is_run_as_root_by_compose():
    """It writes every other service's key into that service's volume, once."""
    services = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]
    as_root = sorted(name for name, service in services.items() if str(service.get("user", "")).split(":")[0] in ("0", "root"))
    assert as_root == ["pki"]


def test_each_key_is_given_to_the_account_that_serves_with_it():
    services = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]
    owners = {
        arg.split("=", 1)[0]: arg.rsplit("=", 1)[1]
        for arg in services["pki"]["command"]
        if arg.count("=") == 3
    }
    assert owners == {
        **{name: "10001:10001" for name in ("api", "review", "console", "auth")},
        **{name: "101:101" for name in ("gui", "reviewgui", "curategui", "consolegui", "directorygui", "mlflowproxy")},
    }
