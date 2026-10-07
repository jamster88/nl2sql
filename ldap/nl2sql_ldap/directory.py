"""Reading and writing the directory's people and groups.

Three callers, one set of operations: the container seeding a standalone
directory (as root, over the local socket), the replica copying a primary
(the same), and the auth service's web interface (as its own account, over
TLS). Each holds an ldap3 connection and hands it here.

Two things are done explicitly that slapd's overlays would also do, because
code that relies on an overlay is code that only works against this one
server: removing a person removes them from every group first (refint would),
and a group's members are read from the group rather than from `memberOf`.

Passwords are only ever *set*, never read. A plain one goes through the
Password Modify extended operation, so slapd hashes it with the scheme it is
configured for; a ready-made hash from another directory is stored as given.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable

from ldap3 import BASE, LEVEL, MODIFY_ADD, MODIFY_DELETE, MODIFY_REPLACE
from ldap3.core.exceptions import LDAPException

from .layout import Layout, login_problem
from .records import Records, UserRecord, hash_problem

PERSON_CLASSES = ["top", "person", "organizationalPerson", "inetOrgPerson"]
GROUP_CLASSES = ["top", "groupOfNames"]

#: Read for every person. `seeAlso` is the primary's DN for a replica's
#: copy -- what a bind is passed through to -- and `pwdAccountLockedTime`
#: is set by the password policy while an account is locked out.
PERSON_ATTRIBUTES = ["uid", "cn", "sn", "givenName", "mail", "displayName", "seeAlso", "pwdAccountLockedTime"]

#: Page size for every search: slapd answers at most 500 entries to an
#: ordinary account without paging.
PAGE = 200


class DirectoryError(RuntimeError):
    """An operation the directory refused, with a code a page can branch on."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Person:
    """One person as the directory holds them."""

    uid: str
    cn: str
    sn: str = ""
    given_name: str = ""
    mail: str = ""
    display_name: str = ""
    groups: tuple[str, ...] = ()
    #: The primary's DN for a replica's copy; empty in a standalone one.
    upstream: str = ""
    locked: bool = False
    #: When the password policy locked them (`pwdAccountLockedTime`, LDAP
    #: generalized time); empty when it has not.
    locked_since: str = ""

    @property
    def name(self) -> str:
        return self.display_name or self.cn or self.uid


@dataclass
class ImportSummary:
    """What applying a file did."""

    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    passwords: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _first(attributes: dict, name: str) -> str:
    value = attributes.get(name)
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return "" if value is None else str(value)


def _all(attributes: dict, name: str) -> list[str]:
    value = attributes.get(name)
    if value is None:
        return []
    return [str(item) for item in value] if isinstance(value, list) else [str(value)]


