"""The literal catalog and the Literal Matcher -- section 4.3 of
`multi-agent_arch_specs/Multi-Agent_NL2SQL_arch4.md`.

arch2 and arch3 both describe a "Fuzzy Match Agent" and neither gives it
anything to search. This module is the missing half: a catalog of every real
value a question could plausibly name, and a deterministic search over it.

**Why a catalog at all.** Three of the benchmark questions turn on a literal
the model has to spell exactly -- `Dairy & Eggs`, `SCAN_BACK`, `Print Flyer`.
They pass today only because the question happens to spell each one the way
the database does. A user types "dairy and eggs" and "scan-back allowances",
and `WHERE department_name = 'Dairy and Eggs'` returns zero rows with no
error: the worst failure mode this system has, because it looks like an
answer. Resolving the phrase to the stored value before generation is what
turns a silent wrong answer into a correct one.

**What goes in it.** Every text column whose distinct count is at most
`max_distinct` (500). In the retail schema that is 42 of the 43 text columns,
1,277 values in total; the one excluded is `fact_pos_retail_sales.basket_id`
at 258,308 distinct values, which is an identifier nobody types by name. The
cutoff is what separates a vocabulary from a data column.

**How it searches.** `pg_trgm`'s `word_similarity` when the extension is
installed, Python's `difflib` when it is not. Which one is live is detected
per database, never assumed: the extension ships in the retail image but is
installed only at image build time, and the agent connects as a read-only
role that could not install it itself. Both paths score the same normalised
forms and are held to the same floor, so a database without the extension
gives slightly different numbers and the same answers.

Nothing here calls a model, and nothing here is random: the same question
against the same catalog produces the same list every time, which is what
makes the Literal Matcher unit-testable in the way the architecture claims
for every agent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Iterable, Sequence

from sqlalchemy import text

from .state import LiteralMatch

#: Per candidate, the best entry found in each (table, column) and its score.
_ByColumn = dict[tuple[str, str], tuple["CatalogEntry", float]]

#: The header the generator's human turn carries above the resolved literals.
LITERAL_MAP_HEADER = "Literal values in this database that match the question:"

#: Types worth cataloguing. Anything else is a number, a date or a key, none of
#: which a user misspells in a way fuzzy matching could repair.
_TEXT_TYPES = ("text", "varchar", "bpchar", "char")

#: English function words plus the verbs that open a data question. A token has
#: to clear this set to become a candidate on its own. Kept deliberately small:
#: a word wrongly listed here silently stops a real literal from ever being
#: looked up, while a word wrongly missing only costs one search that scores
#: below the floor. Note what is *not* here -- "produce", "display", "new",
#: "volume", "item", "print" are all real values in this schema.
STOPWORDS = frozenset(
    """
    a about above across after again against all also although am an and any are
    as at be because been before being below between both but by can cannot could
    did do does doing done down during each either else every few for from further
    had has have having he her here hers him his how i if in into is it its just
    me more most much my neither no nor not of off on once only or other our out
    over own per please same she should since so some such than that the their
    them then there these they this those through to too under until up us very
    was we were what when where whether which while who whom whose why will with
    within would you your
    average avg break compare count fetch find get give list rank show sort sum
    tell total whats
    across best bottom current date dates first highest last latest lowest many
    month months next number numbers previous prior quarter quarters recent
    second versus vs week weeks worst years
    """.split()
)

#: The score at which a match needs no shared word to be believable. Two short
#: unrelated words reach the 0.6 floor by accident all the time -- "sales"
#: against `Salem` scores 0.80, "store" against `Steak` scores 0.60 -- so a
#: match has to either agree on a real word or be a character-level near-miss
#: of the whole phrase, which is what a misspelling ("produse" -> `Produce`,
#: 0.857) looks like. Without this the generator's prompt fills with plausible
#: nonsense, which is worse than an empty literal map.
#:
#: It is measured on `difflib`'s ratio even when `pg_trgm` did the searching,
#: because the two scales do not agree and only one of them separates a typo
#: from a coincidence. Measured against this database, `word_similarity` scores
#: the junk pair "sales"/`Salem` at 0.667 and the real typo "produse"/`Produce`
#: lower, at 0.625, so no trigram threshold can divide them; difflib puts the
#: same pairs at 0.80 and 0.857, on either side of this line. Trigram still
#: does the searching -- it is indexed and runs in the database -- and this
#: decides what survives it.
NEAR_MISS_SCORE = 0.85

#: Words allowed *inside* a capitalised phrase without breaking it, so
#: "Dairy & Eggs" and "Health & Beauty" survive as single phrases.
_CONNECTORS = frozenset({"&", "and", "of", "the"})

#: A word, keeping internal hyphens and underscores so "scan-back" and
#: "SCAN_BACK" each arrive as one token rather than two.
_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_'-]*|&|\d+")

#: Quoted spans. A straight single quote only opens a span at a word boundary,
#: or every possessive in the question would start one.
_QUOTED = re.compile(r"\"([^\"\n]{2,80})\"|“([^”\n]{2,80})”|(?<!\w)'([^'\n]{2,80})'(?!\w)")

#: Punctuation a multi-word phrase may not span: "sales, dairy and eggs" is two
#: thoughts, not one phrase.
_PHRASE_BREAK = re.compile(r"[,.;:!?()\[\]{}/\\\n]")

_NON_ALNUM = re.compile(r"[^0-9a-z]+")

#: Longest word n-gram considered. Four bounds the candidate count at roughly
#: four per word of the question. Longer values exist -- `dim_ad_placement`
#: has nine-word theme names -- but a user who types one of those types it
#: capitalised or in quotes, and both of those forms are taken whole.
_MAX_NGRAM = 4

#: Ceiling on candidates from one question, so a pasted paragraph cannot turn
#: into thousands of similarity computations.
_MAX_CANDIDATES = 60


@dataclass(frozen=True)
class CatalogEntry:
    """One real value, and where it lives. Frozen so it can key a dict."""

    table: str
    column: str
    value: str

    @property
    def qualified_column(self) -> str:
        return f"{self.table}.{self.column}"


def normalize(value: str) -> str:
    """The comparison form: case, punctuation and `&`/`and` folded away.

    This one function is what makes "dairy and eggs" reach `Dairy & Eggs` and
    "scan-back" reach `SCAN_BACK`. Both sides of every comparison go through
    it, so the similarity score is measuring the words rather than the
    typography.
    """
    folded = value.casefold().replace("&", " and ")
    return " ".join(_NON_ALNUM.sub(" ", folded).split())


# --- building the catalog ---------------------------------------------------


def _engine(database: Any) -> Any:
    """The SQLAlchemy engine behind a `Database`.

    `Database` keeps its engine private and exposes only capped, single-
    statement helpers: `run_select` stops at 50 rows, which is a tenth of the
    500 values a catalogued column may hold. Cataloguing therefore needs the
    engine itself. A public `engine` property is preferred if one ever appears.
    """
    engine = getattr(database, "engine", None) or getattr(database, "_engine", None)
    if engine is None:
        raise TypeError(f"{database!r} exposes no SQLAlchemy engine to read the catalog from")
    return engine


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _text_columns(conn: Any, schema: str) -> list[tuple[str, str]]:
    """Every text-typed column in the schema, ordered for reproducibility.

    Read from `pg_catalog` rather than `information_schema` to match
    `database.py`, and matched on `pg_type.typname` rather than on the
    rendered type, so `character varying(50)` needs no string parsing.
    """
    rows = conn.execute(
        text(
            """
            SELECT c.relname AS table_name, a.attname AS column_name
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_attribute a ON a.attrelid = c.oid
            JOIN pg_type t ON t.oid = a.atttypid
            WHERE n.nspname = :schema
              AND c.relkind IN ('r', 'p', 'm')
              AND a.attnum > 0
              AND NOT a.attisdropped
              AND t.typname = ANY(:types)
            ORDER BY c.relname, a.attnum
            """
        ),
        {"schema": schema, "types": list(_TEXT_TYPES)},
    ).all()
    return [(r.table_name, r.column_name) for r in rows]


def build_catalog(
    database: Any, *, max_distinct: int = 500, schema: str = "public"
) -> list[CatalogEntry]:
    """Every value of every text column narrow enough to be a vocabulary.

    Three round trips regardless of how wide the schema is: the text columns,
    then one `count(DISTINCT ...)` per column unioned into a single statement,
    then the values of the surviving columns unioned into another. The count
    pass runs first on purpose -- `fact_pos_retail_sales.basket_id` has 258,308
    distinct values, and it is cheap to count them and expensive to ship them
    only to throw them away.
    """
    engine = _engine(database)
    with engine.connect() as conn:
        columns = _text_columns(conn, schema)
        if not columns:
            return []

        counts = conn.execute(text(_distinct_count_sql(columns, schema))).all()
        keep = [(r.table_name, r.column_name) for r in counts if r.distinct_count <= max_distinct]
        if not keep:
            return []

        values = conn.execute(text(_values_sql(keep, schema, max_distinct))).all()

    entries = {
        CatalogEntry(table=r.table_name, column=r.column_name, value=r.value)
        for r in values
        if r.value is not None and r.value.strip()
    }
    return sorted(entries, key=lambda e: (e.table, e.column, e.value))


def _distinct_count_sql(columns: Sequence[tuple[str, str]], schema: str) -> str:
    """One `count(DISTINCT ...)` per column, in a single statement.

    The identifiers come from `pg_catalog`, never from a question, so quoting
    them is about surviving odd names rather than about injection.
    """
    branches = [
        f"SELECT {_quote_literal(table)} AS table_name, {_quote_literal(column)} AS column_name, "
        f"count(DISTINCT {_quote_ident(column)})::bigint AS distinct_count "
        f"FROM {_quote_ident(schema)}.{_quote_ident(table)}"
        for table, column in columns
    ]
    return "\nUNION ALL\n".join(branches)


def _values_sql(columns: Sequence[tuple[str, str]], schema: str, max_distinct: int) -> str:
    """The distinct values of the columns that passed the cutoff.

    The per-branch `LIMIT` is a guard, not a filter: these columns were counted
    under the cutoff moments ago, and the limit only bounds the damage if one
    of them grew between the two statements.
    """
    branches = [
        f"(SELECT DISTINCT {_quote_literal(table)} AS table_name, "
        f"{_quote_literal(column)} AS column_name, {_quote_ident(column)}::text AS value "
        f"FROM {_quote_ident(schema)}.{_quote_ident(table)} "
        f"WHERE {_quote_ident(column)} IS NOT NULL LIMIT {int(max_distinct)})"
        for table, column in columns
    ]
    return "\nUNION ALL\n".join(branches)


def trigram_available(database: Any) -> bool:
    """Whether `pg_trgm` is installed in the connected database.

    Detected, not assumed: the extension ships in the retail image but is
    installed only at image build time, and the agent's read-only role cannot
    install it, so the same code meets databases both ways. An unreachable
    database answers False rather than raising -- the only consequence of the
    answer is which of two scorers runs.
    """
    try:
        with _engine(database).connect() as conn:
            found = conn.execute(
                text("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")
            ).scalar()
    except Exception:
        return False
    return bool(found)


# --- extracting candidates from the question --------------------------------


@dataclass(frozen=True)
class _Token:
    text: str
    start: int
    end: int

    @property
    def is_word(self) -> bool:
        return self.text[0].isalpha()

    @property
    def is_capitalised(self) -> bool:
        return self.text[0].isupper()


def _tokenize(question: str) -> list[_Token]:
    return [_Token(m.group(0), m.start(), m.end()) for m in _TOKEN.finditer(question)]


def _span(question: str, first: _Token, last: _Token) -> str:
    return question[first.start : last.end]


def _capitalised_phrases(question: str, tokens: Sequence[_Token]) -> list[str]:
    """Runs of capitalised words, plus every suffix of a run.

    The suffixes matter because a question rarely starts a capitalised phrase
    cleanly: "Show Dairy & Eggs sales" is one run of three capitalised words,
    and the phrase actually in the database is the last two. Emitting each
    suffix that still holds two capitalised words is linear in the run length
    and costs one extra search apiece.
    """
    phrases: list[str] = []
    i = 0
    while i < len(tokens):
        if not (tokens[i].is_word and tokens[i].is_capitalised):
            i += 1
            continue
        run = [i]
        j = i + 1
        while j < len(tokens):
            token = tokens[j]
            if token.is_word and token.is_capitalised and not _PHRASE_BREAK.search(
                question[tokens[j - 1].end : token.start]
            ):
                run.append(j)
                j += 1
                continue
            connector = token.text.casefold() in _CONNECTORS
            nxt = tokens[j + 1] if j + 1 < len(tokens) else None
            follows = nxt is not None and nxt.is_word and nxt.is_capitalised
            if connector and follows:
                j += 1
                continue
            break
        if len(run) >= 2:
            for start in run[:-1]:
                phrases.append(_span(question, tokens[start], tokens[run[-1]]))
        i = j
    return phrases


def _code_tokens(tokens: Sequence[_Token]) -> list[str]:
    """ALL_CAPS and underscored tokens: `SCAN_BACK`, `VOLUME_REBATE`, `CH01`."""
    out = []
    for token in tokens:
        if not token.is_word:
            continue
        if "_" in token.text or (token.text.isupper() and len(token.text) >= 2):
            out.append(token.text)
    return out


def _ngrams(question: str, tokens: Sequence[_Token]) -> list[str]:
    """Word n-grams of 2 to 4 tokens, for phrases a user typed in lower case.

    "dairy and eggs" is not a capitalised run and not a code token, so without
    this form the only candidates would be the single words "dairy" and
    "eggs", neither of which scores anywhere near `Dairy & Eggs` on its own.
    An n-gram may not begin or end on a stopword and may not span sentence
    punctuation, which is what keeps "for dairy" and "eggs, show" out.
    """
    words = [t for t in tokens if t.is_word or t.text == "&"]
    out = []
    for size in range(2, _MAX_NGRAM + 1):
        for start in range(len(words) - size + 1):
            group = words[start : start + size]
            first, last = group[0].text.casefold(), group[-1].text.casefold()
            if not group[0].is_word or not group[-1].is_word:
                continue
            if first in STOPWORDS or last in STOPWORDS:
                continue
            span = _span(question, group[0], group[-1])
            if _PHRASE_BREAK.search(span):
                continue
            out.append(span)
    return out


def _bare_tokens(tokens: Sequence[_Token], column_names: frozenset[str]) -> list[str]:
    """Single words that are neither stopwords nor the name of a column."""
    out = []
    for token in tokens:
        if not token.is_word or len(token.text) < 3:
            continue
        folded = token.text.casefold()
        if folded in STOPWORDS or normalize(token.text) in column_names:
            continue
        out.append(token.text)
    return out


def extract_candidates(question: str, *, column_names: Iterable[str] = ()) -> list[str]:
    """The phrases worth looking up, in the order they appear in the question.

    Deterministic by construction -- no model, no randomness, no dependence on
    the catalog beyond the column names it suppresses. The four forms the
    architecture names are collected in priority order (quoted, capitalised,
    code, n-gram, bare word) and then deduplicated on their normalised form,
    so the longest and most specific spelling of a phrase is the one kept.
    """
    if not question or not question.strip():
        return []
    names = frozenset(normalize(c) for c in column_names)
    tokens = _tokenize(question)

    quoted = [next(g for g in m.groups() if g is not None) for m in _QUOTED.finditer(question)]
    ordered = [
        *quoted,
        *_capitalised_phrases(question, tokens),
        *_code_tokens(tokens),
        *_ngrams(question, tokens),
        *_bare_tokens(tokens, names),
    ]

    seen: set[str] = set()
    candidates: list[str] = []
    for phrase in ordered:
        key = normalize(phrase)
        if len(key) < 2 or key in seen or key in names:
            continue
        seen.add(key)
        candidates.append(phrase.strip())
        if len(candidates) >= _MAX_CANDIDATES:
            break
    return candidates


# --- matching ---------------------------------------------------------------


@dataclass(frozen=True)
class _Indexed:
    """A catalog entry with the two forms the scorers compare against."""

    entry: CatalogEntry
    norm: str
    words: frozenset[str]


def _content_words(normalized: str) -> frozenset[str]:
    """The words of a normalised phrase that carry meaning.

    Stopwords are excluded because they are shared by accident: "flyer and
    paid" and "floral and garden" agree on "and" and on nothing else.
    """
    return frozenset(w for w in normalized.split() if w not in STOPWORDS)


def _near_miss(candidate_norm: str, value_norm: str) -> bool:
    """Is this pair close enough at the character level to be a misspelling?

    Always `difflib`, whichever scorer found the pair, so the anchor means the
    same thing on both paths.
    """
    if not candidate_norm or not value_norm:
        return False
    return SequenceMatcher(autojunk=False, a=value_norm, b=candidate_norm).ratio() >= NEAR_MISS_SCORE


class LiteralMatcher:
    """Resolves phrases in a question to real values in the database.

    The matcher holds the catalog in memory and scores every candidate against
    every value. With 1,277 values and a few dozen candidates that is tens of
    milliseconds on the `difflib` path, which is why there is no index here:
    the catalog is a vocabulary, not a table.

    `use_trigram` left as None means "detect": `pg_trgm` if the database has
    it, `difflib` otherwise. A trigram query that fails at match time falls
    back to `difflib` for the rest of the process rather than failing the
    question -- retrieval is best-effort everywhere in this architecture.
    """

    def __init__(
        self,
        catalog: Iterable[CatalogEntry],
        *,
        min_score: float = 0.6,
        use_trigram: bool | None = None,
        database: Any = None,
    ) -> None:
        self.catalog = list(catalog)
        self.min_score = min_score
        self._database = database
        # Normalised form and content words are computed once per value, not
        # once per (value, candidate) pair: the inner loop runs 1,277 times a
        # candidate and a few dozen candidates a question.
        self._entries = [
            _Indexed(entry, norm, _content_words(norm))
            for entry, norm in ((e, normalize(e.value)) for e in self.catalog)
            if norm
        ]
        self._column_names = frozenset(normalize(e.column) for e in self.catalog)
        if use_trigram is None:
            self.use_trigram = database is not None and trigram_available(database)
        elif use_trigram and database is None:
            raise ValueError("use_trigram=True needs a database to run word_similarity on")
        else:
            self.use_trigram = bool(use_trigram)

    @property
    def scorer(self) -> str:
        """Which similarity is in use, for the trace and for tests."""
        return "pg_trgm" if self.use_trigram else "difflib"

    def match(self, question: str, *, limit_per_phrase: int = 3) -> list[LiteralMatch]:
        """Resolve the question's phrases, best first, at most one per column.

        One match per (table, column) per phrase, because three spellings of
        the same product name tell the generator nothing; up to
        `limit_per_phrase` columns per phrase, because a phrase that is a
        department name *and* a category name is exactly the ambiguity the
        generator should be shown rather than left to guess at.
        """
        candidates = extract_candidates(question, column_names=self._column_names)
        if not candidates or not self._entries:
            return []

        matches: list[LiteralMatch] = []
        for phrase, by_column in zip(candidates, self._score(candidates)):
            ranked = sorted(by_column.items(), key=lambda kv: (-kv[1][1], kv[0]))
            for _, (entry, score) in ranked[:limit_per_phrase]:
                matches.append(
                    LiteralMatch(
                        phrase=phrase,
                        table=entry.table,
                        column=entry.column,
                        value=entry.value,
                        score=round(score, 3),
                    )
                )
        return _drop_duplicate_values(matches)

    # --- scoring backends ---------------------------------------------------

    def _score(self, candidates: Sequence[str]) -> list[_ByColumn]:
        """Per candidate, the best entry in each column at or above the floor."""
        if self.use_trigram:
            try:
                return self._score_trigram(candidates)
            except Exception:
                # A dropped extension or a dead connection costs precision,
                # not the question: keep going on the pure-Python scorer.
                self.use_trigram = False
        return [self._score_difflib(normalize(c)) for c in candidates]

    def _keep(self, candidate: str, indexed: _Indexed, score: float) -> bool:
        """Whether a scored pair is believable enough to show the generator.

        The floor is the scorer's own; the near-miss anchor is always
        `difflib`'s, for the reason recorded on `NEAR_MISS_SCORE`.
        """
        if score < self.min_score:
            return False
        if indexed.words & _content_words(candidate):
            return True
        return _near_miss(normalize(candidate), indexed.norm)

    def _score_difflib(self, candidate: str) -> _ByColumn:
        """`difflib` similarity, keeping the best value per column.

        `get_close_matches` is the function the architecture names, but it
        returns strings without their scores and without which column they came
        from, both of which `LiteralMatch` carries. This is the same
        SequenceMatcher ratio behind it, including the two cheap upper bounds
        it uses to skip most of the comparisons outright.
        """
        best: _ByColumn = {}
        if not candidate:
            return best
        words = _content_words(candidate)
        matcher = SequenceMatcher(autojunk=False)
        matcher.set_seq2(candidate)
        for indexed in self._entries:
            matcher.set_seq1(indexed.norm)
            if matcher.real_quick_ratio() < self.min_score:
                continue
            if matcher.quick_ratio() < self.min_score:
                continue
            score = matcher.ratio()
            if score < self.min_score:
                continue
            if not indexed.words & words and not _near_miss(candidate, indexed.norm):
                continue
            key = (indexed.entry.table, indexed.entry.column)
            if key not in best or score > best[key][1]:
                best[key] = (indexed.entry, score)
        return best

    def _score_trigram(self, candidates: Sequence[str]) -> list[_ByColumn]:
        """`pg_trgm`'s `word_similarity`, every candidate in one round trip.

        `word_similarity(a, b)` scores `a` against the best-matching run of
        whole words inside `b`, which is what makes "scan-back" reach
        `Scan-Back Allowance` as well as `SCAN_BACK`. The normalised forms go
        over the wire, not the raw ones, so the two backends agree on what is
        being compared; ordinality carries the row back to its catalog entry,
        so the table and column never need to make the trip.
        """
        phrases = [normalize(c) for c in candidates]
        params = {
            "phrases": phrases,
            "values": [i.norm for i in self._entries],
            "floor": self.min_score,
        }
        with _engine(self._database).connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT p.ord AS phrase_ord, v.ord AS value_ord,
                           word_similarity(p.phrase, v.value) AS score
                    FROM unnest(CAST(:phrases AS text[])) WITH ORDINALITY AS p(phrase, ord)
                    CROSS JOIN unnest(CAST(:values AS text[]))
                         WITH ORDINALITY AS v(value, ord)
                    WHERE word_similarity(p.phrase, v.value) >= :floor
                    """
                ),
                params,
            ).all()

        best: list[_ByColumn] = [{} for _ in candidates]
        for row in rows:
            indexed = self._entries[row.value_ord - 1]
            score = float(row.score)
            if not self._keep(phrases[row.phrase_ord - 1], indexed, score):
                continue
            bucket = best[row.phrase_ord - 1]
            key = (indexed.entry.table, indexed.entry.column)
            if key not in bucket or score > bucket[key][1]:
                bucket[key] = (indexed.entry, score)
        return best


