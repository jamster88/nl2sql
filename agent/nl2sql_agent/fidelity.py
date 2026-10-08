"""Is a rewording still the same question? The fidelity gate's checks in code.

Section 22.3 of `multi-agent_arch_specs/Multi-Agent_NL2SQL_arch7.md`. The
ensemble asks one question several ways and compares the answers, which
compares like with like only while every wording asks what the original
asks. Five checks decide that a rewording does; four of them are here, and
none of them calls a model:

| Check | Holds | Fails when |
|---|---|---|
| F1 numbers | the same numerals, as a multiset; "ten" and "tenth" are 10 | "top ten" became "top five" |
| F2 literals | the original's quoted strings, names, codes and FY2025s, kept | "Dairy & Eggs" became "dairy" |
| F3 polarity | the same directions: high, low, up, down, negation | "lowest" became "highest" |
| F5 distinct | token Jaccard at most 0.8 to the original and each kept one | two differ by a comma |

The fifth, F4 -- the Supervisor's reading of the rewording builds the
original's answer contract -- reads meaning, needs a model, and comes with
the Paraphraser. These run before it, so a rewording that visibly changed
the question costs no call.

They are literal on purpose. A rewording they pass may still have changed
the question, which is F4's to catch; one they fail is discarded, not
argued with, because a faithful rewording lost costs one candidate and an
unfaithful one run costs a wrong vote. The word lists began as the
implementation specification's (section 3.4) and grow from the
hand-written paraphrase set (`benchmarks/paraphrases.py`), where a
rewording a person checked and these checks would discard is a failing
test (Phase 0, and S1 of `Multi-Agent_NL2SQL_arch7_risks_by_phase.md`).
What was added for a rewording the starting lists discarded is marked
"grown" below, with the rewording that showed it; `docs/benchmark.md`
counts them.
"""

from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal
from typing import Sequence

#: F1: a number written as a word is the number.
NUMBER_WORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
    "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17", "eighteen": "18",
    "nineteen": "19", "twenty": "20",
}
#: Grown: an ordinal is its number too -- "the fourth fiscal quarter" is
#: "fiscal quarter 4" (B06), "the twelfth fiscal month" is "fiscal month
#: 12" (B07) -- and so is "4th", which is therefore no letter-digit literal.
ORDINALS = {
    "first": "1", "second": "2", "third": "3", "fourth": "4", "fifth": "5", "sixth": "6", "seventh": "7",
    "eighth": "8", "ninth": "9", "tenth": "10", "eleventh": "11", "twelfth": "12", "thirteenth": "13",
    "fourteenth": "14", "fifteenth": "15", "sixteenth": "16", "seventeenth": "17", "eighteenth": "18",
    "nineteenth": "19", "twentieth": "20",
}
_NUMBERS = {**NUMBER_WORDS, **ORDINALS}

#: F3: the direction classes, by the words that put a question in each.
HIGH = ("top", "highest", "best", "most", "largest", "biggest", "greatest", "maximum", "max", "leading",
        "strongest", "peak")
#: Grown: "cheapest" -- "which competitor is cheapest relative to our
#: prices" is the one that "prices lowest relative to us" (B13).
LOW = ("bottom", "lowest", "worst", "least", "fewest", "smallest", "minimum", "min", "weakest", "poorest",
       "cheapest")
UP = ("increase", "increased", "increasing", "grew", "grow", "growth", "rose", "rise", "rising", "gain",
      "gained", "improved", "up")
DOWN = ("decrease", "decreased", "decreasing", "decline", "declined", "declining", "fell", "fall", "falling",
        "drop", "dropped", "shrank", "shrink", "loss", "lost", "down")
NEGATION = ("not", "no", "never", "without", "excluding", "exclude", "excluded", "except", "non")
POLARITY = {"high": HIGH, "low": LOW, "up": UP, "down": DOWN, "negation": NEGATION}
#: Grown: a direction word with no direction in it. "Broken down by
#: allowance type" asks for a breakdown, not a decline: read as one, it
#: discarded "for each allowance type" (B09) and put a decline into "break
#: down our net sales by state" (B14).
NOT_DIRECTION = re.compile(
    r"\b(?:break|breaks|breaking|broke|broken)\s+(?:(?:it|them|this|that|these|those)\s+)?down\b"
)

#: F5: above this token Jaccard similarity, two wordings are one.
MAX_JACCARD = 0.8

#: A number as digits: thousands separated by commas, or not, and a decimal part.
_NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
#: A word, an apostrophe inside it kept ("company's", "didn't").
_WORD = re.compile(r"[^\W_]+(?:'[^\W_]+)*")
#: The joining words a capitalised name may carry ("Dairy & Eggs", "Bank of America").
_JOINERS = ("&", "and", "of", "the")
#: Words (an apostrophe inside one kept), the ampersand, and every other
#: mark as a token of its own, which ends a name.
_TOKEN = re.compile(r"\w+(?:'\w+)*|&|[^\w\s]")
#: The marks that end a sentence, after which a capital is grammar's.
_SENTENCE_ENDS = (".", "?", "!")
#: Quoted strings: in double quotes, straight or curly; in single quotes
#: that open at the start of a word and close at the end of one, so the
#: apostrophe in "Joe's" quotes nothing (read with curly ones straightened).
_DOUBLE_QUOTED = (re.compile(r'"([^"]+)"'), re.compile(r"“([^”]+)”"))
_SINGLE_QUOTED = re.compile(r"(?<![\w'])'(.+?)'(?!\w)")
#: A token mixing letters and digits (FY2025, Q4), but not an ordinal (4th), which is F1's.
_MIXED = re.compile(r"\b(?=\w*[^\W\d_])(?=\w*\d)(?!\d+(?:st|nd|rd|th)\b)\w+\b", re.IGNORECASE)
#: Grown: a code, written with an underscore -- "like SCAN_BACK" names the
#: column the answer is reported by (B09), and nothing else held it.
_CODE = re.compile(r"\b[^\W_]+(?:_[^\W_]+)+\b")


