"""Dropping root, once a process has taken what it writes.

The directory image started this pattern in 6.0.1: start as root just long
enough to make the directories it writes its own -- a volume Docker created
empty, or one an older release's container made root's -- then run as an
account that cannot do anything else as root. Since 6.2 every Python service
that writes something starts this way (V6-31); the ones that write nothing
start as their account outright.
"""

from __future__ import annotations

import os
import pwd
from typing import Callable, Sequence


def become(
    account: str,
    *,
    own: Sequence[str] = (),
    getuid: Callable[[], int] = os.getuid,
    lookup: Callable = pwd.getpwnam,
    makedirs: Callable = os.makedirs,
    chown: Callable = os.chown,
    setgroups: Callable = os.setgroups,
    setgid: Callable = os.setgid,
    setuid: Callable = os.setuid,
    environ: dict | None = None,
    recursive: bool = False,
    walk: Callable = os.walk,
) -> bool:
    """Run as `account` from here on, giving it `own` first, if started as root.

    Returns whether it changed anything: false when the process was not root
    to begin with, which is a container started with `--user`, or a test.

    A directory is given to the account, and what is written in it from then
    on is the account's anyway. `recursive` gives it what is already there
    too -- a key an older release wrote as root, which the account must
    read. One that cannot be given -- mounted read-only, say -- is left as it
    is, and whatever needed to write it says so in words when that fails.
    """
    if getuid() != 0:
        return False
    entry = lookup(account)
    for path in own:
        makedirs(path, exist_ok=True)
        inside = [
            os.path.join(root, name) for root, dirs, files in (walk(path) if recursive else ()) for name in dirs + files
        ]
        for target in (path, *inside):
            try:
                chown(target, entry.pw_uid, entry.pw_gid)
            except OSError:
                pass
    setgroups([])
    setgid(entry.pw_gid)
    setuid(entry.pw_uid)
    (os.environ if environ is None else environ)["HOME"] = entry.pw_dir
    return True


def owner_of(path: str, *, stat: Callable = os.stat) -> tuple[int, int]:
    """The uid and gid that own `path` -- a bind mount's, from the host."""
    found = stat(path)
    return found.st_uid, found.st_gid


def become_owner_of(
    path: str,
    *,
    fallback: str,
    getuid: Callable[[], int] = os.getuid,
    stat: Callable = os.stat,
    lookup: Callable = pwd.getpwnam,
    makedirs: Callable = os.makedirs,
    chown: Callable = os.chown,
    setgroups: Callable = os.setgroups,
    setgid: Callable = os.setgid,
    setuid: Callable = os.setuid,
    environ: dict | None = None,
) -> bool:
    """Run as whoever owns `path`, if started as root -- or as `fallback` when root does.

    For a directory bind-mounted from the host and written here: the review
    service's golden question document lives in the checkout, which belongs
    to the person who cloned it. Writing it as that person keeps its files
    theirs, and needs no uid baked into the image. A directory root owns --
    one Docker made because nothing was there -- is written as `fallback`
    instead, once it has been given to it. Either way the process keeps
    `fallback`'s group beside its own, which is how it reads the key the pki
    service gave that group.
    """
    if getuid() != 0:
        return False
    uid, gid = owner_of(path, stat=stat)
    if uid == 0:
        return become(fallback, own=(path,), getuid=getuid, lookup=lookup, makedirs=makedirs, chown=chown,
                      setgroups=setgroups, setgid=setgid, setuid=setuid, environ=environ)
    setgroups([lookup(fallback).pw_gid])
    setgid(gid)
    setuid(uid)
    (os.environ if environ is None else environ)["HOME"] = "/tmp"
    return True
