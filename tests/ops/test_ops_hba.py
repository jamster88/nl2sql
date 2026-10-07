"""nl2sql_ops.hba: one marked block at the top of pg_hba.conf, through SQL."""

from __future__ import annotations

import pytest

from nl2sql_ops.hba import HbaError, rewrite, with_block

from .conftest import FakeConn

BEGIN, END = "# BEGIN nl2sql sign-in", "# END nl2sql sign-in"
STOCK = "local all all trust\nhost all all all scram-sha-256\n"


def test_a_block_goes_at_the_top_and_every_other_line_stays():
    text = with_block(STOCK, BEGIN, END, ["hostssl all a all scram-sha-256"])
    lines = text.splitlines()
    assert lines[0].startswith(BEGIN) and "change .env, not these lines" in lines[0]
    assert lines[1:3] == ["hostssl all a all scram-sha-256", END]
    assert lines[3:] == STOCK.splitlines()


def test_a_block_already_there_is_replaced_wherever_it_was_and_whatever_its_note():
    old = f"local all all trust\n{BEGIN} -- written by docker/ldap_hba.sh\nold rule\n{END}\nhost all all all md5\n"
    text = with_block(old, BEGIN, END, ["new rule"])
    assert "old rule" not in text and "written by docker/ldap_hba.sh" not in text
    assert text.splitlines()[1:3] == ["new rule", END]
    assert text.splitlines()[3:] == ["local all all trust", "host all all all md5"]


def test_no_lines_takes_the_block_out_and_leaves_the_rest():
    old = f"{BEGIN}\nrule\n{END}\n{STOCK}"
    assert with_block(old, BEGIN, END, []) == STOCK


def test_another_block_is_not_this_one():
    old = f"# BEGIN nl2sql transport\nhost all postgres all reject\n# END nl2sql transport\n{STOCK}"
    text = with_block(old, BEGIN, END, ["rule"])
    assert "# BEGIN nl2sql transport" in text and "host all postgres all reject" in text


def _conn(current: str, errors: int = 0) -> FakeConn:
    return FakeConn({
        "current_setting('hba_file')": [("/var/lib/pgdata/pg_hba.conf",)],
        "pg_read_file": [(current,)],
        "lo_from_bytea": [(4242,)],
        "pg_hba_file_rules": [(errors,)],
    })


def test_a_change_is_written_through_the_file_checked_and_reloaded():
    conn = _conn(STOCK)
    assert rewrite(conn, BEGIN, END, ["rule"]) is True
    assert conn.ran("lo_export") == ["SELECT lo_export(%s, %s)"]
    written = conn.params[conn.statements.index("SELECT lo_from_bytea(0, %s)")][0].decode()
    assert written.startswith(BEGIN) and written.endswith(STOCK)
    assert conn.params[conn.statements.index("SELECT lo_export(%s, %s)")] == (4242, "/var/lib/pgdata/pg_hba.conf")
    assert conn.ran("lo_unlink") and conn.transactions == 1, "the large object is made and removed in one transaction"
    assert conn.statements[-1] == "SELECT pg_reload_conf()"


def test_an_unchanged_file_is_not_written_or_reloaded():
    conn = _conn(with_block(STOCK, BEGIN, END, ["rule"]))
    assert rewrite(conn, BEGIN, END, ["rule"]) is False
    assert not conn.ran("lo_export") and not conn.ran("pg_reload_conf")


def test_rules_that_do_not_parse_are_put_back_and_the_start_fails():
    conn = _conn(STOCK, errors=2)
    with pytest.raises(HbaError, match=r"did not parse \(2 errors\), so the old file is back"):
        rewrite(conn, BEGIN, END, ["hostssl nonsense"])
    writes = [conn.params[i][0].decode() for i, s in enumerate(conn.statements) if s.startswith("SELECT lo_from_bytea")]
    assert len(writes) == 2 and writes[-1] == STOCK
    assert not conn.ran("pg_reload_conf")
