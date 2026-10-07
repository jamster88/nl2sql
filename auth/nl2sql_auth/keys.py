"""The key every session is signed with.

Made on the first start and kept in a volume only this service mounts, so a
restart does not sign everyone out. Its public half is written beside it into
the volume the verifying services mount read-only, every start -- so a
replaced or rotated key reaches them without anyone copying anything.

Rotating it is deleting the key file and restarting: every session then fails
to verify and everyone signs in again, which is what rotation is for.
"""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from nl2sql_identity.tokens import public_key_pem


class SigningKeyError(RuntimeError):
    """A key file that exists and is not a signing key."""


def _write_private(path: Path, key: Ed25519PrivateKey) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    # Private from the first byte: created 0600 rather than chmod-ed after.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)


def load_or_create(private_path: str, public_path: str) -> tuple[Ed25519PrivateKey, str]:
    """The signing key, made if missing, with its public half published."""
    private = Path(private_path)
    if private.is_file():
        key = serialization.load_pem_private_key(private.read_bytes(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise SigningKeyError(f"{private} holds a private key, but not an Ed25519 one")
        note = f"signing with the key at {private}"
    else:
        key = Ed25519PrivateKey.generate()
        _write_private(private, key)
        note = f"made a signing key at {private}"
    public = Path(public_path)
    public.parent.mkdir(parents=True, exist_ok=True)
    public.write_bytes(public_key_pem(key.public_key()))
    os.chmod(public, 0o644)
    return key, f"{note}; its public half is at {public}"
