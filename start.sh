#!/usr/bin/env bash
#
# One command. Everything up, and the web interface open in your browser.
#
#     ./start.sh
#
# This is the front door. It does nothing the other two scripts do not --
# it runs them, waits until the page actually answers, and opens it:
#
#   * ./setup.sh --gui    first time on a machine: pulls every image,
#                         including the web interface, and pins them in .env
#   * ./launch.sh --gui   every time: starts whatever is down, checks each
#                         database is populated and both models are reachable,
#                         then brings up the API and the GUI in front of it
#   * your browser        at http://localhost:8080
#
# With --desktop the last step is a window instead of a page: the same stack
# comes up, the client's jar is fetched (or built, if there is no published
# one for this machine), the API's certificate is copied out for it to verify
# against, and it is started. The web interface is not started at all -- one
# interface is what was asked for, and it is this one.
#
#     ./start.sh --desktop
#
# With --review it does the same for the feedback system: the staging
# database that keeps verdicts, the service that promotes them into the
# golden questions, and a second page at http://localhost:8081. That page
# opens whichever interface was chosen, because reviewing happens in one
# place and there is no desktop half of it.
#
# Use those two directly when you want the parts separately -- a terminal
# session with no API, a different agent tag, no knowledge base. This script
# is for when you want the whole thing and do not want to think about it.
set -euo pipefail

cd "$(dirname "$0")"

OPEN_BROWSER=1
QUIET=0
WITH_REVIEW=0
WITH_DESKTOP=0
# Flags handed on. The interface itself is decided after parsing, because
# --desktop replaces the web one rather than adding to it.
LAUNCH_ARGS=()
SETUP_ARGS=()

step() { [[ $QUIET -eq 1 ]] || printf '\n==> %s\n' "$1"; }
info() { [[ $QUIET -eq 1 ]] || printf '    %s\n' "$1"; }
warn() { printf '    WARNING: %s\n' "$1" >&2; }
die()  { printf '\nERROR: %s\n' "$1" >&2; exit 1; }

compose_env() {  # compose_env KEY DEFAULT -- what compose hands the agent: shell, then .env
    local value="${!1:-}"
    if [[ -z "$value" && -f .env ]]; then
        value=$(grep -E "^$1=" .env | tail -1 | cut -d= -f2-)
    fi
    printf '%s' "${value:-$2}"
}

usage() {
    cat <<'EOF'
Usage: ./start.sh [options]

Brings up the whole stack and opens the web interface in your browser.

      --desktop      Use the Java desktop client instead of the web interface:
                     builds its jar, copies the API's certificate out and runs
                     it. Needs a Java runtime of 21 or later on this machine
      --review       Also bring up the feedback system -- the staging database
                     that keeps verdicts, the service that promotes them into
                     the golden questions, and the review interface -- and open
                     that in a second browser window as well
      --feedback     Keep verdicts without the review interface: starts the
                     staging database only, so votes are staged for later
      --no-browser   Start everything, but print the URLs instead of opening them
      --no-rag       Start only the retail database; the agent answers from the
                     schema alone, with neither knowledge nor worked examples
      --restart      Recreate the containers instead of reusing what is running
  -q, --quiet        Only print problems
  -h, --help         Show this message

First run on a machine takes a few minutes: it pulls about 3 GB of images.
Afterwards it is seconds.

Set BROWSER to choose what opens the pages. Whether the second one lands in a
new window or a new tab is the browser's decision, not this script's.

./setup.sh and ./launch.sh are the same steps with the parts separated.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        # The review interface is nothing without the service behind it, the
        # service is nothing without the staging database in front of it, and
        # none of it is any use without the web interface people vote in --
        # so one flag asks for the lot. launch.sh resolves the chain.
        --review) WITH_REVIEW=1; LAUNCH_ARGS+=(--review); SETUP_ARGS+=(--review); shift ;;
        --feedback) LAUNCH_ARGS+=(--feedback); shift ;;
        # The desktop client is an interface, not an addition to one: with
        # this the web interface is not started and no browser is opened for
        # it. --review still opens the review page, which has no desktop
        # equivalent and is not going to get one.
        --desktop) WITH_DESKTOP=1; shift ;;
        --no-browser) OPEN_BROWSER=0; shift ;;
        --no-rag) LAUNCH_ARGS+=(--no-rag); SETUP_ARGS+=(--no-rag); shift ;;
        --restart) LAUNCH_ARGS+=(--restart); shift ;;
        -q|--quiet) QUIET=1; LAUNCH_ARGS+=(--quiet); shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; die "unknown option: $1" ;;
    esac
