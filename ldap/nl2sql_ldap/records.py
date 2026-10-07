"""People and groups read from a file: CSV for a spreadsheet, LDIF for a directory.

A standalone directory loads one on its first start (`LDAP_SEED_FILE`), and
the web interface's import takes the same two formats -- one reader, so a
file that loads in one place loads in the other.

**CSV** has a header row naming its columns, in any order and any case:

    uid,given_name,surname,display_name,mail,groups,password,password_hash
    alice,Alice,Smith,,alice@example.com,nl2sql-users;nl2sql-reviewers,,{ARGON2}$argon2id$...

Only `uid` is required (`username` and `login` are read as it, `email` as
`mail`, `first_name`/`last_name` as the two names). `groups` is separated by
`;`. A person can be given a `password`, which is hashed by the directory as
it is stored, or a `password_hash` already made by another directory; with
neither they exist but cannot sign in until someone sets one.

**LDIF** is what any directory exports (`ldapsearch -LLL`, AD's `ldifde`):
entries with a `person` object class become people, `groupOfNames` and
`groupOfUniqueNames` become groups whose members are those people. Only
additions are read: a `changetype` other than `add` is a problem, not an edit.

Nothing here touches a directory. A row that cannot be used is reported by
line and skipped, so one typo does not stop the other two hundred people.
"""

from __future__ import annotations

import base64
import binascii
import csv
import io
from dataclasses import dataclass, field

from .layout import first_value, login_problem, normalise_login

#: Hashes a directory may be handed ready-made: the ones slapd here can
#: check (argon2 and sha2 are loaded as modules), and none of the weak ones.
HASH_SCHEMES = ("{ARGON2}", "{SSHA512}", "{SSHA384}", "{SSHA256}", "{SSHA}", "{CRYPT}")

_COLUMNS = {
    "uid": "uid",
    "username": "uid",
    "login": "uid",
    "given_name": "given_name",
    "givenname": "given_name",
    "first_name": "given_name",
    "surname": "surname",
    "sn": "surname",
    "last_name": "surname",
    "display_name": "display_name",
    "displayname": "display_name",
    "name": "display_name",
    "cn": "display_name",
    "mail": "mail",
    "email": "mail",
    "groups": "groups",
    "password": "password",
    "password_hash": "password_hash",
}

_PERSON_CLASSES = {"person", "inetorgperson", "organizationalperson", "user"}
_GROUP_CLASSES = {"groupofnames", "groupofuniquenames"}


@dataclass(frozen=True)
class UserRecord:
    """One person, as a file describes them."""

    uid: str
    given_name: str = ""
    surname: str = ""
    display_name: str = ""
    mail: str = ""
    groups: tuple[str, ...] = ()
    password: str | None = None
    password_hash: str | None = None

    @property
    def cn(self) -> str:
        """The common name an inetOrgPerson must have."""
        return self.display_name or " ".join(p for p in (self.given_name, self.surname) if p) or self.uid

    @property
    def sn(self) -> str:
        """The surname an inetOrgPerson must have, even when nobody gave one."""
        return self.surname or self.uid


@dataclass
class Records:
    """What a file held, and what in it could not be used."""

    users: list[UserRecord] = field(default_factory=list)
    #: group -> members, from a file that lists groups (LDIF). A CSV's
    #: memberships are on its people instead.
    groups: dict[str, tuple[str, ...]] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    def memberships(self) -> dict[str, set[str]]:
        """Every group mentioned, with the logins in it, from both places."""
        found: dict[str, set[str]] = {name: set(members) for name, members in self.groups.items()}
        for user in self.users:
            for group in user.groups:
                found.setdefault(group, set()).add(user.uid)
        return found


def hash_problem(value: str) -> str | None:
    """Why a ready-made hash cannot be stored, or None."""
    if not value.upper().startswith(HASH_SCHEMES):
        return (
            f"a password hash must start with one of {', '.join(HASH_SCHEMES)}; "
            f"this one starts {value[:10]!r}"
        )
    return None


def _split_groups(raw: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(part.strip() for part in raw.replace("|", ";").split(";") if part.strip()))


def parse(text: str, *, filename: str) -> Records:
    """A file's records, read as CSV or LDIF by its name."""
    lowered = filename.lower()
    if lowered.endswith(".csv"):
        return parse_csv(text)
    if lowered.endswith(".ldif") or lowered.endswith(".ldf"):
        return parse_ldif(text)
    return Records(problems=[f"{filename}: not a .csv or .ldif file, so there is nothing to read it as"])


def _user(where: str, fields: dict[str, str], records: Records) -> UserRecord | None:
    uid = normalise_login(fields.get("uid", ""))
    if not uid:
        records.problems.append(f"{where}: no uid, so there is nobody to create")
        return None
    problem = login_problem(uid)
    if problem:
        records.problems.append(f"{where}: {problem}")
        return None
    password = fields.get("password") or None
    hashed = fields.get("password_hash") or None
    if password and hashed:
        records.problems.append(f"{where}: {uid} has both a password and a password hash; give one")
        return None
    if hashed and (problem := hash_problem(hashed)):
        records.problems.append(f"{where}: {uid}: {problem}")
        return None
    return UserRecord(
        uid=uid,
        given_name=fields.get("given_name", ""),
        surname=fields.get("surname", ""),
        display_name=fields.get("display_name", ""),
        mail=fields.get("mail", ""),
        groups=_split_groups(fields.get("groups", "")),
        password=password,
        password_hash=hashed,
    )


