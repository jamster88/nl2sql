"""The session token: what the auth service signs and every other service reads.

A JSON Web Token signed with Ed25519 (`"alg": "EdDSA"`, RFC 8037), written
out here rather than taken from a JWT library. The format is three base64url
segments and one signature, and the whole of it fits on a screen; a library
would add a dependency to three images and, with it, the feature that has
caused most JWT vulnerabilities -- a header that chooses its own algorithm.
Here the header is compared to the one value it may have, so `"alg": "none"`
or an HMAC signed with the public key is a malformed token, not a question.

Asymmetric because of who holds what. The auth service signs with a private
key nobody else has; the agent API, the SQL console and the review service
verify with the public half. A shared HMAC secret would put the power to mint
a reviewer's session inside the internet-facing API, which is the blast
radius the review service was split out to avoid.

A token says who someone is and which of the nl2sql roles Postgres said they
held when they signed in. It is not the last word on the roles: a service that
can ask Postgres again does (see `guard.Guard`), so a user removed from a
group in the directory loses it within a minute rather than at expiry. Nor is
it the last word on itself: its `jti` names it, and a session signed out, or
signed in before its holder's password changed or their account was locked or
removed, is refused within the same minute (`postgres.REVOKED_SQL`).
"""

from __future__ import annotations

import base64
import binascii
import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

#: Who signs, and who the token is for. Checked on every verify, so a token
#: some other Ed25519 issuer minted is refused even if its key were trusted.
ISSUER = "nl2sql-auth"
AUDIENCE = "nl2sql"

#: The only header a token may carry, compared whole.
HEADER = {"alg": "EdDSA", "typ": "JWT"}

#: Seconds of disagreement tolerated between the signing container's clock
#: and the verifying one's.
LEEWAY_SECONDS = 30

#: What kind of caller an `Identity` is. A *session* is a person who signed
#: in; a *service* presented the static token a deployment configured for
#: machines (`API_TOKEN` and its siblings), and is the name and the roles
#: configured beside it; *anonymous* is what every caller is when sign-in is
#: off and no token is configured.
SESSION = "session"
SERVICE = "service"
ANONYMOUS = "anonymous"


class TokenError(ValueError):
    """A token that cannot be accepted, and the machine-readable reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Identity:
    """Who is calling.

    `user` is the Postgres role -- the directory's login name, lower-cased --
    and therefore also the role their SQL runs as. `roles` are the nl2sql
    group roles they hold.
    """

    user: str
    name: str = ""
    roles: frozenset[str] = frozenset()
    kind: str = SESSION
    issued_at: int = 0
    expires_at: int = 0
    token_id: str = ""

    @property
    def display(self) -> str:
        """What to call them on a page or in a record."""
        return self.name or self.user or self.kind

    @property
    def principal(self) -> str | None:
        """The database role this caller's SQL runs as.

        Only a signed-in person has one. A service token and an anonymous
        caller run as the service's own role, which is what every caller
        did before there was sign-in.
        """
        return self.user if self.kind == SESSION and self.user else None

    @property
    def actor(self) -> str | None:
        """Who did it, for a record that keeps a name.

        A person is their role name. A service token is the name the
        deployment gave it (`REVIEW_TOKEN_NAME` and its siblings), marked as
        a token so it is never mistaken for a person who happens to share
        it. Anonymous is nobody: no caller could be told apart.
        """
        if self.kind == SESSION:
            return self.user or None
        if self.kind == SERVICE:
            return f"token:{self.user or 'service'}"
        return None

    def has_any(self, roles: Iterable[str]) -> bool:
        """True when the caller holds one of `roles` -- or none were asked for.

        Anonymous is the deployment with sign-in off and no token, where
        every caller could already do everything; it stays that way.
        """
        wanted = set(roles)
        if not wanted or self.kind == ANONYMOUS:
            return True
        return bool(wanted & self.roles)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode((text + "=" * (-len(text) % 4)).encode("ascii"))


def _json(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def sign(
    identity: Identity,
    private_key: Ed25519PrivateKey,
    *,
    lifetime_seconds: int,
    now: float | None = None,
) -> str:
    """A token for `identity`, valid from now for `lifetime_seconds`."""
    issued = int(time.time() if now is None else now)
    payload = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": identity.user,
        "name": identity.name,
        "roles": sorted(identity.roles),
        "iat": issued,
        "nbf": issued,
        "exp": issued + int(lifetime_seconds),
        "jti": secrets.token_urlsafe(12),
    }
    signing_input = f"{_b64(_json(HEADER))}.{_b64(_json(payload))}"
    signature = private_key.sign(signing_input.encode("ascii"))
    return f"{signing_input}.{_b64(signature)}"


def _number(payload: dict, key: str) -> int:
    value = payload.get(key)
    # `type is int`, not isinstance: JSON's `true` is a Python bool, and a
    # bool is an int that nobody meant as a timestamp.
    if type(value) is not int:
        raise TokenError("malformed", f"the token's {key} is not a timestamp")
    return value


def verify(
    token: str,
    public_key: Ed25519PublicKey,
    *,
    now: float | None = None,
    leeway: int = LEEWAY_SECONDS,
) -> Identity:
    """The identity a token carries, or `TokenError` saying why not."""
    parts = token.split(".")
    if len(parts) != 3:
        raise TokenError("malformed", "the session token is not a signed token")
    header_b64, payload_b64, signature_b64 = parts
    try:
        header = json.loads(_unb64(header_b64))
        payload = json.loads(_unb64(payload_b64))
        signature = _unb64(signature_b64)
    except (ValueError, binascii.Error) as exc:
        raise TokenError("malformed", "the session token cannot be decoded") from exc
    if header != HEADER:
        raise TokenError("malformed", "the session token's header is not the one this service signs")
    try:
        public_key.verify(signature, f"{header_b64}.{payload_b64}".encode("ascii"))
    except InvalidSignature as exc:
        raise TokenError(
            "bad_signature", "the session token was not signed by this deployment's auth service"
        ) from exc
    if not isinstance(payload, dict):
        raise TokenError("malformed", "the session token carries no claims")
    if payload.get("iss") != ISSUER or payload.get("aud") != AUDIENCE:
        raise TokenError("wrong_audience", "the session token was issued for something else")

    expires = _number(payload, "exp")
    not_before = _number(payload, "nbf")
    issued = _number(payload, "iat")
    moment = time.time() if now is None else now
    if moment > expires + leeway:
        raise TokenError("expired", "the session has expired; sign in again")
    if moment + leeway < not_before:
        raise TokenError("not_yet_valid", "the session token is not valid yet")

    user = payload.get("sub")
    roles = payload.get("roles")
    if not isinstance(user, str) or not user:
        raise TokenError("malformed", "the session token names nobody")
    if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
        raise TokenError("malformed", "the session token's roles are not a list of names")
    return Identity(
        user=user,
        name=str(payload.get("name") or ""),
        roles=frozenset(roles),
        kind=SESSION,
        issued_at=issued,
        expires_at=expires,
        token_id=str(payload.get("jti") or ""),
    )


def public_key_pem(key: Ed25519PublicKey) -> bytes:
    """The public half as PEM, for the file the other services read."""
    return key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def load_public_key(path: str | Path) -> Ed25519PublicKey:
    """The verifying key, read from the PEM file the auth service wrote."""
    key = serialization.load_pem_public_key(Path(path).read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        raise TokenError("wrong_key", f"{path} holds a key, but not an Ed25519 one")
    return key
