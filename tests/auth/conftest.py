"""Fixtures for the auth service, the shared identity package and the directory.

The signing key is generated per test session: Ed25519 key generation is
microseconds, and a key committed to the repository would be a key someone
eventually trusts.
"""

from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from nl2sql_identity.tokens import Identity, sign

#: A moment to sign and verify at, so expiry never depends on the wall clock.
NOW = 1_800_000_000


@pytest.fixture(scope="session")
def private_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture(scope="session")
def public_key(private_key):
    return private_key.public_key()


@pytest.fixture
def token_for(private_key):
    """`token_for("alice", roles=..., now=..., lifetime=...)` -> a signed token."""

    def make(user: str = "alice", *, roles=("nl2sql_users",), name="Alice Smith", now=NOW, lifetime=3600):
        return sign(
            Identity(user=user, name=name, roles=frozenset(roles)),
            private_key,
            lifetime_seconds=lifetime,
            now=now,
        )

    return make


def b64(value) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(value).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@pytest.fixture
def forge(private_key):
    """Sign an arbitrary header and payload with the real key.

    What a verifier has to refuse even when the signature is good: the
    claims are wrong, not the cryptography.
    """

    def make(payload, header=None) -> str:
        head = b64(header if header is not None else {"alg": "EdDSA", "typ": "JWT"})
        body = b64(payload)
        signature = private_key.sign(f"{head}.{body}".encode())
        return f"{head}.{body}.{b64(signature)}"

    return make
