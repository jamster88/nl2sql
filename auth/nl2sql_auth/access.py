"""Reaching the directory from the auth service.

Always encrypted -- StartTLS on ldap://, or ldaps:// -- and always verified
against the directory's own certificate, which it writes into a volume this
service mounts read-only. The directory refuses a password over anything
less, so a misconfiguration here fails loudly rather than leaking quietly.

Two identities: the service's own account, which reads people and groups for
the role sync and edits them for the web interface, and a person's, for the
one thing they do to the directory themselves -- changing their password,
which needs their current one.
"""

from __future__ import annotations

import json
import os
import ssl
from contextlib import contextmanager
from typing import Callable, Iterator

from ldap3 import BASE, NONE, Connection, Server, Tls
from ldap3.core.exceptions import LDAPException

from nl2sql_ldap.directory import Directory, DirectoryError
from nl2sql_ldap.layout import Layout

from .settings import AuthSettings


class DirectoryUnavailable(RuntimeError):
    """The directory could not be reached, or refused the service's account."""


class PasswordRefused(RuntimeError):
    """A person's current password was not accepted."""


def connect(settings: AuthSettings, *, user: str | None = None, password: str | None = None) -> Connection:
    """A bound connection: as the service, or as `user` when one is given."""
    layout = Layout(settings.ldap_base_dn)
    cacert = settings.ldap_cacert if settings.ldap_cacert and os.path.isfile(settings.ldap_cacert) else None
    tls = Tls(validate=ssl.CERT_REQUIRED, ca_certs_file=cacert)
    server = Server(
        settings.ldap_url,
        use_ssl=settings.ldap_url.lower().startswith("ldaps://"),
        tls=tls,
        connect_timeout=settings.ldap_timeout,
        get_info=NONE,
    )
    conn = Connection(
        server,
        user=user or layout.service_account,
        password=password if user else settings.ldap_service_password,
        receive_timeout=settings.ldap_timeout,
        raise_exceptions=False,
    )
    try:
        conn.open()
        if settings.ldap_starttls and not server.ssl:
            conn.start_tls()
        bound = conn.bind()
    except LDAPException as exc:
        raise DirectoryUnavailable(f"cannot reach the directory at {settings.ldap_url}: {exc}") from exc
    if not bound:
        if user:
            raise PasswordRefused("the current password was not accepted")
        description = (conn.result or {}).get("description", "refused")
        raise DirectoryUnavailable(f"the directory refused the auth service's account ({description})")
    return conn


@contextmanager
def directory(settings: AuthSettings, *, opener: Callable[..., Connection] = connect) -> Iterator[Directory]:
    """The directory as the service sees it, for the length of a request."""
    conn = opener(settings)
    try:
        yield Directory(conn, Layout(settings.ldap_base_dn))
    finally:
        conn.unbind()


def change_own_password(
    settings: AuthSettings,
    uid: str,
    current: str,
    new: str,
    *,
    opener: Callable[..., Connection] = connect,
) -> None:
    """RFC 3062 as the person: the directory checks the old password and its policy."""
    conn = opener(settings, user=Layout(settings.ldap_base_dn).user(uid), password=current)
    try:
        if not conn.extend.standard.modify_password(old_password=current, new_password=new):
            result = conn.result or {}
            raise DirectoryError(
                str(result.get("description") or "refused"),
                result.get("message") or "the directory refused the new password",
            )
    finally:
        conn.unbind()


def replica_status(found: Directory) -> dict | None:
    """What the replica's last copy from its primary did, or None if never."""
    conn = found.conn
    if not conn.search(found.layout.replica_status, "(objectClass=*)", search_scope=BASE, attributes=["description"]):
        return None
    values = conn.response[0]["attributes"].get("description") or []
    try:
        return json.loads(values[0]) if values else None
    except ValueError:
        return None
