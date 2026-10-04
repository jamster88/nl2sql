"""What every start makes sure of, and what only the first start does."""

from __future__ import annotations

from nl2sql_ldap.bootstrap import bootstrap, ensure_policy
from nl2sql_ldap.records import UserRecord
from nl2sql_ldap.settings import DEFAULT_GROUPS, REPLICA, DirectorySettings, UpstreamSettings


def _attributes(conn, dn, *names):
    conn.search(dn, "(objectClass=*)", search_scope="BASE", attributes=list(names))
    return conn.response[0]["attributes"]


def _standalone(**overrides) -> DirectorySettings:
    return DirectorySettings(**{"service_password": "svc", "admin_password": "admin-password", **overrides})


def test_a_first_start_builds_the_tree_and_the_first_administrator(directory, layout):
    notes = bootstrap(directory, _standalone())
    assert notes[0].startswith("created dc=nl2sql,dc=local")
    assert "the auth service's account is cn=nl2sql-auth,ou=services,dc=nl2sql,dc=local" in notes
    assert f"created the groups {', '.join(DEFAULT_GROUPS)}" in notes
    assert notes[-1] == f"first start: created admin in {', '.join(DEFAULT_GROUPS)}"
    admin = directory.person("admin")
    assert admin.name == "Directory administrator"
    assert set(admin.groups) == set(DEFAULT_GROUPS)
    assert _attributes(directory.conn, layout.user("admin"), "userPassword")["userPassword"] == ["admin-password"]
    assert _attributes(directory.conn, layout.service_account, "userPassword")["userPassword"] == ["svc"]


def test_the_password_policy_follows_the_environment(directory, layout):
    directory.ensure_base("nl2sql")
    ensure_policy(directory, _standalone(lockout_failures=3, lockout_seconds=60, min_password_length=14))
    policy = _attributes(directory.conn, layout.password_policy, "pwdLockout", "pwdMaxFailure", "pwdMinLength", "pwdLockoutDuration")
    assert policy == {
        "pwdLockout": ["TRUE"],
        "pwdMaxFailure": ["3"],
        "pwdMinLength": ["14"],
        "pwdLockoutDuration": ["60"],
    }
    ensure_policy(directory, _standalone(lockout_failures=0))
    assert _attributes(directory.conn, layout.password_policy, "pwdLockout")["pwdLockout"] == ["FALSE"]


def test_a_later_start_changes_nothing_but_what_the_environment_owns(directory, layout):
    bootstrap(directory, _standalone())
    directory.delete_person("admin")
    directory.add_person(UserRecord(uid="someone"))
    notes = bootstrap(directory, _standalone(service_password="rotated"))
    assert notes == ["the auth service's account is cn=nl2sql-auth,ou=services,dc=nl2sql,dc=local"]
    assert directory.person("admin") is None, "a removed administrator is not put back"
    assert _attributes(directory.conn, layout.service_account, "userPassword")["userPassword"] == ["rotated"]


def test_a_seed_file_is_loaded_on_the_first_start(directory, tmp_path):
    seed = tmp_path / "people.csv"
    seed.write_text("uid,groups,password\nalice,nl2sql-reviewers,pw\nbob,sales,\nJane Doe,,\n")
    notes = bootstrap(directory, _standalone(seed_file=str(seed)))
    assert "loaded people.csv: 2 created, 0 updated, 1 memberships" in notes
    assert any("'jane doe' is not a usable login name" in note for note in notes)
    assert any("sales is not a group" in note for note in notes)
    assert directory.person("alice").groups == ("nl2sql-reviewers",)


def test_a_seed_file_that_is_not_there_is_said_and_skipped(directory, tmp_path):
    notes = bootstrap(directory, _standalone(seed_file=str(tmp_path / "missing.csv")))
    assert notes[-1].endswith("missing.csv is not a file, so nobody else was loaded")
    assert [person.uid for person in directory.people()] == ["admin"]


def test_a_replica_gets_its_tree_and_the_service_account_only(directory):
    upstream = UpstreamSettings(uri="ldaps://d", bind_dn="x", bind_password="y", base_dn="z")
    settings = DirectorySettings(mode=REPLICA, service_password="svc", upstream=upstream)
    notes = bootstrap(directory, settings)
    assert notes[-1] == "replica: people and groups come from the primary"
    assert directory.people() == [] and directory.groups() == {}
