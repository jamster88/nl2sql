"""Where things live in the directory, and what a login name may be.

The tree is the same in both modes, so the retail database and the auth
service never need to know which one they are talking to:

    <base>
    ├── ou=people     uid=<login>       one per person (inetOrgPerson)
    ├── ou=groups     cn=<group>        groupOfNames, `member` holds people
    ├── ou=services   cn=nl2sql-auth    the auth service's own account
    │                 cn=nobody         every group's placeholder member
    │                 cn=replica-status what the last copy from the primary did
    └── ou=policies   cn=default        password policy (standalone)

A login name becomes a Postgres role, a bind DN and a cookie's subject, so it
is held to the narrowest spelling all three agree on: lower case, letters,
digits, `.`, `_` and `-`, at most 63 characters (Postgres's limit), starting
with a letter or digit. A name that is not -- `Jane Doe`, `j@corp` -- is
refused on the way in rather than quoted everywhere it goes.
"""

from __future__ import annotations

import re

from ldap3.utils.dn import escape_rdn, parse_dn

#: groupOfNames must have a member, and an empty group is an ordinary thing:
#: every group holds this entry, which is no one, so it can be emptied of
#: people without being deleted.
PLACEHOLDER = "nobody"

#: The auth service's account.
SERVICE_ACCOUNT = "nl2sql-auth"

#: Where a replica records its last copy, for the auth service to report.
REPLICA_STATUS = "replica-status"

_LOGIN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")

#: Names that are already roles in the retail database, or are spelled like
#: the ones this stack creates. A person called `postgres` would otherwise be
#: a directory entry whose password signs in as the superuser.
RESERVED = frozenset({"postgres", "public", "nl2sql", "nobody", "root"})
RESERVED_PREFIXES = ("pg_", "nl2sql_")


def login_problem(name: str) -> str | None:
    """Why `name` cannot be a login, or None when it can."""
    if not _LOGIN.match(name):
        return (
            f"{name!r} is not a usable login name: lower-case letters, digits, '.', '_' "
            "and '-', starting with a letter or digit, at most 63 characters"
        )
    if name in RESERVED or name.startswith(RESERVED_PREFIXES):
        return f"{name!r} is reserved for the database's own roles"
    return None


def normalise_login(name: str) -> str:
    """How a login typed into a form, or read from a primary, is compared.

    Directories match `uid` without regard to case; Postgres role names are
    case-sensitive. Lower-casing on every way in is what makes `Alice` and
    `alice` the same person in both.
    """
    return name.strip().lower()


def first_value(dn: str, attribute: str) -> str | None:
    """The value of a DN's first RDN when it is `attribute`, else None."""
    try:
        parts = parse_dn(dn, strip=True)
    except Exception:  # noqa: BLE001 - ldap3 raises its own several kinds, "" included
        return None
    name, value, _ = parts[0]
    return value if name.lower() == attribute.lower() else None


class Layout:
    """The DNs of one directory."""

    def __init__(self, base_dn: str) -> None:
        self.base_dn = base_dn
        self.people = f"ou=people,{base_dn}"
        self.groups = f"ou=groups,{base_dn}"
        self.services = f"ou=services,{base_dn}"
        self.policies = f"ou=policies,{base_dn}"
        self.placeholder = f"cn={PLACEHOLDER},{self.services}"
        self.service_account = f"cn={SERVICE_ACCOUNT},{self.services}"
        self.replica_status = f"cn={REPLICA_STATUS},{self.services}"
        self.password_policy = f"cn=default,{self.policies}"

    def user(self, login: str) -> str:
        return f"uid={escape_rdn(login)},{self.people}"

    def group(self, name: str) -> str:
        return f"cn={escape_rdn(name)},{self.groups}"

    def login_of(self, dn: str) -> str | None:
        """The login a person's DN names, or None when it is not a person here."""
        if not dn.lower().endswith("," + self.people.lower()):
            return None
        return first_value(dn, "uid")

    def group_of(self, dn: str) -> str | None:
        """The group a DN names, or None when it is not a group here."""
        if not dn.lower().endswith("," + self.groups.lower()):
            return None
        return first_value(dn, "cn")

    @property
    def organisational_units(self) -> tuple[str, ...]:
        return (self.people, self.groups, self.services, self.policies)
