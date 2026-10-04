"""Sign-in end to end: the retail database, the directory, the auth service.

Opt-in (`pytest --run-docker`). Builds the directory and auth images, starts
them beside a copy of the published retail database on a network of their
own -- nothing published to the host, nothing shared with a running stack --
prepares the database the way launch.sh does (docker/reader_role.sql,
docker/auth_roles.sql, docker/ldap_hba.sh), and then signs people in:

* the first administrator, whom the directory made on its first start and
  the role sync made a role;
* a person an administrator adds through the directory's web interface, who
  can sign in as soon as the sync has run, reads the retail tables as their
  own role, and stops being able to once they are removed;
* the agent's reader, which keeps its own password, and becomes a person
  only for a transaction (SET ROLE);
* MLflow behind its proxy: a browser sent to sign in, an MLflow client's
  Basic credentials accepted, a write from another site refused.
"""

from __future__ import annotations

import json
import subprocess
import time
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.docker

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RETAIL = "mcfaddja/nl2sql-retail-postgres:v1_1"
LDAP_IMAGE = "nl2sql-ldap:pytest"
AUTH_IMAGE = "nl2sql-auth:pytest"
PROXY_IMAGE = "nl2sql-mlflow-proxy:pytest"
#: MLflow's own server, as the last release published it -- a tag that exists
#: to pull before this one is pushed. Only its HTTP surface is used here,
#: against a throwaway SQLite store.
MLFLOW = "mcfaddja/nl2sql-mlflow:v5_6_1"


def docker(*args: str, check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check, **kwargs)


def wait_until(predicate, what: str, seconds: int = 60) -> None:
    for _ in range(seconds):
        if predicate():
            return
        time.sleep(1)
    pytest.fail(f"timed out waiting for {what}")


@pytest.fixture(scope="module")
def stack(docker_daemon_available):
    if not docker_daemon_available:
        pytest.skip("no Docker daemon")
    docker("build", "-q", "-f", str(REPO_ROOT / "ldap" / "Dockerfile"), "-t", LDAP_IMAGE, str(REPO_ROOT))
    docker("build", "-q", "-f", str(REPO_ROOT / "auth" / "Dockerfile"), "-t", AUTH_IMAGE, str(REPO_ROOT))
    net = f"nl2sql-signin-{uuid.uuid4().hex[:8]}"
    tls = f"{net}-ldaptls"
    names = {part: f"{net}-{part}" for part in ("ldap", "pg", "auth", "mlflow", "proxy")}
    docker("network", "create", net)
    try:
        docker(
            "run", "-d", "--name", names["ldap"], "--hostname", "nl2sql-ldap", "--network", net,
            "--network-alias", "nl2sql-ldap", "-v", f"{tls}:/etc/nl2sql/ldap-tls",
            "-e", "LDAP_SERVICE_PASSWORD=svc-password-1", "-e", "LDAP_ADMIN_PASSWORD=admin-password-1", LDAP_IMAGE,
        )
        docker(
            "run", "-d", "--name", names["pg"], "--network", net, "--network-alias", "nl2sql-postgres",
            "-v", f"{tls}:/etc/nl2sql/ldap-tls:ro", "-e", "LDAPTLS_CACERT=/etc/nl2sql/ldap-tls/ldap.crt", RETAIL,
        )
        wait_until(
            lambda: docker("exec", names["pg"], "pg_isready", "-q", "-U", "nl2sql", "-d", "nl2sql_retail", check=False).returncode == 0
            and docker("inspect", "-f", "{{.State.Health.Status}}", names["ldap"], check=False).stdout.strip() == "healthy",
            "the database and the directory",
        )
        time.sleep(2)  # the image's entrypoint restarts the server once after its first check
        psql = ["exec", "-i", "-u", "postgres", names["pg"], "psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", "nl2sql_retail"]
        docker(*psql, "-v", "reader=nl2sql_reader", "-v", "reader_password=nl2sql_reader", "-v", "owner=nl2sql",
               "-f", "-", input=(REPO_ROOT / "docker" / "reader_role.sql").read_text())
        apply_auth_roles(names["pg"])
        docker(
            "exec", "-i", "-u", "postgres", "-e", "NL2SQL_SIGNIN=on", "-e", "NL2SQL_DB=nl2sql_retail",
            "-e", "NL2SQL_SERVICE_ROLES=nl2sql_reader,nl2sql_rolesync", "-e", "NL2SQL_LDAP_HOST=nl2sql-ldap",
            "-e", "NL2SQL_LDAP_BASE_DN=dc=nl2sql,dc=local", names["pg"], "sh", "-s",
            input=(REPO_ROOT / "docker" / "ldap_hba.sh").read_text(),
        )
        docker(
            "run", "-d", "--name", names["auth"], "--network", net, "-v", f"{tls}:/etc/nl2sql/ldap-tls:ro",
            "-e", "AUTH_TLS_ENABLED=false", "-e", "AUTH_ROLE_SYNC_INTERVAL=2",
            "-e", "AUTH_ROLESYNC_DB_URL=postgresql://nl2sql_rolesync:sync-password-1@nl2sql-postgres:5432/nl2sql_retail",
            "-e", "LDAP_SERVICE_PASSWORD=svc-password-1", AUTH_IMAGE,
        )
        docker("build", "-q", "-f", str(REPO_ROOT / "docker" / "mlflow-proxy" / "Dockerfile"), "-t", PROXY_IMAGE, str(REPO_ROOT))
        docker(
            "run", "-d", "--name", names["mlflow"], "--network", net, "--network-alias", "nl2sql-mlflow", MLFLOW,
            "mlflow", "server", "--host", "0.0.0.0", "--port", "5000", "--backend-store-uri", "sqlite:////tmp/mlflow.db",
            "--allowed-hosts", "nl2sql-mlflow:*",
        )
        docker(
            "run", "-d", "--name", names["proxy"], "--network", net, "-e", "AUTH_ENABLED=true",
            "-e", "MLFLOW_PROXY_TLS_ENABLED=false", "-e", f"AUTH_UPSTREAM=http://{names['auth']}:8446", PROXY_IMAGE,
        )
        yield names
    finally:
        for container in names.values():
            docker("rm", "-f", container, check=False)
        docker("network", "rm", net, check=False)
        docker("volume", "rm", tls, check=False)


