"""Reading and writing people and groups, against an in-memory directory.

slapd's overlays (refint, memberOf) are deliberately not relied on, so the
mock -- which has neither -- is a fair stand-in for what this code does.
"""

from __future__ import annotations

import pytest
from ldap3 import MODIFY_REPLACE
from ldap3.core.exceptions import LDAPException

from nl2sql_ldap import directory as module
from nl2sql_ldap.directory import Directory, DirectoryError, ImportSummary, Person
from nl2sql_ldap.records import Records, UserRecord

from .conftest import BASE, store_password

ARGON = "{ARGON2}$argon2id$v=19$m=65536,t=2,p=1$c2FsdA$aGFzaA"


def _attributes(conn, dn, *names):
    conn.search(dn, "(objectClass=*)", search_scope="BASE", attributes=list(names))
    return conn.response[0]["attributes"]


def test_the_tree_is_made_once(directory, layout):
    created = directory.ensure_base("nl2sql")
    assert created == [BASE, *layout.organisational_units, layout.placeholder]
    assert directory.ensure_base("nl2sql") == []
    assert _attributes(directory.conn, BASE, "o", "dc") == {"o": ["nl2sql"], "dc": ["nl2sql"]}


def test_groups_are_made_once_each_holding_the_placeholder(directory, layout):
    directory.ensure_base("nl2sql")
    assert directory.ensure_groups(["a", "b"]) == ["a", "b"]
    assert directory.ensure_groups(["a", "c"]) == ["c"]
    assert _attributes(directory.conn, layout.group("a"), "member")["member"] == [layout.placeholder]
    assert directory.groups() == {"a": set(), "b": set(), "c": set()}


def test_the_service_account_gets_this_starts_password(tree, layout):
    tree.ensure_service_account("first")
    tree.ensure_service_account("second")
    assert _attributes(tree.conn, layout.service_account, "userPassword")["userPassword"] == ["second"]


def test_a_person_is_added_with_a_password_and_groups(tree, layout):
    tree.add_person(
        UserRecord(
            uid="alice",
            given_name="Alice",
            surname="Smith",
            mail="alice@x",
            password="pw",
            groups=("nl2sql-users", "nl2sql-reviewers"),
        )
    )
    person = tree.person("alice")
    assert person == Person(
        uid="alice",
        cn="Alice Smith",
        sn="Smith",
        given_name="Alice",
        mail="alice@x",
        groups=("nl2sql-reviewers", "nl2sql-users"),
    )
    assert person.name == "Alice Smith"
    assert _attributes(tree.conn, layout.user("alice"), "userPassword")["userPassword"] == ["pw"]


def test_a_person_can_arrive_with_another_directorys_hash(tree, layout):
    tree.add_person(UserRecord(uid="bob", password_hash=ARGON))
    assert _attributes(tree.conn, layout.user("bob"), "userPassword")["userPassword"] == [ARGON]
    assert tree.person("bob").name == "bob"


def test_a_person_with_no_password_exists_but_has_none(tree, layout):
    tree.add_person(UserRecord(uid="carol"))
    assert _attributes(tree.conn, layout.user("carol"), "userPassword")["userPassword"] == []


def test_an_unusable_or_taken_login_is_refused(tree):
    with pytest.raises(DirectoryError) as caught:
        tree.add_person(UserRecord(uid="Jane Doe"))
    assert caught.value.code == "invalid_login"
    tree.add_person(UserRecord(uid="dan"))
    with pytest.raises(DirectoryError) as caught:
        tree.add_person(UserRecord(uid="dan"))
    assert caught.value.code == "already_exists"


def test_what_the_directory_refuses_is_reported_with_its_reason(tree, monkeypatch):
    def refuse(*args, **kwargs):
        tree.conn.result = {"description": "insufficientAccessRights", "message": "no write access to parent"}
        return False

    monkeypatch.setattr(tree.conn, "add", refuse)
    with pytest.raises(DirectoryError) as caught:
        tree.add_person(UserRecord(uid="eve"))
    assert caught.value.code == "insufficientAccessRights"
    assert "creating eve: no write access to parent" in str(caught.value)


def test_a_refusal_without_a_reason_still_says_it_was_refused(tree, monkeypatch):
    def refuse(*args, **kwargs):
        tree.conn.result = None
        return False

    monkeypatch.setattr(tree.conn, "add", refuse)
    with pytest.raises(DirectoryError) as caught:
        tree.add_person(UserRecord(uid="eve"))
    assert caught.value.code == "refused" and str(caught.value).endswith("refused")


