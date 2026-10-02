"""start.sh -- the one command.

It adds three things to what `setup.sh` and `launch.sh` already do: it runs
them in the right order for a first run, it waits until the page actually
answers rather than until the container calls itself healthy, and it opens a
browser. Each of those is a place to be wrong in a way nobody notices until
someone new runs it on a machine that is not this one.

The sandbox drives the real `setup.sh` and `launch.sh` against a fake
`docker`, so what is asserted here is the orchestration rather than a
re-test of the other two.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import pytest

# No marker: every one of these runs against fake `docker`, `curl`, `sleep`,
# `uname` and browser binaries, so none of them needs a daemon -- the same
# arrangement `test_setup_script.py` uses, and the reason both run on an
# ordinary `pytest`.

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
START_SH = REPO_ROOT / "start.sh"

SHIPPED = next(
    line.split('"')[1]
    for line in (REPO_ROOT / "setup.sh").read_text().splitlines()
    if line.startswith("AGENT_TAG=")
)

_URL = re.compile(r"https?://localhost:\d+")


def pages(result) -> list[str]:
    """Every URL put in front of someone, in the order it was, however it was
    opened: the generic openers log `browser`, and a browser asked for a
    window of its own logs `window`."""
    found = []
    for call in result.calls:
        if call.startswith(("browser ", "window ")):
            match = _URL.search(call)
            if match:
                found.append(match.group(0))
    return found


def eventually(result, fragment: str, seconds: float = 5.0) -> list[str]:
    """Calls matching `fragment`, waiting for them if need be.

    For what start.sh starts in the background so that it outlives the
    script -- a browser, an Ollama server. The script has exited by the time
    the result is read, but the thing it started may not have got as far as
    saying so.
    """
    log = result.workdir.parent / "calls.log"
    deadline = time.monotonic() + seconds
    while True:
        found = [line for line in log.read_text().splitlines() if fragment in line]
        if found or time.monotonic() > deadline:
            return found
        time.sleep(0.05)


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


def test_one_command_brings_up_the_databases_the_api_and_the_gui(run_start):
    result = run_start()
    assert result.returncode == 0
    assert result.called("compose up -d postgres")
    assert result.called("--profile api up -d api")
    assert result.called("--profile api --profile gui up -d gui")


def test_it_opens_a_browser_at_the_interface(run_start):
    result = run_start()
    opened = [call for call in result.calls if call.startswith("browser ")]
    assert opened, "no browser was opened:\n" + "\n".join(result.calls)
    assert opened[0].endswith("http://localhost:8080")
    assert "Opening http://localhost:8080" in result.output


def test_it_opens_the_page_only_once(run_start):
    """Each opener is tried until one succeeds; all of them succeeding must
    not mean eight tabs -- and without --review the review page is not one
    of them."""
    result = run_start()
    assert len([call for call in result.calls if call.startswith("browser ")]) == 1
    assert "8081" not in result.output


def test_it_waits_for_the_page_before_opening_it(run_start):
    """Healthy is not answering. The container reports healthy as soon as
    nginx is up, which is a moment before it has read its configuration.
    """
    result = run_start()
    probe = result.index_of("curl http://localhost:8080")
    browser = next(i for i, call in enumerate(result.calls) if call.startswith("browser "))
    assert probe < browser
    assert "Waiting for the interface" in result.output


def test_the_closing_lines_say_where_it_is_and_how_to_stop_it(run_start):
    """Short on purpose: launch.sh has just said what the stack is and how
    to watch it, and a front door that repeats all of that is a wall of text
    rather than one command.
    """
    output = run_start().output
    assert "http://localhost:8080" in output
    assert "docker compose --profile api --profile gui down" in output
    # The paragraph launch.sh already printed is not printed twice.
    assert output.count("Ask a question and watch the pipeline") <= 1


# ---------------------------------------------------------------------------
# The first run
# ---------------------------------------------------------------------------


def test_the_first_run_fetches_the_images_including_the_interface(run_start):
    """launch.sh hands off to setup.sh on its own, but without --gui -- which
    would leave the interface to be built from source on first start. Doing
    the handoff here is what makes the published image the one that runs.
    """
    result = run_start(env_file=None)
    assert result.returncode == 0
    assert result.called("pull mcfaddja/nl2sql-agent")
    assert result.called("pull mcfaddja/nl2sql-gui")
    assert "First run on this machine" in result.output
    assert result.env_file()["GUI_IMAGE_NAME"] == "mcfaddja/nl2sql-gui"


def test_a_second_run_fetches_nothing(run_start):
    result = run_start()
    assert not result.called("pull mcfaddja/nl2sql-gui")
    assert "First run on this machine" not in result.output


def test_no_rag_reaches_both_scripts_on_a_first_run(run_start):
    """setup.sh decides which images to pull and launch.sh which checks to
    run; a flag that reached only one of them would pull a knowledge base
    that is then never started, or check one that was never pulled.
    """
    result = run_start("--no-rag", env_file=None)
    assert result.returncode == 0
    assert not result.called("pull mcfaddja/nl2sql-rag-vectordb")
    assert result.env_file()["RAG_ENABLED"] == "false"


# ---------------------------------------------------------------------------
# Machines without a desktop, and people who did not want a tab
# ---------------------------------------------------------------------------


def test_no_browser_starts_everything_and_only_prints_the_url(run_start):
    result = run_start("--no-browser")
    assert result.returncode == 0
    assert result.called("--profile api --profile gui up -d gui")
    assert not any(call.startswith("browser ") for call in result.calls)
    assert "Ready at http://localhost:8080" in result.output


def test_a_machine_where_nothing_can_open_a_page_is_not_a_failure(run_start):
    """A server with no desktop is an ordinary place to run this, and
    `xdg-open` exits non-zero on plenty of working machines besides. The
    stack is up either way, so the only thing left to do is say where it is.
    """
    result = run_start(env={"FAKE_BROWSER_EXIT": "3"})
    assert result.returncode == 0
    assert "could not open a browser" in result.output
    assert "http://localhost:8080" in result.output


def test_a_named_browser_that_is_not_installed_falls_through_to_the_next(run_start):
    """BROWSER is tried first and may well name something that is not here;
    that has to move on to the platform's own opener rather than give up.
    """
    result = run_start(env={"BROWSER": "definitely-not-installed"})
    opened = [call for call in result.calls if call.startswith("browser ")]
    assert len(opened) == 1
    assert "definitely-not-installed" not in opened[0]
    assert opened[0].endswith("http://localhost:8080")


def test_the_browser_variable_chooses_what_opens_the_page(run_start):
    """The standard way to say "not that one", and the only override this
    script has."""
    result = run_start(env={"BROWSER": "x-www-browser"})
    opened = [call for call in result.calls if call.startswith("browser ")]
    assert opened == ["browser x-www-browser http://localhost:8080"]


# ---------------------------------------------------------------------------
# When it does not come up
# ---------------------------------------------------------------------------


def test_a_page_that_never_answers_is_reported_rather_than_opened(run_start):
    """Opening a browser on a connection error is worse than saying so: the
    user is left looking at their browser's diagnosis of a problem that is
    not their browser's.
    """
    result = run_start(env={"FAKE_GUI_DOWN": "1"})
    assert result.returncode != 0
    assert "never answered at http://localhost:8080" in result.output
    assert "logs gui" in result.output
    assert not any(call.startswith("browser ") for call in result.calls)


def test_compose_v2_is_required(run_start):
    result = run_start(env={"FAKE_NO_COMPOSE": "1"})
    assert result.returncode != 0
    assert "Docker Compose v2 is required" in result.output


def test_docker_missing_entirely_is_named_as_such(run_start):
    result = run_start(env={"PATH": "/usr/bin:/bin"})
    assert result.returncode != 0
    assert "docker is not installed or not on PATH" in result.output


# ---------------------------------------------------------------------------
# The port
# ---------------------------------------------------------------------------


def test_the_port_follows_what_compose_will_publish(run_start):
    """The GUI's port is configurable, and a browser opened at the wrong one
    is the same as no browser at all.
    """
    result = run_start(
        env_file="IMAGE_NAME=x\nGUI_PORT=9090\n",
        env={"FAKE_GUI_PORT": "9090"},
    )
    assert result.returncode == 0
    assert result.called("curl http://localhost:9090")
    assert any(call.endswith("http://localhost:9090") for call in result.calls)


def test_the_shell_wins_over_the_env_file(run_start):
    result = run_start(env={"GUI_PORT": "9191", "FAKE_GUI_PORT": "9191"})
    assert any(call.endswith("http://localhost:9191") for call in result.calls)


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------


def test_restart_is_passed_through(run_start):
    result = run_start("--restart")
    assert result.called("compose up -d --force-recreate")


def test_quiet_says_nothing_it_does_not_have_to(run_start):
    result = run_start("--quiet")
    assert result.returncode == 0
    assert result.stdout.strip() == ""
    # ...and still opens the page, which is the point of running it.
    assert any(call.startswith("browser ") for call in result.calls)


def test_help_exits_zero_and_starts_nothing(run_start):
    result = run_start("--help")
    assert result.returncode == 0
    assert "Brings up the whole stack" in result.output
    assert result.calls == []


def test_an_unknown_flag_is_rejected_with_the_usage(run_start):
    result = run_start("--not-a-flag")
    assert result.returncode != 0
    assert "unknown option: --not-a-flag" in result.output
    assert "Usage: ./start.sh" in result.output


# ---------------------------------------------------------------------------
# The script itself
# ---------------------------------------------------------------------------


def test_it_never_expands_an_empty_array(run_start):
    """bash 3.2 -- which is what macOS ships -- aborts on "${arr[@]}" for an
    empty array under `set -u`. It has already cost this repository one bug,
    in smoke.sh, invisible inside its own Alpine container.
    """
    source = "\n".join(
        line for line in START_SH.read_text().splitlines()
        if not line.lstrip().startswith("#")
    )
    for name in set(re.findall(r'"\$\{(\w+)\[@\]\}"', source)):
        # `${name[@]+"${name[@]}"}` expands to nothing when the array is
        # empty instead of erroring, which is the other way to be safe.
        if f'${{{name}[@]+"${{{name}[@]}}"}}' in source:
            continue
        assignment = re.search(rf"^\s*(?:local )?{name}=\((.*?)\)", source, re.MULTILINE)
        assert assignment, f"{name} is expanded unguarded but never initialised"
        assert assignment.group(1).strip(), (
            f'{name} starts empty and is expanded unguarded; that aborts on '
            "bash 3.2. Seed it, or use ${%s[@]+\"${%s[@]}\"}." % (name, name)
        )


def test_it_runs_from_anywhere():
    assert 'cd "$(dirname "$0")"' in START_SH.read_text()


def test_it_is_executable():
    assert START_SH.stat().st_mode & 0o111


def test_the_scripts_it_drives_are_the_ones_that_exist():
    source = START_SH.read_text()
    for called in re.findall(r"^\./(\S+\.sh)", source, re.MULTILINE):
        assert (REPO_ROOT / called).is_file(), f"start.sh runs ./{called}, which is not here"


# ---------------------------------------------------------------------------
# The platform this machine is not
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("uname", "expected"),
    [
        ("Darwin", "open"),
        ("Linux", "xdg-open"),
        ("MINGW64_NT-10.0", "start"),
        ("CYGWIN_NT-10.0", "start"),
    ],
)
def test_each_platform_reaches_for_its_own_opener(run_start, uname, expected):
    """Three of these four branches can never run on the machine the suite
    is running on, which is exactly why they are the ones worth pinning: an
    opener that is wrong for Linux is invisible from a Mac until someone
    on Linux runs it.
    """
    result = run_start(env={"FAKE_UNAME_S": uname})
    opened = [call for call in result.calls if call.startswith("browser ")]
    assert opened == [f"browser {expected} http://localhost:8080"]


def test_an_unknown_platform_still_starts_everything(run_start):
    """No candidate matches, so the list is empty -- and expanding an empty
    array under `set -u` aborts on bash 3.2. The stack is up by then, so
    aborting there would leave it running and claim it had failed.
    """
    result = run_start(env={"FAKE_UNAME_S": "Haiku"})
    assert result.returncode == 0
    assert result.called("--profile api --profile gui up -d gui")
    assert "could not open a browser" in result.output


def test_gio_is_invoked_the_way_gio_wants(run_start):
    """It takes a subcommand: `gio open URL`, not `gio URL`. Everything else
    in the list takes the URL directly.
    """
    result = run_start(env={"FAKE_UNAME_S": "Linux", "BROWSER": "gio"})
    opened = [call for call in result.calls if call.startswith("browser ")]
    assert opened == ["browser gio open http://localhost:8080"]


def test_wsl_reaches_for_the_windows_side_opener(run_start):
    """WSL has `xdg-open` and it frequently opens nothing, so the
    Windows-side openers are tried first there. It is also the one branch
    that cannot be reached by faking `uname` alone -- WSL is told apart by
    what is in /proc/version, and a Mac has no /proc at all.
    """
    result = run_start(env={"FAKE_UNAME_S": "Linux", "FAKE_WSL": "1"})
    opened = [call for call in result.calls if call.startswith("browser ")]
    assert opened == ["browser wslview http://localhost:8080"]


def test_plain_linux_is_not_mistaken_for_wsl(run_start):
    result = run_start(env={"FAKE_UNAME_S": "Linux"})
    opened = [call for call in result.calls if call.startswith("browser ")]
    assert opened == ["browser xdg-open http://localhost:8080"]


# ---------------------------------------------------------------------------
# The feedback system (--review, --feedback)
# ---------------------------------------------------------------------------


def test_review_brings_up_the_whole_feedback_stack(run_start):
    """One flag, because the chain is not optional at any link: the interface
    needs the service, the service needs the staging database, and none of it
    is any use without the web interface people vote in."""
    result = run_start("--review")

    assert result.returncode == 0
    assert result.called("--profile feedback up -d feedbackdb")
    assert result.called("--profile feedback --profile review up -d review")
    assert result.called("--profile reviewgui up -d reviewgui")
    assert result.called("--profile api --profile gui up -d gui")


def test_review_opens_both_pages_the_review_one_second(run_start):
    """So the interface people actually ask questions in is left in front."""
    result = run_start("--review")

    assert pages(result) == ["http://localhost:8080", "http://localhost:8081"], (
        "expected two pages:\n" + "\n".join(result.calls))
    order = [line for line in result.output.splitlines() if "Opening http" in line]
    assert order == ["==> Opening http://localhost:8080", "==> Opening http://localhost:8081"]


def test_review_waits_for_the_second_page_too(run_start):
    """The same reason as the first: nginx answers its health check a moment
    before it has read its configuration."""
    result = run_start("--review")
    assert "Waiting for the review interface" in result.output
    assert result.called("curl http://localhost:8081")


def test_a_review_page_that_never_answers_does_not_take_the_stack_down(run_start):
    """The web interface is already up and answering. Exiting here would take
    a working stack away over a page the user may not have looked at yet."""
    result = run_start("--review", env={"FAKE_GUI_DOWN": "1", "FAKE_GUI_PORT": "8081"})

    assert result.returncode == 0
    assert "the review interface never answered" in result.output
    assert "verdicts are staged and can be reviewed later" in result.output
    # The web interface still opened.
    opened = [call for call in result.calls if call.startswith("browser ")]
    assert len(opened) == 1
    assert opened[0].endswith("http://localhost:8080")


def test_feedback_stages_verdicts_without_the_review_interface(run_start):
    """For the machine that collects feedback and a different one that reviews
    it -- the staging database is the only part that has to be where people
    are voting."""
    result = run_start("--feedback")

    assert result.called("--profile feedback up -d feedbackdb")
    assert not result.called("up -d reviewgui")
    assert len([c for c in result.calls if c.startswith("browser ")]) == 1


def test_no_browser_prints_both_urls_rather_than_opening_them(run_start):
    result = run_start("--review", "--no-browser")

    assert not [call for call in result.calls if call.startswith("browser ")]
    assert "http://localhost:8080" in result.output
    assert "http://localhost:8081" in result.output


def test_the_review_ports_follow_what_compose_will_publish(run_start):
    result = run_start("--review", env_file="IMAGE_NAME=x\nGUI_PORT=9080\nREVIEW_GUI_PORT=9081\n")
    assert pages(result) == ["http://localhost:9080", "http://localhost:9081"]


def test_the_closing_lines_say_what_each_page_is_for(run_start):
    """Two URLs with no explanation is worse than one, because the second is
    the one nobody has seen before."""
    output = run_start("--review").output
    assert "say whether the answer was right" in output
    assert "promote, correct, complete, take back" in output


def test_the_closing_lines_say_that_promoting_edits_this_checkout(run_start):
    output = run_start("--review").output
    assert "context_questions/translated_questions.md" in output
    assert "git diff" in output


def test_the_closing_lines_say_how_to_stop_the_whole_thing(run_start):
    """The whole command, not the word "down".

    Five profiles have to be named or `compose down` leaves containers
    running, and a test that only looked for "down" passed against any
    sentence containing it.
    """
    # The backslash is a real line continuation -- the command is printed
    # across two lines because it is long -- so it is folded away here along
    # with the wrapping, leaving the command someone would paste.
    output = " ".join(run_start("--review").output.replace("\\", " ").split())
    assert (
        "docker compose --profile api --profile gui --profile feedback "
        "--profile review --profile reviewgui down" in output
    )


def test_a_first_run_pulls_the_review_images_too(run_start):
    """Otherwise compose builds them from source on first start, which is a
    pip install and an npm ci inside two containers."""
    result = run_start("--review", env_file=None)
    assert result.called("pull mcfaddja/nl2sql-review:")
    assert result.called("pull mcfaddja/nl2sql-review-gui:")


def test_a_machine_that_cannot_open_the_review_page_still_says_where_it_is(run_start):
    """The second page has its own failure to report. Falling back to the
    first one's message would leave the URL nobody has seen before unsaid --
    and that is the one someone needs written down.
    """
    # Safari says no to the window as well, so this is the last resort failing.
    result = run_start("--review", env={
        "FAKE_BROWSER_EXIT": "3", "FAKE_UNAME_S": "Darwin", "FAKE_OSASCRIPT_EXIT": "1"})

    assert result.returncode == 0
    assert "could not open the review interface" in result.output
    assert "http://localhost:8081" in result.output
    # And the first page's failure is still reported separately.
    assert "could not open a browser" in result.output


# ---------------------------------------------------------------------------
# The SQL console (--console)
# ---------------------------------------------------------------------------


def test_console_brings_up_the_console_beside_the_web_interface(run_start):
    """A troubleshooting tool beside the interface, not instead of it: the
    answer being troubleshot was asked for in the web interface."""
    result = run_start("--console")

    assert result.returncode == 0
    assert result.called("--profile console up -d console")
    assert result.called("--profile console --profile consolegui up -d consolegui")
    assert result.called("--profile api --profile gui up -d gui")


def test_console_opens_its_page_second_in_a_window_of_its_own(run_start):
    result = run_start("--console")

    assert pages(result) == ["http://localhost:8080", "http://localhost:8082"]
    assert [call for call in result.calls if call.startswith("window ")], (
        "the console was opened in a tab rather than asked for a window"
    )
    assert "==> Opening http://localhost:8082" in result.output


def test_review_and_console_open_three_pages_in_order(run_start):
    result = run_start("--review", "--console")
    assert pages(result) == ["http://localhost:8080", "http://localhost:8081", "http://localhost:8082"]


def test_console_waits_for_its_page_too(run_start):
    result = run_start("--console")
    assert "Waiting for the SQL console" in result.output
    assert result.called("curl http://localhost:8082")


def test_a_console_page_that_never_answers_does_not_take_the_stack_down(run_start):
    result = run_start("--console", env={"FAKE_GUI_DOWN": "1", "FAKE_GUI_PORT": "8082"})

    assert result.returncode == 0
    assert "the SQL console never answered at http://localhost:8082." in result.output
    assert "docker compose --profile console --profile consolegui logs" in result.output
    assert "Everything else is up; ./launch.sh --console tries it again on its own." in result.output
    assert pages(result) == ["http://localhost:8080"]


def test_a_machine_that_cannot_open_the_console_still_says_where_it_is(run_start):
    result = run_start("--console", env={
        "FAKE_BROWSER_EXIT": "3", "FAKE_UNAME_S": "Darwin", "FAKE_OSASCRIPT_EXIT": "1"})

    assert result.returncode == 0
    assert "could not open the SQL console. Open it yourself:" in result.output
    assert "http://localhost:8082" in result.output


def test_no_browser_prints_the_console_url_rather_than_opening_it(run_start):
    result = run_start("--console", "--no-browser")
    assert pages(result) == []
    assert "SQL console at http://localhost:8082" in result.output


def test_the_console_port_follows_what_compose_will_publish(run_start):
    result = run_start("--console", env_file="IMAGE_NAME=x\nCONSOLE_GUI_PORT=9082\n")
    assert pages(result)[-1] == "http://localhost:9082"


def test_the_closing_lines_say_what_the_console_is_for_and_how_to_stop_it(run_start):
    output = " ".join(run_start("--console").output.split())
    assert "http://localhost:8082 query the retail database as the agent sees it" in output
    assert "docker compose --profile console --profile consolegui down and the console" in output


def test_desktop_and_console_still_opens_the_console_page(run_start):
    """The console is a page whichever interface asked the question."""
    result = run_start("--desktop", "--console", env={"FAKE_UNAME_S": "Darwin", "FAKE_UNAME_M": "arm64"})

    assert result.calls_matching("java -jar")
    assert pages(result) == ["http://localhost:8082"]
    assert "query the retail database as the agent sees it" in result.output


def test_desktop_console_and_no_browser_prints_the_console_url(run_start):
    result = run_start(
        "--desktop", "--console", "--no-browser", env={"FAKE_UNAME_S": "Darwin", "FAKE_UNAME_M": "arm64"}
    )
    assert "SQL console at http://localhost:8082" in result.output


def test_a_console_that_was_never_pinned_is_pulled_rather_than_built(run_start):
    """Its interface would otherwise be an npm build inside a container on
    first start, when the published image is a pull away."""
    result = run_start("--console")

    assert "the SQL console's interface is not pinned" in result.output
    assert result.called(f"pull mcfaddja/nl2sql-console-gui:{SHIPPED}")
    assert result.env_file()["CONSOLE_GUI_IMAGE_NAME"] == "mcfaddja/nl2sql-console-gui"


def test_a_pinned_console_is_not_fetched_again(run_start):
    env_file = (
        f"AGENT_IMAGE_NAME=mcfaddja/nl2sql-agent\nAGENT_IMAGE_TAG={SHIPPED}\n"
        f"GUI_IMAGE_NAME=mcfaddja/nl2sql-gui\nGUI_IMAGE_TAG={SHIPPED}\n"
        f"CONSOLE_GUI_IMAGE_NAME=mcfaddja/nl2sql-console-gui\nCONSOLE_GUI_IMAGE_TAG={SHIPPED}\n"
    )
    result = run_start("--console", env_file=env_file)
    assert "Fetching the images" not in result.output


# ---------------------------------------------------------------------------
# The desktop client
# ---------------------------------------------------------------------------


def test_desktop_runs_the_jar_against_the_api(run_start):
    result = run_start("--desktop", env={"FAKE_UNAME_S": "Darwin", "FAKE_UNAME_M": "arm64"})

    assert result.returncode == 0
    assert result.calls_matching("java -jar desktop/target/nl2sql-desktop.jar")
    assert result.calls_matching("--cacert ./nl2sql-api.crt")
    assert result.calls_matching("--url https://localhost:8443")


def test_desktop_is_an_interface_rather_than_an_addition_to_one(run_start):
    """The questions are asked in a window this script has already opened.
    Starting the web interface as well would be a second one nobody asked
    for, and a browser tab in front of it."""
    result = run_start("--desktop", env={"FAKE_UNAME_S": "Darwin", "FAKE_UNAME_M": "arm64"})

    assert not result.calls_matching("compose --profile api --profile gui up -d gui")
    assert "==> Opening http://localhost:8080" not in result.output


def test_desktop_and_review_still_opens_the_review_page(run_start):
    """There is no desktop equivalent of the review interface and there is
    not going to be one: curation happens in the web interface."""
    result = run_start("--desktop", "--review",
                       env={"FAKE_UNAME_S": "Darwin", "FAKE_UNAME_M": "arm64"})

    assert result.calls_matching("java -jar")
    assert pages(result) == ["http://localhost:8081"]


def test_a_java_too_old_to_run_it_says_which_it_found(run_start):
    """JavaFX 21 needs a runtime of 21; an 8 that says "1.8.0_412" reads as
    1 and is refused with the number it reported. No java at all reports 0
    and gets the same sentence, because it is the same answer."""
    result = run_start("--desktop", env={"FAKE_JAVA_VERSION": "1.8.0_412"})

    assert result.returncode == 0
    assert "reports Java 1 " in result.output
    assert "21 or later" in result.output
    assert "-jar desktop/target/nl2sql-desktop.jar" in result.output


def test_an_early_access_runtime_is_new_enough(run_start):
    """`openjdk version "28-ea"` is an ordinary thing to have installed."""
    result = run_start("--desktop", env={"FAKE_JAVA_VERSION": "28-ea"})

    assert result.calls_matching("java -jar")


def test_a_runtime_that_reports_nothing_recognisable_is_refused(run_start):
    """The same branch a machine with no java at all takes."""
    result = run_start("--desktop", env={"FAKE_JAVA_VERSION": "who knows"})

    assert "reports Java 0" in result.output
    assert not result.calls_matching("java -jar")


def test_a_jar_that_was_never_built_is_not_run(run_start):
    result = run_start("--desktop", env={"FAKE_DESKTOP_BUILD_FAILS": "1"})

    assert "was not built" in result.output
    assert not result.calls_matching("java -jar")


def test_the_closing_notes_explain_where_the_verdicts_go(run_start):
    result = run_start("--desktop")

    assert "the same staging table the web interface" in result.output
    assert "desktop/target/desktop.log" in result.output


def test_without_the_certificate_the_client_is_told_not_to_verify(run_start):
    """Verifying beats not verifying and the certificate is normally right
    there. When it is not, the client is still run -- with the check off, and
    saying so, which is better than not starting at all."""
    result = run_start("--desktop", env={"FAKE_CERT_COPY_FAILS": "1"})

    assert "will not verify the API's" in result.output
    assert result.calls_matching("--insecure")
    assert not result.calls_matching("--cacert")


def test_no_browser_with_the_desktop_client_says_it_is_already_open(run_start):
    """There is no URL to print for it: it is a window, and it is up."""
    result = run_start("--desktop", "--no-browser")

    assert "The desktop client is running" in result.output
    assert result.calls_matching("java -jar")


def test_the_client_outlives_the_script_that_started_it(run_start):
    """This script exits; the window should not.

    Two mechanisms, and neither is enough alone. The subshell is what
    survives the script exiting -- backgrounded directly, the client is a job
    of that shell and is reaped with its process group moments later, which
    is a window that closes itself. `nohup` is what survives the terminal
    being closed afterwards, which cannot be observed from here without
    hanging up on ourselves, so it is asserted against the command.

    Both are checked against the line rather than the word, because both
    words also appear in the comment that explains them.
    """
    result = run_start("--desktop")

    assert result.calls_matching("java -jar")
    launcher = re.search(r'^\s*\( nohup "\$java_bin" -jar',
                         (result.workdir / "start.sh").read_text(), re.MULTILINE)
    assert launcher, "the client is not detached into a subshell, or not run under nohup"

    pid_file = result.workdir / "desktop/target/desktop.pid"
    assert pid_file.is_file()
    # The stand-in stays alive for five seconds, so this is asking whether
    # the script left it running rather than waited for it.
    os.kill(int(pid_file.read_text()), 0)


def test_a_second_run_does_not_open_a_second_window(run_start):
    """`launch.sh` starts whatever is down and leaves what is up alone. The
    front door should behave the same way: two windows onto one API is a
    thing nobody asked for."""
    first = run_start("--desktop")
    assert "Opening the desktop client" in first.output

    second = run_start("--desktop")

    assert "already open" in second.output
    assert len(second.calls_matching("java -jar")) == 1


def test_a_pid_file_left_behind_by_a_dead_client_starts_a_new_one(run_start):
    """The file outlives the process it names, so the file alone proves
    nothing -- which is why the check asks the operating system."""
    result = run_start("--desktop")
    (result.workdir / "desktop/target/desktop.pid").write_text("999999")

    again = run_start("--desktop")

    assert "Opening the desktop client" in again.output
    assert "already open" not in again.output


def test_a_client_that_will_not_open_says_what_it_said(run_start):
    """The browser half waits until the page answers before reporting
    success. This is the same promise: a window that fails to open leaves a
    stack trace, and printing the first lines of it beats printing nothing."""
    result = run_start("--desktop", env={"FAKE_JAVA_DIES": "1"})

    assert result.returncode == 0
    assert "started and stopped again" in result.output
    assert "Exception in Application start method" in result.output
    assert "opens the web interface instead" in result.output



# ---------------------------------------------------------------------------
# The review page, in a window of its own
#
# No generic opener can ask for one: `open` and `xdg-open` hand the browser a
# URL, and its settings decide between a tab and a window. So the default
# browser is asked directly, in the words its family understands, and one
# this script does not know gets the generic opener and its choice.
# ---------------------------------------------------------------------------


def test_on_a_mac_that_never_chose_a_browser_safari_is_asked_for_a_window(run_start):
    """Nothing recorded for https means the browser macOS came with, and
    Safari takes no flags: AppleScript is the way it is asked."""
    result = run_start("--review", env={"FAKE_UNAME_S": "Darwin"})

    windows = result.calls_matching("window osascript")
    assert len(windows) == 1
    assert 'tell application "Safari" to make new document' in windows[0]
    assert '{URL:"http://localhost:8081"}' in windows[0]
    # The page people ask questions in is still the ordinary kind.
    assert result.calls_matching("browser open http://localhost:8080")
    assert not result.calls_matching("browser open http://localhost:8081")


def test_a_terminal_safari_will_not_take_orders_from_still_opens_the_page(run_start):
    """macOS asks once whether a terminal may control Safari. Saying no is a
    tab rather than a window, not a page that never opens."""
    result = run_start("--review", env={"FAKE_UNAME_S": "Darwin", "FAKE_OSASCRIPT_EXIT": "1"})

    assert result.calls_matching("window osascript")
    assert result.calls_matching("browser open http://localhost:8081")


@pytest.mark.parametrize("bundle, flag", [
    ("com.google.Chrome", "--new-window"),
    ("org.chromium.Chromium", "--new-window"),
    ("com.microsoft.edgemac", "--new-window"),
    ("com.brave.Browser", "--new-window"),
    ("org.mozilla.firefox", "-new-window"),
])
def test_a_mac_whose_browser_takes_flags_is_asked_by_bundle(run_start, bundle, flag):
    """Chrome and everything built on Chromium say --new-window; Firefox says
    -new-window. `open -n -b` hands the flag to that browser and no other,
    and a browser already running passes it to the window it has open."""
    result = run_start("--review", env={"FAKE_UNAME_S": "Darwin", "FAKE_MAC_BROWSER": bundle})

    assert result.calls_matching(
        f"window open -n -b {bundle.lower()} --args {flag} http://localhost:8081")
    assert not result.calls_matching("window osascript")


def test_a_browser_that_will_not_open_a_window_falls_back_to_a_page(run_start):
    result = run_start("--review", env={
        "FAKE_UNAME_S": "Darwin", "FAKE_MAC_BROWSER": "com.google.Chrome", "FAKE_WINDOW_EXIT": "1"})

    assert result.calls_matching("window open -n -b com.google.chrome")
    assert result.calls_matching("browser open http://localhost:8081")


def test_a_mac_browser_this_script_does_not_know_gets_the_generic_opener(run_start):
    """Its flags are unknown, so it gets a URL and decides for itself."""
    result = run_start("--review", env={
        "FAKE_UNAME_S": "Darwin", "FAKE_MAC_BROWSER": "com.kagi.kagimacOS"})

    assert result.calls_matching("browser open http://localhost:8081")
    assert not result.calls_matching("window ")


@pytest.mark.parametrize("desktop, command", [
    ("firefox.desktop", "firefox"),
    ("firefox-esr.desktop", "firefox"),
    ("google-chrome.desktop", "google-chrome"),
])
def test_a_linux_desktop_asks_the_default_browser_itself(run_start, desktop, command):
    """xdg-settings names the browser as a .desktop file; its own command
    takes --new-window, which xdg-open has no way to say."""
    result = run_start("--review", env={"FAKE_UNAME_S": "Linux", "FAKE_LINUX_BROWSER": desktop})

    assert eventually(result, f"window {command} --new-window http://localhost:8081")
    assert result.calls_matching("browser xdg-open http://localhost:8080")
    assert not result.calls_matching("browser xdg-open http://localhost:8081")


@pytest.mark.parametrize("desktop", [
    "chromium.desktop", "microsoft-edge.desktop", "brave-browser.desktop", "vivaldi-stable.desktop",
])
def test_a_linux_browser_whose_command_is_not_here_gets_the_generic_opener(run_start, desktop):
    """The desktop file can name a browser whose command is not on PATH -- a
    Flatpak, say -- and then all that is left is to hand xdg-open the URL.
    PATH is cut back so no real browser on the machine running this can be
    found and opened."""
    result = run_start("--review", without=("chromium", "chromium-browser"),
                       env={"FAKE_UNAME_S": "Linux", "FAKE_LINUX_BROWSER": desktop})

    assert result.calls_matching("browser xdg-open http://localhost:8081")
    assert not result.calls_matching("window ")


def test_wsl_does_not_ask_a_linux_browser_for_a_window(run_start):
    """WSL's browsers are Windows programs. Whatever xdg-settings says there
    describes a Linux desktop that is not in front of anyone."""
    result = run_start("--review", env={
        "FAKE_UNAME_S": "Linux", "FAKE_WSL": "1", "FAKE_LINUX_BROWSER": "firefox.desktop"})

    assert result.calls_matching("browser wslview http://localhost:8081")
    assert not eventually(result, "window firefox", seconds=0.5)


def test_a_browser_named_in_browser_opens_both_pages_itself(run_start):
    """BROWSER names a command, and this script cannot know its flags."""
    result = run_start("--review", env={"FAKE_UNAME_S": "Darwin", "BROWSER": "x-www-browser"})

    assert result.calls_matching("browser x-www-browser http://localhost:8081")
    assert not result.calls_matching("window ")


# ---------------------------------------------------------------------------
# Docker, started rather than asked for
# ---------------------------------------------------------------------------


def test_a_stopped_docker_desktop_is_started_on_a_mac(run_start):
    result = run_start(env={"FAKE_UNAME_S": "Darwin", "FAKE_DAEMON_STOPPED": "1"})

    assert result.returncode == 0
    assert result.calls_matching("app Docker")
    assert "Starting Docker" in result.output
    assert "Docker is running" in result.output
    assert result.index_of("app Docker") < result.index_of("compose up -d postgres")


def test_a_stopped_docker_desktop_is_started_on_linux(run_start):
    """Docker Desktop for Linux runs as a user service, which a user may
    start without root."""
    result = run_start(env={"FAKE_UNAME_S": "Linux", "FAKE_DAEMON_STOPPED": "1"})

    assert result.returncode == 0
    assert result.calls_matching("systemctl --user start docker-desktop")


def test_a_linux_daemon_only_root_can_start_is_left_to_root(run_start):
    """Docker Engine is a system service. This script does not ask for root,
    so when the user service is not there it says what to do instead."""
    result = run_start(env={
        "FAKE_UNAME_S": "Linux", "FAKE_DAEMON_STOPPED": "1", "FAKE_SYSTEMCTL_FAILS": "1"})

    assert result.returncode != 0
    assert "could not be started from here" in result.output
    assert not result.called("compose up")


def test_a_mac_without_docker_desktop_is_told_to_start_docker(run_start):
    result = run_start(env={
        "FAKE_UNAME_S": "Darwin", "FAKE_DAEMON_STOPPED": "1", "FAKE_APPS_MISSING": "Docker"})

    assert result.returncode != 0
    assert "Start Docker and retry" in result.output


def test_a_machine_this_script_cannot_start_docker_on_says_so(run_start):
    result = run_start(env={"FAKE_UNAME_S": "FreeBSD", "FAKE_DAEMON_STOPPED": "1"})

    assert result.returncode != 0
    assert "could not be started from here" in result.output
    assert not result.calls_matching("app Docker")


def test_a_docker_that_never_answers_is_not_waited_on_forever(run_start):
    """Started, and still not answering after two minutes -- which is named
    as such before anything is started."""
    result = run_start(env={"FAKE_UNAME_S": "Darwin", "FAKE_NO_DAEMON": "1"})

    assert result.returncode != 0
    assert result.calls_matching("app Docker")
    assert "waiting for its daemon" in result.output
    assert len(result.calls_matching("info")) > 30
    assert "the Docker daemon is not running" in result.output
    assert not result.called("compose up")


# ---------------------------------------------------------------------------
# The Ollama on this machine
#
# Every question is embedded with the model the knowledge base was, on the
# Ollama EMBED_BASE_URL names -- by default this machine's.
# ---------------------------------------------------------------------------


def test_a_stopped_ollama_here_is_started_on_a_mac(run_start):
    result = run_start(env={"FAKE_UNAME_S": "Darwin", "FAKE_LOCAL_OLLAMA_DOWN": "2"})

    assert result.returncode == 0
    assert result.calls_matching("app Ollama")
    assert "Starting Ollama on this machine" in result.output
    assert "Ollama is running at http://localhost:11434" in result.output


def test_ollama_is_started_before_the_images_are_fetched(run_start):
    """setup.sh checks the embedding model as well, and would warn about an
    Ollama that is a moment from being started."""
    result = run_start(env_file=None, env={"FAKE_UNAME_S": "Darwin", "FAKE_LOCAL_OLLAMA_DOWN": "1"})

    assert result.index_of("app Ollama") < result.index_of("pull mcfaddja/nl2sql-agent")


def test_without_the_desktop_app_the_server_is_started_on_its_own(run_start):
    result = run_start(env={
        "FAKE_UNAME_S": "Darwin", "FAKE_LOCAL_OLLAMA_DOWN": "2", "FAKE_APPS_MISSING": "Ollama"})

    assert result.returncode == 0
    assert eventually(result, "ollama serve")
    assert "Ollama is running" in result.output


def test_on_linux_the_server_is_started_on_its_own(run_start):
    result = run_start(env={"FAKE_UNAME_S": "Linux", "FAKE_LOCAL_OLLAMA_DOWN": "2"})

    assert eventually(result, "ollama serve")
    assert not result.calls_matching("app Ollama")


def test_a_machine_without_ollama_is_told_where_to_get_it(run_start):
    """Not a failure: the agent still answers, without the knowledge base."""
    result = run_start(without=("ollama",), env={"FAKE_UNAME_S": "Linux", "FAKE_LOCAL_OLLAMA_DOWN": "99"})

    assert result.returncode == 0
    assert "Ollama is not installed here" in result.output
    assert "https://ollama.com" in result.output


def test_an_ollama_that_never_answers_is_reported_and_left(run_start):
    result = run_start(env={"FAKE_UNAME_S": "Darwin", "FAKE_LOCAL_OLLAMA_DOWN": "99"})

    assert result.returncode == 0
    assert "had not answered at http://localhost:11434 after 30 seconds" in result.output


def test_an_ollama_here_without_the_embedding_model_is_given_it(run_start):
    result = run_start(env={"FAKE_OLLAMA_MODELS": '{"name":"qwen3.8-256k"}'})

    assert result.called("curl http://localhost:11434/api/pull")
    assert "Pulling bge-m3 into the Ollama on this machine" in result.output
    assert "bge-m3 is ready" in result.output


def test_a_pull_that_fails_says_how_to_do_it_by_hand(run_start):
    result = run_start(env={"FAKE_OLLAMA_MODELS": '{"name":"qwen3.8-256k"}', "FAKE_PULL_FAILS": "1"})

    assert result.returncode == 0
    assert "could not pull bge-m3 into the Ollama here" in result.output
    assert "ollama pull bge-m3" in result.output


def test_an_ollama_that_has_the_model_is_left_alone(run_start):
    result = run_start()

    assert not result.called("/api/pull")
    assert "Starting Ollama" not in result.output


def test_no_rag_needs_no_ollama_here(run_start):
    result = run_start("--no-rag", env={"FAKE_UNAME_S": "Darwin", "FAKE_LOCAL_OLLAMA_DOWN": "99"})

    assert not result.calls_matching("app Ollama")
    assert "Starting Ollama" not in result.output


def test_an_embedding_host_on_another_machine_is_not_this_scripts_to_start(run_start):
    env_file = (
        "AGENT_IMAGE_NAME=mcfaddja/nl2sql-agent\n"
        f"AGENT_IMAGE_TAG={SHIPPED}\n"
        "GUI_IMAGE_NAME=mcfaddja/nl2sql-gui\n"
        "EMBED_BASE_URL=http://gpu-box:11434\n"
    )
    result = run_start(env_file=env_file, env={"FAKE_UNAME_S": "Darwin", "FAKE_LOCAL_OLLAMA_DOWN": "99"})

    assert not result.calls_matching("app Ollama")
    assert not result.called("/api/pull")


# ---------------------------------------------------------------------------
# A .env this checkout has moved on from
# ---------------------------------------------------------------------------


def _older_env(**extra: str) -> str:
    lines = {
        "AGENT_IMAGE_NAME": "mcfaddja/nl2sql-agent",
        "AGENT_IMAGE_TAG": "v5_1_2",
        "GUI_IMAGE_NAME": "mcfaddja/nl2sql-gui",
        "GUI_IMAGE_TAG": "v5_1_2",
        "RAG_ENABLED": "true",
        **extra,
    }
    return "".join(f"{key}={value}\n" for key, value in lines.items())


def test_an_older_pin_is_brought_up_to_what_this_checkout_ships(run_start):
    """Otherwise the new interface runs in front of the old agent, which
    launch.sh would say -- and saying so is not fixing it."""
    result = run_start(env_file=_older_env())

    assert result.returncode == 0
    assert "Fetching the images this checkout runs" in result.output
    assert f"this checkout ships {SHIPPED}, and .env pins v5_1_2" in result.output
    assert result.called(f"pull mcfaddja/nl2sql-agent:{SHIPPED}")
    assert result.env_file()["AGENT_IMAGE_TAG"] == SHIPPED
    assert result.env_file()["GUI_IMAGE_TAG"] == SHIPPED
    assert "You are running the older agent" not in result.output


def test_re_pinning_keeps_what_was_set_by_hand(run_start):
    """The Ollama host setup.sh has always carried over. The API token and a
    port are what it used to drop, into .env.bak, where nobody looks."""
    result = run_start(env_file=_older_env(
        OLLAMA_BASE_URL="http://elsewhere:11434", API_TOKEN="s3cret", GUI_PORT="9080"),
        env={"FAKE_GUI_PORT": "9080"})

    written = result.env_file()
    assert written["OLLAMA_BASE_URL"] == "http://elsewhere:11434"
    assert written["API_TOKEN"] == "s3cret"
    assert written["GUI_PORT"] == "9080"
    assert pages(result) == ["http://localhost:9080"]


def test_a_tag_exported_for_this_run_is_not_re_pinned_under_it(run_start):
    result = run_start(env_file=_older_env(), env={"AGENT_IMAGE_TAG": "v5_1_2"})

    assert "Fetching the images" not in result.output
    assert not result.called("pull mcfaddja/nl2sql-agent")


def test_an_agent_from_another_repository_is_somebody_elses_choice(run_start):
    result = run_start(env_file=_older_env(AGENT_IMAGE_NAME="someone/their-agent"))

    assert "Fetching the images" not in result.output


def test_a_env_that_pins_no_agent_builds_it_here_and_is_left_alone(run_start):
    """Written by hand, or by a developer: there is nothing published to
    bring it up to date with."""
    result = run_start(env_file="RAG_ENABLED=true\nGUI_PORT=8080\n")

    assert "Fetching the images" not in result.output
    assert not result.called("pull mcfaddja/nl2sql-agent")


def test_an_interface_that_was_never_pinned_is_pulled_rather_than_built(run_start):
    """The first-run reasoning again: a published image is a pull away."""
    env_file = f"AGENT_IMAGE_NAME=mcfaddja/nl2sql-agent\nAGENT_IMAGE_TAG={SHIPPED}\nRAG_ENABLED=true\n"
    result = run_start(env_file=env_file)

    assert "the web interface is not pinned" in result.output
    assert result.called("pull mcfaddja/nl2sql-gui")
    assert result.env_file()["GUI_IMAGE_NAME"] == "mcfaddja/nl2sql-gui"


def test_a_desktop_client_that_was_never_pinned_is_pulled_rather_than_built(run_start):
    result = run_start("--desktop", env={"FAKE_UNAME_S": "Darwin", "FAKE_UNAME_M": "arm64"})

    assert "the desktop client is not pinned" in result.output
    assert result.called(f"pull mcfaddja/nl2sql-desktop-build:{SHIPPED}-mac-aarch64")
    assert result.env_file()["DESKTOP_IMAGE_NAME"] == "mcfaddja/nl2sql-desktop-build"


def test_review_images_that_were_never_pinned_are_pulled_rather_than_built(run_start):
    result = run_start("--review")

    assert "the review images are not pinned" in result.output
    assert result.called(f"pull mcfaddja/nl2sql-review:{SHIPPED}")
    assert result.env_file()["REVIEW_IMAGE_NAME"] == "mcfaddja/nl2sql-review"


def test_a_env_that_is_up_to_date_is_not_rewritten(run_start):
    env_file = (
        f"AGENT_IMAGE_NAME=mcfaddja/nl2sql-agent\nAGENT_IMAGE_TAG={SHIPPED}\n"
        f"GUI_IMAGE_NAME=mcfaddja/nl2sql-gui\nGUI_IMAGE_TAG={SHIPPED}\n"
        f"REVIEW_IMAGE_NAME=mcfaddja/nl2sql-review\nREVIEW_IMAGE_TAG={SHIPPED}\n"
    )
    result = run_start("--review", env_file=env_file)

    assert "Fetching the images" not in result.output
    assert not (result.workdir / ".env.bak").exists()


# ---------------------------------------------------------------------------
# MLflow (--mlflow)
# ---------------------------------------------------------------------------


def test_mlflow_brings_up_mlflow_beside_the_web_interface(run_start):
    result = run_start("--mlflow")

    assert result.returncode == 0
    assert result.called("--profile mlflow up -d mlflow")
    assert result.called("--profile api --profile gui up -d gui")
    assert "MLflow" not in "".join(line for line in result.output.splitlines(True) if "WARNING" in line)


def test_mlflow_opens_its_page_last_in_a_window_of_its_own(run_start):
    result = run_start("--mlflow")

    assert pages(result) == ["http://localhost:8080", "http://localhost:5001"]
    assert any(call.startswith("window ") and "5001" in call for call in result.calls), (
        "MLflow was opened in a tab rather than asked for a window"
    )
    assert "==> Opening http://localhost:5001" in result.output


def test_every_page_opens_in_order(run_start):
    """The combination --help offers for everything, doing what it says."""
    assert "./start.sh --review --console --mlflow brings up every" in run_start("--help").output
    result = run_start("--review", "--console", "--mlflow")
    assert pages(result) == [
        "http://localhost:8080", "http://localhost:8081", "http://localhost:8082", "http://localhost:5001",
    ]


def test_mlflow_waits_for_its_page_too(run_start):
    result = run_start("--mlflow")
    assert "Waiting for MLflow" in result.output
    assert result.called("curl http://localhost:5001")


def test_an_mlflow_page_that_never_answers_does_not_take_the_stack_down(run_start):
    result = run_start("--mlflow", env={"FAKE_GUI_DOWN": "1", "FAKE_GUI_PORT": "5001"})

    assert result.returncode == 0
    assert "MLflow never answered at http://localhost:5001." in result.output
    assert "Check what it said: docker compose --profile mlflow logs mlflow mlflowdb" in result.output
    assert "Everything else is up, and answers questions untraced until it is." in result.output
    assert pages(result) == ["http://localhost:8080"]


def test_a_machine_that_cannot_open_mlflow_still_says_where_it_is(run_start):
    result = run_start("--mlflow", env={
        "FAKE_BROWSER_EXIT": "3", "FAKE_UNAME_S": "Darwin", "FAKE_OSASCRIPT_EXIT": "1"})

    assert result.returncode == 0
    assert "could not open MLflow. Open it yourself:" in result.output
    assert "http://localhost:5001" in result.output


def test_no_browser_prints_the_mlflow_url_rather_than_opening_it(run_start):
    result = run_start("--mlflow", "--no-browser")
    assert pages(result) == []
    assert "MLflow at http://localhost:5001" in result.output


def test_the_mlflow_port_follows_what_compose_will_publish(run_start):
    result = run_start("--mlflow", env_file="IMAGE_NAME=x\nMLFLOW_PORT=6001\n")
    assert pages(result)[-1] == "http://localhost:6001"


def test_the_closing_lines_say_what_mlflow_is_for_and_how_to_stop_it(run_start):
    output = " ".join(run_start("--mlflow").output.split())
    assert "http://localhost:5001 every question traced, agent by agent" in output
    assert "docker compose --profile mlflow down and MLflow" in output


def test_desktop_and_mlflow_still_opens_the_mlflow_page(run_start):
    """A question asked in the desktop client is traced like any other."""
    result = run_start("--desktop", "--mlflow", env={"FAKE_UNAME_S": "Darwin", "FAKE_UNAME_M": "arm64"})

    assert result.calls_matching("java -jar")
    assert pages(result) == ["http://localhost:5001"]
    assert "every question traced, agent by agent" in result.output


def test_desktop_mlflow_and_no_browser_prints_the_mlflow_url(run_start):
    result = run_start(
        "--desktop", "--mlflow", "--no-browser", env={"FAKE_UNAME_S": "Darwin", "FAKE_UNAME_M": "arm64"}
    )
    assert "MLflow at http://localhost:5001" in result.output


def test_a_first_run_with_mlflow_pulls_its_images_and_writes_where_traces_go(run_start):
    result = run_start("--mlflow", env_file=None)

    assert result.returncode == 0
    assert result.called("pull mcfaddja/nl2sql-mlflow:")
    assert result.called("pull mcfaddja/nl2sql-mlflowdb:")
    env = result.env_file()
    assert env["MLFLOW_TRACKING_URI"] == "http://nl2sql-mlflow:5000"
    assert env["MLFLOW_IMAGE_NAME"] == "mcfaddja/nl2sql-mlflow"
    assert "MLFLOW_TRACKING_URI is not set" not in result.output


def test_mlflow_that_was_never_pinned_is_pulled_rather_than_built(run_start):
    """A .env from before 5.5 pins no MLflow, and compose would build both
    images here -- a pull of MLflow's and Postgres's own -- when the
    published ones are a pull away."""
    result = run_start("--mlflow")

    assert "MLflow's images are not pinned, so they would be built here from source" in result.output
    assert result.called(f"pull mcfaddja/nl2sql-mlflow:{SHIPPED}")
    assert result.env_file()["MLFLOW_DB_IMAGE_NAME"] == "mcfaddja/nl2sql-mlflowdb"
