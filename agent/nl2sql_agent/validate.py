"""The Static Validator: section 6.1 of the v4 architecture.

The v3 pipeline asked a model whether the generated SQL was safe. Over the
15-question benchmark that call cost 499 seconds and was the only component
that ever turned a right answer into no answer -- it is a deterministic
question, and this module answers it deterministically.

Every check here runs against the tree that `pglast` gets from PostgreSQL's
own parser (`libpg_query`), so what this module accepts is what the server
accepts, and the checks see the query the way the server will rather than the
way a regex reads it. That distinction is the whole point. The classic escape
is a data-modifying CTE:

    WITH d AS (DELETE FROM dim_store RETURNING *) SELECT * FROM d

which begins with `WITH`, ends with a `SELECT`, contains one statement, and
deletes a table. Every prefix check in the codebase passes it (there is a test
in `test_database_safety.py` pinning that); the parse tree shows a
`DeleteStmt` hanging off the outer `SelectStmt`'s `withClause`, which is what
the CTE check below looks for.

This is one layer of several. `SET TRANSACTION READ ONLY` in the executor is
what actually *stops* a write; the value of catching it here is that a
rejection at this stage is free, arrives with a message the Repair Agent can
act on, and never reaches the database. The regex prefix check from
`database.py` still runs first because it costs nothing, but nothing relies on
it: every verdict it can reach is one the parse tree reaches too.

The output is a list of `Issue` with `source = static`, empty when the
statement is acceptable. Issues accumulate rather than short-circuit, so one
repair round can fix three problems, except where a later check has nothing
to stand on -- there is no tree to walk after a syntax error.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from difflib import get_close_matches

from pglast import ast, parse_sql
from pglast.parser import ParseError
from pglast.visitors import referenced_relations

from .database import UnsafeQueryError, ensure_read_only, strip_sql
from .state import STATIC, Issue

#: Functions a read-only analytics query never needs, and that an injected or
#: hallucinated one might reach for: sleeping (a denial of service inside the
#: statement timeout's blind spot), reading server files, large-object import
#: and export, opening a second connection through dblink, killing other
#: backends, and mutating session state. Matched on the bare function name, so
#: `pg_catalog.pg_sleep` is the same entry as `pg_sleep`.
FUNCTION_DENYLIST: frozenset[str] = frozenset(
    {
        "pg_sleep",
        "pg_read_file",
        "pg_read_binary_file",
        "lo_import",
        "lo_export",
        "dblink",
        "dblink_exec",
        "pg_terminate_backend",
        "pg_cancel_backend",
        "set_config",
        "pg_notify",
    }
)

#: CTE bodies that write. PostgreSQL allows all four inside `WITH`.
_WRITING_CTE_KINDS: dict[type, str] = {
    ast.InsertStmt: "INSERT",
    ast.UpdateStmt: "UPDATE",
    ast.DeleteStmt: "DELETE",
    ast.MergeStmt: "MERGE",
}

#: How much of the statement to quote around a syntax error. Wide enough to
#: show the clause the parser choked on, narrow enough that the Repair Agent's
#: hint stays a hint.
_ERROR_CONTEXT = 30


def validate(sql: str, *, allowed_tables: Iterable[str] | None = None) -> list[Issue]:
    """Check `sql` statically and return one `Issue` per problem found.

    An empty list means the statement is a single read-only `SELECT` over
    tables that are in scope, calling nothing on `FUNCTION_DENYLIST`.

    `allowed_tables` is the Schema Retriever's `selected_tables`. When it is
    given, a reference to any other relation is rejected with the closest
    allowed name attached, because "not in scope" on its own tells the
    generator nothing it can act on. When it is `None` the check is skipped
    entirely -- there is a difference between an empty scope, where nothing is
    allowed, and no scope, where the caller has not said.

    Nothing here touches the database, so this is safe to call on anything,
    including text that is not SQL at all.
    """
    cleaned = strip_sql(sql)
    try:
        ensure_read_only(cleaned)
    except UnsafeQueryError as exc:
        # Free, and right about emptiness and the leading keyword. Its
        # single-statement rule, though, is a search for a semicolon, which a
        # string literal can legitimately carry -- `string_agg(name, '; ')` is
        # not two statements. So that one verdict is left to the parser, which
        # counts statements rather than punctuation.
        if ";" not in cleaned:
            return [Issue(source=STATIC, message=str(exc))]

    try:
        statements = parse_sql(cleaned)
    except ParseError as exc:
        return [Issue(source=STATIC, message=_syntax_error_message(cleaned, exc))]

    if len(statements) != 1:
        return [
            Issue(
                source=STATIC,
                message=f"Expected exactly one statement; this is {len(statements)}.",
            )
        ]

    statement = statements[0].stmt
    if not isinstance(statement, ast.SelectStmt):
        return [
            Issue(
                source=STATIC,
                message=(
                    "Only a SELECT statement is allowed; the parser reads this as "
                    f"{type(statement).__name__}."
                ),
            )
        ]

    issues: list[Issue] = []
    issues.extend(_writing_cte_issues(statement))
    issues.extend(_into_issues(statement))
    issues.extend(_denied_function_issues(statement))
    if allowed_tables is not None:
        issues.extend(_out_of_scope_issues(statement, allowed_tables))
    return issues


# --- the individual checks ---------------------------------------------------


def _writing_cte_issues(statement: ast.SelectStmt) -> Iterator[Issue]:
    """W4: a `WITH` body that is an INSERT/UPDATE/DELETE/MERGE.

    The outer statement is still a `SelectStmt`, so this is invisible to every
    check that looks at the statement kind alone.
    """
    for node in _walk(statement):
        if isinstance(node, ast.CommonTableExpr):
            kind = _WRITING_CTE_KINDS.get(type(node.ctequery))
            if kind is not None:
                yield Issue(
                    source=STATIC,
                    message=(
                        f'The CTE "{node.ctename}" is a {kind}; a data-modifying '
                        "CTE is not allowed in a read-only query."
                    ),
                )


def _into_issues(statement: ast.SelectStmt) -> Iterator[Issue]:
    """`SELECT ... INTO`, which creates a table rather than returning rows."""
    for node in _walk(statement):
        if isinstance(node, ast.SelectStmt) and node.intoClause is not None:
            target = node.intoClause.rel
            name = target.relname if target is not None else "a new table"
            yield Issue(
                source=STATIC,
                message=(
                    f"SELECT ... INTO {name} creates a table; drop the INTO clause "
                    "and return the rows instead."
                ),
            )


def _denied_function_issues(statement: ast.SelectStmt) -> Iterator[Issue]:
    """Calls to `FUNCTION_DENYLIST`, anywhere in the tree.

    A denylisted call hides just as well in a `WHERE` subquery or a CTE as in
    the target list, which is why this walks rather than scans.
    """
    seen: set[str] = set()
    for node in _walk(statement):
        if not isinstance(node, ast.FuncCall) or not node.funcname:
            continue
        parts = [part.sval for part in node.funcname if isinstance(part, ast.String)]
        if not parts or parts[-1].lower() not in FUNCTION_DENYLIST:
            continue
        written = ".".join(parts)
        if written not in seen:
            seen.add(written)
            yield Issue(
                source=STATIC,
                message=(
                    f"{written}() is not allowed; this query may only read the "
                    "analytics tables."
                ),
            )


def _out_of_scope_issues(
    statement: ast.SelectStmt, allowed_tables: Iterable[str]
) -> Iterator[Issue]:
    """The arch2 allowlist: every relation must be one the retriever selected.

    `referenced_relations` is used rather than a hand-rolled sweep of
    `RangeVar` nodes because CTE scoping is the hard part: in
    `WITH x AS (SELECT * FROM real_table) SELECT * FROM x`, `real_table` is a
    relation and `x` is not, and the rule has to survive nesting, shadowing
    and `WITH RECURSIVE`.
    """
    allowed = list(allowed_tables)
    # An allowed name may be written qualified or quoted on either side, so
    # both spellings of both sides are compared.
    by_bare = {_bare(name): name for name in allowed}
    accepted = {_normalise(name) for name in allowed} | set(by_bare)

    for relation in sorted(referenced_relations(statement)):
        if _normalise(relation) in accepted or _bare(relation) in accepted:
            continue
        message = f"{relation} is not in scope"
        near = get_close_matches(_bare(relation), list(by_bare), n=1, cutoff=0.6)
        if near:
            message += f"; did you mean {by_bare[near[0]]}?"
        else:
            message += "."
        yield Issue(source=STATIC, message=message)


# --- helpers -----------------------------------------------------------------


def _walk(node: object) -> Iterator[ast.Node]:
    """Every `ast.Node` under `node`, itself included.

    Depth-first over the declared slots, which is how the nesting that makes
    prefix checks wrong -- subqueries, CTEs, join trees, set operations --
    gets visited at all.
    """
    if isinstance(node, ast.Node):
        yield node
        for slot in type(node).__slots__:
            if slot != "ancestors":
                yield from _walk(getattr(node, slot, None))
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from _walk(item)


def _syntax_error_message(sql: str, exc: ParseError) -> str:
    """The parser's complaint, its offset, and the text on either side.

    The Repair Agent's classified hint for a syntax error is "quote the
    characters around position n", so the position and the quotation are
    produced here where the offset is still trustworthy.
    """
    detail = str(exc.args[0]) if exc.args else str(exc)
    location = exc.args[1] if len(exc.args) > 1 and isinstance(exc.args[1], int) else -1
    if not 0 <= location <= len(sql):
        return f"Syntax error: {detail}"
    start = max(0, location - _ERROR_CONTEXT)
    end = min(len(sql), location + _ERROR_CONTEXT)
    quoted = sql[start:location] + ">>>" + sql[location:end]
    if start > 0:
        quoted = "..." + quoted
    if end < len(sql):
        quoted += "..."
    return f"Syntax error at character {location}: {detail}. Here: {quoted}"


def _normalise(name: str) -> str:
    """A relation name as it compares: unquoted and case-folded.

    `pglast` hands back the name double-quoted when it needs quoting, and
    PostgreSQL folds an unquoted name to lower case, so neither spelling
    should decide whether a table is in scope.
    """
    return name.replace('"', "").strip().lower()


def _bare(name: str) -> str:
    """The relation name without its schema or catalog qualification."""
    return _normalise(name).rsplit(".", 1)[-1]
