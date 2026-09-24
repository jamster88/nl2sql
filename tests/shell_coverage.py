"""Command coverage for the shell scripts.

    python -m tests.shell_coverage

Nothing in coverage.py can see a shell script, and ten of them here carry
real decisions -- which image to pull, whether a container is already
serving queries, which browser opener this platform has. They are driven by
the same sandboxes the tests use; this runs those tests with `bash -x`
turned on and counts which lines the traces mention.

It is a measurement, not a gate. What is asserted in CI is structural --
every flag parsed, documented and passed by some test, every `warn` and
`die` triggered by one (`tests/docker/test_script_coverage.py`) -- because
a percentage is a number to look at and an unasserted warning is a bug. This
is how the percentages in README.md are arrived at, so that they can be
checked rather than believed.

What bash cannot report is excluded rather than counted as missed: it
attributes no line number to a function header, a block keyword, a bare
`case` label, a heredoc body, or the continuation lines of a command split
across several -- it reports the whole command at its first line.
"""

from __future__ import annotations

import collections
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Each script, and the test files whose sandbox runs it.
DRIVEN_BY: dict[str, tuple[str, ...]] = {
    "start.sh": ("tests/docker/test_start_script.py",),
    "setup.sh": ("tests/docker/test_setup_script.py",),
    "launch.sh": ("tests/docker/test_launch_script.py",),
    "rag/lib.sh": ("tests/rag/test_rag_scripts.py",),
    "rag/run_all.sh": ("tests/rag/test_rag_scripts.py",),
    "rag/run_update.sh": ("tests/rag/test_rag_scripts.py",),
    "rag/start_rag_db.sh": ("tests/rag/test_rag_scripts.py",),
    "rag/01_start_chunk_db.sh": ("tests/rag/test_rag_scripts.py",),
    "rag/03_start_vector_db.sh": ("tests/rag/test_rag_scripts.py",),
    "rag/publish_db_image.sh": ("tests/rag/test_rag_scripts.py",),
    # Driven against a real HTTPS server rather than a fake Docker -- bash,
    # curl and jq against something built from `create_app` -- which changes
    # how it runs but not what is worth knowing about it.
    "docker/apitest/smoke.sh": ("tests/api/test_smoke_script.py",),
    # Sourced by the nginx image's entrypoint rather than executed, and by
    # `sh` rather than bash, so it is traced the same way it runs.
    "gui/10-nl2sql-config.envsh": ("tests/gui/test_gui_project.py",),
}

#: Lines bash never attributes a line number to, so counting them as missed
#: would mean a ceiling below 100% that no test could ever lift.
_BLOCK_KEYWORDS = re.compile(
    r"^(fi|done|esac|\}|\{|else|;;|\)\s*;;|do|then)\s*(;;)?$|^\}\s*[<>|]"
)
_FUNCTION_HEADER = re.compile(r"^(local\s+)?[\w_]+\(\)\s*\{")
_BARE_CASE_LABEL = re.compile(r"^[^(]*\)\s*$")
_HEREDOC_START = re.compile(r"<<-?'?EOF'?")
_SINGLE_QUOTED = re.compile(r"'[^']*'")
#: A command continues onto the next line when it ends with a backslash, a
#: pipe, or a boolean operator. bash reports the whole thing as one.
_CONTINUES = re.compile(r"(\\|\|\||&&|\||&)\s*$")


