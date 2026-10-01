#!/usr/bin/env bash
#
# One command. Everything up, and the web interface open in your browser.
#
#     ./start.sh
#
# This is the front door. The stack itself is the other two scripts' --
# this runs them, after starting what they run on, waits until the page
# actually answers, and opens it:
#
#   * Docker              started if its daemon is not running, and waited on
#   * Ollama, here        started if the embedding model is served from this
#                         machine and nothing answers, and given that model
#                         if it does not have it
#   * ./setup.sh --gui    first time on a machine -- or when .env pins images
#                         other than the ones this checkout ships: pulls every
#                         image, including the web interface, and pins them
#   * ./launch.sh --gui   every time: starts whatever is down, checks each
#                         database is populated, both models are reachable and
#                         which models calls will be routed to, then brings up
#                         the API and the GUI in front of it
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
# database that keeps verdicts, the service that promotes the correct ones
# into the golden questions and fixes the wrong and incomplete ones into
# their own stores, and a second page at http://localhost:8081, in a browser
# window of its own. That page opens whichever interface was chosen, because
# reviewing happens in one place and there is no desktop half of it.
#
# With --console it brings up the SQL console as well -- the retail database
# queried as the agent sees it, through the agent's own gates, for working
# out why an answer was wrong -- and opens it at http://localhost:8082, in a
# window of its own for the same reason.
#
# Use those two directly when you want the parts separately -- a terminal
# session with no API, a different agent tag, no knowledge base. This script
# is for when you want the whole thing and do not want to think about it,
# which includes running the images this checkout was written for.
set -euo pipefail

cd "$(dirname "$0")"

OPEN_BROWSER=1
QUIET=0
WITH_REVIEW=0
WITH_CONSOLE=0
WITH_DESKTOP=0
WITH_RAG=1
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
                     that keeps verdicts, the service that turns them into
                     golden questions, corrections and completions, the two
                     stores for those fixes, and the review interface -- and
                     open that in a browser window of its own
      --console      Also bring up the SQL console -- the retail database
                     queried as the agent's read-only role, through the
                     agent's own gates -- and open it in a browser window of
                     its own
      --feedback     Keep verdicts without the review interface: starts the
                     staging database only, so votes are staged for later
      --no-browser   Start everything, but print the URLs instead of opening them
      --no-rag       Start only the retail database; the agent answers from the
                     schema alone, with neither knowledge nor worked examples
      --restart      Recreate the containers instead of reusing what is running
  -q, --quiet        Only print problems
  -h, --help         Show this message

First run on a machine takes a few minutes: it pulls about 3 GB of images.
Afterwards it is seconds. A checkout that ships newer images than .env pins
runs ./setup.sh again first, which keeps the Ollama host, models and port.

Docker is started if its daemon is not running (Docker Desktop, on macOS or
Linux), and so is the Ollama on this machine when the embedding model is
served from here; that Ollama is given the embedding model if it lacks it.

Set BROWSER to choose what opens the pages. Without it the review page and
the console are opened in windows of their own by Safari, Firefox, Chrome
and the browsers built on Chromium; macOS asks once before a terminal may
ask Safari.

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
        # A troubleshooting tool beside whichever interface was chosen, not
        # instead of it. launch.sh brings the API up for it.
        --console) WITH_CONSOLE=1; LAUNCH_ARGS+=(--console); SETUP_ARGS+=(--console); shift ;;
        # The desktop client is an interface, not an addition to one: with
        # this the web interface is not started and no browser is opened for
        # it. --review still opens the review page, which has no desktop
        # equivalent and is not going to get one.
        --desktop) WITH_DESKTOP=1; shift ;;
        --no-browser) OPEN_BROWSER=0; shift ;;
        --no-rag) WITH_RAG=0; LAUNCH_ARGS+=(--no-rag); SETUP_ARGS+=(--no-rag); shift ;;
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

# --- Docker ----------------------------------------------------------------
# Docker Desktop is an application like any other, so starting it is what
# anyone would do by hand. Waiting for it is the part that goes wrong: the
# whale is in the menu bar a good while before `docker` can talk to the
# daemon behind it. Docker Engine on Linux is a system service that only root
# may start, and this script does not ask for root.
start_docker() {
    case "$(uname -s)" in
        Darwin) open -a Docker >/dev/null 2>&1 || return 1 ;;
        Linux) systemctl --user start docker-desktop >/dev/null 2>&1 || return 1 ;;
        *) return 1 ;;
    esac
    info "waiting for its daemon -- up to a couple of minutes from cold"
    local _
    for _ in $(seq 1 60); do
        docker info >/dev/null 2>&1 && return 0
        sleep 2
    done
    return 1
}

