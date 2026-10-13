"""Nothing in the stack runs as root (V6-31, the second review's K2 and M-01).

Each image either names an unprivileged account in its last stage (`USER`),
or starts as root for one reason -- a volume or a bind mount whose owner is
not known until it is mounted -- and drops to an account by a route this
test can point at. A new image is a failing test until it says which.

And nothing Docker runs *in* one is root's either (7.0). A health check is
run as the user its container was started as, so in a container that starts
as root it was root's, every ten seconds, long after the service itself had
dropped. Each of those is started through `setpriv` as the service's
account, and a new health check is a failing test until it says who it is.
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
    # Every page and MLflow's front door, one image since 6.3 (V6-37).
    "proxy/Dockerfile": "101:101",
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


#: The services compose starts as root, which is the nine with a health
#: check and the account each one's check is started as: the account the
#: service itself drops to, or -- for the review service, which becomes
#: whoever owns the checkout -- the one that owns its certificate.
CHECKED_AS = {
    "postgres": "postgres",
    "vectordb": "postgres",
    "chunkdb": "postgres",
    "stores": "postgres",
    "mlflowdb": "postgres",
    "ldap": "ldap",
    "auth": "nl2sql",
    "review": "nl2sql",
    "mlflow": "mlflow",
}

#: Images that start as root and declare a health check of their own: the
#: module it runs, and the file where that module gives root up before
#: anything else. An image cannot know who it will be started as -- as its
#: account, with `docker run --user`, the drop would be refused -- so its
#: check cannot be started through `setpriv`, and gives root up itself.
SELF_DROPPING = {
    "auth/Dockerfile": ("nl2sql_common.health", "common/nl2sql_common/health.py", "(become or privileges.become)(ACCOUNT)"),
    "review/Dockerfile": ("nl2sql_common.health", "common/nl2sql_common/health.py", "(become or privileges.become)(ACCOUNT)"),
    "ldap/Dockerfile": ("nl2sql_ldap", "ldap/nl2sql_ldap/service.py", "become()\n        return health("),
}


def drop_to(account: str) -> list[str]:
    """How a health check starts in a container that starts as root: as the
    account, in its group, and in no other."""
    return ["CMD", "setpriv", f"--reuid={account}", f"--regid={account}", "--clear-groups"]


def _tracked_dockerfiles() -> set[str]:
    """Every Dockerfile in the working tree that git would track: added since
    the last commit included, deleted since left out."""
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "*Dockerfile", "*.Dockerfile"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    return {name for name in listed.stdout.split("\0") if name and (ROOT / name).is_file()}


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


def _services(compose: str = "docker-compose.yml") -> dict:
    return yaml.safe_load((ROOT / compose).read_text())["services"]


def test_every_health_check_of_a_service_that_starts_as_root_is_started_as_its_account():
    """A service that starts as root to drop is one given SETUID to drop
    with, and that is how they are found here: nine with a health check, and
    the desktop build, which has none. Each check begins with the drop, and
    is a list -- `CMD`, not `CMD-SHELL` -- so that no shell stands in front
    of it as root for as long as it runs."""
    services = _services()
    drops = {name for name, service in services.items() if "SETUID" in service.get("cap_add", [])}
    assert drops == set(CHECKED_AS) | {"desktop"}
    assert "healthcheck" not in services["desktop"]
    for name, account in CHECKED_AS.items():
        test = services[name]["healthcheck"]["test"]
        assert test[:5] == drop_to(account), f"{name}: {test}"
        assert len(test) > 5 and test[5] in ("pg_isready", "python", "python3"), f"{name}: what is run is the check itself"
        assert "SETGID" in services[name]["cap_add"], f"{name}: the drop changes group too"


def test_every_other_health_check_is_in_a_container_that_never_was_root():
    """The API and the console: the agent image, which names its account.
    The pages' check is in their image, which does too. A service given a
    health check from now on is here, or above."""
    services = _services()
    others = {name for name, service in services.items() if "healthcheck" in service} - set(CHECKED_AS)
    assert others == {"api", "console"}
    for name in others:
        assert services[name]["build"]["dockerfile"] in USERS, name
        assert services[name]["healthcheck"]["test"][:2] == ["CMD", "python"], name


def test_the_checks_are_started_as_the_accounts_the_images_make():
    """`setpriv --reuid=nl2sql` in an image with no such account fails, and
    the container is unhealthy for ever. The accounts this repository's
    images make are held here; `postgres` is the stock image's own."""
    made = {
        "nl2sql": ("auth/Dockerfile", "review/Dockerfile"),
        "mlflow": ("docker/mlflow/Dockerfile",),
    }
    for account, dockerfiles in made.items():
        for dockerfile in dockerfiles:
            assert re.search(rf"useradd .*--gid {account} .* {account}\b", (ROOT / dockerfile).read_text()), dockerfile
    # Alpine's openldap package makes `ldap`; its `setpriv` is BusyBox's,
    # which cannot change who a process is, until util-linux's is installed.
    assert re.search(r"^\s+setpriv~", (ROOT / "ldap/Dockerfile").read_text(), re.MULTILINE)
    assert set(CHECKED_AS.values()) == set(made) | {"postgres", "ldap"}


@pytest.mark.parametrize("service", ["chunkdb", "vectordb"])
def test_the_rag_pipelines_own_databases_are_checked_as_postgres_too(service: str):
    """`rag/docker-compose.yml`, the two databases the pipeline builds: the
    same images' parents, started as root the same way."""
    test = _services("rag/docker-compose.yml")[service]["healthcheck"]["test"]
    assert test[:6] == drop_to("postgres") + ["pg_isready"], test


def test_an_image_that_starts_as_root_declares_a_check_that_gives_root_up_itself():
    """Outside compose -- `docker run`, another orchestrator's probe -- the
    image's own HEALTHCHECK is what runs, as whoever the container was
    started as. Each is run directly, with no shell in front of it, and is a
    module whose first act is to give root up."""
    declaring = {name for name in DROPS if re.search(r"^HEALTHCHECK ", (ROOT / name).read_text(), re.MULTILINE)}
    assert declaring == set(SELF_DROPPING)
    for dockerfile, (module, source, does) in SELF_DROPPING.items():
        [check] = re.findall(r"^HEALTHCHECK [^\n]*\\\n\s+CMD (.*)$", (ROOT / dockerfile).read_text(), re.MULTILINE)
        assert check.startswith("[") and f'"-m", "{module}"' in check, f"{dockerfile}: {check}"
        assert does in (ROOT / source).read_text(), f"{source} gives root up by {does!r}"


def test_the_drop_comes_before_anything_else_in_each_check():
    """Held in words here and in behaviour beside each module
    (tests/common/test_health.py, tests/ldap/test_service.py): in the shared
    check the drop is the first statement of `main`; in the directory's, the
    first thing done once the command is known to be `health`, ahead of the
    settings."""
    shared = (ROOT / "common/nl2sql_common/health.py").read_text()
    body = shared.split("def main(", 1)[1]
    assert body.index("privileges.become") < body.index("sys.argv") < body.index("target(")
    directory = (ROOT / "ldap/nl2sql_ldap/service.py").read_text().split("def main(", 1)[1]
    assert directory.index("become()") < directory.index("DirectorySettings.from_env()")
