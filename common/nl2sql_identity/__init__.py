"""Who is calling: the session the auth service signs, read by every service.

Shared by the agent API, the SQL console and the review service, each of
which copies this package into its image. It verifies; it cannot sign --
the private key lives in the auth service alone (`nl2sql_auth`).
"""

from .guard import (
    ADMINS,
    CURATORS,
    DEFAULT_PUBLIC_KEY_FILE,
    REVIEWERS,
    ROLES,
    SESSION_COOKIE,
    USERS,
    Guard,
    GuardSettings,
    IdentityError,
    check_origin,
    env_roles,
)
from .tokens import ANONYMOUS, SERVICE, SESSION, Identity, TokenError, sign, verify

__all__ = [
    "ADMINS",
    "ANONYMOUS",
    "CURATORS",
    "DEFAULT_PUBLIC_KEY_FILE",
    "Guard",
    "GuardSettings",
    "Identity",
    "IdentityError",
    "REVIEWERS",
    "ROLES",
    "SERVICE",
    "SESSION",
    "SESSION_COOKIE",
    "TokenError",
    "USERS",
    "check_origin",
    "env_roles",
    "sign",
    "verify",
]