def test_people_are_listed_in_order_with_their_groups_and_state(tree, layout, conn):
    tree.add_person(UserRecord(uid="zed", groups=("nl2sql-admins",)))
    tree.add_person(UserRecord(uid="amy"))
    conn.modify(
        layout.user("amy"),
        {
            "seeAlso": [(MODIFY_REPLACE, ["CN=Amy,DC=corp"])],
            "pwdAccountLockedTime": [(MODIFY_REPLACE, ["20260101000000Z"])],
        },
    )
    amy, zed = tree.people()
    assert (amy.uid, amy.upstream, amy.locked, amy.groups) == ("amy", "CN=Amy,DC=corp", True, ())
    assert (zed.uid, zed.locked, zed.groups) == ("zed", False, ("nl2sql-admins",))
    assert tree.person("nobody-at-all") is None


def test_updating_replaces_and_removes_optional_attributes(tree, layout):
    tree.add_person(UserRecord(uid="alice", mail="old@x", given_name="A"))
    tree.update_person("alice", mail="new@x", given_name="", display_name="Alice S")
    person = tree.person("alice")
    assert (person.mail, person.given_name, person.display_name) == ("new@x", "", "Alice S")
    tree.update_person("alice")  # nothing asked, nothing sent


@pytest.mark.parametrize("required", ["cn", "sn"])
def test_a_required_attribute_cannot_be_emptied(tree, required):
    tree.add_person(UserRecord(uid="alice"))
    with pytest.raises(DirectoryError) as caught:
        tree.update_person("alice", **{required: ""})
    assert caught.value.code == "required"


def test_operations_on_somebody_who_is_not_there_say_so(tree):
    for operation in (
        lambda: tree.update_person("ghost", mail="x"),
        lambda: tree.delete_person("ghost"),
        lambda: tree.set_password("ghost", "pw"),
        lambda: tree.unlock("ghost"),
    ):
        with pytest.raises(DirectoryError) as caught:
            operation()
        assert caught.value.code == "not_found"


def test_removing_a_person_takes_them_out_of_their_groups_first(tree, layout):
    tree.add_person(UserRecord(uid="alice", groups=("nl2sql-users", "nl2sql-admins")))
    tree.delete_person("alice")
    assert not tree.exists(layout.user("alice"))
    assert all(not members for members in tree.groups().values())


def test_passwords_and_hashes_are_set(tree, layout):
    tree.add_person(UserRecord(uid="alice"))
    tree.set_password("alice", "new")
    assert _attributes(tree.conn, layout.user("alice"), "userPassword")["userPassword"] == ["new"]
    tree.set_password_hash("alice", ARGON)
    assert _attributes(tree.conn, layout.user("alice"), "userPassword")["userPassword"] == [ARGON]
    with pytest.raises(DirectoryError) as caught:
        tree.set_password_hash("alice", "{MD5}weak")
    assert caught.value.code == "invalid_hash"


def test_unlocking_clears_the_lockout_and_minds_nothing_if_there_was_none(tree, layout, conn):
    tree.add_person(UserRecord(uid="alice"))
    tree.unlock("alice")
    conn.modify(layout.user("alice"), {"pwdAccountLockedTime": [(MODIFY_REPLACE, ["20260101000000Z"])]})
    assert tree.person("alice").locked
    tree.unlock("alice")
    assert not tree.person("alice").locked


def test_group_membership_is_set_exactly_and_idempotently(tree):
    tree.add_person(UserRecord(uid="alice"))
    tree.add_member("nl2sql-users", "alice")
    tree.add_member("nl2sql-users", "alice")
    tree.remove_member("nl2sql-admins", "alice")
    assert tree.person("alice").groups == ("nl2sql-users",)
    tree.set_groups("alice", ["nl2sql-reviewers", "nl2sql-curators"])
    assert tree.person("alice").groups == ("nl2sql-curators", "nl2sql-reviewers")
    with pytest.raises(DirectoryError) as caught:
        tree.set_groups("alice", ["nl2sql-users", "sales", "hr"])
    assert caught.value.code == "not_found" and "hr, sales" in str(caught.value)


def test_a_group_can_be_given_exactly_its_members(tree, layout):
    for uid in ("a", "b", "c"):
        tree.add_person(UserRecord(uid=uid))
    tree.set_members("nl2sql-users", ["c", "a", "a"])
    assert tree.groups()["nl2sql-users"] == {"a", "c"}
    members = _attributes(tree.conn, layout.group("nl2sql-users"), "member")["member"]
    assert members[0] == layout.placeholder, "the placeholder keeps an emptied group valid"