def _add(records: Records, user: UserRecord | None, seen: dict[str, str], where: str) -> None:
    if user is None:
        return
    if user.uid in seen:
        records.problems.append(f"{where}: {user.uid} was already given at {seen[user.uid]}; the first wins")
        return
    seen[user.uid] = where
    records.users.append(user)


def parse_csv(text: str) -> Records:
    records = Records()
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not reader.fieldnames:
        records.problems.append("the CSV file is empty")
        return records
    unknown = [
        name for name in reader.fieldnames if name and name.strip().lower() not in _COLUMNS
    ]
    if unknown:
        records.problems.append(f"columns not read: {', '.join(unknown)}")
    if not any(_COLUMNS.get((name or "").strip().lower()) == "uid" for name in reader.fieldnames):
        records.problems.append("the CSV file has no uid column (or username, or login)")
        return records
    seen: dict[str, str] = {}
    for number, row in enumerate(reader, start=2):
        fields: dict[str, str] = {}
        for name, value in row.items():
            key = _COLUMNS.get((name or "").strip().lower())
            if key and isinstance(value, str) and value.strip():
                fields.setdefault(key, value.strip())
        if not fields:
            continue
        _add(records, _user(f"line {number}", fields, records), seen, f"line {number}")
    return records


def _logical_lines(text: str) -> list[tuple[int, str]]:
    """Unfolded LDIF lines with the number each began on; comments dropped.

    A line starting with one space continues the one before it (RFC 2849).
    """
    unfolded: list[tuple[int, str]] = []
    for number, raw in enumerate(text.lstrip("﻿").splitlines(), start=1):
        if raw.startswith(" ") and unfolded and unfolded[-1][1]:
            unfolded[-1] = (unfolded[-1][0], unfolded[-1][1] + raw[1:])
        elif raw.startswith("#"):
            continue
        else:
            unfolded.append((number, raw.rstrip("\r")))
    return unfolded


def _entries(text: str, records: Records) -> list[tuple[int, list[tuple[str, str]]]]:
    """LDIF entries as (first line, [(attribute, value)]) -- decoded, in order."""
    entries: list[tuple[int, list[tuple[str, str]]]] = []
    current: list[tuple[str, str]] = []
    start = 0
    for number, line in _logical_lines(text) + [(0, "")]:
        if not line.strip():
            if current:
                entries.append((start, current))
            current = []
            continue
        name, sep, value = line.partition(":")
        if not sep:
            records.problems.append(f"line {number}: {line[:40]!r} is not 'attribute: value'")
            continue
        if not current:
            start = number
        if value.startswith(":"):
            try:
                value = base64.b64decode(value[1:].strip(), validate=True).decode("utf-8")
            except (binascii.Error, UnicodeDecodeError):
                records.problems.append(f"line {number}: {name}'s base64 value cannot be decoded")
                continue
        elif value.startswith("<"):
            records.problems.append(f"line {number}: {name} names a file to read, which is not supported")
            continue
        else:
            value = value.strip()
        current.append((name.strip(), value))
    return entries


def parse_ldif(text: str) -> Records:
    records = Records()
    people: list[tuple[str, dict[str, str], str, str]] = []  # where, fields, dn, uid-as-given
    groups: list[tuple[str, str, list[str]]] = []  # where, name, member DNs
    for start, attributes in _entries(text, records):
        where = f"line {start}"
        values: dict[str, list[str]] = {}
        for name, value in attributes:
            values.setdefault(name.lower(), []).append(value)
        if "version" in values and len(values) == 1:
            continue
        dn = (values.get("dn") or [""])[0]
        if not dn:
            records.problems.append(f"{where}: an entry with no dn")
            continue
        change = (values.get("changetype") or ["add"])[0].lower()
        if change != "add":
            records.problems.append(f"{where}: changetype {change} is an edit, and only additions are read")
            continue
        classes = {value.lower() for value in values.get("objectclass", [])}
        if classes & _PERSON_CLASSES:
            uid = (values.get("uid") or [first_value(dn, "uid") or ""])[0]
            fields = {
                "uid": uid,
                "given_name": (values.get("givenname") or [""])[0],
                "surname": (values.get("sn") or [""])[0],
                "display_name": (values.get("displayname") or values.get("cn") or [""])[0],
                "mail": (values.get("mail") or [""])[0],
            }
            secret = (values.get("userpassword") or [""])[0]
            if secret.startswith("{"):
                fields["password_hash"] = secret
            elif secret:
                fields["password"] = secret
            people.append((where, fields, dn, uid))
        elif classes & _GROUP_CLASSES:
            name = (values.get("cn") or [first_value(dn, "cn") or ""])[0]
            members = values.get("member", []) + values.get("uniquemember", [])
            groups.append((where, name, members))
        else:
            records.problems.append(f"{where}: {dn} is neither a person nor a group, so it is skipped")

    seen: dict[str, str] = {}
    by_dn: dict[str, str] = {}
    for where, fields, dn, _ in people:
        user = _user(where, fields, records)
        _add(records, user, seen, where)
        if user is not None:
            by_dn[dn.lower()] = user.uid
    for where, name, members in groups:
        if not name:
            records.problems.append(f"{where}: a group with no cn")
            continue
        logins: list[str] = []
        for member in members:
            login = by_dn.get(member.lower()) or normalise_login(first_value(member, "uid") or "")
            if login in seen:
                logins.append(login)
            elif member:
                records.problems.append(f"{where}: {name} lists {member}, who is not a person in this file")
        records.groups[name] = tuple(dict.fromkeys(logins))
    return records