command -v docker >/dev/null 2>&1 || die "docker is not installed or not on PATH."
if ! docker info >/dev/null 2>&1; then
    step "Starting Docker"
    start_docker || die "the Docker daemon is not running, and could not be started from here. Start Docker and retry."
    info "Docker is running"
fi
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required ('docker compose')."

# --- Ollama on this machine --------------------------------------------------
# The knowledge base was embedded with one model, and every question is
# embedded with the same one, on the Ollama that EMBED_BASE_URL names. By
# default that is this machine -- host.docker.internal is this machine as a
# container sees it -- and then it is as much a part of the stack as the
# databases are. An Ollama on another machine is not this script's to start,
# and with --no-rag nothing is embedded at all.
#
# Done before the images, because setup.sh checks the embedding model too and
# would otherwise warn about an Ollama that is a moment from being started.
ollama_tags() {  # ollama_tags URL -- the models it serves, or a failure
    curl -sf --max-time 3 "$1/api/tags" 2>/dev/null
}

# The desktop application where there is one, since that is what keeps
# Ollama running and updated afterwards; the bare server otherwise, started
# the way the desktop client is, so that it outlives this script.
start_ollama() {
    if [[ "$(uname -s)" == "Darwin" ]] && open -a Ollama >/dev/null 2>&1; then
        return 0
    fi
    command -v ollama >/dev/null 2>&1 || return 1
    ( nohup ollama serve >"${TMPDIR:-/tmp}/nl2sql-ollama.log" 2>&1 & )
}

ensure_local_ollama() {  # ensure_local_ollama URL MODEL
    local url="$1" model="$2" tags="" _
    if ! tags=$(ollama_tags "$url"); then
        step "Starting Ollama on this machine"
        if ! start_ollama; then
            warn "nothing answers at $url and Ollama is not installed here, so questions"
            warn "are answered without the knowledge base. https://ollama.com has it."
            return 0
        fi
        for _ in $(seq 1 30); do
            tags=$(ollama_tags "$url") && break
            sleep 1
        done
        if [[ -z "$tags" ]]; then
            warn "Ollama was started but had not answered at $url after 30 seconds."
            return 0
        fi
        info "Ollama is running at $url"
    fi
    # The prefix, as launch.sh matches it: Ollama lists `bge-m3` as
    # `bge-m3:latest`.
    if ! printf '%s' "$tags" | grep -q "\"$model"; then
        step "Pulling $model into the Ollama on this machine"
        info "Once. Questions are embedded with the model the knowledge base was."
        if curl -sf --max-time 1800 -H 'Content-Type: application/json' \
               -d "{\"model\": \"$model\", \"stream\": false}" \
               "$url/api/pull" >/dev/null 2>&1; then
            info "$model is ready"
        else
            warn "could not pull $model into the Ollama here, so questions are answered"
            warn "without the knowledge base until it is there: ollama pull $model"
        fi
    fi
}

