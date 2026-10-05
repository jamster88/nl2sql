"""nl2sql_common.privileges: dropping root once a process has what it writes."""

from __future__ import annotations

import os
from types import SimpleNamespace

from nl2sql_common import privileges


class Calls:
    def __init__(self, uid: int = 0) -> None:
        self.log: list[tuple] = []
        self.uid = uid

    def kwargs(self, **extra):
        return dict(
            getuid=lambda: self.uid,
            lookup=lambda name: SimpleNamespace(pw_uid=10001, pw_gid=10002, pw_dir=f"/home/{name}"),
            setgroups=lambda groups: self.log.append(("setgroups", tuple(groups))),
            setgid=lambda gid: self.log.append(("setgid", gid)),
            setuid=lambda uid: self.log.append(("setuid", uid)),
            **extra,
        )


def test_as_root_it_gives_away_what_it_writes_then_drops(tmp_path):
    calls, environ = Calls(), {}
    chowned = []
    done = privileges.become(
        "nl2sql", own=[str(tmp_path / "a"), str(tmp_path / "b")], environ=environ,
        makedirs=os.makedirs, chown=lambda path, uid, gid: chowned.append((os.path.basename(path), uid, gid)),
        **calls.kwargs(),
    )
    assert done is True
    assert chowned == [("a", 10001, 10002), ("b", 10001, 10002)]
    assert calls.log == [("setgroups", ()), ("setgid", 10002), ("setuid", 10001)]
    assert environ["HOME"] == "/home/nl2sql"


def test_recursive_gives_what_an_older_release_wrote_there_as_root_too(tmp_path):
    """A key the auth service wrote as root before 6.2, 0600, which the
    account must read once it is no longer root."""
    (tmp_path / "keys" / "old").mkdir(parents=True)
    (tmp_path / "keys" / "session.key").write_text("k")
    (tmp_path / "keys" / "old" / "session.pub").write_text("p")
    chowned = []
    privileges.become(
        "nl2sql", own=[str(tmp_path / "keys")], recursive=True, environ={}, makedirs=os.makedirs,
        chown=lambda path, uid, gid: chowned.append(os.path.relpath(path, tmp_path)), **Calls().kwargs(),
    )
    assert sorted(chowned) == ["keys", "keys/old", "keys/old/session.pub", "keys/session.key"]


def test_a_directory_it_cannot_give_is_left_alone(tmp_path):
    def refuse(*_):
        raise PermissionError("read-only")

    calls = Calls()
    assert privileges.become("nl2sql", own=[str(tmp_path)], chown=refuse, environ={}, **calls.kwargs())
    assert ("setuid", 10001) in calls.log


def test_not_root_is_left_as_it_is():
    calls = Calls(uid=1000)
    assert privileges.become("nl2sql", **calls.kwargs()) is False
    assert calls.log == []
    assert privileges.become("nl2sql") is False  # the real calls, as this test is not root


def test_a_bind_mount_is_written_as_whoever_owns_it():
    calls, environ = Calls(), {}
    owned = SimpleNamespace(st_uid=501, st_gid=20)
    assert privileges.become_owner_of("/app/doc", fallback="nl2sql", stat=lambda _: owned, environ=environ,
                                      **calls.kwargs())
    assert calls.log == [("setgroups", (10002,)), ("setgid", 20), ("setuid", 501)], (
        "the fallback's group kept beside its own: how it reads the key pki gave that group"
    )
    assert environ["HOME"] == "/tmp"


def test_a_root_owned_mount_is_given_to_the_fallback_account(tmp_path):
    calls, chowned = Calls(), []
    root = SimpleNamespace(st_uid=0, st_gid=0)
    assert privileges.become_owner_of(
        str(tmp_path), fallback="nl2sql", stat=lambda _: root, environ={},
        makedirs=os.makedirs, chown=lambda path, uid, gid: chowned.append((uid, gid)), **calls.kwargs(),
    )
    assert chowned == [(10001, 10002)] and ("setuid", 10001) in calls.log


def test_become_owner_of_is_nothing_when_not_root():
    assert privileges.become_owner_of("/", fallback="nl2sql", **Calls(uid=5).kwargs()) is False
    assert privileges.owner_of("/") == (os.stat("/").st_uid, os.stat("/").st_gid)
