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

from tests.shell_coverage import (
    DRIVEN_BY,
    _percent,
    logical_commands,
    report,
    traced_lines,
)

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


# ---------------------------------------------------------------------------
# Heredocs, whatever they call their delimiter
# ---------------------------------------------------------------------------


def test_a_heredoc_body_is_not_counted_as_commands():
    """Its lines are data, and bash never gives them a line number."""
    source = (
        'cat > /tmp/x <<EOF\n'
        'proxy_ssl_verify on;\n'
        'proxy_ssl_name ${NAME};\n'
        'EOF\n'
        'echo done\n'
    )
    texts = [text for _, _, text in logical_commands(source)]
    assert texts == ["cat > /tmp/x <<EOF", "echo done"]


@pytest.mark.parametrize(
    "delimiter", ["EOF", "TLSEOF", "'EOF'", '"EOF"', "'TLSEOF'", "SQL", "_END_"]
)
def test_the_delimiter_is_read_rather_than_assumed(delimiter: str):
    """`EOF` used to be hardcoded.

    A script whose heredoc ended with anything else had every line of the
    body counted as an uncovered command -- a ceiling no test could lift,
    reported as a gap in the tests rather than in this tool. One script does
    use another delimiter, because it writes a heredoc from inside one.
    """
    end = delimiter.strip("'\"")
    source = f"cat > /tmp/x <<{delimiter}\nnot a command\nalso not one\n{end}\necho done\n"
    texts = [text for _, _, text in logical_commands(source)]
    assert texts == [f"cat > /tmp/x <<{delimiter}", "echo done"]


def test_a_dash_heredoc_may_indent_its_delimiter():
    source = "cat <<-EOF\n\tbody\n\tEOF\necho done\n"
    texts = [text for _, _, text in logical_commands(source)]
    assert texts == ["cat <<-EOF", "echo done"]


def test_a_delimiter_that_is_not_alone_on_its_line_does_not_end_the_heredoc():
    source = "cat > /tmp/x <<EOF\nEOF is mentioned here\nEOF\necho done\n"
    texts = [text for _, _, text in logical_commands(source)]
    assert texts == ["cat > /tmp/x <<EOF", "echo done"]


def test_two_heredocs_with_different_delimiters_both_close():
    source = (
        "cat > /a <<ONE\nbody\nONE\n"
        "cat > /b <<TWO\nbody\nTWO\n"
        "echo done\n"
    )
    texts = [text for _, _, text in logical_commands(source)]
    assert texts == ["cat > /a <<ONE", "cat > /b <<TWO", "echo done"]


# ---------------------------------------------------------------------------
# Escaped quotes
# ---------------------------------------------------------------------------


def test_an_escaped_quote_does_not_open_a_string():
    """This one had been hiding a third of launch.sh.

    `grep -q "\\"$model"` has three double quotes, one of them escaped and
    therefore literal. Counting it left the scanner permanently inside a
    string, and every command after it was folded into the one before --
    silently, because the result is a *shorter* list of commands that are
    all still reported as covered. The measurement said 100% of 112
    commands; the script had 169.
    """
    source = (
        'if printf \'%s\' "$tags" | grep -q "\\"$model"; then\n'
        '    echo found\n'
        'fi\n'
        'echo after\n'
    )
    texts = [text for _, _, text in logical_commands(source)]
    assert "echo after" in texts, "everything after the escaped quote was swallowed"
    assert "echo found" in texts


def test_an_escaped_quote_inside_a_command_substitution_does_not_unbalance_it():
    source = 'x=$(echo "a \\" b")\necho after\n'
    texts = [text for _, _, text in logical_commands(source)]
    assert texts == ['x=$(echo "a \\" b")', "echo after"]


def test_a_genuinely_unbalanced_quote_still_continues_the_command():
    """The behaviour the escaped-quote fix must not undo: a string really
    spanning two lines is one command, and bash reports it as one."""
    source = 'echo "first\nsecond"\necho after\n'
    texts = [text for _, _, text in logical_commands(source)]
    assert texts == ['echo "first', "echo after"]


def test_every_command_in_launch_sh_is_seen():
    """A whole-file check, because the failure mode above is invisible in a
    per-line one: the scanner stops emitting and nothing says so.

    The last command is near the end of the file, not two thirds through it.
    """
    source = (REPO_ROOT / "launch.sh").read_text()
    commands = logical_commands(source)
    last = commands[-1][0]
    total = len(source.splitlines())
    assert last > total * 0.9, (
        f"the scanner stopped at line {last} of {total}; everything after it "
        "is being folded into one command and reported as covered"
    )


@pytest.mark.parametrize("script", sorted(DRIVEN_BY))
def test_no_script_is_scanned_only_part_way(script: str):
    """The same check for every script the measurement covers."""
    source = (REPO_ROOT / script).read_text()
    commands = logical_commands(source)
    lines = source.splitlines()
    # The last *command* line, ignoring the trailing comments and blanks a
    # script ends with.
    meaningful = [
        n for n, raw in enumerate(lines, 1) if raw.strip() and not raw.strip().startswith("#")
    ]
    assert commands, f"{script} has no commands at all"
    assert commands[-1][0] >= meaningful[-1] - 30, (
        f"{script}: the scanner stopped at line {commands[-1][0]}, but the file "
        f"has commands as late as line {meaningful[-1]}"
    )


# ---------------------------------------------------------------------------
# The number the whole tool is trusted for
# ---------------------------------------------------------------------------


def test_only_a_clean_sweep_prints_a_hundred():
    """`%.0f` rounded 867 of 869 up to "100%".

    That is the one number this tool exists to be trusted about: a report
    saying 100 while two commands have never run is worse than one saying
    nothing, because nobody goes looking.
    """
    assert _percent(869, 869) == 100
    assert _percent(867, 869) == 99
    assert _percent(9999, 10000) == 99


@pytest.mark.parametrize("hit,total,expected", [(0, 10, 0), (5, 10, 50), (1, 3, 33)])
def test_everything_else_is_rounded_down(hit: int, total: int, expected: int):
    assert _percent(hit, total) == expected
