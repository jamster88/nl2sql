"""The signing key, signing in through Postgres, the throttle and the sign-in page."""

from __future__ import annotations

import stat

import psycopg
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from nl2sql_auth import keys
from nl2sql_auth.login import REFUSED_MESSAGE, WHO_SQL, PostgresLogin, SignInError, display_name, roles_named
from nl2sql_auth.pages import login_page, safe_next
from nl2sql_auth.throttle import Throttle
from nl2sql_identity import ROLES
from nl2sql_identity.tokens import load_public_key

# --- the key ---------------------------------------------------------------


def test_a_key_is_made_on_first_start_and_kept(tmp_path):
    private, public = tmp_path / "data" / "session.key", tmp_path / "keys" / "session.pub"
    key, note = keys.load_or_create(str(private), str(public))
    assert note.startswith(f"made a signing key at {private}")
    assert stat.S_IMODE(private.stat().st_mode) == 0o600
    assert stat.S_IMODE(public.stat().st_mode) == 0o644
    assert load_public_key(public) == key.public_key()
    again, note = keys.load_or_create(str(private), str(public))
    assert note.startswith("signing with the key at")
    assert again.private_bytes_raw() == key.private_bytes_raw()


def test_the_public_half_is_rewritten_every_start(tmp_path):
    private, public = tmp_path / "session.key", tmp_path / "session.pub"
    key, _ = keys.load_or_create(str(private), str(public))
    public.write_text("stale")
    keys.load_or_create(str(private), str(public))
    assert load_public_key(public) == key.public_key()


def test_a_key_file_of_another_kind_is_refused(tmp_path):
    private = tmp_path / "session.key"
    other = ec.generate_private_key(ec.SECP256R1())
    private.write_bytes(
        other.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    with pytest.raises(keys.SigningKeyError, match="not an Ed25519 one"):
        keys.load_or_create(str(private), str(tmp_path / "session.pub"))


# --- signing in --------------------------------------------------------------


class FakeConn:
    def __init__(self, row):
        self.row = row
        self.asked = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params):
        self.asked.append((query, params))
        return self

    def fetchone(self):
        return self.row


def _login(outcome):
    seen = {}

    def connect(**options):
        seen.update(options)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return PostgresLogin(host="db", port=5432, dbname="retail", connect=connect), seen


def test_a_password_postgres_accepts_is_a_session():
    conn = FakeConn(("alice", "Alice Smith <alice@x>", ["nl2sql_reviewers", "nl2sql_users"]))
    login, seen = _login(conn)
    identity = login.check("  Alice ", "pw")
    assert (identity.user, identity.name) == ("alice", "Alice Smith")
    assert identity.roles == {"nl2sql_reviewers", "nl2sql_users"}
    assert seen["user"] == "alice" and seen["password"] == "pw" and seen["sslmode"] == "prefer"
    assert seen["application_name"] == "nl2sql-auth sign-in"
    assert conn.asked == [(WHO_SQL, {"roles": list(ROLES)})]
    assert login.describe() == "db:5432/retail (sslmode=prefer)"


@pytest.mark.parametrize(("username", "password"), [("", "pw"), ("alice", ""), ("Jane Doe", "pw"), ("postgres", "pw")])
def test_names_that_cannot_be_people_are_refused_without_asking(username, password):
    login, seen = _login(AssertionError("asked"))
    with pytest.raises(SignInError) as caught:
        login.check(username, password)
    assert caught.value.code == "invalid_credentials" and str(caught.value) == REFUSED_MESSAGE
    assert seen == {}


class Refusal(psycopg.OperationalError):
    def __init__(self, message, sqlstate=None):
        self._sqlstate = sqlstate  # first: psycopg's own __init__ reads it
        super().__init__(message)

    @property
    def sqlstate(self):
        return self._sqlstate


@pytest.mark.parametrize(
    "error",
    [
        Refusal('connection failed: FATAL:  password authentication failed for user "alice"'),
        Refusal('connection failed: FATAL:  LDAP authentication failed for user "alice"'),
        Refusal("FATAL:  no pg_hba.conf entry for host"),
        Refusal("refused", sqlstate="28P01"),
    ],
    ids=["password", "ldap", "no-rule", "sqlstate"],
)
def test_a_refusal_never_says_which_kind(error):
    login, _ = _login(error)
    with pytest.raises(SignInError) as caught:
        login.check("alice", "pw")
    assert caught.value.code == "invalid_credentials" and str(caught.value) == REFUSED_MESSAGE


