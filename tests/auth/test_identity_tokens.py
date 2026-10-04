"""The session token: signed by the auth service, verified everywhere else.

Every way a token can be wrong is a way a forged session could be accepted,
so each is refused here with the code a client branches on.
"""

from __future__ import annotations

import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from nl2sql_identity import tokens
from nl2sql_identity.tokens import (
    ANONYMOUS,
    AUDIENCE,
    ISSUER,
    LEEWAY_SECONDS,
    SERVICE,
    SESSION,
    Identity,
    TokenError,
    load_public_key,
    public_key_pem,
    sign,
    verify,
)

from .conftest import NOW, b64


def _claims(**overrides):
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "alice",
        "name": "Alice",
        "roles": ["nl2sql_users"],
        "iat": NOW,
        "nbf": NOW,
        "exp": NOW + 600,
        "jti": "x",
    }
    claims.update(overrides)
    return claims


def test_a_signed_token_verifies_to_the_identity_it_was_signed_for(token_for, public_key):
    token = token_for("alice", roles=("nl2sql_reviewers", "nl2sql_users"), lifetime=600)
    identity = verify(token, public_key, now=NOW + 1)
    assert identity.user == "alice"
    assert identity.name == "Alice Smith"
    assert identity.roles == frozenset({"nl2sql_reviewers", "nl2sql_users"})
    assert identity.kind == SESSION
    assert (identity.issued_at, identity.expires_at) == (NOW, NOW + 600)
    assert identity.token_id


def test_the_token_is_a_standard_eddsa_jwt(token_for):
    """Three base64url segments with the RFC 8037 header, so any JWT library
    with Ed25519 could read one -- the format is not this repository's own."""
    header, payload, signature = token_for().split(".")
    assert json.loads(tokens._unb64(header)) == {"alg": "EdDSA", "typ": "JWT"}
    claims = json.loads(tokens._unb64(payload))
    assert claims["iss"] == ISSUER and claims["aud"] == AUDIENCE
    assert claims["roles"] == ["nl2sql_users"]
    assert len(tokens._unb64(signature)) == 64


def test_each_token_is_unique_even_for_the_same_person_and_moment(token_for):
    assert token_for() != token_for()


@pytest.mark.parametrize(
    "token",
    ["", "one.two", "a.b.c.d", "!!!.###.$$$"],
)
def test_something_that_is_not_a_signed_token_is_malformed(token, public_key):
    with pytest.raises(TokenError) as caught:
        verify(token, public_key, now=NOW)
    assert caught.value.code == "malformed"


def test_segments_that_decode_to_something_other_than_json_are_malformed(public_key):
    with pytest.raises(TokenError) as caught:
        verify(f"{b64(b'not json')}.{b64(b'nor this')}.{b64(b'sig')}", public_key, now=NOW)
    assert caught.value.code == "malformed"


@pytest.mark.parametrize(
    "header",
    [
        {"alg": "none", "typ": "JWT"},
        {"alg": "HS256", "typ": "JWT"},
        {"alg": "EdDSA", "typ": "JWT", "kid": "attacker"},
        {"alg": "EdDSA"},
    ],
    ids=["none", "hmac", "extra-field", "no-typ"],
)
def test_a_header_that_is_not_exactly_eddsa_is_refused_before_the_signature(header, forge, public_key):
    """The header never chooses the algorithm: it is compared whole."""
    with pytest.raises(TokenError) as caught:
        verify(forge(_claims(), header=header), public_key, now=NOW)
    assert caught.value.code == "malformed"


def test_a_token_signed_by_another_key_is_refused(public_key):
    stranger = Ed25519PrivateKey.generate()
    token = sign(Identity(user="mallory", roles=frozenset({"nl2sql_admins"})), stranger, lifetime_seconds=60, now=NOW)
    with pytest.raises(TokenError) as caught:
        verify(token, public_key, now=NOW)
    assert caught.value.code == "bad_signature"


def test_changing_one_claim_breaks_the_signature(token_for, public_key):
    header, payload, signature = token_for().split(".")
    claims = json.loads(tokens._unb64(payload))
    claims["roles"] = ["nl2sql_admins"]
    with pytest.raises(TokenError) as caught:
        verify(f"{header}.{b64(claims)}.{signature}", public_key, now=NOW)
    assert caught.value.code == "bad_signature"


def test_claims_that_are_not_an_object_are_malformed(forge, public_key):
    with pytest.raises(TokenError) as caught:
        verify(forge(["alice"]), public_key, now=NOW)
    assert caught.value.code == "malformed"


@pytest.mark.parametrize(
    "overrides",
    [{"iss": "someone-else"}, {"aud": "another-app"}, {"iss": None}],
    ids=["issuer", "audience", "no-issuer"],
)
def test_a_token_for_another_issuer_or_audience_is_refused(overrides, forge, public_key):
    with pytest.raises(TokenError) as caught:
        verify(forge(_claims(**overrides)), public_key, now=NOW)
    assert caught.value.code == "wrong_audience"