class Directory:
    """One directory, through one connection."""

    def __init__(
        self,
        conn,
        layout: Layout,
        *,
        set_password: Callable[[object, str, str], bool] | None = None,
    ) -> None:
        self.conn = conn
        self.layout = layout
        self._set_password = set_password or _password_modify

    # --- plumbing --------------------------------------------------------

    def _check(self, ok: bool, what: str) -> None:
        if not ok:
            result = self.conn.result or {}
            detail = result.get("message") or result.get("description") or "refused"
            code = str(result.get("description") or "refused").replace(" ", "_")
            raise DirectoryError(code, f"{what}: {detail}")

    def _search(self, base: str, query: str, scope, attributes: list[str]) -> list[dict]:
        try:
            found = self.conn.extend.standard.paged_search(
                base, query, search_scope=scope, attributes=attributes, paged_size=PAGE, generator=False
            )
        except LDAPException as exc:
            raise DirectoryError("search_failed", f"searching {base}: {exc}") from exc
        return [entry for entry in found or [] if entry.get("type") == "searchResEntry"]

    def exists(self, dn: str) -> bool:
        try:
            return bool(self.conn.search(dn, "(objectClass=*)", search_scope=BASE, attributes=["1.1"]))
        except LDAPException:
            return False

    def ensure(self, dn: str, classes: list[str], attributes: dict) -> bool:
        """Create `dn` unless it exists. True when it was created."""
        if self.exists(dn):
            return False
        self._check(self.conn.add(dn, classes, attributes), f"creating {dn}")
        return True

    # --- the tree --------------------------------------------------------

    def ensure_base(self, organisation: str) -> list[str]:
        """The base entry, the four branches and the placeholder member."""
        created: list[str] = []
        dc = self.layout.base_dn.split(",", 1)[0].partition("=")[2]
        if self.ensure(self.layout.base_dn, ["top", "dcObject", "organization"], {"dc": dc, "o": organisation}):
            created.append(self.layout.base_dn)
        for unit in self.layout.organisational_units:
            name = unit.split(",", 1)[0].partition("=")[2]
            if self.ensure(unit, ["top", "organizationalUnit"], {"ou": name}):
                created.append(unit)
        if self.ensure(
            self.layout.placeholder,
            ["top", "organizationalRole"],
            {"cn": "nobody", "description": "Every group's placeholder member: no one."},
        ):
            created.append(self.layout.placeholder)
        return created

    def ensure_groups(self, names: Iterable[str]) -> list[str]:
        created = []
        for name in names:
            if self.ensure(
                self.layout.group(name),
                GROUP_CLASSES,
                {"cn": name, "member": [self.layout.placeholder]},
            ):
                created.append(name)
        return created

    def ensure_service_account(self, password: str) -> None:
        """The auth service's account, with this start's password."""
        dn = self.layout.service_account
        self.ensure(
            dn,
            ["top", "organizationalRole", "simpleSecurityObject"],
            {
                "cn": dn.split(",", 1)[0].partition("=")[2],
                "userPassword": "{CRYPT}!",  # replaced below; the class requires one
                "description": "The auth service: reads people and groups, and edits them in a standalone directory.",
            },
        )
        self.set_password_of(dn, password)

    # --- reading ---------------------------------------------------------

    def groups(self) -> dict[str, set[str]]:
        """Every group here, with the logins of the people in it."""
        found: dict[str, set[str]] = {}
        for entry in self._search(self.layout.groups, "(objectClass=groupOfNames)", LEVEL, ["cn", "member"]):
            attributes = entry["attributes"]
            name = _first(attributes, "cn")
            members = {self.layout.login_of(dn) for dn in _all(attributes, "member")}
            found[name] = {login for login in members if login}
        return found

    def people(self) -> list[Person]:
        memberships: dict[str, list[str]] = {}
        for group, members in sorted(self.groups().items()):
            for login in members:
                memberships.setdefault(login, []).append(group)
        people = []
        for entry in self._search(self.layout.people, "(objectClass=inetOrgPerson)", LEVEL, PERSON_ATTRIBUTES):
            attributes = entry["attributes"]
            uid = _first(attributes, "uid")
            people.append(
                Person(
                    uid=uid,
                    cn=_first(attributes, "cn"),
                    sn=_first(attributes, "sn"),
                    given_name=_first(attributes, "givenName"),
                    mail=_first(attributes, "mail"),
                    display_name=_first(attributes, "displayName"),
                    groups=tuple(memberships.get(uid, ())),
                    upstream=_first(attributes, "seeAlso"),
                    locked=bool(_first(attributes, "pwdAccountLockedTime")),
                    locked_since=_first(attributes, "pwdAccountLockedTime"),
                )
            )
        return sorted(people, key=lambda person: person.uid)

    def person(self, uid: str) -> Person | None:
        return next((person for person in self.people() if person.uid == uid), None)

    # --- people ----------------------------------------------------------

    def _person_attributes(self, record: UserRecord) -> dict:
        attributes: dict = {"uid": record.uid, "cn": record.cn, "sn": record.sn}
        for key, value in (
            ("givenName", record.given_name),
            ("mail", record.mail),
            ("displayName", record.display_name),
        ):
            if value:
                attributes[key] = value
        return attributes

    def add_person(self, record: UserRecord) -> None:
        problem = login_problem(record.uid)
        if problem:
            raise DirectoryError("invalid_login", problem)
        dn = self.layout.user(record.uid)
        if self.exists(dn):
            raise DirectoryError("already_exists", f"{record.uid} already exists")
        self._check(self.conn.add(dn, PERSON_CLASSES, self._person_attributes(record)), f"creating {record.uid}")
        if record.password:
            self.set_password(record.uid, record.password)
        elif record.password_hash:
            self.set_password_hash(record.uid, record.password_hash)
        if record.groups:
            self.set_groups(record.uid, record.groups)

    def update_person(self, uid: str, **changes: str) -> None:
        """Replace the named attributes; an empty value removes an optional one."""
        dn = self._existing(uid)
        names = {"cn": "cn", "sn": "sn", "given_name": "givenName", "mail": "mail", "display_name": "displayName"}
        modifications = {}
        for key, value in changes.items():
            attribute = names[key]
            if value:
                modifications[attribute] = [(MODIFY_REPLACE, [value])]
            elif attribute in ("cn", "sn"):
                raise DirectoryError("required", f"{attribute} cannot be empty")
            else:
                modifications[attribute] = [(MODIFY_REPLACE, [])]
        if modifications:
            self._check(self.conn.modify(dn, modifications), f"updating {uid}")

    def delete_person(self, uid: str) -> None:
        dn = self._existing(uid)
        self.set_groups(uid, ())
        self._check(self.conn.delete(dn), f"removing {uid}")

    def _existing(self, uid: str) -> str:
        dn = self.layout.user(uid)
        if not self.exists(dn):
            raise DirectoryError("not_found", f"there is nobody called {uid}")
        return dn

    def set_password(self, uid: str, password: str) -> None:
        self.set_password_of(self._existing(uid), password)

    def set_password_of(self, dn: str, password: str) -> None:
        self._check(self._set_password(self.conn, dn, password), f"setting the password of {dn}")

    def set_password_hash(self, uid: str, hashed: str) -> None:
        problem = hash_problem(hashed)
        if problem:
            raise DirectoryError("invalid_hash", problem)
        dn = self._existing(uid)
        self._check(
            self.conn.modify(dn, {"userPassword": [(MODIFY_REPLACE, [hashed])]}),
            f"storing the password hash of {uid}",
        )

    def unlock(self, uid: str) -> None:
        """Clear a lockout the password policy set after too many wrong passwords."""
        dn = self._existing(uid)
        self.conn.modify(dn, {"pwdAccountLockedTime": [(MODIFY_DELETE, [])]})
        # Not checked: an account that was not locked has nothing to clear,
        # and "no such attribute" is that, not a failure.

    # --- groups ----------------------------------------------------------

    def _members(self, group: str) -> list[str]:
        found = self.conn.search(self.layout.group(group), "(objectClass=groupOfNames)", search_scope=BASE, attributes=["member"])
        if not found:
            raise DirectoryError("not_found", f"there is no group called {group}")
        return _all(self.conn.response[0]["attributes"], "member")

    def add_member(self, group: str, uid: str) -> None:
        dn = self.layout.user(uid)
        if dn.lower() in {member.lower() for member in self._members(group)}:
            return
        self._check(
            self.conn.modify(self.layout.group(group), {"member": [(MODIFY_ADD, [dn])]}),
            f"adding {uid} to {group}",
        )

    def remove_member(self, group: str, uid: str) -> None:
        dn = self.layout.user(uid)
        present = [member for member in self._members(group) if member.lower() == dn.lower()]
        if not present:
            return
        self._check(
            self.conn.modify(self.layout.group(group), {"member": [(MODIFY_DELETE, present)]}),
            f"removing {uid} from {group}",
        )

    def set_groups(self, uid: str, groups: Iterable[str]) -> None:
        """Make `uid` a member of exactly `groups` among the existing ones."""
        wanted = set(groups)
        current = self.groups()
        unknown = sorted(wanted - set(current))
        if unknown:
            raise DirectoryError("not_found", f"there is no group called {', '.join(unknown)}")
        for group, members in sorted(current.items()):
            if group in wanted and uid not in members:
                self.add_member(group, uid)
            elif group not in wanted and uid in members:
                self.remove_member(group, uid)

    def set_members(self, group: str, logins: Iterable[str]) -> None:
        """Make `group` hold exactly these people (and the placeholder)."""
        members = [self.layout.placeholder] + [self.layout.user(login) for login in sorted(set(logins))]
        self._check(
            self.conn.modify(self.layout.group(group), {"member": [(MODIFY_REPLACE, members)]}),
            f"setting the members of {group}",
        )

    def create_group(self, name: str, description: str = "") -> None:
        attributes = {"cn": name, "member": [self.layout.placeholder]}
        if description:
            attributes["description"] = description
        if self.exists(self.layout.group(name)):
            raise DirectoryError("already_exists", f"the group {name} already exists")
        self._check(self.conn.add(self.layout.group(name), GROUP_CLASSES, attributes), f"creating {name}")

    def delete_group(self, name: str) -> None:
        if not self.exists(self.layout.group(name)):
            raise DirectoryError("not_found", f"there is no group called {name}")
        self._check(self.conn.delete(self.layout.group(name)), f"removing {name}")

    # --- a file ----------------------------------------------------------

    def apply(self, records: Records) -> ImportSummary:
        """Load a file's people and groups: new people created, existing ones updated.

        Memberships are added, never taken away: a file lists who should be
        in a group, not everyone who should not, and an import that quietly
        removed the administrator from nl2sql-admins would lock the door
        behind it.
        """
        summary = ImportSummary(problems=list(records.problems))
        existing = {person.uid for person in self.people()}
        groups = self.groups()
        for record in records.users:
            try:
                if record.uid in existing:
                    self.update_person(
                        record.uid,
                        cn=record.cn,
                        sn=record.sn,
                        given_name=record.given_name,
                        mail=record.mail,
                        display_name=record.display_name,
                    )
                    summary.updated.append(record.uid)
                    if record.password:
                        self.set_password(record.uid, record.password)
                    elif record.password_hash:
                        self.set_password_hash(record.uid, record.password_hash)
                else:
                    self.add_person(UserRecord(**{**record.__dict__, "groups": ()}))
                    summary.created.append(record.uid)
                if record.password or record.password_hash:
                    summary.passwords.append(record.uid)
            except DirectoryError as exc:
                summary.problems.append(f"{record.uid}: {exc}")
        imported = set(summary.created) | set(summary.updated)
        for group, logins in sorted(records.memberships().items()):
            if group not in groups:
                summary.problems.append(f"{group} is not a group in this directory; its members were not added")
                continue
            for login in sorted(logins & imported):
                self.add_member(group, login)
                summary.groups.append(f"{login} -> {group}")
        return summary


def _password_modify(conn, dn: str, password: str) -> bool:
    """RFC 3062: the server hashes the password with its configured scheme."""
    return bool(conn.extend.standard.modify_password(user=dn, new_password=password))