done

# The interface goes first so the rest of the list reads as additions to it.
# Guarded expansion: the array is empty when no other flag was given, and
# "${arr[@]}" on an empty array aborts under `set -u` on bash 3.2, which is
# what macOS ships.
if [[ $WITH_DESKTOP -eq 1 ]]; then
    LAUNCH_ARGS=(--desktop ${LAUNCH_ARGS[@]+"${LAUNCH_ARGS[@]}"})
    SETUP_ARGS=(--desktop ${SETUP_ARGS[@]+"${SETUP_ARGS[@]}"})
else
    LAUNCH_ARGS=(--gui ${LAUNCH_ARGS[@]+"${LAUNCH_ARGS[@]}"})
    SETUP_ARGS=(--gui ${SETUP_ARGS[@]+"${SETUP_ARGS[@]}"})
fi

# --- Prerequisites ---------------------------------------------------------
command -v docker >/dev/null 2>&1 || die "docker is not installed or not on PATH."
docker info >/dev/null 2>&1 || die "the Docker daemon is not running. Start Docker and retry."
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required ('docker compose')."

# --- First run -------------------------------------------------------------
# launch.sh hands off to setup.sh on its own when .env is missing, but it
# does so without --gui, which leaves the interface to be built from source
# on first start. Doing it here instead pulls the published image.
if [[ ! -f .env ]]; then
    step "First run on this machine -- fetching the images"
    info "About 3 GB, once. ./setup.sh is what does it."
    ./setup.sh "${SETUP_ARGS[@]}"
fi

# --- Everything else -------------------------------------------------------
./launch.sh "${LAUNCH_ARGS[@]}"

# --- The page --------------------------------------------------------------
gui_port=$(compose_env GUI_PORT 8080)
url="http://localhost:${gui_port}"
review_gui_port=$(compose_env REVIEW_GUI_PORT 8081)
review_url="http://localhost:${review_gui_port}"

# Healthy is not the same as answering. The container reports healthy as soon
# as nginx is up, and nginx is up a moment before it has read its generated
# configuration -- so a browser opened on the health check alone lands on a
# connection error often enough to matter.
wait_for_page() {  # wait_for_page URL
    local _
    for _ in $(seq 1 60); do
        curl -s -f -o /dev/null --max-time 3 "$1" && return 0
        sleep 1
    done
    return 1
}

if [[ $WITH_DESKTOP -eq 0 ]]; then
    step "Waiting for the interface"
    if ! wait_for_page "$url"; then
        warn "the web interface never answered at $url."
        warn "Check what it said: docker compose --profile api --profile gui logs gui"
        exit 1
    fi
fi

# The review interface is a separate container on a separate port, and it is
# allowed to be the one thing that did not come up: the web interface is
# already answering, and exiting here would take a working stack away over a
# page the user may not have looked at yet.
review_ready=0
if [[ $WITH_REVIEW -eq 1 ]]; then
    step "Waiting for the review interface"
    if wait_for_page "$review_url"; then
        review_ready=1
    else
        warn "the review interface never answered at $review_url."
        warn "Check what it said: docker compose --profile reviewgui logs reviewgui"
        warn "The web interface is up; verdicts are staged and can be reviewed later."
    fi
fi

# --- The desktop client ----------------------------------------------------
# A local process rather than a page, so there is no URL to open and no
# container to wait on: launch.sh has already built the jar and copied the
# certificate out, and this runs it.
DESKTOP_JAR="desktop/target/nl2sql-desktop.jar"
DESKTOP_LOG="desktop/target/desktop.log"
DESKTOP_PID="desktop/target/desktop.pid"