#: Run inside the auth container, which has Python and nothing else needed.
def apply_auth_roles(pg: str) -> None:
    """docker/auth_roles.sql, as launch.sh applies it on every start."""
    docker(
        "exec", "-i", "-u", "postgres", pg, "psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", "nl2sql_retail",
        "-v", "reader=nl2sql_reader", "-v", "owner=nl2sql", "-v", "rolesync=nl2sql_rolesync",
        "-v", "rolesync_password=sync-password-1", "-f", "-", input=(REPO_ROOT / "docker" / "auth_roles.sql").read_text(),
    )


REQUEST = """
import json, os, urllib.request, urllib.error
body = os.environ["BODY"]
request = urllib.request.Request(
    "http://127.0.0.1:8446" + os.environ["ROUTE"], method=os.environ["METHOD"],
    data=body.encode() if body else None,
    headers={"Content-Type": "application/json", **json.loads(os.environ["HEADERS"])},
)
try:
    with urllib.request.urlopen(request, timeout=20) as answer:
        text, status = answer.read().decode(), answer.status
except urllib.error.HTTPError as error:
    text, status = error.read().decode(), error.code
print(json.dumps([status, json.loads(text) if text.startswith("{") else None]))
"""


def call(stack, method: str, path: str, body=None, headers=None) -> tuple[int, dict | None]:
    result = docker(
        "exec", "-i", "-e", f"METHOD={method}", "-e", f"ROUTE={path}",
        "-e", f"BODY={json.dumps(body) if body is not None else ''}", "-e", f"HEADERS={json.dumps(headers or {})}",
        stack["auth"], "python", "-", input=REQUEST, check=False,
    )
    if result.returncode != 0:
        return 0, None
    status, payload = json.loads(result.stdout.strip().splitlines()[-1])
    return status, payload


def token(stack, user: str, password: str) -> dict[str, str]:
    status, body = call(stack, "POST", "/auth/token", {"username": user, "password": password})
    assert status == 200, body
    return {"Authorization": f"Bearer {body['token']}"}


def sql_as(stack, user: str, password: str, query: str) -> subprocess.CompletedProcess:
    return docker(
        "exec", "-e", f"PGPASSWORD={password}", stack["pg"], "psql", "-X", "-At", "-h", "localhost",
        "-U", user, "-d", "nl2sql_retail", "-c", query, check=False,
    )


def test_the_first_administrator_signs_in_through_the_database(stack):
    wait_until(lambda: call(stack, "GET", "/healthz")[0] == 200, "the auth service")
    wait_until(lambda: call(stack, "POST", "/auth/token", {"username": "admin", "password": "admin-password-1"})[0] == 200,
               "the sync to make the administrator a role")
    status, body = call(stack, "POST", "/auth/token", {"username": "admin", "password": "admin-password-1"})
    assert body["roles"] == ["nl2sql_admins", "nl2sql_curators", "nl2sql_reviewers", "nl2sql_users"]
    assert body["name"] == "Directory administrator"
    assert call(stack, "POST", "/auth/token", {"username": "admin", "password": "wrong"})[0] == 401