def logical_commands(source: str) -> list[tuple[int, set[int], str]]:
    """The script's commands, each as (first line, its lines, its text).

    Physical lines are grouped because bash does not report them
    individually: a command continued with a backslash, or one whose
    argument is a quoted string spanning several lines, is one command.

    Which line it is *reported* at varies by construct -- a backslash-
    continued simple command is traced at its first line, an assignment from
    a multi-line `$(...)` at its last -- so this deliberately does not model
    that. A command counts as run when the trace mentions any of its lines,
    which is the question actually being asked and the one bash answers
    consistently.

    Excluded entirely: blanks and comments, heredoc bodies, and the shapes
    bash never numbers at all -- function headers, block keywords, bare
    `case` labels, and a `}` that only carries a redirect.
    """
    lines = source.splitlines()
    commands: list[tuple[int, set[int], str]] = []
    in_heredoc = False
    open_quote = False
    continued = False
    depth = 0
    start: int | None = None
    span: set[int] = set()

    for number, raw in enumerate(lines, 1):
        stripped = raw.strip()

        if in_heredoc:
            if stripped in ("EOF", "'EOF'"):
                in_heredoc = False
            continue

        if not (continued or open_quote or depth):
            if not stripped or stripped.startswith("#"):
                continue
            if _FUNCTION_HEADER.match(stripped) or _BLOCK_KEYWORDS.match(stripped):
                continue
            if _BARE_CASE_LABEL.match(stripped) and "(" not in stripped:
                continue
            start, span = number, set()

        span.add(number)

        if _HEREDOC_START.search(raw) and not open_quote:
            in_heredoc = True
            if start is not None:
                commands.append((start, set(span), lines[start - 1].strip()))
            start, span, continued, open_quote = None, set(), False, False
            continue

        # A backslash is not the only thing that continues a command: a line
        # ending in a pipe or a boolean operator does too, and bash reports
        # the whole pipeline as one.
        continued = bool(_CONTINUES.search(raw))
        # An unclosed `$(` continues a command however the lines inside it
        # end -- `events=$(curl ... | while read; do ... done)` is one
        # command, and bash reports it at the `done)`.
        literal = _SINGLE_QUOTED.sub("", raw)
        depth = max(0, depth + literal.count("$(") - literal.count(")"))
        # Double quotes inside a single-quoted string are literal -- `awk
        # -F'"'` is not an unbalanced quote, and treating it as one swallows
        # every line after it into the same command.
        if _SINGLE_QUOTED.sub("", raw).count('"') % 2 == 1:
            open_quote = not open_quote

        if not continued and not open_quote and not depth and start is not None:
            commands.append((start, set(span), lines[start - 1].strip()))
            start, span = None, set()

    return commands


def traced_lines(trace: str) -> dict[str, set[int]]:
    """Which lines of which script a `bash -x` trace mentions."""
    hit: dict[str, set[int]] = collections.defaultdict(set)
    for name, number in re.findall(r"\+@([^@]+)@(\d+)@", trace):
        hit[name].add(int(number))
    return hit


def run(directory: Path) -> str:
    """Run every sandbox that drives a script, tracing into `directory`."""
    targets = sorted({path for paths in DRIVEN_BY.values() for path in paths})
    environment = {**os.environ, "NL2SQL_SHELL_TRACE": str(directory)}
    subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--run-docker", *targets],
        cwd=REPO_ROOT, env=environment, capture_output=True, text=True, timeout=3600,
    )
    log = directory / "trace.log"
    return log.read_text(errors="replace") if log.exists() else ""


def report(trace: str) -> tuple[list[tuple[str, int, int, list[tuple[int, str]]]], int, int]:
    hit = traced_lines(trace)
    rows = []
    covered_total = command_total = 0
    for name in DRIVEN_BY:
        commands = logical_commands((REPO_ROOT / name).read_text())
        seen = hit[Path(name).name]
        covered = sum(1 for _, span, _ in commands if span & seen)
        missed = [(first, text) for first, span, text in commands if not (span & seen)]
        rows.append((name, covered, len(commands), missed))
        covered_total += covered
        command_total += len(commands)
    return rows, covered_total, command_total


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        trace = run(Path(directory))
    if not trace:
        print("no trace was produced -- did the sandboxes stop honouring NL2SQL_SHELL_TRACE?")
        return 1

    rows, covered, total = report(trace)
    print(f"{'script':30} {'run':>8} {'commands':>9} {'':>5}")
    print("-" * 52)
    for name, hit_count, line_count, _ in rows:
        print(f"{name:30} {hit_count:>8} {line_count:>9} {100 * hit_count / line_count:>4.0f}%")
    print("-" * 52)
    print(f"{'TOTAL':30} {covered:>8} {total:>9} {100 * covered / total:>4.0f}%")

    for name, _, _, missed in rows:
        if missed:
            print(f"\n{name} misses {len(missed)}:")
            for number, text in missed:
                print(f"   {number:>4}: {text[:88]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
