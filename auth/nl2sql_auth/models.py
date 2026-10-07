"""What the auth service sends and accepts.

`extra="forbid"` on every model, so a misspelt field a client sends is a 422
rather than silently ignored -- `pasword` must not sign anybody in with an
empty password -- and a response built from a record that has grown a field
fails a test rather than dropping it. The error envelope is the other services' own, so a client
that already reads one reads this.
"""

from __future__ import annotations


from pydantic import Field
from nl2sql_common.envelope import Wire

#: A file pasted or uploaded into the web interface's import: a few thousand
#: people as CSV is well under this.
MAX_IMPORT_BYTES = 5_000_000


class SignIn(Wire):
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=1024)


class Session(Wire):
    """Who is signed in, and until when (seconds since the epoch)."""

    user: str
    name: str
    roles: list[str]
    kind: str
    expires_at: int


class Token(Session):
    """A session for a client that is not a browser: it holds the token itself."""

    token: str


class PasswordChange(Wire):
    current: str = Field(min_length=1, max_length=1024)
    new: str = Field(min_length=1, max_length=1024)


class AuthMeta(Wire):
    """What a sign-in form needs to know before anyone has signed in."""

    version: str
    mode: str
    #: Whether people and groups can be edited here: false for a replica,
    #: whose people come from its primary.
    directory_editable: bool
    session_hours: float
    min_password_length: int


# --- the directory's web interface ------------------------------------------


class Person(Wire):
    uid: str
    name: str
    cn: str
    sn: str
    given_name: str
    mail: str
    display_name: str
    groups: list[str]
    locked: bool


class PersonList(Wire):
    people: list[Person]
    count: int


class NewPerson(Wire):
    uid: str = Field(min_length=1, max_length=63)
    given_name: str = Field(default="", max_length=256)
    surname: str = Field(default="", max_length=256)
    display_name: str = Field(default="", max_length=256)
    mail: str = Field(default="", max_length=256)
    groups: list[str] = Field(default_factory=list)
    password: str | None = Field(default=None, max_length=1024)


class PersonChange(Wire):
    given_name: str | None = Field(default=None, max_length=256)
    surname: str | None = Field(default=None, max_length=256)
    display_name: str | None = Field(default=None, max_length=256)
    mail: str | None = Field(default=None, max_length=256)
    groups: list[str] | None = None


class NewPassword(Wire):
    password: str = Field(min_length=1, max_length=1024)


class Group(Wire):
    name: str
    #: The Postgres role membership grants, or None for a group no role maps to.
    role: str | None
    members: list[str]


class GroupList(Wire):
    groups: list[Group]


class ImportRequest(Wire):
    filename: str = Field(min_length=1, max_length=256)
    content: str = Field(max_length=MAX_IMPORT_BYTES)


class ImportResult(Wire):
    created: list[str]
    updated: list[str]
    passwords: list[str]
    groups: list[str]
    problems: list[str]


class SyncReport(Wire):
    at: str
    ok: bool
    people: int
    created: list[str]
    removed: list[str]
    changed: list[str]
    conflicts: list[str]
    skipped: list[str]
    errors: list[str]


class DirectoryMeta(Wire):
    version: str
    mode: str
    base_dn: str
    people: int
    groups: list[Group]
    min_password_length: int
    role_sync: SyncReport | None