def _drop_duplicate_values(matches: Sequence[LiteralMatch]) -> list[LiteralMatch]:
    """One row per resolved value, keeping the phrase that matched it best.

    "Show Dairy & Eggs" produces the candidates "Show Dairy & Eggs", "Dairy &
    Eggs" and "dairy and eggs", all of which resolve to the same stored value.
    Three identical lines in the generator's prompt is noise; the longest
    phrase at the highest score is the informative one.
    """
    best: dict[tuple[str, str, str], LiteralMatch] = {}
    for match in matches:
        key = (match.table, match.column, match.value)
        current = best.get(key)
        if current is None:
            best[key] = match
            continue
        if (match.score, len(match.phrase)) > (current.score, len(current.phrase)):
            best[key] = match
    kept = set(id(m) for m in best.values())
    return [m for m in matches if id(m) in kept]


def render_literal_map(matches: Sequence[LiteralMatch]) -> str:
    """The block the generator's human turn carries (section 4.3).

    Aligned on the arrow so the mapping reads as a table; empty for no matches,
    so the caller can concatenate it unconditionally.
    """
    if not matches:
        return ""
    quoted = [f'"{m.phrase}"' for m in matches]
    width = max(len(q) for q in quoted)
    lines = [
        f"  {q.ljust(width)} -> {m.table}.{m.column} = {m.value!r}"
        for q, m in zip(quoted, matches)
    ]
    return "\n".join([LITERAL_MAP_HEADER, *lines])