def _fold(text: str) -> str:
    """Case-folded, its curly apostrophes straightened and its spaces collapsed."""
    return " ".join(text.replace("’", "'").casefold().split())


def _words(text: str) -> list[str]:
    return _WORD.findall(_fold(text))


def _number(digits: str) -> str:
    """One spelling per number: no thousands separators, no trailing zeros."""
    value = Decimal(digits.replace(",", ""))
    return str(value.quantize(Decimal(1)) if value == value.to_integral_value() else value.normalize())


def numerals(text: str) -> Counter[str]:
    """F1: every number in the text, as digits; a number word counts as its number."""
    found = Counter(_number(match) for match in _NUMBER.findall(text))
    found.update(_NUMBERS[word] for word in _words(text) if word in _NUMBERS)
    return found


def _capitalised(token: str) -> bool:
    """A word of letters that begins with a capital. FY2025 and SCAN_BACK
    are literals of their own, not words of a name."""
    return token[:1].isupper() and token.replace("'", "").isalpha()


def _names(text: str) -> list[str]:
    """Capitalised words in a run, joined by the joining words: "Dairy & Eggs".

    Grown: a sentence's first word is capitalised by grammar, not because it
    names anything, and starts no name -- read as one, "Give the SKU and the
    amount" asked every rewording of B10 to begin "Give the SKU". The price
    is a name that opens a sentence, which this does not hold; F4 reads it.
    """
    names: list[str] = []
    run: list[str] = []
    pending: list[str] = []  # joining words seen since the last capitalised one
    sentence_start = True

    def close() -> None:
        if sum(1 for token in run if _capitalised(token)) >= 2:
            names.append(" ".join(run))
        run.clear()
        pending.clear()

    for token in _TOKEN.findall(text.replace("’", "'")):
        if _capitalised(token) and not sentence_start:
            run.extend(pending + [token])
            pending.clear()
        elif run and token in _JOINERS:
            pending.append(token)
        else:
            close()
        if token in _SENTENCE_ENDS:
            sentence_start = True
        elif token[:1].isalnum():
            sentence_start = False
    close()
    return names


def literals(text: str) -> set[str]:
    """F2: what a rewording must carry word for word, case-folded.

    Quoted strings, capitalised multi-word names ("Dairy & Eggs", "Print
    Flyer"), codes (SCAN_BACK) and tokens that mix letters and digits
    (FY2025, Q4).
    """
    found: list[str] = []
    for pattern in _DOUBLE_QUOTED:
        found.extend(pattern.findall(text))
    found.extend(_SINGLE_QUOTED.findall(text.replace("‘", "'").replace("’", "'")))
    found.extend(_names(text))
    found.extend(_CODE.findall(text))
    found.extend(_MIXED.findall(text))
    return {_fold(literal) for literal in found if literal.strip()}


def polarity(text: str) -> set[str]:
    """F3: the direction classes the text has -- high, low, up, down, negation."""
    words = set(_WORD.findall(NOT_DIRECTION.sub(" ", _fold(text))))
    classes = {name for name, vocabulary in POLARITY.items() if words & set(vocabulary)}
    if any(word.endswith("n't") for word in words):
        classes.add("negation")
    return classes


def similarity(a: str, b: str) -> float:
    """Token Jaccard, case-folded and with the punctuation stripped."""
    left, right = set(_words(a)), set(_words(b))
    if not left and not right:
        return 1.0
    return len(left & right) / len(left | right)


def distinct(text: str, others: Sequence[str], *, max_jaccard: float = MAX_JACCARD) -> bool:
    """F5: no closer than `max_jaccard` to any of `others`."""
    return all(similarity(text, other) <= max_jaccard for other in others)


def _difference(original: Counter[str] | set[str], rewording: Counter[str] | set[str]) -> str:
    missing = sorted(Counter(original) - Counter(rewording))
    added = sorted(Counter(rewording) - Counter(original))
    parts = [f"{', '.join(missing)} missing"] if missing else []
    parts += [f"{', '.join(added)} added"] if added else []
    return "; ".join(parts)


def check(original: str, rewording: str, kept: Sequence[str] = ()) -> str | None:
    """None when the rewording passes F1, F2, F3 and F5; else the first failure.

    `kept` is the rewordings already kept, which F5 holds this one apart
    from as it does from the original. The reason names the check and what
    differs -- "F2 literals: 'dairy & eggs' not found" -- so a discarded
    rewording's record says why.
    """
    if numerals(original) != numerals(rewording):
        return f"F1 numbers: {_difference(numerals(original), numerals(rewording))}"
    folded = _fold(rewording)
    for literal in sorted(literals(original)):
        if not re.search(rf"(?<!\w){re.escape(literal)}(?!\w)", folded):
            return f"F2 literals: '{literal}' not found"
    if polarity(original) != polarity(rewording):
        return f"F3 polarity: {_difference(polarity(original), polarity(rewording))}"
    for index, other in enumerate((original, *kept)):
        score = similarity(rewording, other)
        if score > MAX_JACCARD:
            which = "the original" if index == 0 else f"kept rewording {index}"
            return f"F5 distinct: too close to {which} (Jaccard {score:.2f})"
    return None