if [[ $WITH_RAG -eq 1 ]]; then
    embed_url=$(compose_env EMBED_BASE_URL http://host.docker.internal:11434)
    embed_host=${embed_url#*://}
    embed_host=${embed_host%%[:/]*}
    case "$embed_host" in
        host.docker.internal|localhost|127.0.0.1)
            ensure_local_ollama "${embed_url/host.docker.internal/localhost}" \
                "$(compose_env EMBED_MODEL bge-m3)" ;;
    esac
fi

# --- The images --------------------------------------------------------------
# launch.sh hands off to setup.sh on its own when .env is missing, but it
# does so without --gui, which leaves the interface to be built from source
# on first start. Doing it here instead pulls the published image.
#
# The same goes for a .env that is there but out of date. It pins tags, and a
# checkout that has moved on from them would otherwise keep running the old
# agent behind the new interface -- launch.sh says so, but saying so is not
# the same as fixing it, and fixing it is one re-run of setup.sh, which keeps
# the Ollama host, the models and the port it finds in .env. An interface that
# was never pinned is the first-run case again: it would be built here from
# source when a published image is a pull away.
env_file_value() {  # env_file_value KEY -- what .env says, not the shell
    [[ -f .env ]] || return 0
    grep -E "^$1=" .env | tail -1 | cut -d= -f2- || true
}

stale_pins() {  # stale_pins -- why .env is not what this checkout runs, if it is not
    local image shipped pinned
    image=$(awk -F'"' '/^AGENT_IMAGE=/ {print $2; exit}' setup.sh)
    shipped=$(awk -F'"' '/^AGENT_TAG=/ {print $2; exit}' setup.sh)
    pinned=$(env_file_value AGENT_IMAGE_TAG)
    # Only a .env that pins the image this project publishes is one setup.sh
    # wrote and can bring up to date. One that pins no agent builds it from
    # this checkout, and one that pins another repository is somebody's own
    # build; both are choices, and neither has a newer tag to move to.
    if [[ -z "$pinned" || "$(env_file_value AGENT_IMAGE_NAME)" != "$image" ]]; then
        return 0
    fi
    # A tag exported in the shell is a choice made for this run, and compose
    # takes it over .env; re-pinning under it would change nothing it runs.
    if [[ -n "${AGENT_IMAGE_TAG:-}" ]]; then
        return 0
    fi
    if [[ "$pinned" != "$shipped" ]]; then
        printf 'this checkout ships %s, and .env pins %s' "$shipped" "$pinned"
    elif [[ $WITH_DESKTOP -eq 0 && -z "$(env_file_value GUI_IMAGE_NAME)" ]]; then
        printf 'the web interface is not pinned, so it would be built here from source'
    elif [[ $WITH_DESKTOP -eq 1 && -z "$(env_file_value DESKTOP_IMAGE_NAME)" ]]; then
        printf "the desktop client is not pinned, so its jar would be built here from source"
    elif [[ $WITH_REVIEW -eq 1 && -z "$(env_file_value REVIEW_IMAGE_NAME)" ]]; then
        printf 'the review images are not pinned, so they would be built here from source'
    elif [[ $WITH_CONSOLE -eq 1 && -z "$(env_file_value CONSOLE_GUI_IMAGE_NAME)" ]]; then
        printf "the SQL console's interface is not pinned, so it would be built here from source"
    fi
}

if [[ ! -f .env ]]; then
    step "First run on this machine -- fetching the images"
    info "About 3 GB, once. ./setup.sh is what does it."
    ./setup.sh "${SETUP_ARGS[@]}"
else
    stale=$(stale_pins)
    if [[ -n "$stale" ]]; then
        step "Fetching the images this checkout runs"
        info "$stale."
        info "./setup.sh re-pins them, and keeps the Ollama host, models and port in .env."
        ./setup.sh "${SETUP_ARGS[@]}"
    fi
fi

# --- Everything else -------------------------------------------------------
./launch.sh "${LAUNCH_ARGS[@]}"

# --- The page --------------------------------------------------------------
gui_port=$(compose_env GUI_PORT 8080)
url="http://localhost:${gui_port}"
review_gui_port=$(compose_env REVIEW_GUI_PORT 8081)
review_url="http://localhost:${review_gui_port}"
console_gui_port=$(compose_env CONSOLE_GUI_PORT 8082)
console_url="http://localhost:${console_gui_port}"

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

# The console likewise: a troubleshooting page that did not come up is not a
# reason to take the rest away.
console_ready=0
if [[ $WITH_CONSOLE -eq 1 ]]; then
    step "Waiting for the SQL console"
    if wait_for_page "$console_url"; then
        console_ready=1
    else
        warn "the SQL console never answered at $console_url."
        warn "Check what it said: docker compose --profile console --profile consolegui logs"
        warn "Everything else is up; ./launch.sh --console tries it again on its own."
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

# The review page in a window of its own, which no generic opener can ask
# for: `open` and `xdg-open` hand the browser a URL, and the browser's own
# settings pick a tab or a window. So the default browser is asked directly,
# in the words its family understands, and one this script does not know --
# or one BROWSER names, whose flags it cannot know -- gets the generic opener
# and its choice.
mac_default_browser() {  # the default browser's bundle id, lower-cased
    # LaunchServices keeps one handler per URL scheme. awk reads to the end
    # rather than exiting on the match: under pipefail an early exit can take
    # the pipeline down while `defaults` is still writing. With nothing
    # recorded for https, the browser is the one macOS came with.
    local id
    id=$(defaults read com.apple.LaunchServices/com.apple.launchservices.secure LSHandlers 2>/dev/null |
        awk '/^[[:space:]]*[{][[:space:]]*$/ { role = "" }
             /LSHandlerRoleAll/ { role = $3; gsub(/[";]/, "", role) }
             /LSHandlerURLScheme = "?https"?;/ && !found { found = 1; if (role != "-") print role }' || true)
    printf '%s' "${id:-com.apple.safari}" | tr '[:upper:]' '[:lower:]'
}

linux_window_opener() {  # the default browser's own command, if it takes --new-window
    local desktop candidate
    local candidates=()
    desktop=$(xdg-settings get default-web-browser 2>/dev/null || true)
    case "$desktop" in
        *firefox*) candidates=(firefox firefox-esr) ;;
        *google-chrome*) candidates=(google-chrome google-chrome-stable) ;;
        *chromium*) candidates=(chromium chromium-browser) ;;
        *microsoft-edge*) candidates=(microsoft-edge microsoft-edge-stable) ;;
        *brave*) candidates=(brave-browser brave) ;;
        *vivaldi*) candidates=(vivaldi vivaldi-stable) ;;
    esac
    for candidate in ${candidates[@]+"${candidates[@]}"}; do
        if command -v "$candidate" >/dev/null 2>&1; then
            printf '%s' "$candidate"
            return 0
        fi
    done
    return 1
}

