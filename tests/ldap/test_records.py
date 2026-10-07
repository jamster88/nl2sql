"""People and groups read from a CSV or LDIF file.

A row that cannot be used is reported by line and skipped; everything else
in the file still loads.
"""

from __future__ import annotations

import base64

import pytest

from nl2sql_ldap.records import HASH_SCHEMES, Records, UserRecord, hash_problem, parse, parse_csv, parse_ldif

ARGON = "{ARGON2}$argon2id$v=19$m=65536,t=2,p=1$c2FsdHNhbHQ$aGFzaGhhc2hoYXNo"


def test_a_csv_with_every_column():
    records = parse_csv(
        "uid,given_name,surname,display_name,mail,groups,password,password_hash\n"
        "Alice,Alice,Smith,Dr Alice Smith,alice@example.com,nl2sql-users; nl2sql-reviewers|nl2sql-users,correct horse,\n"
        f'bob,,,,,,,"{ARGON}"\n'  # quoted: a hash has commas in it
    )
    assert records.problems == []
    alice, bob = records.users
    assert alice == UserRecord(
        uid="alice",
        given_name="Alice",
        surname="Smith",
        display_name="Dr Alice Smith",
        mail="alice@example.com",
        groups=("nl2sql-users", "nl2sql-reviewers"),
        password="correct horse",
    )
    assert bob.password is None and bob.password_hash == ARGON
    assert (bob.cn, bob.sn) == ("bob", "bob"), "an inetOrgPerson must have both"


def test_column_names_have_aliases_and_any_case():
    records = parse_csv("﻿UserName,First_Name,LAST_NAME,Email,Name\ncarol,Carol,Jones,c@x,\n")
    assert records.users == [UserRecord(uid="carol", given_name="Carol", surname="Jones", mail="c@x")]
    assert records.users[0].cn == "Carol Jones"


def test_a_column_nobody_reads_is_reported_not_fatal():
    records = parse_csv("uid,shoe_size\ndan,44\n")
    assert [user.uid for user in records.users] == ["dan"]
    assert records.problems == ["columns not read: shoe_size"]


def test_a_csv_without_a_uid_column_loads_nobody():
    records = parse_csv("name,mail\nEve,e@x\n")
    assert records.users == []
    assert "no uid column" in records.problems[-1]


def test_an_empty_csv_says_so():
    assert parse_csv("").problems == ["the CSV file is empty"]


def test_bad_rows_are_reported_by_line_and_skipped():
    records = parse_csv(
        "uid,password,password_hash\n"
        "good,pw,\n"
        ",pw,\n"
        "Jane Doe,pw,\n"
        "postgres,pw,\n"
        f'both,pw,"{ARGON}"\n'
        "weak,,{MD5}abc\n"
        "good,pw2,\n"
        ",,\n"
    )
    assert [user.uid for user in records.users] == ["good"]
    assert records.users[0].password == "pw", "the first of a repeated uid wins"
    problems = "\n".join(records.problems)
    assert "line 3: no uid" in problems
    assert "line 4: 'jane doe' is not a usable login name" in problems
    assert "line 5: 'postgres' is reserved" in problems
    assert "line 6: both has both a password and a password hash" in problems
    assert "line 7: weak: a password hash must start with one of" in problems
    assert "line 8: good was already given at line 2" in problems
    assert "line 9" not in problems, "a blank row is not a problem"


@pytest.mark.parametrize("scheme", HASH_SCHEMES)
def test_strong_hash_schemes_are_accepted(scheme):
    assert hash_problem(f"{scheme}abc") is None
    assert hash_problem(f"{scheme.lower()}abc") is None


@pytest.mark.parametrize("value", ["{CLEARTEXT}pw", "{MD5}x", "{SHA}x", "plain"])
def test_weak_or_missing_schemes_are_refused(value):
    assert "must start with one of" in hash_problem(value)


