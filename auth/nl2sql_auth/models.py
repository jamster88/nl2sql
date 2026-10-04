"""What the auth service sends and accepts.

`extra="forbid"` on everything a client sends, so a misspelt field is a 422
rather than silently ignored -- `pasword` must not sign anybody in with an
empty password. The error envelope is the other services' own, so a client
that already reads one reads this.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

#: A file pasted or uploaded into the web interface's import: a few thousand
#: people as CSV is well under this.
MAX_IMPORT_BYTES = 5_000_000


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApiErrorBody(BaseModel):
    code: str
    message: str
    detail: dict[str, Any] | None = None


class ApiError(BaseModel):
    error: ApiErrorBody

    @classmethod
    def of(cls, code: str, message: str, **detail: Any) -> "ApiError":
        return cls(error=ApiErrorBody(code=code, message=message, detail=detail or None))


class Health(BaseModel):
    status: str = "ok"
    version: str
    uptime_seconds: float


class Check(BaseModel):
    ok: bool
    detail: str


class Readiness(BaseModel):
    ready: bool
    checks: dict[str, Check]
    warnings: list[str] = Field(default_factory=list)


class SignIn(_Strict):
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=1024)


class Session(BaseModel):
    """Who is signed in, and until when (seconds since the epoch)."""

    user: str
    name: str
    roles: list[str]
    kind: str
    expires_at: int


class Token(Session):
    """A session for a client that is not a browser: it holds the token itself."""

    token: str


class PasswordChange(_Strict):
    current: str = Field(min_length=1, max_length=1024)
    new: str = Field(min_length=1, max_length=1024)


class AuthMeta(BaseModel):
    """What a sign-in form needs to know before anyone has signed in."""

    version: str
    mode: str
    #: Whether people and groups can be edited here: false for a replica,
    #: whose people come from its primary.
    directory_editable: bool
    session_hours: float
    min_password_length: int


# --- the directory's web interface ------------------------------------------


class Person(BaseModel):
    uid: str
    name: str
    cn: str
    sn: str
    given_name: str
    mail: str
    display_name: str
    groups: list[str]
    locked: bool


class PersonList(BaseModel):
    people: list[Person]
    count: int


class NewPerson(_Strict):
    uid: str = Field(min_length=1, max_length=63)
    given_name: str = Field(default="", max_length=256)
    surname: str = Field(default="", max_length=256)
    display_name: str = Field(default="", max_length=256)
    mail: str = Field(default="", max_length=256)
    groups: list[str] = Field(default_factory=list)
    password: str | None = Field(default=None, max_length=1024)


class PersonChange(_Strict):
    given_name: str | None = Field(default=None, max_length=256)
    surname: str | None = Field(default=None, max_length=256)
    display_name: str | None = Field(default=None, max_length=256)
    mail: str | None = Field(default=None, max_length=256)
    groups: list[str] | None = None


class NewPassword(_Strict):
    password: str = Field(min_length=1, max_length=1024)


class Group(BaseModel):
    name: str
    #: The Postgres role membership grants, or None for a group no role maps to.
    role: str | None
    members: list[str]


class GroupList(BaseModel):
    groups: list[Group]


class ImportRequest(_Strict):
    filename: str = Field(min_length=1, max_length=256)
    content: str = Field(max_length=MAX_IMPORT_BYTES)


class ImportResult(BaseModel):
    created: list[str]
    updated: list[str]
    passwords: list[str]
    groups: list[str]
    problems: list[str]


class SyncReport(BaseModel):
    at: str
    ok: bool
    people: int
    created: list[str]
    removed: list[str]
    changed: list[str]
    conflicts: list[str]
    skipped: list[str]
    errors: list[str]


class DirectoryMeta(BaseModel):
    version: str
    mode: str
    base_dn: str
    people: int
    groups: list[Group]
    min_password_length: int
    role_sync: SyncReport | None
