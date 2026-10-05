"""Sign-in end to end: the retail database, the directory, the auth service.

Opt-in (`pytest --run-docker`). Builds the retail database, the directory
and the auth images from this checkout, starts them on a network of their
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
  Basic credentials accepted, a write from another site refused;
* a session ended before it expires (V6-61): signed out, signed in before a
  password an administrator set, or before the directory locked the account
  -- refused by the lookup every service's guard runs, as the reader.

The database is the `v1_2` image as `setup.sh` runs it: passwords from the
environment and none in the image, a certificate of its own, nothing over
the network without TLS, the superuser not over the network at all -- and
the auth service checks passwords against it with `verify-full`.
"""

from __future__ import annotations

import base64
import json
import subprocess
import time
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.docker

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
#: Built from docker/Dockerfile rather than pulled: what is under test is
#: this checkout's entrypoint, and a cached build costs nothing after the first.
RETAIL = "nl2sql-retail-postgres:pytest"
OWNER_PASSWORD = "owner-password-1"
READER_PASSWORD = "reader-password-1"
#: Where the database writes its certificate, and where the auth service and
#: anyone else who verifies it reads it from.
PG_TLS = "/etc/nl2sql/pg-tls"
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
    docker("build", "-q", "-f", str(REPO_ROOT / "docker" / "Dockerfile"), "-t", RETAIL, str(REPO_ROOT))
    docker("build", "-q", "-f", str(REPO_ROOT / "ldap" / "Dockerfile"), "-t", LDAP_IMAGE, str(REPO_ROOT))
    docker("build", "-q", "-f", str(REPO_ROOT / "auth" / "Dockerfile"), "-t", AUTH_IMAGE, str(REPO_ROOT))
    net = f"nl2sql-signin-{uuid.uuid4().hex[:8]}"
    tls = f"{net}-ldaptls"
    pgtls = f"{net}-pgtls"
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
            "-v", f"{tls}:/etc/nl2sql/ldap-tls:ro", "-e", "LDAPTLS_CACERT=/etc/nl2sql/ldap-tls/ldap.crt",
            "-v", f"{pgtls}:{PG_TLS}", "-e", f"POSTGRES_PASSWORD={OWNER_PASSWORD}",
            "-e", f"POSTGRES_READER_PASSWORD={READER_PASSWORD}", RETAIL,
        )
        # Over TCP: the server the entrypoint sets passwords through answers
        # on the socket only, so a socket check can catch it before it stops.
        wait_until(
            lambda: docker("exec", names["pg"], "pg_isready", "-q", "-h", "127.0.0.1", "-d", "nl2sql_retail", check=False).returncode == 0
            and docker("inspect", "-f", "{{.State.Health.Status}}", names["ldap"], check=False).stdout.strip() == "healthy",
            "the database and the directory",
        )
        psql = ["exec", "-i", "-u", "postgres", names["pg"], "psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", "nl2sql_retail"]
        docker(*psql[:2], "-e", f"NL2SQL_READER_PASSWORD={READER_PASSWORD}", *psql[2:],
               "-v", "reader=nl2sql_reader", "-v", "owner=nl2sql",
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
            "-v", f"{pgtls}:{PG_TLS}:ro",
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
        docker("volume", "rm", tls, pgtls, check=False)


#: Run inside the auth container, which has Python and nothing else needed.
def apply_auth_roles(pg: str) -> None:
    """docker/auth_roles.sql, as launch.sh applies it on every start."""
    docker(
        "exec", "-i", "-u", "postgres", "-e", "NL2SQL_ROLESYNC_PASSWORD=sync-password-1", pg,
        "psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", "nl2sql_retail",
        "-v", "reader=nl2sql_reader", "-v", "owner=nl2sql", "-v", "rolesync=nl2sql_rolesync",
        "-f", "-", input=(REPO_ROOT / "docker" / "auth_roles.sql").read_text(),
    )


REQUEST = """
import json, os, urllib.request, urllib.error
body = os.environ["BODY"]
request = urllib.request.Request(
    "http://127.0.0.1:" + os.environ["PORT"] + os.environ["ROUTE"], method=os.environ["METHOD"],
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


def call(stack, method: str, path: str, body=None, headers=None, port: int | None = None) -> tuple[int, dict | None]:
    """A request to the auth service, from inside it. The directory's API
    answers on its own port (8447), which only the directory page reaches;
    everything else on 8446."""
    if port is None:
        port = 8447 if path.startswith("/directory/") else 8446
    result = docker(
        "exec", "-i", "-e", f"METHOD={method}", "-e", f"ROUTE={path}", "-e", f"PORT={port}",
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


def sql_as(stack, user: str, password: str, query: str, sslmode: str = "verify-full") -> subprocess.CompletedProcess:
    """psql over the network, as anyone outside the container connects: by
    the database's name, verifying its certificate."""
    return docker(
        "exec", "-e", f"PGPASSWORD={password}", "-e", f"PGSSLMODE={sslmode}", "-e", f"PGSSLROOTCERT={PG_TLS}/server.crt",
        stack["pg"], "psql", "-X", "-At", "-h", "nl2sql-postgres", "-U", user, "-d", "nl2sql_retail", "-c", query, check=False,
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
    roles = sql_as(stack, "nl2sql_reader", READER_PASSWORD, "SELECT count(*) FROM pg_roles WHERE rolname = 'alice'")
    assert roles.stdout.strip() == "0"


def test_the_reader_keeps_its_password_and_becomes_a_person_only_for_a_transaction(stack):
    statement = "BEGIN; SET TRANSACTION READ ONLY; SET LOCAL ROLE admin; SELECT current_user, session_user; COMMIT;"
    result = sql_as(stack, "nl2sql_reader", READER_PASSWORD, statement)
    assert "admin|nl2sql_reader" in result.stdout, result.stderr
    refused = sql_as(stack, "nl2sql_reader", READER_PASSWORD, "SET ROLE nl2sql_admins")
    assert "permission denied" in refused.stderr, "a group cannot be become, only a person"


def test_the_directory_api_is_not_answered_on_the_sign_in_port(stack):
    """8446 is published; 8447, where the directory's API answers, is not,
    and only the directory page proxies to it."""
    admin = token(stack, "admin", "admin-password-1")
    assert call(stack, "GET", "/directory/v1/people", headers=admin, port=8446)[0] == 404
    assert call(stack, "GET", "/directory/v1/people", headers=admin)[0] == 200


def test_nothing_reaches_the_database_over_the_network_in_clear_text(stack):
    refused = sql_as(stack, "nl2sql_reader", READER_PASSWORD, "SELECT 1", sslmode="disable")
    assert refused.returncode != 0
    assert "no encryption" in refused.stderr, refused.stderr
    assert sql_as(stack, "nl2sql_reader", READER_PASSWORD, "SELECT 1").stdout.strip() == "1"


def test_the_superuser_is_refused_over_the_network_whatever_the_password(stack):
    refused = sql_as(stack, "postgres", OWNER_PASSWORD, "SELECT 1")
    assert refused.returncode != 0
    assert "rejects connection" in refused.stderr, refused.stderr


def test_the_passwords_are_the_environments_and_none_is_baked_in(stack):
    assert sql_as(stack, "nl2sql", OWNER_PASSWORD, "SELECT current_user").stdout.strip() == "nl2sql"
    for user, old in (("nl2sql", "nl2sql"), ("nl2sql_reader", "nl2sql_reader")):
        refused = sql_as(stack, user, old, "SELECT 1")
        assert "password authentication failed" in refused.stderr, f"{user} still takes {old!r}"


def test_the_certificate_is_the_databases_own_and_names_it(stack):
    """What the auth service verifies against: written on first start into
    the volume, for the names it is reached by, and kept."""
    subject = docker("exec", stack["pg"], "openssl", "x509", "-in", f"{PG_TLS}/server.crt", "-noout", "-ext", "subjectAltName").stdout
    assert "DNS:nl2sql-postgres" in subject and "DNS:localhost" in subject
    assert sql_as(stack, "nl2sql_reader", READER_PASSWORD, "SHOW ssl").stdout.strip() == "on"


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



# --- ending a session before it expires (V6-61) ----------------------------------


def claims(headers: dict[str, str]) -> dict:
    """The claims of the bearer token in `headers`, unverified: only read."""
    payload = headers["Authorization"].split()[1].split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


#: The recheck the API, the console and the review service run, as they run
#: it: as the reader, over verified TLS. Inside the auth container, which has
#: the same package installed.
LOOKUP = """
import json, os
from nl2sql_identity import Identity
from nl2sql_identity.postgres import membership_lookup
lookup = membership_lookup(os.environ["URL"])
standing = lookup(Identity(user=os.environ["WHO"], token_id=os.environ["JTI"], issued_at=int(os.environ["IAT"])))
print(json.dumps([sorted(standing.roles or []), standing.revoked]))
"""


def standing(stack, user: str, headers: dict[str, str]) -> tuple[list[str], bool]:
    session = claims(headers)
    url = (
        f"postgresql://nl2sql_reader:{READER_PASSWORD}@nl2sql-postgres:5432/nl2sql_retail"
        f"?sslmode=verify-full&sslrootcert={PG_TLS}/server.crt"
    )
    result = docker(
        "exec", "-i", "-e", f"URL={url}", "-e", f"WHO={user}", "-e", f"JTI={session['jti']}",
        "-e", f"IAT={session['iat']}", stack["auth"], "python", "-", input=LOOKUP,
    )
    roles, revoked = json.loads(result.stdout.strip().splitlines()[-1])
    return roles, revoked


def add_person(stack, admin: dict[str, str], uid: str, password: str) -> None:
    status, body = call(
        stack, "POST", "/directory/v1/people",
        {"uid": uid, "given_name": uid.title(), "surname": "Test", "groups": ["nl2sql-users"], "password": password},
        admin,
    )
    assert status == 201, body
    wait_until(lambda: call(stack, "POST", "/auth/token", {"username": uid, "password": password})[0] == 200,
               f"the sync to make {uid} a role")


def refused_as_revoked(stack, headers: dict[str, str]) -> bool:
    status, body = call(stack, "GET", "/auth/session", headers=headers)
    return status == 401 and body["error"]["code"] == "session_revoked"


def test_the_reader_may_ask_about_one_session_and_read_no_list(stack):
    asked = sql_as(stack, "nl2sql_reader", READER_PASSWORD, "SELECT nl2sql_auth.session_revoked('nobody', 'none', 0)")
    assert asked.stdout.strip() == "f", asked.stderr
    for table in ("revoked_sessions", "session_cutoffs"):
        refused = sql_as(stack, "nl2sql_reader", READER_PASSWORD, f"SELECT count(*) FROM nl2sql_auth.{table}")
        assert "permission denied" in refused.stderr, (table, refused.stderr)
    wait_until(lambda: sql_as(stack, "admin", "admin-password-1", "SELECT 1").returncode == 0, "the administrator's role")
    person = sql_as(stack, "admin", "admin-password-1", "SELECT nl2sql_auth.session_revoked('admin', 'x', 0)")
    assert "permission denied" in person.stderr, "a person's own SQL cannot ask"


def test_a_signed_out_session_is_refused_everywhere_and_only_that_one(stack):
    admin = token(stack, "admin", "admin-password-1")
    assert call(stack, "GET", "/auth/session", headers=admin)[0] == 200
    assert standing(stack, "admin", admin)[1] is False
    assert call(stack, "POST", "/auth/logout", headers=admin)[0] == 204
    assert refused_as_revoked(stack, admin), "here at once"
    assert standing(stack, "admin", admin)[1] is True, "and by every service's recheck"
    again = token(stack, "admin", "admin-password-1")
    assert call(stack, "GET", "/auth/session", headers=again)[0] == 200, "the other sessions are not touched"


def test_a_password_an_administrator_sets_ends_that_persons_sessions(stack):
    admin = token(stack, "admin", "admin-password-1")
    add_person(stack, admin, "bob", "bob-password-1")
    before = token(stack, "bob", "bob-password-1")
    assert call(stack, "GET", "/auth/session", headers=before)[0] == 200
    assert call(stack, "POST", "/directory/v1/people/bob/password", {"password": "bob-password-2"}, admin)[0] == 204
    assert refused_as_revoked(stack, before)
    assert standing(stack, "bob", before) == (["nl2sql_users"], True)
    after = token(stack, "bob", "bob-password-2")
    assert call(stack, "GET", "/auth/session", headers=after)[0] == 200, "signed in straight after, and kept"
    assert call(stack, "DELETE", "/directory/v1/people/bob", headers=admin)[0] == 204


def test_a_lockout_ends_the_sessions_from_before_it(stack):
    admin = token(stack, "admin", "admin-password-1")
    add_person(stack, admin, "carol", "carol-password-1")
    before = token(stack, "carol", "carol-password-1")
    time.sleep(1.1)  # the lock is after the second this was signed in
    for _ in range(5):
        sql_as(stack, "carol", "wrong-password", "SELECT 1")
    status, body = call(stack, "GET", "/directory/v1/people/carol", headers=admin)
    assert status == 200 and body["locked"], "the directory locked her after five wrong passwords"
    wait_until(lambda: standing(stack, "carol", before)[1], "the role sync to record the lock", seconds=30)
    assert refused_as_revoked(stack, before)
    assert call(stack, "POST", "/directory/v1/people/carol/unlock", headers=admin)[0] == 204
    after = token(stack, "carol", "carol-password-1")
    assert call(stack, "GET", "/auth/session", headers=after)[0] == 200, "a session after the unlock is not refused"
    assert call(stack, "DELETE", "/directory/v1/people/carol", headers=admin)[0] == 204