LDIF = f"""version: 1
# A comment, and an export's usual shape.

dn: uid=alice,ou=people,dc=corp,dc=example
objectClass: inetOrgPerson
uid: alice
cn: Alice
  Smith
sn: Smith
givenName: Alice
mail: alice@corp.example
userPassword: {ARGON}

dn: cn=Bob B,ou=people,dc=corp,dc=example
objectClass: person
objectClass: organizationalPerson
uid: Bob
cn: Bob B
displayName:: {base64.b64encode("Bób".encode()).decode()}
sn: B
userPassword: bob-plain-password

dn: cn=nl2sql-reviewers,ou=groups,dc=corp,dc=example
objectClass: groupOfNames
cn: nl2sql-reviewers
member: uid=alice,ou=people,dc=corp,dc=example
member: CN=Bob B,OU=People,DC=corp,DC=example
member: uid=stranger,ou=people,dc=corp,dc=example

dn: cn=nl2sql-users,ou=groups,dc=corp,dc=example
objectClass: groupOfUniqueNames
uniqueMember: uid=alice,ou=people,dc=corp,dc=example
"""


def test_an_ldif_export_loads_people_and_their_groups():
    records = parse_ldif(LDIF)
    alice, bob = records.users
    assert alice.uid == "alice" and alice.cn == "Alice Smith", "a folded line is one value, its first space dropped"
    assert alice.password_hash == ARGON and alice.password is None
    assert bob.uid == "bob" and bob.display_name == "Bób" and bob.password == "bob-plain-password"
    assert records.groups == {"nl2sql-reviewers": ("alice", "bob"), "nl2sql-users": ("alice",)}
    assert records.problems == [
        "line 23: nl2sql-reviewers lists uid=stranger,ou=people,dc=corp,dc=example, "
        "who is not a person in this file"
    ]


def test_memberships_combine_a_files_groups_and_its_peoples():
    records = Records(
        users=[UserRecord(uid="a", groups=("g1",)), UserRecord(uid="b", groups=("g2",))],
        groups={"g1": ("b",)},
    )
    assert records.memberships() == {"g1": {"a", "b"}, "g2": {"b"}}


def test_ldif_entries_that_are_not_people_or_groups_or_additions_are_reported():
    records = parse_ldif(
        "dn: ou=people,dc=x\nobjectClass: organizationalUnit\n\n"
        "dn: uid=x,ou=people,dc=x\nchangetype: modify\nreplace: mail\nmail: y\n\n"
        "objectClass: person\nuid: nodn\n\n"
        "dn: cn=g,dc=x\nobjectClass: groupOfNames\nmember: uid=nobody-here,dc=x\n\n"
        "dn: dc=x\nobjectClass: groupOfNames\n\n"
        "garbage line\n\n"
        "dn: uid=c,dc=x\nobjectClass: person\nphoto:: !!!notbase64\ncert:< file:///etc/passwd\n\n"
    )
    problems = "\n".join(records.problems)
    assert "line 1: ou=people,dc=x is neither a person nor a group" in problems
    assert "line 4: changetype modify is an edit" in problems
    assert "line 9: an entry with no dn" in problems
    assert "line 12: g lists uid=nobody-here,dc=x, who is not a person in this file" in problems
    assert "line 16: a group with no cn" in problems
    assert "line 19: 'garbage line' is not 'attribute: value'" in problems
    assert "line 23: photo's base64 value cannot be decoded" in problems
    assert "line 24: cert names a file to read" in problems
    assert records.groups == {"g": ()}
    assert [user.uid for user in records.users] == ["c"], "uid from the DN when there is no attribute"


def test_parse_chooses_by_extension():
    assert parse("uid\nx\n", filename="People.CSV").users[0].uid == "x"
    assert parse("dn: uid=y,dc=x\nobjectClass: person\n", filename="export.ldif").users[0].uid == "y"
    assert parse("dn: uid=z,dc=x\nobjectClass: person\n", filename="export.LDF").users[0].uid == "z"
    other = parse("whatever", filename="people.xlsx")
    assert other.users == [] and "not a .csv or .ldif file" in other.problems[0]


def test_an_ldif_person_who_cannot_be_used_is_not_a_member_either():
    records = parse_ldif(
        "dn: uid=Jane Doe,ou=people,dc=x\nobjectClass: person\n\n"
        "dn: cn=g,dc=x\nobjectClass: groupOfNames\nmember: uid=Jane Doe,ou=people,dc=x\nmember:\n"
    )
    assert records.users == []
    assert records.groups == {"g": ()}
    assert "line 1: 'jane doe' is not a usable login name" in records.problems[0]
    assert len(records.problems) == 2, "an empty member value is nobody, not a stranger"