def test_groups_are_created_and_removed(tree):
    tree.create_group("sales", "People who sell")
    tree.create_group("ops")
    assert {"sales", "ops"} <= set(tree.groups())
    with pytest.raises(DirectoryError) as caught:
        tree.create_group("sales")
    assert caught.value.code == "already_exists"
    tree.delete_group("sales")
    assert "sales" not in tree.groups()
    with pytest.raises(DirectoryError) as caught:
        tree.delete_group("sales")
    assert caught.value.code == "not_found"
    with pytest.raises(DirectoryError) as caught:
        tree.add_member("sales", "alice")
    assert caught.value.code == "not_found"


def test_a_search_the_directory_refuses_is_reported(tree, monkeypatch):
    def broken(*args, **kwargs):
        raise LDAPException("socket closed")

    monkeypatch.setattr(tree.conn.extend.standard, "paged_search", broken)
    with pytest.raises(DirectoryError) as caught:
        tree.groups()
    assert caught.value.code == "search_failed"
    monkeypatch.setattr(tree.conn, "search", broken)
    assert tree.exists(BASE) is False


def test_an_import_creates_updates_and_adds_memberships_without_removing_any(tree):
    tree.add_person(UserRecord(uid="admin", groups=("nl2sql-admins",)))
    tree.add_person(UserRecord(uid="old", mail="old@x"))
    records = Records(
        users=[
            UserRecord(uid="new", password="pw", groups=("nl2sql-users",)),
            UserRecord(uid="old", mail="old2@x", password_hash=ARGON, groups=("nl2sql-reviewers",)),
            UserRecord(uid="fresh", password="pw2"),
            UserRecord(uid="Bad Name"),
        ],
        groups={"nl2sql-admins": ("new",), "sales": ("new",)},
        problems=["line 9: something the parser saw"],
    )
    summary = tree.apply(records)
    assert summary.created == ["new", "fresh"]
    assert summary.updated == ["old"]
    assert summary.passwords == ["new", "old", "fresh"]
    assert summary.groups == ["new -> nl2sql-admins", "old -> nl2sql-reviewers", "new -> nl2sql-users"]
    assert summary.problems[0] == "line 9: something the parser saw"
    assert any(problem.startswith("Bad Name: 'Bad Name' is not a usable") for problem in summary.problems)
    assert summary.problems[-1] == "sales is not a group in this directory; its members were not added"
    assert tree.person("admin").groups == ("nl2sql-admins",), "nobody is removed from a group"
    assert tree.person("old").mail == "old2@x"


def test_an_import_sets_a_plain_password_on_somebody_who_exists(tree, layout):
    tree.add_person(UserRecord(uid="old"))
    summary = tree.apply(Records(users=[UserRecord(uid="old", password="changed")]))
    assert summary == ImportSummary(updated=["old"], passwords=["old"])
    assert _attributes(tree.conn, layout.user("old"), "userPassword")["userPassword"] == ["changed"]


def test_the_default_password_setter_is_the_password_modify_operation():
    class Standard:
        def __init__(self):
            self.calls = []

        def modify_password(self, **kwargs):
            self.calls.append(kwargs)
            return True

    class Extend:
        standard = Standard()

    class Conn:
        extend = Extend()

    assert module._password_modify(Conn(), "uid=a,ou=people", "pw") is True
    assert Conn.extend.standard.calls == [{"user": "uid=a,ou=people", "new_password": "pw"}]
    assert Directory(Conn(), None)._set_password is module._password_modify


def test_value_helpers_cope_with_what_a_server_returns():
    assert module._first({"a": ["x", "y"]}, "a") == "x"
    assert module._first({"a": []}, "a") == ""
    assert module._first({"a": "x"}, "a") == "x"
    assert module._first({}, "a") == ""
    assert module._all({"a": "x"}, "a") == ["x"]
    assert module._all({}, "a") == []
    assert store_password  # the fixture's stand-in is what the seam takes


def test_an_import_that_names_no_password_leaves_the_existing_one(tree, layout):
    tree.add_person(UserRecord(uid="old", password="kept"))
    summary = tree.apply(Records(users=[UserRecord(uid="old", mail="new@x")]))
    assert summary == ImportSummary(updated=["old"])
    assert _attributes(tree.conn, layout.user("old"), "userPassword")["userPassword"] == ["kept"]
