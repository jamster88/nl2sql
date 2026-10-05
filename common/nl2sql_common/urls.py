"""Connection URLs, as a person may see them."""

from __future__ import annotations


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
