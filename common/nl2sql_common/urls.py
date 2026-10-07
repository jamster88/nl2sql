"""Connection URLs, as a person may see them."""

from __future__ import annotations

from urllib.parse import quote


def redacted(url: str | None, *, missing: str = "") -> str:
    """The URL with its password taken out, for a banner or a readiness body.

    `/readyz` is answered to anyone -- an orchestrator has to be able to ask
    -- and a URL is the classic way a password ends up in one. The user is
    kept: which role a service connects as is worth seeing.
    """
    if not url:
        return missing
    scheme, sep, rest = url.partition("://")
    if not sep:  # `user:password@host`, with no scheme in front
        scheme, rest = "", url
    credentials, at, host = rest.rpartition("@")
    if not at:
        return url
    user = credentials.partition(":")[0]
    prefix = f"{scheme}{sep}"
    return f"{prefix}{user}:***@{host}" if user else f"{prefix}{host}"


def with_password(url: str, password: str) -> str:
    """`url` with its user's password made `password`, encoded for a URL.

    The user is the URL's own; a URL that names none is refused rather than
    given a password nobody would send.
    """
    scheme, sep, rest = url.partition("://")
    credentials, at, host = rest.rpartition("@")
    if not sep or not at or not credentials.partition(":")[0]:
        raise ValueError(f"{redacted(url)} names no user to give a password to")
    user = credentials.partition(":")[0]
    return f"{scheme}://{user}:{quote(password, safe='')}@{host}"
