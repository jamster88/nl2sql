"""Copying a primary directory into this one, on an interval.

A copy by search rather than by replication protocol, because the protocols
are not shared: OpenLDAP's syncrepl (RFC 4533) is not something Active
Directory speaks, and AD's DirSync is not something anything else does. Every
directory answers a paged search, so that is what this does -- read the
mirrored groups and their people, and make the local tree say exactly that.

What is copied:

* **groups** named in LDAP_REPLICA_GROUPS (by `cn`, or by DN), each under its
  local name -- `NL2SQL Reviewers=nl2sql-reviewers` -- with every person in
  it, through nested groups, resolved here: `member` is a DN that may be
  another group, and only AD knows its own transitive-membership rule;
* **people** in those groups (or every person the filter matches, with
  LDAP_REPLICA_ONLY_GROUP_MEMBERS=false), under `uid=<login>`, with the
  primary's DN in `seeAlso` -- which is what a bind is passed through to;
* **no passwords**. A bind to a copied person is checked by the primary,
  through slapd's `remoteauth` overlay. AD will not hand out a hash, and a
  copy of one would be a second place to steal it from.

A primary that suddenly answers with nobody is far more often a changed
filter or a moved base than an organisation with no staff, so a copy that
would empty a directory with people in it is refused unless
LDAP_REPLICA_ALLOW_EMPTY=true.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import ssl
import threading
from dataclasses import dataclass, field
from typing import Callable

from ldap3 import BASE, MODIFY_REPLACE, NONE, SUBTREE, Connection, Server, Tls
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars
from ldap3.utils.dn import parse_dn

from .directory import PERSON_CLASSES, Directory, DirectoryError
from .layout import Layout, login_problem, normalise_login
from .settings import UpstreamSettings

#: How deep nested groups are followed. A cycle is stopped by the visited
#: set; this stops a pathological chain.
MAX_NESTING = 16

#: How many skipped names the status entry keeps.
STATUS_LIMIT = 25

#: The local attribute each upstream one is copied into.
COPIED = {"cn": "cn", "sn": "sn", "givenName": "givenName", "mail": "mail", "displayName": "displayName"}


class ReplicaError(RuntimeError):
    """A copy that was not made, and why."""


def dn_key(dn: str) -> tuple:
    """A DN in a form two spellings of it agree on: case, spacing, escapes."""
    try:
        return tuple((name.lower(), value.lower()) for name, value, _ in parse_dn(dn, strip=True))
    except Exception:  # noqa: BLE001 - an unparseable DN only has to equal itself
        return (dn.lower(),)


@dataclass(frozen=True)
class RemotePerson:
    dn: str
    login: str
    attributes: dict = field(default_factory=dict, hash=False, compare=False)


@dataclass
class Snapshot:
    """What the primary holds that this directory should."""

    people: dict[str, RemotePerson] = field(default_factory=dict)
    groups: dict[str, set[str]] = field(default_factory=dict)
    missing_groups: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def connect_upstream(settings: UpstreamSettings) -> Connection:
    """A read-only, bound connection to the primary."""
    tls = Tls(
        validate=ssl.CERT_REQUIRED if settings.verify else ssl.CERT_NONE,
        ca_certs_file=settings.cacert,
    )
    server = Server(
        settings.uri,
        use_ssl=settings.uri.lower().startswith("ldaps://"),
        tls=tls,
        connect_timeout=settings.timeout_seconds,
        get_info=NONE,
    )
    conn = Connection(
        server,
        user=settings.bind_dn,
        password=settings.bind_password,
        read_only=True,
        auto_referrals=False,
        receive_timeout=settings.timeout_seconds,
        raise_exceptions=True,
    )
    conn.open()
    if settings.starttls:
        conn.start_tls()
    conn.bind()
    return conn


def _values(attributes: dict, name: str) -> list[str]:
    for key, value in attributes.items():
        if key.lower() == name.lower():
            if isinstance(value, list):
                return [str(item) for item in value]
            return [] if value is None else [str(value)]
    return []


class Upstream:
    """The primary, read whole, once per copy."""

    def __init__(
        self,
        settings: UpstreamSettings,
        *,
        connect: Callable[[UpstreamSettings], Connection] = connect_upstream,
    ) -> None:
        self.settings = settings
        self._connect = connect

    def _search(self, conn, base: str, query: str, attributes: list[str], scope=SUBTREE) -> list[dict]:
        found = conn.extend.standard.paged_search(
            base,
            query,
            search_scope=scope,
            attributes=attributes,
            paged_size=self.settings.page_size,
            generator=False,
        )
        # A referral (AD hands them out for its DNS zones) is not an entry.
        return [entry for entry in found or [] if entry.get("type") == "searchResEntry"]

    def read(self) -> Snapshot:
        try:
            conn = self._connect(self.settings)
        except LDAPException as exc:
            raise ReplicaError(f"cannot reach {self.settings.uri}: {exc}") from exc
        try:
            return self._read(conn)
        except LDAPException as exc:
            raise ReplicaError(f"reading {self.settings.uri}: {exc}") from exc
        finally:
            conn.unbind()

    def _read(self, conn) -> Snapshot:
        s = self.settings
        snapshot = Snapshot()
        login = s.login_attribute
        people_by_dn: dict[tuple, RemotePerson] = {}
        people_by_login: dict[str, RemotePerson] = {}
        for entry in self._search(conn, s.user_base, s.user_filter, [login, *COPIED]):
            attributes = entry["attributes"]
            raw = (_values(attributes, login) or [""])[0]
            name = normalise_login(raw)
            problem = login_problem(name) if name else f"{entry['dn']} has no {login}"
            if problem:
                snapshot.skipped.append(problem)
                continue
            if name in people_by_login:
                snapshot.skipped.append(f"{entry['dn']}: {name} is already {people_by_login[name].dn}")
                continue
            copied = {local: (_values(attributes, remote) or [""])[0] for remote, local in COPIED.items()}
            person = RemotePerson(dn=entry["dn"], login=name, attributes=copied)
            people_by_dn[dn_key(entry["dn"])] = person
            people_by_login[name] = person

        member = s.member_attribute
        groups_by_dn: dict[tuple, list[str]] = {}
        for wanted, local in s.groups.items():
            if "=" in wanted:
                found = self._search(conn, wanted, "(objectClass=*)", [member], scope=BASE)
            else:
                query = f"(&{s.group_filter}(cn={escape_filter_chars(wanted)}))"
                found = self._search(conn, s.group_base, query, [member])
            if not found:
                snapshot.missing_groups.append(wanted)
                snapshot.groups[local] = set()
                continue
            members = snapshot.groups.setdefault(local, set())
            for entry in found:
                groups_by_dn[dn_key(entry["dn"])] = _values(entry["attributes"], member)
                members |= self._members(conn, entry["dn"], groups_by_dn, people_by_dn, people_by_login, set(), 0)

        if s.only_group_members:
            wanted_logins = set().union(*snapshot.groups.values()) if snapshot.groups else set()
            snapshot.people = {name: people_by_login[name] for name in sorted(wanted_logins)}
        else:
            snapshot.people = dict(sorted(people_by_login.items()))
        return snapshot

    def _members(self, conn, dn, groups_by_dn, people_by_dn, people_by_login, seen, depth) -> set[str]:
        """The logins in a group, through any groups nested in it."""
        key = dn_key(dn)
        if key in seen or depth > MAX_NESTING:
            return set()
        seen.add(key)
        # Always known by now: a mirrored group was read before it was
        # walked, and a nested one by `_is_group` before it was followed.
        logins: set[str] = set()
        for value in groups_by_dn[key]:
            if self.settings.member_attribute.lower() == "memberuid":
                name = normalise_login(value)
                if name in people_by_login:
                    logins.add(name)
                continue
            person = people_by_dn.get(dn_key(value))
            if person is not None:
                logins.add(person.login)
            elif self._is_group(conn, value, groups_by_dn):
                logins |= self._members(conn, value, groups_by_dn, people_by_dn, people_by_login, seen, depth + 1)
        return logins

    def _is_group(self, conn, dn: str, groups_by_dn: dict) -> bool:
        """Whether a member that is not a copied person is a group to look inside.

        Asked of the primary once per DN: a person the filter left out (a
        disabled account) answers with no member attribute and is skipped.
        """
        key = dn_key(dn)
        if key in groups_by_dn:
            return True
        found = self._search(conn, dn, "(objectClass=*)", [self.settings.member_attribute], scope=BASE)
        values = _values(found[0]["attributes"], self.settings.member_attribute) if found else []
        if not values:
            return False
        groups_by_dn[key] = values
        return True


@dataclass
class Changes:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.added or self.updated or self.removed or self.groups)


def apply(directory: Directory, snapshot: Snapshot, *, allow_empty: bool = False) -> Changes:
    """Make the local people and groups say what the snapshot says."""
    layout = directory.layout
    changes = Changes()
    local = {person.uid: person for person in directory.people()}
    if local and not snapshot.people and not allow_empty:
        raise ReplicaError(
            f"the primary returned nobody, which would remove all {len(local)} people here; "
            "check LDAP_UPSTREAM_BASE_DN, the filters and LDAP_REPLICA_GROUPS, or set "
            "LDAP_REPLICA_ALLOW_EMPTY=true if it really is empty"
        )
    conn = directory.conn
    for login, remote in snapshot.people.items():
        wanted = {key: value for key, value in remote.attributes.items() if value}
        wanted.setdefault("cn", login)
        wanted.setdefault("sn", login)
        wanted["seeAlso"] = remote.dn
        current = local.get(login)
        if current is None:
            directory._check(
                conn.add(layout.user(login), PERSON_CLASSES, {"uid": login, **wanted}), f"copying {login}"
            )
            changes.added.append(login)
            continue
        have = {
            "cn": current.cn,
            "sn": current.sn,
            "givenName": current.given_name,
            "mail": current.mail,
            "displayName": current.display_name,
            "seeAlso": current.upstream,
        }
        modifications = {
            key: [(MODIFY_REPLACE, [wanted[key]] if wanted.get(key) else [])]
            for key in have
            if (wanted.get(key) or "") != (have[key] or "")
        }
        if modifications:
            directory._check(conn.modify(layout.user(login), modifications), f"updating {login}")
            changes.updated.append(login)
    for login in sorted(set(local) - set(snapshot.people)):
        directory.delete_person(login)
        changes.removed.append(login)

    existing = directory.groups()
    for name, members in sorted(snapshot.groups.items()):
        members = {login for login in members if login in snapshot.people}
        if name not in existing:
            directory.create_group(name, "Copied from the primary directory.")
            existing[name] = set()
        if existing[name] != members:
            directory.set_members(name, members)
            changes.groups.append(name)
    for name in sorted(set(existing) - set(snapshot.groups)):
        directory.delete_group(name)
        changes.groups.append(name)
    return changes


def status_entry(directory: Directory, record: dict) -> None:
    """What the last copy did, where the auth service can read it."""
    dn = directory.layout.replica_status
    text = json.dumps(record, sort_keys=True)
    if not directory.ensure(dn, ["top", "organizationalRole"], {"cn": "replica-status", "description": text}):
        directory._check(
            directory.conn.modify(dn, {"description": [(MODIFY_REPLACE, [text])]}),
            "recording the copy's status",
        )


class Replicator:
    """The copy, repeated until told to stop."""

    def __init__(
        self,
        settings: UpstreamSettings,
        local: Callable[[], Directory],
        *,
        upstream: Upstream | None = None,
        clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.timezone.utc),
        log: Callable[[str], None] = print,
    ) -> None:
        self.settings = settings
        self._local = local
        self._upstream = upstream or Upstream(settings)
        self._clock = clock
        self._log = log
        self._allow_empty = os.getenv("LDAP_REPLICA_ALLOW_EMPTY", "").strip().lower() in {"1", "true", "yes", "on"}

    def run_once(self) -> bool:
        """One copy. True when it succeeded; either way the status says what happened."""
        directory = self._local()
        record: dict = {"upstream": self.settings.uri, "at": self._clock().isoformat()}
        try:
            snapshot = self._upstream.read()
            changes = apply(directory, snapshot, allow_empty=self._allow_empty)
        except (ReplicaError, DirectoryError) as exc:
            record.update(ok=False, error=str(exc))
            self._log(f"replica: copy from {self.settings.uri} failed: {exc}")
            status_entry(directory, record)
            return False
        record.update(
            ok=True,
            people=len(snapshot.people),
            groups={name: len(members) for name, members in sorted(snapshot.groups.items())},
            missing_groups=snapshot.missing_groups,
            skipped=snapshot.skipped[:STATUS_LIMIT],
            skipped_count=len(snapshot.skipped),
        )
        status_entry(directory, record)
        if changes:
            self._log(
                f"replica: {len(changes.added)} added, {len(changes.updated)} updated, "
                f"{len(changes.removed)} removed, groups changed: {', '.join(changes.groups) or 'none'}"
            )
        for name in snapshot.missing_groups:
            self._log(f"replica: the primary has no group {name}, so its copy is empty")
        return True

    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            self.run_once()
            stop.wait(self.settings.interval_seconds)
