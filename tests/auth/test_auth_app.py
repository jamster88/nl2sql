"""The auth service's HTTP surface, against fakes.

Signing in is a fake Postgres; the directory is the in-memory one the
directory package is tested on; the role sync is a fake that records being
asked. What is under test is what each route answers, and with what cookie.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from nl2sql_auth import access
from nl2sql_auth import app as app_module
from nl2sql_auth.access import DirectoryUnavailable, PasswordRefused
from nl2sql_auth.app import create_app
from nl2sql_auth.login import SignInError
from nl2sql_auth.rolesync import RoleSync, SyncResult
from nl2sql_auth.settings import AuthSettings
from nl2sql_identity import ADMINS, REVIEWERS, USERS, Guard, GuardSettings, Identity
from nl2sql_identity.tokens import verify
from nl2sql_ldap.directory import Directory, DirectoryError
from nl2sql_ldap.layout import Layout
from nl2sql_ldap.records import UserRecord

from ..ldap.conftest import mock_connection, store_password

KEY = Ed25519PrivateKey.generate()
NOW = 1_800_000_000
SAME = {"Sec-Fetch-Site": "same-origin"}


class FakeLogin:
    def __init__(self):
        self.accounts = {
            "admin": ("admin-password", Identity(user="admin", name="Admin", roles=frozenset({ADMINS, USERS}))),
            "rita": ("rita-password", Identity(user="rita", name="Rita", roles=frozenset({REVIEWERS, USERS}))),
            "uma": ("uma-password", Identity(user="uma", roles=frozenset({USERS}))),
        }
        self.calls = 0
        self.down = False

    def check(self, username, password):
        self.calls += 1
        if self.down:
            raise SignInError("sign_in_unavailable", "cannot reach the retail database")
        found = self.accounts.get(username.strip().lower())
        if found is None or found[0] != password:
            raise SignInError("invalid_credentials", "that name and password were not accepted")
        return found[1]


class FakeSync:
    def __init__(self, last=None):
        self.last = last
        self.triggered = 0
        self.ran = []

    def trigger(self):
        self.triggered += 1

    def run_once(self):
        self.last = SyncResult(at="now", ok=True, people=2, created=["x"])
        return self.last

    def run_forever(self, stop, interval):
        self.ran.append((stop, interval))


class World:
    """The app and everything around it, for one test."""

    def __init__(self, *, settings=None, sync="fake", database_check="ok", guard=None, password_changer=None):
        self.settings = settings or AuthSettings(session_hours=1)
        self.login = FakeLogin()
        self.conn = mock_connection()
        self.layout = Layout(self.settings.ldap_base_dn)
        self.ldap_down = False
        self.changed_passwords: list = []
        directory = Directory(self.conn, self.layout, set_password=store_password)
        directory.ensure_base("nl2sql")
        directory.ensure_groups(["nl2sql-users", "nl2sql-reviewers", "nl2sql-curators", "nl2sql-admins"])
        directory.add_person(UserRecord(uid="admin", display_name="Admin", groups=("nl2sql-admins", "nl2sql-users")))
        directory.add_person(UserRecord(uid="rita", groups=("nl2sql-reviewers",)))
        self.directory = directory
        self.sync = FakeSync() if sync == "fake" else sync

        @contextmanager
        def lend():
            if self.ldap_down:
                raise DirectoryUnavailable("cannot reach the directory at ldap://nl2sql-ldap:389")
            yield self.directory

        def changer(uid, current, new):
            self.changed_passwords.append((uid, current, new))

        checks = {"ok": lambda: "reachable as nl2sql_rolesync", None: None}
        self.app = create_app(
            settings=self.settings,
            signing_key=KEY,
            login=self.login,
            directory=lend,
            password_changer=password_changer or changer,
            rolesync=self.sync,
            guard=guard,
            database_check=checks.get(database_check, database_check),
            clock=lambda: NOW,
        )
        self.client = TestClient(self.app)

    def sign_in(self, name="admin", password=None) -> TestClient:
        password = password or f"{name}-password"
        answer = self.client.post("/auth/login", json={"username": name, "password": password}, headers=SAME)
        assert answer.status_code == 200, answer.text
        return self.client

    def token(self, name="admin") -> dict:
        answer = self.client.post("/auth/token", json={"username": name, "password": f"{name}-password"})
        return {"Authorization": f"Bearer {answer.json()['token']}"}


@pytest.fixture
def world():
    return World()


# --- service -------------------------------------------------------------------


def test_root_names_the_routes(world):
    body = world.client.get("/").json()
    assert body["service"] == "nl2sql-auth" and body["endpoints"]["directory"] == "/directory/v1/meta"
    assert World(settings=AuthSettings(ldap_mode="replica")).client.get("/").json()["endpoints"]["directory"] is None


def test_health(world):
    assert world.client.get("/healthz").json()["status"] == "ok"


def test_ready_when_the_directory_the_database_and_the_sync_answer(world):
    world.sync.last = SyncResult(at="t", ok=True, people=2)
    answer = world.client.get("/readyz")
    assert answer.status_code == 200
    checks = answer.json()["checks"]
    assert checks["directory"] == {"ok": True, "detail": "2 people, standalone"}
    assert checks["database"] == {"ok": True, "detail": "reachable as nl2sql_rolesync"}
    assert checks["role_sync"] == {"ok": True, "detail": "2 people at t"}
    assert "AUTH_ROLESYNC_DB_URL" in " ".join(answer.json()["warnings"])


def test_not_ready_says_which_part_is_not(world):
    world.ldap_down = True
    world.sync.last = SyncResult(at="t", ok=False, errors=["a", "b", "c", "d"])
    answer = world.client.get("/readyz")
    assert answer.status_code == 503
    checks = answer.json()["checks"]
    assert not checks["directory"]["ok"] and "cannot reach" in checks["directory"]["detail"]
    assert checks["role_sync"]["detail"].endswith("; a; b; c")


def test_a_database_check_that_fails_and_a_sync_that_has_not_run(world):
    def broken():
        raise ConnectionError("refused")

    other = World(database_check=broken)
    checks = other.client.get("/readyz").json()["checks"]
    assert checks["database"] == {"ok": False, "detail": "ConnectionError: refused"}
    assert checks["role_sync"] == {"ok": True, "detail": "not run yet"}


def test_without_a_sync_or_a_database_check_readiness_says_so():
    world = World(sync=None, database_check=None)
    checks = world.client.get("/readyz").json()["checks"]
    assert "database" not in checks
    assert checks["role_sync"] == {"ok": False, "detail": "not configured: nobody can be made a role"}


@pytest.mark.parametrize(
    ("status", "ok", "detail"),
    [
        (None, False, "the replica has not copied from its primary yet"),
        ({"ok": True, "upstream": "ldaps://dc1", "at": "t"}, True, "last copy from ldaps://dc1 at t"),
        ({"ok": False, "upstream": "ldaps://dc1", "at": "t", "error": "boom"}, False, "last copy from ldaps://dc1 at t: boom"),
    ],
)
def test_a_replica_reports_its_last_copy(monkeypatch, status, ok, detail):
    monkeypatch.setattr(access, "replica_status", lambda found: status)
    world = World(settings=AuthSettings(ldap_mode="replica"))
    assert world.client.get("/readyz").json()["checks"]["replica"] == {"ok": ok, "detail": detail}


def test_meta_tells_a_form_what_it_needs(world):
    assert world.client.get("/auth/meta").json() == {
        "version": app_module.__version__,
        "mode": "standalone",
        "directory_editable": True,
        "session_hours": 1.0,
        "min_password_length": 12,
    }


def test_errors_and_invalid_bodies_share_one_envelope(world):
    missing = world.client.get("/no/such/route")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"
    invalid = world.client.post("/auth/token", json={"username": "a", "pasword": "typo"})
    assert invalid.status_code == 422
    error = invalid.json()["error"]
    assert error["code"] == "invalid_request" and error["detail"]["errors"]


def test_cors_is_off_unless_origins_are_named():
    world = World(settings=AuthSettings(cors_origins=("https://gui.example",)))
    answer = world.client.options(
        "/auth/meta", headers={"Origin": "https://gui.example", "Access-Control-Request-Method": "GET"}
    )
    assert answer.headers["access-control-allow-origin"] == "https://gui.example"


# --- signing in -----------------------------------------------------------------


def test_a_browser_signs_in_and_holds_the_session_as_a_strict_httponly_cookie(world):
    answer = world.client.post(
        "/auth/login",
        json={"username": "Admin", "password": "admin-password"},
        headers={**SAME, "X-Forwarded-Proto": "https"},
    )
    assert answer.status_code == 200
    assert answer.json() == {
        "user": "admin",
        "name": "Admin",
        "roles": [ADMINS, USERS],
        "kind": "session",
        "expires_at": NOW + 3600,
    }
    cookie = answer.headers["set-cookie"]
    for flag in ("nl2sql_session=", "HttpOnly", "SameSite=strict", "Secure", "Max-Age=3600", "Path=/"):
        assert flag in cookie
    assert "Secure" not in world.client.post(
        "/auth/login", json={"username": "admin", "password": "admin-password"}
    ).headers["set-cookie"], "a plain-HTTP page cannot keep a Secure cookie"


def test_a_sign_in_from_another_site_is_refused(world):
    for headers in ({"Sec-Fetch-Site": "cross-site"}, {"Origin": "https://evil.example"}):
        answer = world.client.post("/auth/login", json={"username": "admin", "password": "admin-password"}, headers=headers)
        assert answer.status_code == 403 and answer.json()["error"]["code"] == "cross_site"


def test_a_wrong_password_is_401_and_too_many_are_429(world):
    for _ in range(5):
        answer = world.client.post("/auth/token", json={"username": "admin", "password": "nope"})
        assert answer.status_code == 401 and answer.json()["error"]["code"] == "invalid_credentials"
    answer = world.client.post("/auth/token", json={"username": "admin", "password": "admin-password"})
    assert answer.status_code == 429 and answer.headers["Retry-After"] == "900"
    assert world.login.calls == 5, "a throttled attempt is not even tried"


def test_the_address_counted_is_the_last_hop_the_proxy_saw(world):
    for hop in range(5):
        world.client.post(
            "/auth/token",
            json={"username": f"guess{hop}", "password": "x"},
            headers={"X-Forwarded-For": f"10.0.0.{hop}, 203.0.113.9"},
        )
    blocked = world.client.post(
        "/auth/token", json={"username": "rita", "password": "rita-password"}, headers={"X-Forwarded-For": "203.0.113.9"}
    )
    assert blocked.status_code == 429
    elsewhere = world.client.post("/auth/token", json={"username": "rita", "password": "rita-password"})
    assert elsewhere.status_code == 200


def test_a_database_that_cannot_be_reached_is_503(world):
    world.login.down = True
    answer = world.client.post("/auth/token", json={"username": "admin", "password": "admin-password"})
    assert answer.status_code == 503 and answer.json()["error"]["code"] == "sign_in_unavailable"


def test_a_token_is_a_session_signed_with_this_services_key(world):
    answer = world.client.post("/auth/token", json={"username": "rita", "password": "rita-password"})
    body = answer.json()
    assert "set-cookie" not in answer.headers
    identity = verify(body["token"], KEY.public_key(), now=NOW)
    assert (identity.user, identity.roles) == ("rita", {REVIEWERS, USERS})
    assert body["expires_at"] == identity.expires_at


def test_the_session_route_answers_for_a_cookie_or_a_bearer(world):
    assert world.client.get("/auth/session").status_code == 401
    world.sign_in("rita")
    assert world.client.get("/auth/session").json()["user"] == "rita"
    plain = TestClient(world.app)
    assert plain.get("/auth/session", headers=world.token("uma")).json()["name"] == "uma"


def test_signing_out_clears_the_cookie(world):
    world.sign_in()
    answer = world.client.post("/auth/logout", headers=SAME)
    assert answer.status_code == 204
    assert 'nl2sql_session=""' in answer.headers["set-cookie"] and "Max-Age=0" in answer.headers["set-cookie"]


# --- your own password ----------------------------------------------------------------


def test_a_person_changes_their_own_password(world):
    world.sign_in("rita")
    answer = world.client.post("/auth/password", json={"current": "rita-password", "new": "a-new-password"}, headers=SAME)
    assert answer.status_code == 204
    assert world.changed_passwords == [("rita", "rita-password", "a-new-password")]


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (PasswordRefused("the current password was not accepted"), 403, "wrong_password"),
        (DirectoryUnavailable("down"), 503, "directory_unavailable"),
        (DirectoryError("constraintViolation", "Password fails quality checking policy"), 422, "constraintViolation"),
        (DirectoryError("busy", "try later"), 502, "busy"),
    ],
)
def test_a_password_change_that_fails_says_why(error, status, code):
    def refuse(uid, current, new):
        raise error

    world = World(password_changer=refuse)
    answer = world.client.post("/auth/password", json={"current": "a", "new": "b"}, headers=world.token("rita"))
    assert answer.status_code == status and answer.json()["error"]["code"] == code


def test_a_replica_has_no_password_to_change_here():
    world = World(settings=AuthSettings(ldap_mode="replica"))
    answer = world.client.post("/auth/password", json={"current": "a", "new": "b"}, headers=world.token("rita"))
    assert answer.status_code == 409 and answer.json()["error"]["code"] == "replica_read_only"


def test_only_a_person_has_a_password_to_change():
    settings = AuthSettings()
    guard = Guard(
        GuardSettings(enabled=True, service_token="machine", service_roles=frozenset({USERS})),
        public_key=KEY.public_key(),
    )
    world = World(settings=settings, guard=guard)
    answer = world.client.post("/auth/password", json={"current": "a", "new": "b"}, headers={"Authorization": "Bearer machine"})
    assert answer.status_code == 403 and answer.json()["error"]["code"] == "not_a_person"


# --- the proxy's question ---------------------------------------------------------------


def test_verify_lets_a_reviewer_through_to_mlflow_and_names_them(world):
    answer = TestClient(world.app).get("/auth/verify", headers=world.token("rita"))
    assert answer.status_code == 204 and answer.headers["X-Auth-User"] == "rita"


def test_verify_refuses_someone_without_the_role(world):
    plain = TestClient(world.app)
    answer = plain.get("/auth/verify", headers=world.token("uma"))
    assert answer.status_code == 403 and "nl2sql_admins, nl2sql_reviewers" in answer.json()["error"]["message"]
    assert plain.get("/auth/verify?role=nl2sql_users", headers=world.token("uma")).status_code == 204
    assert plain.get("/auth/verify").status_code == 401


def test_verify_accepts_an_mlflow_clients_basic_credentials_and_remembers_them(world):
    plain = TestClient(world.app)
    basic = {"Authorization": "Basic " + base64.b64encode(b"rita:rita-password").decode()}
    for _ in range(3):
        answer = plain.get("/auth/verify", headers=basic)
        assert answer.status_code == 204 and answer.headers["X-Auth-User"] == "rita"
    assert world.login.calls == 1, "checked once, then remembered"
    wrong = {"Authorization": "Basic " + base64.b64encode(b"rita:nope").decode()}
    assert plain.get("/auth/verify", headers=wrong).status_code == 401
    garbled = plain.get("/auth/verify", headers={"Authorization": "Basic !!!"})
    assert garbled.status_code == 401 and "cannot be decoded" in garbled.json()["error"]["message"]


def test_a_write_through_the_proxy_riding_on_a_cookie_must_come_from_the_page(world):
    world.sign_in("rita")
    cross = {"X-Original-Method": "POST", "Sec-Fetch-Site": "cross-site"}
    assert world.client.get("/auth/verify", headers=cross).status_code == 403
    same = {"X-Original-Method": "DELETE", "Sec-Fetch-Site": "same-origin"}
    assert world.client.get("/auth/verify", headers=same).status_code == 204
    plain = TestClient(world.app)
    bearer = {**world.token("rita"), "X-Original-Method": "POST", "Sec-Fetch-Site": "cross-site"}
    assert plain.get("/auth/verify", headers=bearer).status_code == 204, "a bearer is never ambient"


# --- the page for what has no page ------------------------------------------------------


def test_the_sign_in_page_signs_in_and_goes_back(world):
    page = world.client.get("/auth/login?next=/%23/experiments")
    assert page.status_code == 200 and 'value="/#/experiments"' in page.text
    answer = world.client.post(
        "/auth/login/form",
        content="username=rita&password=rita-password&next=%2F%23%2Fexperiments",
        headers={"Content-Type": "application/x-www-form-urlencoded", **SAME},
        follow_redirects=False,
    )
    assert answer.status_code == 303 and answer.headers["location"] == "/#/experiments"
    assert "nl2sql_session=" in answer.headers["set-cookie"]


def test_the_sign_in_page_says_what_went_wrong_and_never_goes_elsewhere(world):
    answer = world.client.post(
        "/auth/login/form",
        content="username=rita&password=wrong&next=//evil.example",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert answer.status_code == 401
    assert "not accepted" in answer.text and 'value="/"' in answer.text and 'value="rita"' in answer.text
    empty = world.client.post("/auth/login/form", content="", follow_redirects=False)
    assert empty.status_code == 401


# --- the directory's web interface ----------------------------------------------------


@pytest.fixture
def admin(world):
    world.sign_in("admin")
    world.client.headers.update(SAME)
    return world


def test_the_directory_needs_an_administrator(world):
    assert world.client.get("/directory/v1/people").status_code == 401
    world.sign_in("rita")
    answer = world.client.get("/directory/v1/people")
    assert answer.status_code == 403 and "nl2sql_admins" in answer.json()["error"]["message"]


def test_meta_people_and_groups(admin):
    admin.sync.last = SyncResult(at="t", ok=True, people=2)
    meta = admin.client.get("/directory/v1/meta").json()
    assert meta["people"] == 2 and meta["base_dn"] == "dc=nl2sql,dc=local"
    assert meta["role_sync"]["people"] == 2
    assert {group["name"]: group["role"] for group in meta["groups"]} == {
        "nl2sql-admins": "nl2sql_admins",
        "nl2sql-curators": "nl2sql_curators",
        "nl2sql-reviewers": "nl2sql_reviewers",
        "nl2sql-users": "nl2sql_users",
    }
    people = admin.client.get("/directory/v1/people").json()
    assert people["count"] == 2 and [person["uid"] for person in people["people"]] == ["admin", "rita"]
    assert admin.client.get("/directory/v1/people/rita").json()["groups"] == ["nl2sql-reviewers"]
    missing = admin.client.get("/directory/v1/people/ghost")
    assert missing.status_code == 404
    groups = admin.client.get("/directory/v1/groups").json()["groups"]
    assert next(g for g in groups if g["name"] == "nl2sql-reviewers")["members"] == ["rita"]


def test_meta_before_the_sync_has_run_and_without_a_sync():
    world = World(sync=None)
    world.sign_in("admin")
    assert world.client.get("/directory/v1/meta").json()["role_sync"] is None


def test_adding_a_person_wakes_the_sync(admin):
    answer = admin.client.post(
        "/directory/v1/people",
        json={"uid": "Carol", "given_name": " Carol ", "surname": "King", "mail": "c@x", "groups": ["nl2sql-users"], "password": "a-long-password"},
    )
    assert answer.status_code == 201
    assert answer.json()["uid"] == "carol" and answer.json()["cn"] == "Carol King"
    assert admin.sync.triggered == 1
    again = admin.client.post("/directory/v1/people", json={"uid": "carol"})
    assert again.status_code == 409 and again.json()["error"]["code"] == "already_exists"


def test_a_short_password_is_refused_before_the_directory_is_asked(admin):
    answer = admin.client.post("/directory/v1/people", json={"uid": "dan", "password": "short"})
    assert answer.status_code == 422 and answer.json()["error"]["code"] == "password_too_short"
    assert admin.directory.person("dan") is None


@pytest.mark.parametrize(("uid", "status"), [("Jane Doe", 422), ("postgres", 422)])
def test_an_unusable_login_is_refused(admin, uid, status):
    answer = admin.client.post("/directory/v1/people", json={"uid": uid})
    assert answer.status_code == status and answer.json()["error"]["code"] == "invalid_login"


def test_changing_a_person(admin):
    answer = admin.client.patch(
        "/directory/v1/people/rita",
        json={"display_name": "Rita R", "mail": "r@x", "surname": "Rossi", "groups": ["nl2sql-reviewers", "nl2sql-curators"]},
    )
    assert answer.status_code == 200
    body = answer.json()
    assert (body["display_name"], body["mail"], body["sn"]) == ("Rita R", "r@x", "Rossi")
    assert body["groups"] == ["nl2sql-curators", "nl2sql-reviewers"]
    assert admin.sync.triggered == 1
    only_groups = admin.client.patch("/directory/v1/people/rita", json={"groups": ["nl2sql-users"]})
    assert only_groups.json()["groups"] == ["nl2sql-users"]


def test_changing_someone_who_is_not_there(admin):
    assert admin.client.patch("/directory/v1/people/ghost", json={"mail": "x"}).status_code == 404
    assert admin.client.patch("/directory/v1/people/ghost", json={}).status_code == 404


def test_an_administrator_cannot_lock_themselves_out(admin):
    demote = admin.client.patch("/directory/v1/people/admin", json={"groups": ["nl2sql-users"]})
    assert demote.status_code == 409 and demote.json()["error"]["code"] == "cannot_demote_yourself"
    keep = admin.client.patch("/directory/v1/people/admin", json={"groups": ["nl2sql-admins"]})
    assert keep.status_code == 200
    remove = admin.client.delete("/directory/v1/people/admin")
    assert remove.status_code == 409 and remove.json()["error"]["code"] == "cannot_remove_yourself"


def test_removing_a_person(admin):
    assert admin.client.delete("/directory/v1/people/rita").status_code == 204
    assert admin.directory.person("rita") is None and admin.sync.triggered == 1
    assert admin.client.delete("/directory/v1/people/rita").status_code == 404


def test_setting_a_password_and_unlocking(admin):
    assert admin.client.post("/directory/v1/people/rita/password", json={"password": "short"}).status_code == 422
    assert admin.client.post("/directory/v1/people/rita/password", json={"password": "a-long-password"}).status_code == 204
    assert admin.client.post("/directory/v1/people/rita/unlock").status_code == 204


def test_importing_a_file(admin):
    answer = admin.client.post(
        "/directory/v1/import",
        json={"filename": "people.csv", "content": "uid,groups,password\nzoe,nl2sql-users,zoe-password-1\nBad Name,,\n"},
    )
    body = answer.json()
    assert body["created"] == ["zoe"] and body["groups"] == ["zoe -> nl2sql-users"]
    assert "not a usable login name" in body["problems"][0]
    assert admin.sync.triggered == 1


def test_running_the_sync_now(admin):
    assert admin.client.post("/directory/v1/sync").json()["created"] == ["x"]
    world = World(sync=None)
    world.sign_in("admin")
    answer = world.client.post("/directory/v1/sync", headers=SAME)
    assert answer.status_code == 503 and answer.json()["error"]["code"] == "role_sync_unavailable"


def test_a_directory_that_is_down_or_refuses_is_said_so(admin, monkeypatch):
    admin.ldap_down = True
    assert admin.client.get("/directory/v1/people").status_code == 503
    admin.ldap_down = False

    def refuse(self):
        raise DirectoryError("busy", "the directory is busy")

    monkeypatch.setattr(Directory, "people", refuse)
    answer = admin.client.get("/directory/v1/people")
    assert answer.status_code == 502 and answer.json()["error"]["code"] == "busy"


def test_a_replica_has_no_web_interface():
    world = World(settings=AuthSettings(ldap_mode="replica"))
    world.sign_in("admin")
    for method, path in (("GET", "/directory/v1/people"), ("POST", "/directory/v1/people"), ("DELETE", "/directory/v1/people/rita")):
        answer = world.client.request(method, path, headers=SAME)
        assert answer.status_code == 404 and answer.json()["error"]["code"] == "replica_read_only"
    assert world.client.get("/auth/meta").json()["directory_editable"] is False


# --- what is built when nothing is injected --------------------------------------------


def test_the_sync_runs_beside_the_service_and_is_stopped_with_it(world):
    with TestClient(world.app):
        pass
    [(stop, interval)] = world.sync.ran
    assert interval == 30.0 and stop.is_set()
    assert world.sync.triggered == 1, "woken so it notices the stop"


def test_without_a_sync_nothing_runs_beside_it():
    world = World(sync=None)
    with TestClient(world.app):
        pass


def test_the_defaults_are_the_real_collaborators(monkeypatch):
    lent = []

    @contextmanager
    def fake_directory(settings):
        lent.append(settings)
        directory = Directory(mock_connection(), Layout(settings.ldap_base_dn), set_password=store_password)
        directory.ensure_base("nl2sql")
        yield directory

    changed = []
    monkeypatch.setattr(access, "directory", fake_directory)
    monkeypatch.setattr(access, "change_own_password", lambda settings, *args: changed.append(args))
    settings = AuthSettings(rolesync_url="postgresql://sync:pw@db/retail", ldap_service_password="svc")
    app = create_app(settings=settings, signing_key=KEY)
    assert isinstance(app.state.rolesync, RoleSync)
    assert app.state.rolesync.people() == []
    assert app.state.guard._recheck is not None, "roles are asked of Postgres again"
    client = TestClient(app)
    assert client.get("/readyz").json()["checks"]["directory"]["ok"]

    from nl2sql_identity import sign

    token = sign(Identity(user="rita", roles=frozenset({USERS})), KEY, lifetime_seconds=60)
    monkeypatch.setattr(app.state.guard, "_recheck", lambda user: frozenset({USERS}))
    answer = client.post("/auth/password", json={"current": "a", "new": "b"}, headers={"Authorization": f"Bearer {token}"})
    assert answer.status_code == 204 and changed == [("rita", "a", "b")]
    login = app_module.create_app.__globals__["PostgresLogin"]
    assert login is not None


def test_the_app_needs_its_key():
    with pytest.raises(AssertionError, match="loads the key"):
        create_app(settings=AuthSettings())


def test_an_edit_without_a_sync_configured_still_happens():
    world = World(sync=None)
    world.sign_in("admin")
    answer = world.client.post("/directory/v1/people", json={"uid": "noor"}, headers=SAME)
    assert answer.status_code == 201 and world.directory.person("noor") is not None