def test_preparing_the_database_again_on_a_later_start_changes_no_membership(stack):
    """launch.sh applies auth_roles.sql on every start, as the superuser. Its
    repairs once granted again what the sync already had, which Postgres
    records as a second membership, with the superuser as its grantor."""
    memberships = [
        "exec", "-u", "postgres", stack["pg"], "psql", "-X", "-At", "-d", "nl2sql_retail", "-c",
        "SELECT r.rolname, u.rolname, g.rolname, m.admin_option, m.inherit_option, m.set_option "
        "FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.roleid JOIN pg_roles u ON u.oid = m.member "
        "JOIN pg_roles g ON g.oid = m.grantor ORDER BY 1, 2, 3",
    ]
    wait_until(lambda: "admin|nl2sql_reader" in docker(*memberships).stdout, "the sync to make the administrator a role")
    before = docker(*memberships).stdout
    apply_auth_roles(stack["pg"])
    assert docker(*memberships).stdout == before
    reader_rows = [line for line in before.splitlines() if line.startswith("admin|nl2sql_reader|")]
    assert len(reader_rows) == 1 and reader_rows[0].endswith("|f|f|t"), reader_rows


def test_a_person_added_in_the_web_interface_can_sign_in_read_and_be_removed(stack):
    admin = token(stack, "admin", "admin-password-1")
    status, body = call(
        stack, "POST", "/directory/v1/people",
        {"uid": "alice", "given_name": "Alice", "surname": "Smith", "groups": ["nl2sql-users"], "password": "alice-password-1"},
        admin,
    )
    assert status == 201, body
    wait_until(lambda: sql_as(stack, "alice", "alice-password-1", "SELECT 1").returncode == 0, "alice's role")
    reads = sql_as(stack, "alice", "alice-password-1", "SELECT current_user, count(*) > 0 FROM dim_store")
    assert reads.stdout.strip() == "alice|t", reads.stderr
    writes = sql_as(stack, "alice", "alice-password-1", "CREATE TEMP TABLE t (x int)")
    assert "read-only transaction" in writes.stderr

    assert call(stack, "DELETE", "/directory/v1/people/alice", headers=admin)[0] == 204
    wait_until(lambda: sql_as(stack, "alice", "alice-password-1", "SELECT 1").returncode != 0, "alice's role to go")
    roles = sql_as(stack, "nl2sql_reader", "nl2sql_reader", "SELECT count(*) FROM pg_roles WHERE rolname = 'alice'")
    assert roles.stdout.strip() == "0"


def test_the_reader_keeps_its_password_and_becomes_a_person_only_for_a_transaction(stack):
    statement = "BEGIN; SET TRANSACTION READ ONLY; SET LOCAL ROLE admin; SELECT current_user, session_user; COMMIT;"
    result = sql_as(stack, "nl2sql_reader", "nl2sql_reader", statement)
    assert "admin|nl2sql_reader" in result.stdout, result.stderr
    refused = sql_as(stack, "nl2sql_reader", "nl2sql_reader", "SET ROLE nl2sql_admins")
    assert "permission denied" in refused.stderr, "a group cannot be become, only a person"


def curl(stack, *args: str) -> str:
    """`curl -w code` through the MLflow proxy, from inside it."""
    result = docker("exec", stack["proxy"], "curl", "-s", "-o", "/dev/null", "-w", "%{http_code} %{redirect_url}", *args, check=False)
    return result.stdout.strip()


def test_mlflow_is_behind_sign_in(stack):
    base = "http://127.0.0.1:5001"
    api = f"{base}/api/2.0/mlflow/experiments/search?max_results=1"
    wait_until(lambda: curl(stack, f"{base}/health").startswith("200"), "MLflow behind its proxy")
    assert curl(stack, f"{base}/") == f"302 {base}/auth/login?next=/", "a browser is sent to sign in"
    assert curl(stack, api).startswith("401"), "an API client is told, not redirected"
    good = "admin:admin-password-1"
    assert curl(stack, "-u", good, api).startswith("200"), "MLFLOW_TRACKING_USERNAME and _PASSWORD work"
    assert curl(stack, "-u", "admin:wrong", api).startswith("401")
    docker(
        "exec", stack["proxy"], "curl", "-s", "-c", "/tmp/jar", "-H", "Sec-Fetch-Site: same-origin",
        "--data", "username=admin&password=admin-password-1&next=%2F", f"{base}/auth/login/form",
    )
    assert curl(stack, "-b", "/tmp/jar", f"{base}/").startswith("200"), "signed in through the page"
    create = f"{base}/api/2.0/mlflow/experiments/create"
    body = ["-X", "POST", "-H", "Content-Type: application/json", "--data", '{"name": "from-the-test"}']
    assert curl(stack, "-b", "/tmp/jar", "-H", "Sec-Fetch-Site: cross-site", *body, create).startswith("403")
    assert curl(stack, "-b", "/tmp/jar", "-H", "Sec-Fetch-Site: same-origin", *body, create).startswith("200")

