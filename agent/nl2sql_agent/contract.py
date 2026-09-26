"""The answer contract: what a complete answer carries (arch5 section 4.1).

"Top 10 SKUs" has a correct answer that is useless: ten `sku_id` values. It
ran, it is right, and the reader needs a second query to learn what was
sold or how much. The contract is the sentence a colleague would add before
writing the query -- *name each SKU, show the sales that ranked it, and say
which year* -- built deterministically from three things the Supervisor
read out of the question and two things read once from the database:

* the **label map**: for each dimension, the columns that identify a row
  (the surrogate `*_key` and any natural id such as `sku_id`) and the one
  that names it (`product_name`). Read from the catalog's key constraints,
  so a new dimension is covered without a code change;
* the **fiscal calendar**: the latest fiscal year the sales data holds in
  full, which is the default period for a question that names none.

The same contract is read twice. The SQL Generator sees it rendered as one
line of prose before it writes the query, which is expected to make most
answers complete on the first draft; the Completeness Reviewer checks the
result against it after the query has run (`completeness.py`). It names
columns and never SQL: which table supplies `product_name` and how it is
joined is the generator's job, and the pruned schema already tells it.

Everything here is best-effort in the way retrieval is. A catalog that
cannot be read leaves an empty label map, and a calendar that cannot be
read leaves no default period; the contract then says less, and the run
goes on.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .state import AnswerContract, EntityRef

#: Where the fiscal calendar lives. A default period is a filter on its
#: `fiscal_year`, so a contract with one needs this table in the query's scope.
CALENDAR_TABLE = "dim_date"

#: The quantity a ranking is taken to mean when the question does not say.
#: "Top 10 SKUs" is "top 10 SKUs by something", and in a retail sales
#: database the something a person means is net sales.
DEFAULT_RANK_MEASURE = "net sales"

#: The suffixes that make a column an identifier rather than a name.
_KEY_SUFFIX = re.compile(r"_(key|id|code)$", re.IGNORECASE)

#: A single-column primary key or unique constraint, as `pg_get_constraintdef`
#: prints it. Multi-column keys are a fact table's grain, not an identity.
_SINGLE_COLUMN_KEY = re.compile(
    r'^\s*(?:PRIMARY\s+KEY|UNIQUE)\s*\(\s*"?(\w+)"?\s*\)', re.IGNORECASE
)

_RANKED_WORDS = re.compile(
    r"\b(top|bottom|best|worst|highest|lowest|most|least|largest|smallest|biggest|"
    r"leading|rank|ranked|ranking)\b",
    re.IGNORECASE,
)

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15,
    "twenty": 20, "fifty": 50, "hundred": 100,
}

#: "top 10", "top ten", "which 5 products", "the 3 best". The number is one
#: to three digits, so "the 2025 total" never reads as a row count, and a
#: unit after it ("12 months", "4 weeks") makes it a span, not a count.
_ROW_COUNT = re.compile(
    r"\b(?:top|bottom|best|worst|first|last|which|the)\s+"
    r"(\d{1,3}|" + "|".join(_NUMBER_WORDS) + r")\b"
    r"(?!\s*(?:%|percent|fiscal|days?|weeks?|months?|quarters?|years?)\b)",
    re.IGNORECASE,
)

#: A question whose answer is one number: "how many stores are there?",
#: "what were our total net sales?". Its rows are about nothing, so the
#: contract names no entity for it -- telling the generator to put a name
#: beside every store it shows is, for a count of stores, an invitation to
#: show them. The first benchmark run of arch5 answered B01 with ten named
#: stores instead of the number 10.
_ONE_NUMBER = re.compile(
    r"^\s*(how many|how much|what (is|was|were) (the |our )?(total|number|count))\b",
    re.IGNORECASE,
)
#: ...unless it is one number per something.
_GROUPED = re.compile(r"\b(each|every|per|by|broken down|split)\b", re.IGNORECASE)

#: What the Supervisor writes for a question whose answer does not depend on
#: time at all -- how many stores there are, which banner a store belongs to.
#: Such a question gets no default period: a store count "for FY2025" invites
#: the generator to join a fact table the question never needed.
NO_PERIOD = frozenset({"none", "n/a", "na", "null", "not applicable", "timeless"})


# ---------------------------------------------------------------------------
# The label map
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Label:
    """One identifying column and the column that names the same row."""

    table: str
    key: str
    label: str


class LabelMap:
    """Key column -> label column, for every dimension that has a name.

    Lookups are by column name alone, because a result set says nothing
    about which table a column came from: `store_key` out of the sales fact
    needs `store_name` exactly as much as `store_key` out of `dim_store`.
    A key name that two tables label differently is dropped rather than
    guessed.
    """

    def __init__(self, labels: Iterable[Label] = (), names: Iterable[tuple[str, str]] = ()) -> None:
        by_key: dict[str, Label] = {}
        ambiguous: set[str] = set()
        for label in labels:
            known = by_key.get(label.key.lower())
            if known is not None and known.label != label.label:
                ambiguous.add(label.key.lower())
            by_key.setdefault(label.key.lower(), label)
        self._by_key = {k: v for k, v in by_key.items() if k not in ambiguous}
        #: (table, column) for every `*_name` column, so an entity with a name
        #: and no key of its own -- a department, a banner -- still resolves.
        self._names = list(names)

    def __len__(self) -> int:
        return len(self._by_key)

    @property
    def tables(self) -> list[str]:
        """The tables that have a label, in name order."""
        return sorted({label.table for label in self._by_key.values()})

    def labels(self) -> list[Label]:
        return sorted(self._by_key.values(), key=lambda l: (l.table, l.key))

    def for_key(self, column: str) -> Label | None:
        """The label for a key column, or None when it is not a key."""
        return self._by_key.get((column or "").lower())

    def is_label(self, column: str) -> bool:
        lowered = (column or "").lower()
        return any(label.label.lower() == lowered for label in self._by_key.values())

    def for_entity(self, word: str) -> EntityRef:
        """Resolve the Supervisor's noun to the columns that identify and name it.

        Tried in order: a key whose stem is the word (`sku` -> `sku_id`), a
        dimension named for it (`store` -> `dim_store`), and a `<word>_name`
        column anywhere (`department` -> `department_name`). A word none of
        those match is kept as it is, so the contract can still say it.

        Whichever key matched, the one named is the table's natural id when
        it has one: a reader asking about products knows a `sku_id`, not a
        `product_key`.
        """
        candidates = _entity_candidates(word)
        for candidate in candidates:
            noun = candidate.replace("_", " ")
            tables = {l.table for l in self._by_key.values() if _stem(l.key) == candidate}
            tables |= {l.table for l in self._by_key.values() if _table_stem(l.table) == candidate}
            if tables:
                table = sorted(tables)[0]
                chosen = _prefer_natural([l for l in self._by_key.values() if l.table == table])
                return EntityRef(word=noun, key=chosen.key, label=chosen.label, table=chosen.table)
            for table, column in self._names:
                if column.lower() == f"{candidate}_name":
                    return EntityRef(word=noun, label=column, table=table)
        # Nothing matched: keep the word, singular, so the contract can say it.
        return EntityRef(word=candidates[1].replace("_", " ") if candidates else word)


def build_label_map(tables: Iterable[Any]) -> LabelMap:
    """Read the label map out of the catalog (`Database.catalog()`).

    A table's keys are its single-column primary key and unique
    constraints. Its label is the `*_name` (or `*_desc`) column named for
    one of its keys or for the table itself, else a `*_name` column sharing
    a key's leading word (`ad_id` -> `ad_theme_name`). `dim_date` has none
    by that rule, and should not: its only `*_name` is the day of the week,
    and `date_key` already reads as the date it is. `dim_ad_channel` has
    none because it has no name column at all.
    """
    labels: list[Label] = []
    names: list[tuple[str, str]] = []
    for table in tables:
        columns = [column.name for column in table.columns]
        names.extend((table.name, c) for c in columns if c.lower().endswith("_name"))
        keys = [
            match.group(1)
            for match in (_SINGLE_COLUMN_KEY.match(c) for c in table.constraints)
            if match and match.group(1) in columns
        ]
        label = _choose_label(table.name, keys, columns)
        if label:
            labels.extend(Label(table=table.name, key=key, label=label) for key in keys)
    return LabelMap(labels, names)


def _choose_label(table: str, keys: Sequence[str], columns: Sequence[str]) -> str | None:
    candidates = [c for c in columns if c.lower().endswith(("_name", "_desc"))]
    if not keys or not candidates:
        return None
    stems = [_stem(k) for k in keys if _KEY_SUFFIX.search(k)] + [_table_stem(table)]
    for stem in stems:
        for suffix in ("_name", "_desc"):
            if f"{stem}{suffix}" in candidates:
                return f"{stem}{suffix}"
    for stem in stems:
        head = stem.split("_")[0]
        for column in candidates:
            if column.endswith("_name") and column.split("_")[0] == head:
                return column
    return None


def _stem(column: str) -> str:
    return _KEY_SUFFIX.sub("", column.lower())


def _table_stem(table: str) -> str:
    return re.sub(r"^dim_", "", table.lower())


def _prefer_natural(labels: Sequence[Label]) -> Label:
    """The natural id over the surrogate key: `sku_id` means something to a reader."""
    natural = [l for l in labels if not l.key.lower().endswith("_key")]
    return sorted(natural or labels, key=lambda l: l.key)[0]


def _singular(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("sses"):
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us", "is")) and len(word) > 3:
        return word[:-1]
    return word


def _entity_candidates(word: str) -> list[str]:
    """The forms of a noun worth looking up, most specific first.

    Whole and singular, then its last word and that word's singular, then --
    last, because it is the crudest -- the whole word with a final "s"
    struck off: "skus" ends like "status", and only a lookup can tell an
    acronym's plural from a word that ends in "us". The singular always
    comes second, even when it is the same word, so a noun nothing matched
    can still be printed in the singular.
    """
    normalised = re.sub(r"[\s\-]+", "_", (word or "").strip().lower())
    normalised = re.sub(r"[^a-z0-9_]", "", normalised)
    if not normalised:
        return []
    forms = [normalised, _singular(normalised)]
    last = normalised.rsplit("_", 1)[-1]
    forms.extend([last, _singular(last)])
    if normalised.endswith("s") and len(normalised) > 2:
        forms.append(normalised[:-1])
    seen: list[str] = forms[:2]
    for form in forms[2:]:
        if form and form not in seen:
            seen.append(form)
    return seen


# ---------------------------------------------------------------------------
# What is read from the database once
# ---------------------------------------------------------------------------


@dataclass
class ContractResources:
    """The label map and the fiscal calendar, read once per process."""

    label_map: LabelMap = field(default_factory=LabelMap)
    fiscal_year: int | None = None
    fiscal_year_start: date | None = None
    fiscal_year_end: date | None = None
    #: Why a part could not be read. Best-effort, like retrieval: recorded
    #: and carried on without.
    errors: dict[str, str] = field(default_factory=dict)


def load_resources(database: Any) -> ContractResources:
    """Read the label map and the latest complete fiscal year.

    Each half fails on its own. A database that cannot describe itself costs
    the labels; one with no sales calendar costs the default period.
    """
    resources = ContractResources()
    try:
        resources.label_map = build_label_map(database.catalog())
    except Exception as exc:
        resources.errors["label_map"] = str(exc)
    try:
        latest = database.latest_complete_fiscal_year()
    except Exception as exc:
        resources.errors["fiscal_calendar"] = str(exc)
    else:
        if latest is not None:
            year, start, end = latest
            resources.fiscal_year = int(year)
            resources.fiscal_year_start = start
            resources.fiscal_year_end = end
    return resources


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


def is_ranked(question: str, intent: str = "") -> bool:
    """Does the question ask for rows in an order of merit?"""
    return bool(_RANKED_WORDS.search(question or ""))


def asks_for_one_number(question: str) -> bool:
    """Is the answer a single number, not a list of anything?"""
    text = question or ""
    return bool(_ONE_NUMBER.search(text)) and not _GROUPED.search(text)


def requested_row_count(question: str) -> int | None:
    """The N in "top N", when the question says it."""
    match = _ROW_COUNT.search(question or "")
    if not match:
        return None
    token = match.group(1).lower()
    count = int(token) if token.isdigit() else _NUMBER_WORDS[token]
    return count if count >= 1 else None


def build_contract(
    question: str,
    *,
    intent: str = "",
    entities: Sequence[str] = (),
    measure: str | None = None,
    period: str | None = None,
    resources: ContractResources | None = None,
) -> AnswerContract:
    """Turn the Supervisor's reading of the question into the contract.

    The default period is applied only where a quantity is being asked for:
    "which vendor supplies Dairy & Eggs" has no measure and so no span, and
    giving it one would put a filter in front of the generator that the
    question never implied. For the same reason a period of "none" -- the
    Supervisor's word for an answer that does not depend on time -- gets no
    default either.
    """
    resources = resources or ContractResources()
    refs: list[EntityRef] = []
    seen: set[str] = set()
    for word in [] if asks_for_one_number(question) else entities:
        cleaned = (word or "").strip()
        if not cleaned or cleaned.lower() in seen:
            continue
        seen.add(cleaned.lower())
        refs.append(resources.label_map.for_entity(cleaned))

    ranked = is_ranked(question, intent)
    measure = (measure or "").strip() or None
    if ranked and measure is None:
        measure = DEFAULT_RANK_MEASURE
    period = (period or "").strip() or None
    timeless = period is not None and period.lower() in NO_PERIOD
    if timeless:
        period = None

    contract = AnswerContract(
        entities=refs,
        measure=measure,
        period=period,
        ranked=ranked,
        limit=requested_row_count(question),
    )
    if period is None and not timeless and measure is not None and resources.fiscal_year is not None:
        contract.period = f"FY{resources.fiscal_year}"
        contract.period_default = True
        contract.fiscal_year = resources.fiscal_year
        contract.fiscal_year_start = _iso(resources.fiscal_year_start)
        contract.fiscal_year_end = _iso(resources.fiscal_year_end)
    return contract


def stated_measure(contract: AnswerContract) -> str | None:
    """The measure's name, when the contract may say it -- only the default.

    A measure the question names is the question's to define. The
    Supervisor's paraphrase of it is for the trace, never the prompt: on the
    second benchmark run it read "which competitor prices lowest relative to
    us on average" as "average price difference relative to our prices",
    the contract line repeated that, and the generator dutifully computed a
    difference where the question wanted a ratio. The contract only names a
    measure it is supplying itself -- net sales, for a ranking that named
    none -- and otherwise says "the figure the question asks for".
    """
    measure = (contract.measure or "").strip()
    return measure if measure.lower() == DEFAULT_RANK_MEASURE else None


def contract_tables(contract: AnswerContract | None) -> list[str]:
    """The tables the contract's columns come from.

    The contract names columns and leaves the joins to the generator, but
    the generator can only join what the pruned schema shows it: a contract
    asking for `product_name` or a `fiscal_year` filter has to bring
    `dim_product` or `dim_date` into scope, or the one line meant to make
    the first draft complete makes it fail the table allowlist instead.
    A period the question names needs the calendar as much as a default
    one: B11's "in fiscal year 2025" was rejected on its first draft for a
    `dim_date` join nothing had retrieved.
    """
    if contract is None:
        return []
    tables: list[str] = []
    for entity in contract.entities:
        if entity.table and entity.label and entity.table not in tables:
            tables.append(entity.table)
    if contract.period:
        tables.append(CALENDAR_TABLE)
    return tables


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _span(contract: AnswerContract) -> str:
    if contract.fiscal_year_start and contract.fiscal_year_end:
        return f" ({contract.fiscal_year_start} to {contract.fiscal_year_end})"
    return ""


def render_contract(contract: AnswerContract | None) -> str:
    """The contract as the one line of prose the generator is shown.

    Written the way a colleague would say it, and conditional where the
    question might not produce the column at all: "`product_name` beside
    any `sku_id` it shows" asks for a label without asking for a list, so a
    question whose answer is one number is not turned into a table.
    """
    if contract is None:
        return ""
    parts: list[str] = []
    for entity in contract.entities:
        if entity.key and entity.label:
            parts.append(
                f"each {entity.word} named by `{entity.label}` beside any `{entity.key}` it shows"
            )
        elif entity.label:
            parts.append(f"each {entity.word} named by `{entity.label}`")
    if contract.measure:
        named = stated_measure(contract)
        if contract.ranked:
            what = f"the {named}" if named else "the figure"
            parts.append(f"{what} the rows are ranked by, as a column")
        else:
            what = f"the {named} itself" if named else "the figure the question asks for"
            parts.append(f"{what}, as a column")
    if contract.period_default and contract.fiscal_year is not None:
        parts.append(
            f"for any total over time, {contract.period}{_span(contract)}, the latest "
            f"complete fiscal year, filtered on `fiscal_year` = {contract.fiscal_year}, "
            "since the question names no period"
        )
    if contract.limit:
        parts.append(f"no more than {contract.limit} rows, as the question asks")
    return "; ".join(parts) + "." if parts else ""


def default_period_assumption(contract: AnswerContract) -> str:
    """The sentence the answer must carry when the pipeline chose the period."""
    return (
        f"{contract.period}{_span(contract)}, the latest complete fiscal year, "
        "since the question did not name a period"
    )


def describe(contract: AnswerContract) -> str:
    """A short trace line: what the contract asks for, in column terms."""
    bits: list[str] = []
    for entity in contract.entities:
        if entity.key and entity.label:
            bits.append(f"{entity.key}->{entity.label}")
        elif entity.label:
            bits.append(entity.label)
        else:
            bits.append(entity.word)
    if contract.measure:
        bits.append(("ranked by " if contract.ranked else "") + contract.measure)
    if contract.period:
        bits.append(contract.period + (" (default)" if contract.period_default else ""))
    if contract.limit:
        bits.append(f"{contract.limit} rows")
    return ", ".join(bits) or "nothing to add"
