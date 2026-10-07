"""An in-memory directory for the directory package's tests.

ldap3's mock server answers adds, modifies, deletes and paged searches the
way slapd does, which is all `Directory`, the bootstrap and the replica use.
What it cannot do is the Password Modify extended operation, so the
`Directory` here is given a stand-in that stores the password where the mock
bind looks for it -- the same seam the real one is set through.

The live directory, with slapd's overlays and access rules, is in
tests/ldap/test_ldap_image.py.
"""

from __future__ import annotations

import pytest
from ldap3 import MOCK_SYNC, MODIFY_REPLACE, Connection, Server

from nl2sql_ldap.directory import Directory
from nl2sql_ldap.layout import Layout

BASE = "dc=nl2sql,dc=local"
ROOT = "cn=root"


def mock_connection(*, user: str = ROOT, password: str = "root-password") -> Connection:
    conn = Connection(Server("mock"), user=user, password=password, client_strategy=MOCK_SYNC)
    conn.strategy.add_entry(user, {"userPassword": password, "sn": "root", "cn": "root"})
    conn.bind()
    return conn


def store_password(conn, dn: str, password: str) -> bool:
    """What the Password Modify operation leaves behind, as the mock reads it."""
    return conn.modify(dn, {"userPassword": [(MODIFY_REPLACE, [password])]})


@pytest.fixture
def conn() -> Connection:
    return mock_connection()


@pytest.fixture
def layout() -> Layout:
    return Layout(BASE)


@pytest.fixture
def directory(conn, layout) -> Directory:
    return Directory(conn, layout, set_password=store_password)


@pytest.fixture
def tree(directory) -> Directory:
    """A directory with its branches and the four groups in place."""
    directory.ensure_base("nl2sql")
    directory.ensure_groups(("nl2sql-users", "nl2sql-reviewers", "nl2sql-curators", "nl2sql-admins"))
    return directory
