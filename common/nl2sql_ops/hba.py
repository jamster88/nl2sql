"""pg_hba.conf, rewritten through SQL as the superuser, and checked first.

What `docker/ldap_hba.sh` did from inside the container until 6.3, done over
the socket instead, so the shell scripts pipe nothing into a database:

* the file is read with `pg_read_file`,
* one marked block is put at its top (or taken out) and every other line is
  left as it was -- the block is first because the rules are first-match,
* the new file is written *through* the old one with `lo_export`, which
  opens it for writing rather than replacing it, so it keeps the owner and
  mode Postgres checks,
* `pg_hba_file_rules` is asked whether every line parses. Postgres keeps
  its old rules when a reload finds broken ones -- and refuses to start at
  all on the next restart -- so a file that does not parse is put back as it
  was and the one-shot fails here, saying why,
* and the server is told to reload.

Server-side large-object export needs the superuser, which is what the
socket gives the one-shot (`nl2sql_ops.connect`).
"""

from __future__ import annotations


class HbaError(RuntimeError):
    """The new rules did not parse; the old file is back in place."""


def with_block(text: str, begin: str, end: str, lines: list[str]) -> str:
    """`text` with the block between `begin` and `end` replaced by `lines`.

    The block goes at the top. No lines takes it out. A line that starts
    with `begin` opens the block and one that starts with `end` closes it,
    so the markers may carry a note after them.
    """
    kept: list[str] = []
    skipping = False
    for line in text.splitlines():
        if line.startswith(begin):
            skipping = True
        if not skipping:
            kept.append(line)
        if skipping and line.startswith(end):
            skipping = False
    block = [begin + " -- written on start by the dbprep service (nl2sql_ops); change .env, not these lines",
             *lines, end] if lines else []
    return "\n".join(block + kept) + "\n"


def _write(conn, path: str, text: str) -> None:
    with conn.transaction():
        oid = conn.execute("SELECT lo_from_bytea(0, %s)", (text.encode(),)).fetchone()[0]
        conn.execute("SELECT lo_export(%s, %s)", (oid, path))
        conn.execute("SELECT lo_unlink(%s)", (oid,))


def rewrite(conn, begin: str, end: str, lines: list[str]) -> bool:
    """Put this block in the server's pg_hba.conf. True when anything changed."""
    path = conn.execute("SELECT current_setting('hba_file')").fetchone()[0]
    old = conn.execute("SELECT pg_read_file(%s)", (path,)).fetchone()[0]
    new = with_block(old, begin, end, lines)
    if new.rstrip("\n") == old.rstrip("\n"):
        return False
    _write(conn, path, new)
    errors = conn.execute("SELECT count(*) FROM pg_hba_file_rules WHERE error IS NOT NULL").fetchone()[0]
    if errors:
        _write(conn, path, old)
        raise HbaError(
            f"the new rules in {path} did not parse ({errors} errors), so the old file is back: "
            "see pg_hba_file_rules"
        )
    conn.execute("SELECT pg_reload_conf()")
    return True