open_window() {  # open_window URL -- in a new window where the browser can be asked for one
    local id opener
    if [[ -z "${BROWSER:-}" ]]; then
        case "$(uname -s)" in
            Darwin)
                id=$(mac_default_browser)
                case "$id" in
                    com.apple.safari)
                        # Safari takes no flags; AppleScript is how it is asked
                        # for a window, and macOS asks once whether this
                        # terminal may. A "no" falls through to the tab, and
                        # so does a question left unanswered for half a
                        # minute -- Apple's own limit is two.
                        if osascript -e 'with timeout of 30 seconds' \
                               -e "tell application \"Safari\" to make new document with properties {URL:\"$1\"}" \
                               -e 'end timeout' >/dev/null 2>&1; then
                            return 0
                        fi ;;
                    com.google.chrome*|org.chromium.chromium|com.microsoft.edgemac*|com.brave.browser*|com.vivaldi.vivaldi|com.operasoftware.opera*)
                        if open -n -b "$id" --args --new-window "$1" >/dev/null 2>&1; then
                            return 0
                        fi ;;
                    org.mozilla.firefox*)
                        if open -n -b "$id" --args -new-window "$1" >/dev/null 2>&1; then
                            return 0
                        fi ;;
                esac ;;
            Linux)
                # WSL's browsers are Windows programs; its xdg-settings, when
                # it has one, describes a Linux desktop that is not there.
                if ! grep -qi microsoft /proc/version 2>/dev/null && opener=$(linux_window_opener); then
                    # Detached, because a browser that was not running
                    # already would otherwise hold this script until closed.
                    ( nohup "$opener" --new-window "$1" >/dev/null 2>&1 & )
                    return 0
                fi ;;
        esac
    fi
    open_browser "$1"
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
        # Opened second, and in a window of its own: reviewing is a different
        # job from asking, often done by someone else, and a tab beside the
        # page people ask questions in is one closed by mistake.
        step "Opening $review_url"
        if ! open_window "$review_url"; then
            warn "could not open the review interface. Open it yourself:"
            warn "$review_url"
        fi
    fi
    if [[ $console_ready -eq 1 ]]; then
        # Its own window as well: it is where someone works out what went
        # wrong, next to -- not inside -- the page where it went wrong.
        step "Opening $console_url"
        if ! open_window "$console_url"; then
            warn "could not open the SQL console. Open it yourself:"
            warn "$console_url"
        fi
    fi
elif [[ $WITH_DESKTOP -eq 0 ]]; then
    step "Ready at $url"
    [[ $review_ready -eq 1 ]] && info "Review interface at $review_url"
    [[ $console_ready -eq 1 ]] && info "SQL console at $console_url"
else
    step "The desktop client is running"
    [[ $review_ready -eq 1 ]] && info "Review interface at $review_url"
    [[ $console_ready -eq 1 ]] && info "SQL console at $console_url"
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
    $review_url                     review what people said: promote, correct, complete

EOF
    fi
    if [[ $WITH_CONSOLE -eq 1 ]]; then
        cat <<EOF
    $console_url                     query the retail database as the agent sees it

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
    $review_url                     review what people said: promote, correct, complete

    Promoting appends to context_questions/translated_questions.md in this
    checkout -- it shows up in \`git diff\` and is committed like any other edit.
    Correcting or completing a wrong answer writes to its own store instead,
    never to the golden set.

    docker compose --profile api --profile gui --profile feedback \\
      --profile review --profile reviewgui down          stop everything

EOF
    else
        cat <<EOF

    $url
    docker compose --profile api --profile gui down    stop everything

EOF
    fi
    if [[ $WITH_CONSOLE -eq 1 ]]; then
        cat <<EOF
    $console_url                     query the retail database as the agent sees it
    docker compose --profile console --profile consolegui down    and the console

EOF
    fi
fi
