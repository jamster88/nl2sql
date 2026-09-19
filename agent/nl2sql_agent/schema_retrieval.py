"""The Schema Retriever: DDL-chunk vectors plus foreign-key closure, no model call.

Section 4.2 of `multi-agent_arch_specs/Multi-Agent_NL2SQL_arch4.md`, and M5 in
section 1. It replaces the v3 `select_tables` model call, which costs 364 of the
benchmark's 1499 seconds. Nothing in this module calls a model, and the DDL
index it searches already exists in the vector store -- one chunk per table with
its grain, keywords and row count -- so there is no index to build either.

Similarity alone is not enough for a star schema. A question names what it is
about ("ad impressions", "channel type") and never names the table that
connects those things. `fact_ad_performance` has no channel key at all: the
channel is reached through `dim_ad_placement`, so a table set holding only the
fact and the channel cannot express the join however well those two were
ranked. That is benchmark B15. Foreign-key closure over `pg_constraint` supplies
the missing table deterministically, for the price of one catalog query.

Three decisions the architecture document leaves open, and why:

* **A table is a bridge only when it is the *only* way to connect the pair.**
  In a star schema every two dimensions are two hops apart through each of the
  seven fact tables that reference both, so "add every table on a shortest
  path" would add all seven and crowd out the tables that were actually
  ranked. A table is taken as a bridge when it lies on *every* shortest path
  between the pair. That is what makes it load-bearing, and what gives the
  cap's "never drop a bridge" rule something true to rest on.
* **Hinted tables queue behind the ranked ones.** A cosine distance is a
  measurement of this question against this table's DDL; a hint is a table that
  some neighbouring document or worked example happens to mention. When the cap
  bites, the measured signal is the one to keep. This is also what v3 did.
* **The total-failure fallback is not capped.** With the store down and no
  hints there is no ranking, so capping would hand the generator the first ten
  tables alphabetically -- which in this schema is ten dimensions and not one
  fact table, a guaranteed failure. Degrading to schema-only means the whole
  schema, which is the v2/v3 behaviour the architecture keeps (section 3).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from sqlalchemy import text

from .retrieval import KnowledgeUnavailableError, tables_mentioned

#: Reads every foreign-key edge as an unordered `(table, table)` pair. Injected
#: so the closure can be exercised without a database.
ForeignKeyReader = Callable[[], Sequence[tuple[str, str]]]

# A k-chunk search can yield fewer than k tables: the DDL collection carries a
# table-less header chunk, and nothing stops two chunks documenting one table.
# Asking for a few extra chunks keeps "the top k tables" meaning k tables.
_CHUNK_HEADROOM = 2

_FOREIGN_KEY_SQL = """
SELECT child.relname AS child_table, parent.relname AS parent_table
FROM pg_constraint con
JOIN pg_class child ON child.oid = con.conrelid
JOIN pg_class parent ON parent.oid = con.confrelid
JOIN pg_namespace child_ns ON child_ns.oid = child.relnamespace
JOIN pg_namespace parent_ns ON parent_ns.oid = parent.relnamespace
WHERE con.contype = 'f'
  AND child_ns.nspname = :schema
  AND parent_ns.nspname = :schema