java_major() {  # java_major PATH_TO_JAVA -- the major version, or 0
    local line
    [[ -n "$1" ]] || { printf '0'; return 0; }
    line=$("$1" -version 2>&1 | head -1)
    # `openjdk version "21.0.12" ...` and `openjdk version "28-ea" ...` are
    # both ordinary; so is `"1.8.0_412"`, which yields 1 and is refused.
    line=${line#*\"}
    line=${line%%\"*}
    line=${line%%.*}
    line=${line%%-*}
    case "$line" in
        ''|*[!0-9]*) printf '0' ;;
        *) printf '%s' "$line" ;;
    esac
}

start_desktop() {
    # No java at all and a java too old are the same answer -- "this machine
    # needs a newer runtime" -- so they are the same branch. `java_major`
    # reports 0 for a path that is not there, which is how.
    local java_bin major
    java_bin=$(command -v java 2>/dev/null || true)
    major=$(java_major "$java_bin")
    if [[ "$major" -lt 21 ]]; then
        warn "the desktop client needs a Java runtime of 21 or later, and this"
        warn "machine reports Java ${major} (nothing on PATH reports 0)."
        warn "The jar is built; point a newer runtime at it yourself:"
        warn "  <path-to-java> -jar $DESKTOP_JAR --cacert ./nl2sql-api.crt"
        return 1
    fi
    if [[ ! -f "$DESKTOP_JAR" ]]; then
        warn "$DESKTOP_JAR was not built, so there is nothing to run."
        return 1
    fi

    local api_port
    api_port=$(compose_env API_PORT 8443)
    local trust=(--insecure)
    if [[ -f nl2sql-api.crt ]]; then
        # Verifying beats not verifying, and the certificate is right there.
        trust=(--cacert ./nl2sql-api.crt)
    else
        warn "./nl2sql-api.crt is missing, so the client will not verify the API's"
        warn "certificate. It says so in its own status bar for as long as that is true."
    fi

    # Two things, and neither is enough on its own. `nohup` is what survives
    # the terminal being closed. The subshell is what survives *this script
    # exiting*: backgrounded directly, the client is a job of this shell and
    # goes when this shell's process group does, which is moments later. In
    # a subshell it is reparented away and outlives both.
    mkdir -p "$(dirname "$DESKTOP_PID")"
    ( nohup "$java_bin" -jar "$DESKTOP_JAR" "${trust[@]}" \
          --url "https://localhost:$api_port" >"$DESKTOP_LOG" 2>&1 &
      printf '%s' "$!" > "$DESKTOP_PID" )
    local pid
    pid=$(cat "$DESKTOP_PID")

    # The same promise the browser half makes: do not report success until
    # the thing is actually up. A window that will not open does not open in
    # the first second or two, and the output is already being kept.
    sleep 2
    if ! kill -0 "$pid" 2>/dev/null; then
        warn "the desktop client started and stopped again. It said:"
        while read -r said; do warn "  $said"; done < <(grep -v '^WARNING' "$DESKTOP_LOG" | head -6)
        return 1
    fi
    return 0
}

desktop_already_running() {
    local pid
    [[ -f "$DESKTOP_PID" ]] || return 1
    pid=$(cat "$DESKTOP_PID" 2>/dev/null)
    [[ -n "$pid" ]] || return 1
    # `kill -0` asks whether a process exists without touching it. The file
    # outlives the process it names, so the file alone proves nothing --
    # and a second window onto the same API is a thing nobody asked for.
    kill -0 "$pid" 2>/dev/null
}

desktop_running=0
if [[ $WITH_DESKTOP -eq 1 ]]; then
    if desktop_already_running; then
        step "The desktop client is already open"
        info "pid $(cat "$DESKTOP_PID") -- it is pointed at the API this script just checked"
        desktop_running=1
    else
        step "Opening the desktop client"
        if start_desktop; then
            desktop_running=1
        else
            warn "The API is up; ./start.sh (without --desktop) opens the web interface instead."
        fi
    fi
fi

