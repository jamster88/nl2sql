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

import re
import subprocess
from pathlib import Path

import pytest

# No marker: every one of these runs against fake `docker`, `curl`, `sleep`,
# `uname` and browser binaries, so none of them needs a daemon -- the same
# arrangement `test_setup_script.py` uses, and the reason both run on an
# ordinary `pytest`.

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
START_SH = REPO_ROOT / "start.sh"


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
    assert opened, f"no browser was opened:\n" + "\n".join(result.calls)
    assert opened[0].endswith("http://localhost:8080")
    assert "Opening http://localhost:8080" in result.output


def test_it_opens_the_page_only_once(run_start):
    """Each opener is tried until one succeeds; all of them succeeding must
    not mean eight tabs."""
    result = run_start()
    assert len([call for call in result.calls if call.startswith("browser ")]) == 1


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


def test_a_missing_daemon_is_named_before_anything_is_started(run_start):
    result = run_start(env={"FAKE_NO_DAEMON": "1"})
    assert result.returncode != 0
    assert "the Docker daemon is not running" in result.output
    assert not result.called("compose up")


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


def test_review_opens_both_pages(run_start):
    result = run_start("--review")
    opened = [call for call in result.calls if call.startswith("browser ")]

    assert len(opened) == 2, f"expected two pages:\n" + "\n".join(opened)
    assert opened[0].endswith("http://localhost:8080")
    assert opened[1].endswith("http://localhost:8081")


def test_the_review_page_is_opened_second(run_start):
    """So the interface people actually ask questions in is left in front."""
    result = run_start("--review")
    order = [line for line in result.output.splitlines() if "Opening http" in line]
    assert order == ["==> Opening http://localhost:8080", "==> Opening http://localhost:8081"]


def test_without_review_only_one_page_opens(run_start):
    result = run_start()
    opened = [call for call in result.calls if call.startswith("browser ")]
    assert len(opened) == 1
    assert "8081" not in result.output


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
    opened = [call for call in result.calls if call.startswith("browser ")]
    assert opened[0].endswith("http://localhost:9080")
    assert opened[1].endswith("http://localhost:9081")


def test_the_closing_lines_say_what_each_page_is_for(run_start):
    """Two URLs with no explanation is worse than one, because the second is
    the one nobody has seen before."""
    output = run_start("--review").output
    assert "say whether the answer was right" in output
    assert "promote the good ones" in output


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
    result = run_start("--review", env={"FAKE_BROWSER_EXIT": "3"})

    assert result.returncode == 0
    assert "could not open the review interface" in result.output
    assert "http://localhost:8081" in result.output
    # And the first page's failure is still reported separately.
    assert "could not open a browser" in result.output


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
    assert [call for call in result.calls
            if call.startswith("browser") and "http://localhost:8081" in call]


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

