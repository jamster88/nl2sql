"""Where things live in the directory, and what a login name may be."""

from __future__ import annotations

import pytest

from nl2sql_ldap.layout import Layout, first_value, login_problem, normalise_login

from .conftest import BASE


@pytest.mark.parametrize("name", ["alice", "a", "j.doe", "j_doe", "j-doe", "7up", "x" * 63])
def test_usable_login_names(name):
    assert login_problem(name) is None


@pytest.mark.parametrize(
    "name",
    ["", "Alice", "jane doe", "j@corp", "-lead", ".dot", "x" * 64, "o'brien", "a/b"],
)
def test_names_that_cannot_be_a_role_a_dn_and_a_subject_at_once(name):
    assert "not a usable login name" in login_problem(name)


@pytest.mark.parametrize("name", ["postgres", "public", "nl2sql", "nobody", "root", "pg_monitor", "nl2sql_reader"])
def test_names_reserved_for_the_databases_own_roles(name):
    assert "reserved" in login_problem(name)


def test_logins_are_compared_lower_cased_and_trimmed():
    assert normalise_login("  Alice.Smith ") == "alice.smith"


def test_the_tree(layout):
    assert layout.people == f"ou=people,{BASE}"
    assert layout.groups == f"ou=groups,{BASE}"
    assert layout.service_account == f"cn=nl2sql-auth,ou=services,{BASE}"
    assert layout.placeholder == f"cn=nobody,ou=services,{BASE}"
    assert layout.replica_status == f"cn=replica-status,ou=services,{BASE}"
    assert layout.password_policy == f"cn=default,ou=policies,{BASE}"
    assert layout.organisational_units == (
        layout.people,
        layout.groups,
        layout.services,
        layout.policies,
    )


def test_names_are_escaped_into_their_dns(layout):
    assert layout.user("alice") == f"uid=alice,ou=people,{BASE}"
    assert layout.group("NL2SQL, Users") == f"cn=NL2SQL\\, Users,ou=groups,{BASE}"


def test_a_dn_is_read_back_into_the_login_or_group_it_names(layout):
    assert layout.login_of(layout.user("alice")) == "alice"
    assert layout.login_of(f"UID=Bob,OU=People,{BASE.upper()}") == "Bob"
    assert layout.login_of(layout.group("x")) is None
    assert layout.login_of(f"cn=alice,ou=people,{BASE}") is None
    assert layout.group_of(layout.group("nl2sql-users")) == "nl2sql-users"
    assert layout.group_of(layout.user("alice")) is None


@pytest.mark.parametrize(
    ("dn", "attribute", "value"),
    [
        ("uid=a,ou=x", "uid", "a"),
        ("UID=a,ou=x", "uid", "a"),
        ("cn=a,ou=x", "uid", None),
        ("", "uid", None),
        ("not a dn", "uid", None),
    ],
)
def test_first_value(dn, attribute, value):
    assert first_value(dn, attribute) == value


def test_another_base_gives_another_tree():
    layout = Layout("dc=corp,dc=example")
    assert layout.user("x").endswith(",ou=people,dc=corp,dc=example")
