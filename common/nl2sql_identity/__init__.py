"""Who is calling: the session the auth service signs, read by every service.

Shared by the agent API, the SQL console, the review service and the auth
service, each of which installs it with `nl2sql_common` (`common/`). It
verifies; it cannot sign -- the private key lives in the auth service alone
(`nl2sql_auth`).
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
    Standing,
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
    "Standing",
    "TokenError",
    "USERS",
    "check_origin",
    "env_roles",
    "sign",
    "verify",
]
