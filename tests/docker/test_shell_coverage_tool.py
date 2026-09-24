"""The shell-coverage measurement, checked against shapes it got wrong.

`tests/shell_coverage.py` is where the percentages in README.md come from,
which makes its line classifier load-bearing: every mistake in it is a
number in a document that nobody can tell is wrong. It has made three
already, and each is pinned below.

The classifier answers one question -- which physical lines make up one
command -- because bash does not report them individually and does not even
report them consistently: a backslash-continued simple command is traced at
its first line, an assignment from a multi-line `$(...)` at its last. So a
command counts as run when the trace mentions any of its lines, and what has
to be right is the grouping.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.shell_coverage import DRIVEN_BY, logical_commands, report, traced_lines

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def commands_of(source: str) -> list[str]:
    return [text for _, _, text in logical_commands(source)]


# ---------------------------------------------------------------------------
# What counts as one command
# ---------------------------------------------------------------------------


def test_a_backslash_continued_command_is_one_command():
    """It was three, and the two continuation lines were reported missed on
    every run -- which is how two scripts came to be quoted at 92% and 96%.
    """
    source = 'psql -U postgres \\\n    -d retail \\\n    -c "SELECT 1"\n'
    commands = logical_commands(source)
    assert len(commands) == 1
    first, span, text = commands[0]
    assert first == 1
    assert span == {1, 2, 3}
    assert text.startswith("psql")


def test_a_pipeline_split_across_lines_is_one_command():
    source = 'trgm=$(psql -tAc "SELECT 1" |\n    tr -d "[:space:]" || true)\n'
    assert len(logical_commands(source)) == 1


def test_a_boolean_operator_continues_a_command():
    source = 'ensure_extensions &&\n    info "ready"\n'
    assert len(logical_commands(source)) == 1


def test_a_quoted_string_spanning_lines_is_one_command():
    """A multi-line SQL literal is an argument, not four commands."""
    source = 'psql -c "\nSELECT count(*)\nFROM golden_pairs\n"\n'
    assert len(logical_commands(source)) == 1


def test_a_double_quote_inside_single_quotes_does_not_open_a_string():
    """`awk -F'"'` is balanced. Reading it as an open quote swallowed every
    line after it into one command, which reported the wrong line as missed.
    """
    source = "tag=$(awk -F'\"' '/^TAG=/ {print $2}' setup.sh)\ninfo \"$tag\"\n"
    assert commands_of(source) == [
        "tag=$(awk -F'\"' '/^TAG=/ {print $2}' setup.sh)",
        'info "$tag"',
    ]


# ---------------------------------------------------------------------------
# What bash never numbers, and so is not counted against a script
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "step() {\n    printf '%s' \"$1\"\n}\n",          # function header
        "if true; then\n    :\nfi\n",                      # then/fi
        "for x in 1; do\n    :\ndone\n",                   # do/done
        "case $1 in\n    --flag) :; ;;\nesac\n",           # case/esac
    ],
)
def test_block_shapes_are_not_counted(source: str):
    """bash attributes no line number to any of them, so counting them as
    missed would put a ceiling on every script that no test could lift.
    """
    texts = commands_of(source)
    for keyword in ("then", "fi", "do", "done", "esac", "{", "}"):
        assert keyword not in texts


def test_a_heredoc_body_is_data_not_commands():
    source = "cat <<'EOF'\nUsage: ./x.sh\n  --flag  do a thing\nEOF\n"
    assert commands_of(source) == ["cat <<'EOF'"]


def test_a_closing_brace_carrying_a_redirect_is_not_a_command():
    """`{ ... } > .env` is reported at the line that opens the group."""
    source = '{\n    echo "A=1"\n} > .env\n'
    assert commands_of(source) == ['echo "A=1"']


def test_comments_and_blank_lines_are_not_commands():
    assert commands_of("# a comment\n\n   \ninfo 'x'\n") == ["info 'x'"]


# ---------------------------------------------------------------------------
# Reading a trace
# ---------------------------------------------------------------------------


def test_the_trace_is_read_per_script():
    trace = "+@setup.sh@12@ docker pull x\n+@launch.sh@40@ docker compose up\n+@setup.sh@13@ :\n"
    assert traced_lines(trace) == {"setup.sh": {12, 13}, "launch.sh": {40}}


def test_lines_that_are_not_traces_are_ignored():
    assert traced_lines("WARNING: something\n    ordinary output\n") == {}


def test_a_command_counts_as_run_when_any_of_its_lines_was_traced():
    """The point of grouping. bash reports a backslash-continued command at
    its first line and a multi-line assignment at its last; requiring a
    particular one would make the answer depend on which shape it is.
    """
    script = REPO_ROOT / "setup.sh"
    commands = logical_commands(script.read_text())
    multiline = next((first, span) for first, span, _ in commands if len(span) > 2)
    first, span = multiline

    for reported in (min(span), max(span)):
        rows, covered, _ = report(f"+@setup.sh@{reported}@ whatever\n")
        assert covered >= 1, f"a command traced at line {reported} was counted as missed"


# ---------------------------------------------------------------------------
# The tool knows about every script
# ---------------------------------------------------------------------------


def test_every_shell_script_that_takes_flags_is_measured():
    """A script the tool does not list is one whose percentage nobody sees,
    which is the same as not measuring it.
    """
    from tests.docker.test_script_coverage import ALL_SHELL

    assert set(ALL_SHELL) == set(DRIVEN_BY)


def test_every_script_is_driven_by_test_files_that_exist():
    for script, drivers in DRIVEN_BY.items():
        assert (REPO_ROOT / script).is_file(), script
        for driver in drivers:
            assert (REPO_ROOT / driver).is_file(), driver


def test_a_command_substitution_holding_a_loop_is_one_command():
    """`events=$(curl ... | while read; do ...; done)` is one command, and
    bash reports it at the `done)`. Closing the group at the `do` left the
    loop body looking like two commands nothing ever ran.
    """
    source = (
        'events=$(curl -sS "$URL" |\n'
        "    while IFS= read -r line; do\n"
        "        printf '%s\\n' \"$line\"\n"
        '        [[ "$line" == "event: done" ]] && break\n'
        "    done)\n"
    )
    commands = logical_commands(source)
    assert len(commands) == 1
    first, span, _ = commands[0]
    assert first == 1
    assert span == {1, 2, 3, 4, 5}


def test_a_substitution_opened_and_closed_on_one_line_does_not_continue():
    source = 'name=$(basename "$0")\ninfo "$name"\n'
    assert len(logical_commands(source)) == 2