# Every one of these is the right answer on exactly one kind of machine and
# wrong on the others, so they are tried in order of how likely they are to
# be the one -- and the URL is printed either way, because a server with no
# desktop is a perfectly ordinary place to run this.
open_browser() {
    local opener
    local candidates=()
    [[ -n "${BROWSER:-}" ]] && candidates+=("$BROWSER")

    case "$(uname -s)" in
        Darwin) candidates+=(open) ;;
        Linux)
            # WSL has xdg-open and it frequently opens nothing, so the
            # Windows-side openers are tried first there.
            if grep -qi microsoft /proc/version 2>/dev/null; then
                candidates+=(wslview explorer.exe xdg-open)
            else
                candidates+=(xdg-open gio x-www-browser sensible-browser)
            fi ;;
        MINGW*|MSYS*|CYGWIN*) candidates+=(start) ;;
    esac

    # Guarded expansion: an unrecognised `uname` leaves this empty, and
    # "${candidates[@]}" on an empty array aborts under `set -u` on bash 3.2
    # -- which is what macOS ships. The whole point of this function is to
    # behave on machines nobody tested it on.
    for opener in ${candidates[@]+"${candidates[@]}"}; do
        command -v "$opener" >/dev/null 2>&1 || continue
        if [[ "$opener" == "gio" ]]; then
            gio open "$1" >/dev/null 2>&1 && return 0
        else
            "$opener" "$1" >/dev/null 2>&1 && return 0
        fi
    done
    return 1
}

if [[ $OPEN_BROWSER -eq 1 ]]; then
    # Not under --desktop: the questions are asked in a window this script
    # has already opened, and there is no web interface running to open.
    if [[ $WITH_DESKTOP -eq 0 ]]; then
        step "Opening $url"
        if ! open_browser "$url"; then
            warn "could not open a browser on this machine. Open it yourself:"
            warn "$url"
        fi
    fi
    if [[ $review_ready -eq 1 ]]; then
        # Opened second so the interface people actually ask questions in is
        # the one left in front. Whether this lands in a new window or a new
        # tab is the browser's decision; neither `open` nor `xdg-open` has a
        # say in it, and pretending otherwise would mean special-casing every
        # browser there is.
        step "Opening $review_url"
        if ! open_browser "$review_url"; then
            warn "could not open the review interface. Open it yourself:"
            warn "$review_url"
        fi
    fi
elif [[ $WITH_DESKTOP -eq 0 ]]; then
    step "Ready at $url"
    [[ $review_ready -eq 1 ]] && info "Review interface at $review_url"
else
    step "The desktop client is running"
    [[ $review_ready -eq 1 ]] && info "Review interface at $review_url"
fi

# Deliberately short. launch.sh has just printed what the stack is and how
# to look at it; saying it again is how a front door starts feeling like a
# wall of text rather than one command.
if [[ $QUIET -eq 0 && $WITH_DESKTOP -eq 1 ]]; then
    cat <<EOF

    The desktop client is open. Ask a question, and say whether the answer
    was right -- the verdict goes to the same staging table the web interface
    writes to, so it turns up in the same review queue.

EOF
    if [[ $desktop_running -eq 0 ]]; then
        cat <<EOF
    It could not be started here. The jar is built:

    java -jar $DESKTOP_JAR --cacert ./nl2sql-api.crt

EOF
    fi
    if [[ $WITH_REVIEW -eq 1 ]]; then
        cat <<EOF
    $review_url                     review what people said, and promote the good ones

EOF
    fi
    cat <<EOF
    docker compose --profile api down          stop the API behind it
    $DESKTOP_LOG        what the window said, if it did not open

EOF
elif [[ $QUIET -eq 0 ]]; then
    if [[ $WITH_REVIEW -eq 1 ]]; then
        cat <<EOF

    $url                     ask questions, and say whether the answer was right
    $review_url                     review what people said, and promote the good ones

    Promoting appends to context_questions/translated_questions.md in this
    checkout -- it shows up in \`git diff\` and is committed like any other edit.

    docker compose --profile api --profile gui --profile feedback \\
      --profile review --profile reviewgui down          stop everything

EOF
    else
        cat <<EOF

    $url
    docker compose --profile api --profile gui down    stop everything

EOF
    fi
fi
