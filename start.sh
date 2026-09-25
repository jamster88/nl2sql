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
# With --review it does the same for the feedback system: the staging
# database that keeps verdicts, the service that promotes them into the
# golden questions, and a second page at http://localhost:8081.
#
# Use those two directly when you want the parts separately -- a terminal
# session with no API, a different agent tag, no knowledge base. This script
# is for when you want the whole thing and do not want to think about it.
set -euo pipefail

cd "$(dirname "$0")"

OPEN_BROWSER=1
QUIET=0
WITH_REVIEW=0
# Flags handed to launch.sh. Seeded with the one that is always passed, so
# the array is never empty -- bash 3.2, which is what macOS ships, aborts on
# "${arr[@]}" for an empty array under `set -u`.
LAUNCH_ARGS=(--gui)
SETUP_ARGS=(--gui)

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
        --no-browser) OPEN_BROWSER=0; shift ;;
        --no-rag) LAUNCH_ARGS+=(--no-rag); SETUP_ARGS+=(--no-rag); shift ;;
        --restart) LAUNCH_ARGS+=(--restart); shift ;;
        -q|--quiet) QUIET=1; LAUNCH_ARGS+=(--quiet); shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; die "unknown option: $1" ;;
    esac
done

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

step "Waiting for the interface"
if ! wait_for_page "$url"; then
    warn "the web interface never answered at $url."
    warn "Check what it said: docker compose --profile api --profile gui logs gui"
    exit 1
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
    step "Opening $url"
    if ! open_browser "$url"; then
        warn "could not open a browser on this machine. Open it yourself:"
        warn "$url"
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
else
    step "Ready at $url"
    [[ $review_ready -eq 1 ]] && info "Review interface at $review_url"
fi

# Deliberately short. launch.sh has just printed what the stack is and how
# to look at it; saying it again is how a front door starts feeling like a
# wall of text rather than one command.
if [[ $QUIET -eq 0 ]]; then
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