ORDER BY child.relname, parent.relname
"""


@dataclass
class SchemaSelection:
    """What the Schema Retriever decided, and what it had to give up to decide it.

    `bridges` and `dropped` exist so the choice is auditable from the trace:
    a wrong answer traceable to a table the cap removed is a different bug from
    one traceable to a table the vectors never ranked.
    """

    tables: list[str] = field(default_factory=list)
    #: Tables foreign-key closure added, none of which similarity ranked.
    bridges: list[str] = field(default_factory=list)
    #: Tables the cap removed, lowest-ranked first.
    dropped: list[str] = field(default_factory=list)
    #: Why this selection is degraded: an unreachable vector store, or a
    #: foreign-key read that failed. Retrieval is best-effort and the run
    #: continues either way, so this is a note, never an exception.
    error: str | None = None


def read_foreign_keys(database: Any) -> list[tuple[str, str]]:
    """Every foreign-key edge in the database's configured schema.

    `Database` exposes no connection and this module may not change it, so the
    query runs on its engine directly rather than opening a second engine
    against the same URL. The query belongs on `Database` itself and should
    move there when that file is next touched.
    """
    engine = database._engine
    schema = getattr(database, "_schema", "public")
    with engine.connect() as conn:
        rows = conn.execute(text(_FOREIGN_KEY_SQL), {"schema": schema}).all()
    return [(row[0], row[1]) for row in rows]


def _bfs_distances(graph: dict[str, set[str]], start: str) -> dict[str, int]:
    """Hop count from `start` to every table reachable from it through joins."""
    distances = {start: 0}
    queue = deque([start])
    while queue:
        table = queue.popleft()
        for neighbour in graph.get(table, ()):
            if neighbour not in distances:
                distances[neighbour] = distances[table] + 1
                queue.append(neighbour)
    return distances


def _required_intermediates(graph: dict[str, set[str]], start: str, goal: str) -> list[str]:
    """Tables that *every* shortest join path between two tables goes through.

    Lying on *a* shortest path is not enough to be worth a slot: two dimensions
    in a star schema are two hops apart through any fact table that references
    both, and none of those facts is necessarily the one the question is about.
    A table earns the name bridge when it is alone at its distance from both
    ends, which is exactly the position `dim_ad_placement` holds between
    `fact_ad_performance` and `dim_ad_channel`.
    """
    from_start = _bfs_distances(graph, start)
    if goal not in from_start:
        return []
    from_goal = _bfs_distances(graph, goal)
    span = from_start[goal]

    required: list[str] = []
    for hop in range(1, span):
        on_shortest_path = sorted(
            table
            for table, distance in from_start.items()
            if distance == hop and from_goal.get(table) == span - hop
        )
        if len(on_shortest_path) == 1:
            required.append(on_shortest_path[0])
    return required


#: The one collection in the vector store holding a DDL chunk per table. The
#: Schema Retriever owns it; the Knowledge Retriever excludes it, so the two
#: agents split the store rather than both paying to describe the same tables.
DDL_COLLECTION = "ddl_index_embeddings"


class SchemaRetriever:
    """Picks the tables a question needs: DDL-chunk vectors, FK closure, a cap.

    `knowledge_base` is a `KnowledgeBase` the caller has already restricted to
    the DDL collection (`collections=["ddl_index_embeddings"]`). It is passed in
    rather than built here so that a test can hand over a fake, and so that the
    one embedding client is shared with the other retrievers.
    """

    def __init__(
        self,
        knowledge_base: Any,
        database: Any,
        *,
        top_k: int = 6,
        max_tables: int = 10,
        foreign_keys: ForeignKeyReader | None = None,
    ) -> None:
        self._knowledge_base = knowledge_base
        self._database = database
        self._top_k = top_k
        self._max_tables = max_tables
        self._read_foreign_keys: ForeignKeyReader = foreign_keys or (
            lambda: read_foreign_keys(database)
        )
        self._edges: list[tuple[str, str]] | None = None

    def select(self, question: str, *, hinted_tables: Iterable[str] = ()) -> SchemaSelection:
        """The table set for this question. Never raises; degrades instead."""
        known = set(self._database.table_names())

        error: str | None = None
        try:
            ranked = self._ranked_tables(question, known)
        except KnowledgeUnavailableError as exc:
            ranked, error = [], str(exc)

        selected = list(ranked)
        for hinted in hinted_tables:
            if hinted in known and hinted not in selected:
                selected.append(hinted)

        if not selected:
            return SchemaSelection(tables=sorted(known), error=error)

        bridges, closure_error = self._closure(selected, known)
        tables, dropped = self._apply_cap(selected + bridges, set(bridges))
        return SchemaSelection(
            tables=tables,
            bridges=[b for b in bridges if b in tables],
            dropped=dropped,
            error=error or closure_error,
        )

    def close_and_cap(self, tables: Iterable[str]) -> SchemaSelection:
        """Foreign-key closure and the cap over an already-chosen table set.

        The Context Aggregator is the only place all three proposals are known
        -- the vector ranking, the tables the knowledge chunks document, and
        the tables the worked examples query -- so it is the only place the
        closure can be applied to their union. Re-running `select` there would
        pay for a second embedding call to learn something already in state,
        which is why this is a separate entry point rather than a flag.
        """
        known = set(self._database.table_names())
        selected: list[str] = []
        for name in tables:
            if name in known and name not in selected:
                selected.append(name)
        if not selected:
            return SchemaSelection(tables=sorted(known)[: self._max_tables])

        bridges, error = self._closure(selected, known)
        capped, dropped = self._apply_cap(selected + bridges, set(bridges))
        return SchemaSelection(
            tables=capped,
            bridges=[b for b in bridges if b in capped],
            dropped=dropped,
            error=error,
        )

    def _ranked_tables(self, question: str, known: set[str]) -> list[str]:
        """The top-k tables by cosine distance, best match first."""
        if self._top_k <= 0:
            return []
        chunks = self._knowledge_base.search(question, top_k=self._top_k + _CHUNK_HEADROOM)
        ranked = [table for table in tables_mentioned(chunks) if table in known]
        return ranked[: self._top_k]

    def _closure(self, selected: list[str], known: set[str]) -> tuple[list[str], str | None]:
        """Bridge tables for every pair in the selection, best pair first.

        One pass over the original selection is enough: a bridge is by
        construction already on a path between two tables that are in the set,
        so re-closing over the bridges finds nothing new.
        """
        graph, error = self._graph(known)
        found: list[str] = []
        for index, left in enumerate(selected):
            for right in selected[index + 1 :]:
                if left not in graph or right not in graph:
                    continue
                for table in _required_intermediates(graph, left, right):
                    if table not in selected and table not in found:
                        found.append(table)
        return found, error

    def _graph(self, known: set[str]) -> tuple[dict[str, set[str]], str | None]:
        """The foreign-key graph as an undirected adjacency map.

        Direction is dropped because a join reads the same both ways, and the
        edge list is cached because the catalog cannot change under a run.
        """
        if self._edges is None:
            try:
                self._edges = list(self._read_foreign_keys())
            except Exception as exc:
                # Closure is an enhancement on top of the ranked tables, so a
                # catalog that will not answer costs the bridges, not the run.
                return {}, f"foreign-key closure skipped: {exc}"

        graph: dict[str, set[str]] = {}
        for left, right in self._edges:
            if left == right or left not in known or right not in known:
                continue
            graph.setdefault(left, set()).add(right)
            graph.setdefault(right, set()).add(left)
        return graph, None

    def _apply_cap(self, tables: list[str], bridges: set[str]) -> tuple[list[str], list[str]]:
        """Trim to `max_tables`, spending the lowest-ranked non-bridges first.

        A bridge is the only way to express a join; a weakly ranked leaf is a
        guess. So the guesses are what the cap takes, and a bridge goes only if
        the closure alone somehow overflows the cap.
        """
        kept = list(tables)
        dropped: list[str] = []
        for droppable in (lambda t: t not in bridges, lambda t: True):
            for candidate in reversed(list(kept)):
                if len(kept) <= self._max_tables:
                    break
                if droppable(candidate):
                    kept.remove(candidate)
                    dropped.append(candidate)
        return kept, dropped
