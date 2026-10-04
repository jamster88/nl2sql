"""What every start makes sure of, and what only the first start does.

Every start, in both modes: the tree's branches, the placeholder every group
holds, and the auth service's account with the password the environment
gives it -- so rotating that password is an edit to `.env` and a restart.

Every start of a standalone directory, as well: the four groups the services
grant access by, and the password policy, rewritten from the environment.

Only on a standalone directory's *first* start -- when it has nobody in it --
the first administrator, in every group, and then the seed file if one is
named. After that the directory is the source of truth, and the web interface
is how it changes: re-reading the seed on every start would put back people an
administrator had removed.

A replica gets none of the people or groups here: those are the primary's,
and arrive with the first copy (`replica.py`).
"""

from __future__ import annotations

from pathlib import Path

from ldap3 import MODIFY_REPLACE

from .directory import Directory
from .records import UserRecord, parse
from .settings import DirectorySettings


def ensure_policy(directory: Directory, settings: DirectorySettings) -> None:
    """The password policy, written from the environment on every start."""
    dn = directory.layout.password_policy
    lockout = settings.lockout_failures > 0
    values = {
        "pwdAttribute": "userPassword",
        "pwdLockout": "TRUE" if lockout else "FALSE",
        "pwdMaxFailure": str(settings.lockout_failures),
        "pwdLockoutDuration": str(settings.lockout_seconds),
        "pwdFailureCountInterval": str(settings.lockout_seconds),
        "pwdMinLength": str(settings.min_password_length),
        # 1: check a password's length when it arrives in the clear, and
        # accept one that arrives already hashed (an import), which cannot
        # be checked.
        "pwdCheckQuality": "1",
        "pwdAllowUserChange": "TRUE",
    }
    if not directory.ensure(dn, ["top", "device", "pwdPolicy"], {"cn": "default", **values}):
        directory._check(
            directory.conn.modify(dn, {key: [(MODIFY_REPLACE, [value])] for key, value in values.items()}),
            "updating the password policy",
        )


def bootstrap(directory: Directory, settings: DirectorySettings) -> list[str]:
    """Bring the directory to the shape the environment describes. Returns what was done."""
    notes: list[str] = []
    created = directory.ensure_base(settings.organisation)
    if created:
        notes.append(f"created {', '.join(created)}")
    assert settings.service_password, "settings.problems() is checked before starting"
    directory.ensure_service_account(settings.service_password)
    notes.append(f"the auth service's account is {directory.layout.service_account}")
    if settings.replica:
        notes.append("replica: people and groups come from the primary")
        return notes

    groups = directory.ensure_groups(settings.groups)
    if groups:
        notes.append(f"created the groups {', '.join(groups)}")
    ensure_policy(directory, settings)
    if directory.people():
        return notes

    assert settings.admin_password, "settings.problems() is checked before starting"
    directory.add_person(
        UserRecord(
            uid=settings.admin_user,
            display_name=settings.admin_name,
            password=settings.admin_password,
            groups=settings.groups,
        )
    )
    notes.append(f"first start: created {settings.admin_user} in {', '.join(settings.groups)}")
    if settings.seed_file:
        path = Path(settings.seed_file)
        if not path.is_file():
            notes.append(f"LDAP_SEED_FILE={path} is not a file, so nobody else was loaded")
            return notes
        summary = directory.apply(parse(path.read_text(encoding="utf-8"), filename=path.name))
        notes.append(
            f"loaded {path.name}: {len(summary.created)} created, {len(summary.updated)} updated, "
            f"{len(summary.groups)} memberships"
        )
        notes.extend(f"  {problem}" for problem in summary.problems)
    return notes