def test_a_database_that_cannot_be_reached_is_unavailable():
    login, _ = _login(Refusal("connection failed: Connection refused\n\tIs the server running?"))
    with pytest.raises(SignInError) as caught:
        login.check("alice", "pw")
    assert caught.value.code == "sign_in_unavailable"
    assert str(caught.value).endswith("connection failed: Connection refused")


def test_an_error_with_no_words_is_named_by_its_type():
    login, _ = _login(Refusal(""))
    with pytest.raises(SignInError, match="Refusal"):
        login.check("alice", "pw")


@pytest.mark.parametrize(
    ("comment", "name"),
    [("Alice Smith <a@x>", "Alice Smith"), ("Alice Smith", "Alice Smith"), ("", "")],
)
def test_the_display_name_is_read_from_the_role_comment(comment, name):
    assert display_name(comment) == name


def test_a_session_may_carry_a_mapped_role_as_well_as_the_four():
    assert roles_named(["nl2sql_users", "analysts_role"]) == tuple(sorted({*ROLES, "analysts_role"}))


# --- the throttle -------------------------------------------------------------


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_the_throttle_lets_failures_through_up_to_the_limit_then_waits():
    clock = Clock()
    throttle = Throttle(3, 60, clock=clock)
    for _ in range(2):
        throttle.failed("name:a", "address:1")
        clock.now += 1
    assert throttle.wait("name:a") == 0
    throttle.failed("name:a", "address:1")
    assert throttle.wait("name:a") == 58, "until the oldest of the three ages out"
    assert throttle.wait("name:b", "address:1") == 58, "the address is counted too"
    clock.now += 30.5
    assert throttle.wait("name:a") == 28
    clock.now += 60
    assert throttle.wait("name:a", "address:1") == 0
    assert throttle._seen == {}, "old failures are forgotten as they age out"


def test_success_clears_the_name_and_zero_failures_means_no_throttle():
    clock = Clock()
    throttle = Throttle(1, 60, clock=clock)
    throttle.failed("name:a")
    throttle.succeeded("name:a")
    assert throttle.wait("name:a") == 0
    off = Throttle(0, 60, clock=clock)
    off.failed("name:a")
    assert off.wait("name:a") == 0


def test_an_address_has_a_limit_of_its_own():
    """Everyone behind one proxy is one address: a name's five would let one
    person's typing lock the rest out."""
    clock = Clock()
    throttle = Throttle(2, 60, clock=clock, per_kind={"address": 4})
    for name in ("a", "b", "c"):
        throttle.failed(f"name:{name}", "address:office")
    assert throttle.wait("name:d", "address:office") == 0, "three is under the address's four"
    assert throttle.wait("name:a") == 0 and throttle.limit("name:a") == 2
    throttle.failed("name:d", "address:office")
    assert throttle.wait("name:e", "address:office") == 60
    off = Throttle(2, 60, clock=clock, per_kind={"address": 0})
    for _ in range(5):
        off.failed("address:office")
    assert off.wait("address:office") == 0, "0 is no limit for that kind"


def test_the_throttle_reads_a_monotonic_clock_by_default():
    assert Throttle(1, 1).wait("x") == 0


# --- the page -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("target", "safe"),
    [
        ("/#/experiments", "/#/experiments"),
        ("/ajax-api/2.0/x?y=1", "/ajax-api/2.0/x?y=1"),
        ("//evil.example", "/"),
        ("/\\evil.example", "/"),
        ("https://evil.example", "/"),
        ("", "/"),
        (None, "/"),
    ],
)
def test_only_a_path_on_this_site_is_somewhere_to_go_back_to(target, safe):
    assert safe_next(target) == safe


def test_the_page_escapes_what_it_echoes():
    page = login_page(next_path='/x"><script>', message="<b>no</b>", username='a"b')
    assert "<script>" not in page and "&lt;b&gt;no&lt;/b&gt;" in page
    assert 'value="a&quot;b"' in page
    assert 'name="next" value="/x&quot;&gt;&lt;script&gt;"' in page
    assert 'action="login/form"' in page
    assert 'role="alert"' not in login_page(next_path="/")