@pytest.mark.parametrize("claim", ["exp", "nbf", "iat"])
@pytest.mark.parametrize("value", [True, "1800000000", 1.5, None], ids=["bool", "string", "float", "missing"])
def test_a_timestamp_that_is_not_an_integer_is_malformed(claim, value, forge, public_key):
    """JSON `true` is a Python bool, and a bool is an int nobody meant."""
    claims = _claims()
    if value is None:
        del claims[claim]
    else:
        claims[claim] = value
    with pytest.raises(TokenError) as caught:
        verify(forge(claims), public_key, now=NOW)
    assert caught.value.code == "malformed"
    assert claim in str(caught.value)


def test_expiry_allows_for_a_little_clock_difference_and_no_more(token_for, public_key):
    token = token_for(lifetime=60)
    verify(token, public_key, now=NOW + 60 + LEEWAY_SECONDS)
    with pytest.raises(TokenError) as caught:
        verify(token, public_key, now=NOW + 60 + LEEWAY_SECONDS + 1)
    assert caught.value.code == "expired"
    assert "sign in again" in str(caught.value)


def test_a_token_from_the_future_is_refused_beyond_the_leeway(token_for, public_key):
    token = token_for(now=NOW + 3600)
    verify(token, public_key, now=NOW + 3600 - LEEWAY_SECONDS)
    with pytest.raises(TokenError) as caught:
        verify(token, public_key, now=NOW + 3600 - LEEWAY_SECONDS - 1)
    assert caught.value.code == "not_yet_valid"


def test_sign_and_verify_read_the_clock_when_no_moment_is_given(private_key, public_key, monkeypatch):
    monkeypatch.setattr(tokens.time, "time", lambda: NOW + 10)
    identity = verify(sign(Identity(user="bob"), private_key, lifetime_seconds=60), public_key)
    assert (identity.user, identity.issued_at, identity.expires_at) == ("bob", NOW + 10, NOW + 70)


@pytest.mark.parametrize(
    "overrides",
    [{"sub": ""}, {"sub": 7}, {"sub": None}, {"roles": "nl2sql_users"}, {"roles": ["ok", 3]}, {"roles": None}],
    ids=["empty-sub", "numeric-sub", "no-sub", "roles-string", "roles-mixed", "no-roles"],
)
def test_a_token_that_names_nobody_or_lists_no_roles_is_malformed(overrides, forge, public_key):
    with pytest.raises(TokenError) as caught:
        verify(forge(_claims(**overrides)), public_key, now=NOW)
    assert caught.value.code == "malformed"


def test_a_name_and_token_id_are_optional(forge, public_key):
    claims = _claims()
    del claims["name"], claims["jti"]
    identity = verify(forge(claims), public_key, now=NOW)
    assert (identity.name, identity.token_id) == ("", "")
    assert identity.display == "alice"


def test_the_public_key_round_trips_through_its_pem_file(public_key, tmp_path, token_for):
    path = tmp_path / "session.pub"
    path.write_bytes(public_key_pem(public_key))
    assert b"BEGIN PUBLIC KEY" in path.read_bytes()
    assert verify(token_for(), load_public_key(path), now=NOW).user == "alice"


def test_a_key_file_holding_some_other_kind_of_key_is_refused(tmp_path):
    other = ec.generate_private_key(ec.SECP256R1()).public_key()
    path = tmp_path / "session.pub"
    path.write_bytes(
        other.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    )
    with pytest.raises(TokenError) as caught:
        load_public_key(path)
    assert caught.value.code == "wrong_key"


# --- the identity itself ---------------------------------------------------


def test_a_session_runs_its_sql_as_its_own_role():
    assert Identity(user="alice").principal == "alice"


@pytest.mark.parametrize("kind", [SERVICE, ANONYMOUS])
def test_a_service_or_anonymous_caller_runs_as_the_service(kind):
    assert Identity(user="", kind=kind).principal is None
    assert Identity(user="robot", kind=kind).principal is None


def test_display_prefers_the_name_then_the_user_then_the_kind():
    assert Identity(user="alice", name="Alice Smith").display == "Alice Smith"
    assert Identity(user="alice").display == "alice"
    assert Identity(user="", kind=SERVICE).display == SERVICE


def test_roles_decide_access_except_where_nothing_was_asked_for():
    reviewer = Identity(user="r", roles=frozenset({"nl2sql_reviewers", "nl2sql_users"}))
    assert reviewer.has_any({"nl2sql_reviewers", "nl2sql_curators"})
    assert not reviewer.has_any({"nl2sql_admins"})
    assert reviewer.has_any(())
    assert not Identity(user="x").has_any({"nl2sql_users"})


def test_anonymous_is_the_deployment_without_sign_in_and_may_do_anything():
    assert Identity(user="", kind=ANONYMOUS).has_any({"nl2sql_admins"})


def test_a_service_token_holds_only_the_roles_it_was_given():
    service = Identity(user="", kind=SERVICE, roles=frozenset({"nl2sql_users"}))
    assert service.has_any({"nl2sql_users"})
    assert not service.has_any({"nl2sql_reviewers"})
