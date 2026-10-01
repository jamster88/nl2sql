#!/usr/bin/env bash
#
# Bring the NL2SQL stack up and prove it is ready to answer questions.
#
#     ./launch.sh
#     docker compose run --rm agent "How many stores are there?"
#
# Or, in a browser:
#
#     ./launch.sh --gui
#     open http://localhost:8080
#
# Or, for something else to talk to -- another GUI, a service, curl:
#
#     ./launch.sh --api
#     curl --cacert <cert> https://localhost:8443/v1/meta
#
# Or, to work out why an answer was wrong, the SQL console -- the retail
# database queried as the agent sees it:
#
#     ./launch.sh --console
#     open http://localhost:8082
#
# Or, to see what the agent did with a question -- every agent and every
# model call it made, traced in MLflow:
#
#     ./launch.sh --mlflow
#     open http://localhost:5001
#
# This is the every-time script. setup.sh is the first-time one: it pulls the
# images and writes the .env that pins them. launch.sh assumes that has already
# happened and just starts what is down, then checks that each piece actually
# holds what the agent expects -- the dataset, the knowledge base, the golden
# pairs, both models -- and which models its calls will be routed to.
#
# The distinction matters because the failures are different. Setup fails when
# an image will not pull; launch fails when a container is up but empty, when
# the chat host moved, when the embedding model is not the one the vectors
# were built with, or when the model catalog describes a host that is not
# this one and every call quietly goes to one model. Those are invisible until a question is asked, and then they
# look like the agent being bad at its job.
#
# If .env is missing, launch.sh hands off to setup.sh rather than guessing.
set -euo pipefail

cd "$(dirname "$0")"

WITH_RAG=1
WITH_API=0
WITH_GUI=0
WITH_FEEDBACK=0
WITH_REVIEW=0
WITH_CONSOLE=0
WITH_MLFLOW=0
WITH_DESKTOP=0
RESTART=0
QUIET=0

step() { [[ $QUIET -eq 1 ]] || printf '\n==> %s\n' "$1"; }
info() { [[ $QUIET -eq 1 ]] || printf '    %s\n' "$1"; }
warn() { printf '    WARNING: %s\n' "$1" >&2; }
die()  { printf '\nERROR: %s\n' "$1" >&2; exit 1; }

usage() {
    cat <<'EOF'
Usage: ./launch.sh [options]

      --no-rag     Start only the retail database; the agent answers from the
                   schema alone, with neither knowledge nor worked examples
      --api        Also start the REST API, so a GUI (or curl, or anything
                   that speaks HTTPS) can ask questions instead of a terminal
      --gui        Also start the web interface, and the API it talks to
      --feedback   Also start the staging database, so verdicts given in the
                   web interface are kept instead of staying in the browser
      --review     Also start the review interface, where staged feedback is
                   turned into golden questions, corrections and completions,
                   and the two stores those fixes are kept in (implies
                   --feedback)
      --console    Also start the SQL console, where the retail database is
                   queried as the agent's read-only role and through its
                   gates, to work out why an answer was wrong (implies --api)
      --mlflow     Also start MLflow, where every question the agent answers
                   is traced -- a span per agent and per model call -- and
                   verdicts are recorded on the traces they judge
      --desktop    Also build the desktop client and copy the API's
                   certificate out, so the client can run on this machine
      --restart    Recreate the containers instead of reusing what is running
  -q, --quiet      Only print problems
  -h, --help       Show this message

First run on a machine? Use ./setup.sh -- it pulls the images and writes .env.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-rag) WITH_RAG=0; shift ;;
        --api) WITH_API=1; shift ;;
        # The GUI is nothing without the API behind it, so asking for one
        # asks for both rather than starting a page that cannot load.
        --gui) WITH_GUI=1; WITH_API=1; shift ;;
        # Capture needs somewhere to put a verdict, so this is the staging
        # database plus the API that writes to it.
        --feedback) WITH_FEEDBACK=1; WITH_API=1; shift ;;
        # The review interface is nothing without the service behind it, and
        # the service is nothing without the database in front of it.
        --review) WITH_REVIEW=1; WITH_FEEDBACK=1; WITH_API=1; shift ;;
        # The console presents the certificate the API writes, so the API is
        # what it cannot start without -- and what it is troubleshooting.
        --console) WITH_CONSOLE=1; WITH_API=1; shift ;;
        # Not --api: a question asked from a terminal is traced as well.
        --mlflow) WITH_MLFLOW=1; shift ;;
        # The desktop client talks to the API directly rather than through a
        # proxy of its own, so that is the one thing it cannot do without.
        --desktop) WITH_DESKTOP=1; WITH_API=1; shift ;;
        --restart) RESTART=1; shift ;;
        -q|--quiet) QUIET=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; die "unknown option: $1" ;;
    esac
done

# --- Prerequisites ---------------------------------------------------------
command -v docker >/dev/null 2>&1 || die "docker is not installed or not on PATH."
docker info >/dev/null 2>&1 || die "the Docker daemon is not running. Start Docker and retry."
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required ('docker compose')."

if [[ ! -f .env ]]; then
    step "No .env found -- running setup.sh first"
    info "setup.sh pulls the images and pins them; this only happens once."
    ./setup.sh
    exit $?
fi

SERVICES=(postgres)
[[ $WITH_RAG -eq 1 ]] && SERVICES+=(vectordb chunkdb)

# --- Start -----------------------------------------------------------------
step "Starting ${#SERVICES[@]} service(s): ${SERVICES[*]}"
if [[ $RESTART -eq 1 ]]; then
    docker compose up -d --force-recreate "${SERVICES[@]}"
else
    docker compose up -d "${SERVICES[@]}"
fi

wait_healthy() {
    local container="$1" status=""
    for _ in $(seq 1 60); do
        status=$(docker inspect --format '{{.State.Health.Status}}' "$container" 2>/dev/null || echo starting)
        [[ "$status" == "healthy" ]] && return 0
        sleep 2
    done
    die "$container did not become healthy (last status: ${status:-unknown}). Check: docker compose logs ${container#nl2sql-}"
}

# --- Read-only role --------------------------------------------------------
# The agent connects as a role that can SELECT and nothing else, not as the
# owner that loaded the data. A volume created from an image that predates
# that role keeps whatever roles it had, so the role is (re)created on every
# start; docker/reader_role.sql is idempotent. Local connections inside the
# container are trusted, which is why no superuser password is needed here.
compose_env() {  # compose_env KEY DEFAULT -- what compose hands the agent: shell, then .env
    local value="${!1:-}"
    if [[ -z "$value" && -f .env ]]; then
        value=$(grep -E "^$1=" .env | tail -1 | cut -d= -f2-)
    fi
    printf '%s' "${value:-$2}"
}

ensure_extensions() {
    docker compose exec -T postgres psql -U postgres -q \
        -d "$(compose_env POSTGRES_DB nl2sql_retail)" \
        -v ON_ERROR_STOP=1 \
        -c "SET client_min_messages = warning" \
        -c "CREATE EXTENSION IF NOT EXISTS pg_trgm" >/dev/null 2>&1
}

ensure_reader_role() {
    docker compose exec -T postgres psql -U postgres -q \
        -d "$(compose_env POSTGRES_DB nl2sql_retail)" \
        -v ON_ERROR_STOP=1 \
        -v reader="$(compose_env POSTGRES_READER_USER nl2sql_reader)" \
        -v reader_password="$(compose_env POSTGRES_READER_PASSWORD nl2sql_reader)" \
        -v owner="$(compose_env POSTGRES_USER nl2sql)" \
        -f - < docker/reader_role.sql >/dev/null
}

step "Waiting for health checks"
wait_healthy nl2sql-postgres
info "nl2sql-postgres is healthy"
if [[ $WITH_RAG -eq 1 ]]; then
    wait_healthy nl2sql-vectordb
    info "nl2sql-vectordb is healthy"
    wait_healthy nl2sql-chunkdb
    info "nl2sql-chunkdb is healthy"
fi

step "Making sure the agent's read-only role exists"
ensure_extensions || warn "could not create pg_trgm; literal matching falls back to difflib."
if ensure_reader_role; then
    info "role $(compose_env POSTGRES_READER_USER nl2sql_reader) can read every table and write none"
else
    warn "could not create the agent's read-only role in the retail database."
    warn "The agent will fail to connect. Check: docker compose logs postgres"
fi

# --- Contents --------------------------------------------------------------
# A healthy container is not the same as a populated one. A volume created
# before the image shipped its data comes up healthy and empty, and the only
# symptom is the agent quietly answering without retrieval.
query() {  # query <service> <db-var> <default-db> <sql>
    docker compose exec -T "$1" psql -U "${RAG_DB_USER:-ragproc}" -d "$3" -tAc "$4" 2>/dev/null |
        tr -d '[:space:]' || true
}

step "Checking what is actually in each database"

rows=$(docker compose exec -T postgres psql -U "${POSTGRES_USER:-nl2sql}" \
    -d "${POSTGRES_DB:-nl2sql_retail}" -tAc \
    "SELECT count(*) FROM fact_pos_retail_sales" 2>/dev/null | tr -d '[:space:]' || true)
if [[ -n "$rows" && "$rows" != "0" ]]; then
    info "retail dataset: $rows sales rows"
else
    warn "the retail database is up but has no sales rows in it."
    warn "The agent will connect and then answer nothing. Try: ./setup.sh --reset"
fi

if [[ $WITH_RAG -eq 1 ]]; then
    chunks=$(query vectordb VECTOR_DB_NAME "${VECTOR_DB_NAME:-nl2sql_vectors}" "
        SELECT sum(n) FROM (
            SELECT (xpath('/row/c/text()',
                    query_to_xml('SELECT count(*) AS c FROM ' || quote_ident(tablename),
                                 false, true, '')))[1]::text::bigint AS n
            FROM pg_tables WHERE schemaname = 'public' AND tablename LIKE '%\_embeddings'
        ) t")
    if [[ -n "$chunks" && "$chunks" != "0" ]]; then
        info "knowledge base: $chunks embedded chunks"
    else
        warn "the vector store is up but holds no embedded chunks."
        warn "Retrieval will be skipped; the agent falls back to schema-only."
    fi

    vectors=$(query vectordb VECTOR_DB_NAME "${VECTOR_DB_NAME:-nl2sql_vectors}" \
        "SELECT count(*) FROM golden_pair_question_vectors")
    pairs=$(query chunkdb CONTEXT_DB_NAME "${CONTEXT_DB_NAME:-nl2sql_chunks}" \
        "SELECT count(*) FROM golden_pairs")
    if [[ -n "$pairs" && "$pairs" != "0" && -n "$vectors" && "$vectors" != "0" ]]; then
        info "worked examples: $pairs golden pairs, $vectors embedded questions"
    else
        warn "the context store holds ${pairs:-0} golden pairs and the vector store"
        warn "${vectors:-0} of their embeddings. Multi-shot needs both; it will be skipped."
    fi

    # The v4 Schema Retriever selects tables from this one collection instead
    # of asking the model. Without it there is no vector ranking and the
    # pipeline falls back to whatever the other retrievers named.
    ddl=$(query vectordb VECTOR_DB_NAME "${VECTOR_DB_NAME:-nl2sql_vectors}" \
        "SELECT count(*) FROM ddl_index_embeddings")
    if [[ -n "$ddl" && "$ddl" != "0" ]]; then
        info "schema index: $ddl DDL chunks (table selection needs no model call)"
    else
        warn "the vector store has no ddl_index_embeddings collection."
        warn "Table selection falls back to the knowledge and example hints."
    fi
fi

# --- The v4 pipeline's own prerequisites -----------------------------------
# Two things the multi-agent pipeline needs that the stores above do not
# cover: a trigram index for matching literals, and low-cardinality text
# columns to build the literal catalog from. Neither is fatal -- the matcher
# falls back to difflib, and without a catalog the generator spells literals
# from the question as v3 did -- so both warn rather than stop.
step "Checking the multi-agent pipeline"

trgm=$(docker compose exec -T postgres psql -U postgres \
    -d "$(compose_env POSTGRES_DB nl2sql_retail)" -tAc \
    "SELECT count(*) FROM pg_extension WHERE extname = 'pg_trgm'" 2>/dev/null |
    tr -d '[:space:]' || true)
if [[ "$trgm" == "1" ]]; then
    info "literal matching: pg_trgm installed (trigram search)"
else
    warn "pg_trgm is not installed; literal matching falls back to difflib."
fi

reader_ok=$(docker compose exec -T postgres psql -U postgres \
    -d "$(compose_env POSTGRES_DB nl2sql_retail)" -tAc \
    "SELECT count(*) FROM information_schema.role_table_grants
      WHERE grantee = '$(compose_env POSTGRES_READER_USER nl2sql_reader)'
        AND privilege_type <> 'SELECT'" 2>/dev/null | tr -d '[:space:]' || true)
if [[ "$reader_ok" == "0" ]]; then
    info "least privilege: the agent's role holds SELECT and nothing else"
else
    warn "the agent's role holds ${reader_ok:-?} non-SELECT grants; it should hold none."
fi

# --- Models ----------------------------------------------------------------
# Read back what compose will really hand the agent, rather than what the
# defaults in this script say. awk consumes the whole stream: under pipefail an
# early exit can take the pipeline down with SIGPIPE while compose is writing.
# The agent is behind a profile of its own, and `compose config` leaves out a
# service whose profile is not named -- without `--profile agent` this found
# nothing, and every check below quietly fell back to the defaults on the next
# lines, whatever host and model .env had been given.
compose_value() {
    docker compose --profile agent config 2>/dev/null |
        awk -v key="$1:" '$1 == key && !seen { print $2; seen = 1 }'
}

# An .env written by an earlier setup.sh still pins that release's image, so
# the two-command flow would quietly keep running the old agent after an
# upgrade -- the exact class of "up but not what you think" failure this
# script exists to catch.
pinned_agent=$(compose_env AGENT_IMAGE_TAG "")
expected_agent=$(awk -F'"' '/^AGENT_TAG=/ {print $2; exit}' setup.sh)
if [[ -n "$pinned_agent" && -n "$expected_agent" && "$pinned_agent" != "$expected_agent" ]]; then
    warn ".env pins the agent image at $pinned_agent, but this checkout ships $expected_agent."
    warn "You are running the older agent. Re-run ./setup.sh, or edit AGENT_IMAGE_TAG in .env."
fi

step "Checking the models"
chat_url=$(compose_value OLLAMA_BASE_URL)
chat_model=$(compose_value OLLAMA_MODEL)
chat_url=${chat_url:-http://192.168.10.82:11434}
chat_model=${chat_model:-qwen3.8-256k}

if tags=$(curl -sf --max-time 5 "$chat_url/api/tags" 2>/dev/null); then
# Ollama reports a model the user asked for as `name:latest` when they gave no
# tag, so an exact match on the configured name reports a model that is
# present and working as missing. The embedding check below has always matched
# on the prefix for this reason; this one did not, and warned that "every
# question will fail" about a host the benchmark had just scored 15/15 against.
    if printf '%s' "$tags" | grep -q "\"$chat_model\(\"\|:\)"; then
        info "chat model $chat_model is available at $chat_url"
    else
        warn "$chat_url is reachable but does not have $chat_model."
        warn "Pull it there, or run the agent with --model <name>."
    fi
else
    warn "could not reach the chat host at $chat_url."
    warn "Every question will fail until it is reachable."
fi

if [[ $WITH_RAG -eq 1 ]]; then
    embed_model=$(compose_value EMBED_MODEL)
    embed_model=${embed_model:-bge-m3}
    # The agent reaches the embedding host as host.docker.internal; from here
    # that same Ollama is on localhost.
    if tags=$(curl -sf --max-time 5 "http://localhost:11434/api/tags" 2>/dev/null); then
        if printf '%s' "$tags" | grep -q "\"$embed_model"; then
            info "embedding model $embed_model is available on this machine"
        else
            warn "Ollama is running here but does not have $embed_model."
            warn "Run: ollama pull $embed_model -- retrieval needs the same model"
            warn "the vectors were built with, or it returns confident nonsense."
        fi
    else
        warn "no Ollama on this machine at :11434, so nothing serves $embed_model."
        warn "Retrieval and worked examples will be skipped."
    fi
fi

# --- Model routing ---------------------------------------------------------
# Since v5.2 a call goes to the fastest model calibration measured to be
# suited to its task and the question's complexity, and falls back to
# OLLAMA_MODEL where nothing was measured -- which, on any host but the one
# models/catalog.json describes, is everywhere. The agent works that table
# out once at start-up, from the catalog compose mounts, the settings compose
# hands it and the models the host serves right now. This asks the same
# image to work it out the same way and say what it got, so that a table
# that fell back to one model is seen before the first question rather than
# in a benchmark. --no-deps: the probe reads a file and asks one URL, and the
# stores the agent depends on are not needed for either.
routing_probe='
from nl2sql_agent.config import Settings
from nl2sql_agent.router import RoutingError, build_table, listed_models, load_catalog
s = Settings.from_env()
try:
    catalog = load_catalog(s.model_catalog) if s.model_routing_enabled and s.model_catalog else None
except RoutingError as exc:
    print("ROUTE error " + " ".join(str(exc).split()))
    raise SystemExit(0)
table = build_table(s, catalog, catalog_path=s.model_catalog,
                    host=listed_models(s.ollama_base_url, s.ollama_connect_timeout))
print("ROUTE " + ("on" if table.enabled else "off"))
print("ROUTE models " + " ".join(table.models()))
for note in table.notes:
    print("ROUTE note " + " ".join(note.split()))
'

route_field() {  # route_field NAME -- one field of the probe's answer
    printf '%s\n' "$routing" | sed -n "s/^ROUTE $1 //p"
}

step "Checking model routing"
routing_raw=$(docker compose run --rm --no-deps -T --entrypoint python agent -c "$routing_probe" 2>&1 || true)
routing=$(printf '%s\n' "$routing_raw" | tr -d '\r' | grep '^ROUTE ' || true)
route_error=$(route_field error)
if [[ -n "$route_error" ]]; then
    warn "the agent will not start with these settings: $route_error"
elif printf '%s\n' "$routing" | grep -qx 'ROUTE off'; then
    info "model routing is off (MODEL_ROUTING_ENABLED): every call goes to $chat_model"
elif printf '%s\n' "$routing" | grep -qx 'ROUTE on'; then
    read -r -a routed <<< "$(route_field models)"
    if [[ ${#routed[@]} -gt 1 ]]; then
        others=$(printf '%s, ' "${routed[@]:1}")
        info "model routing: ${#routed[@]} models -- ${routed[0]} (OLLAMA_MODEL), ${others%, }"
    else
        info "model routing: every call goes to ${routed[0]:-$chat_model}"
    fi
    route_field note | while IFS= read -r note; do info "  $note"; done
elif printf '%s' "$routing_raw" | grep -q "No module named 'nl2sql_agent.router'"; then
    warn "the pinned agent image predates model routing, so every call goes to $chat_model."
    warn "./setup.sh pulls the image this checkout ships; ./start.sh does it for you."
else
    warn "could not ask the agent image which models it will route to. It said:"
    while IFS= read -r said; do warn "  $said"; done < <(printf '%s\n' "$routing_raw" | tail -3)
fi

# --- The REST API ----------------------------------------------------------
# The same image as the agent, started as a server instead of a command. It
# is opt-in because most people ask questions from a terminal, and a port
# that nobody asked to be opened should not be.
api_port=$(compose_env API_PORT 8443)
api_scheme=https
api_token=$(compose_env API_TOKEN "")
case "$(compose_env API_TLS_ENABLED true)" in
    0|false|no|off|FALSE|False) api_scheme=http ;;
esac

start_api() {
    docker compose --profile api up -d api >/dev/null 2>&1 || return 1
    local status=""
    for _ in $(seq 1 60); do
        status=$(docker inspect --format '{{.State.Health.Status}}' nl2sql-api 2>/dev/null || echo starting)
        [[ "$status" == "healthy" ]] && return 0
        # A container that has already exited will never become healthy, and
        # waiting two more minutes to find that out hides the reason.
        [[ "$(docker inspect --format '{{.State.Running}}' nl2sql-api 2>/dev/null || echo true)" == "false" ]] && return 1
        sleep 2
    done
    return 1
}

if [[ $WITH_API -eq 1 ]]; then
    step "Starting the REST API"
    if start_api; then
        info "REST API is healthy at $api_scheme://localhost:$api_port"
        info "OpenAPI document: $api_scheme://localhost:$api_port/openapi.json"
    else
        warn "the REST API container did not become healthy."
        if docker compose --profile api logs api 2>/dev/null | grep -q "No module named"; then
            warn "The pinned agent image has no REST API in it -- it predates this"
            warn "checkout. Build it here instead: docker compose --profile api build api"
        else
            warn "Check what it said: docker compose --profile api logs api"
        fi
    fi

    if [[ "$api_scheme" == "http" ]]; then
        warn "API_TLS_ENABLED is off, so the API serves plain HTTP: questions, SQL"
        warn "and rows all cross the network in clear text."
    fi
    if [[ -z "$api_token" ]]; then
        warn "no API_TOKEN is set, so anything that can reach port $api_port may ask"
        warn "questions. Set API_TOKEN in .env before exposing this off this machine."
    fi
fi

# --- Waiting, and the proxies -----------------------------------------------
# `wait_healthy` above is not used here on purpose: it calls `die`, and the
# whole point of these three is to warn and carry on. A missing review
# interface should not stop a working agent from being reported as working.
await_health() {  # await_health CONTAINER
    local container="$1" status=""
    for _ in $(seq 1 60); do
        status=$(docker inspect --format '{{.State.Health.Status}}' "$container" 2>/dev/null || echo starting)
        [[ "$status" == "healthy" ]] && return 0
        [[ "$(docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null || echo true)" == "false" ]] && return 1
        sleep 2
    done
    return 1
}

# --- The proxies, and the certificate they loaded at start ---------------
# nginx reads `proxy_ssl_trusted_certificate` once, while it parses its
# config. A certificate reissued after that -- which the API does when
# API_TLS_HOSTNAMES grows to cover a service that did not exist before -- is
# one the proxy has never seen, and every request through it then fails with
# an upstream verification error while the page itself still loads fine.
#
# The container's own health check cannot see this: it asks for index.html,
# which is served from disk. So the check is a request for a route the API
# owns, through the proxy, and the cure is a restart.
proxy_reaches_api() {  # proxy_reaches_api PORT
    local code
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
        "http://localhost:$1/readyz" 2>/dev/null || echo 000)
    # 503 is the API answering that it is not ready, which still means the
    # proxy reached it. Only a failure to reach it at all is the problem here.
    [[ "$code" == "200" || "$code" == "503" ]]
}

repair_proxy() {  # repair_proxy SERVICE CONTAINER PORT PROFILES...
    local name="$1" container="$2" port="$3"
    shift 3
    proxy_reaches_api "$port" && return 0
    info "$name cannot reach the API through its proxy -- restarting it to pick up"
    info "the current certificate (the API reissues one when a service name is added)"
    docker compose "$@" restart "$name" >/dev/null 2>&1 || return 1
    await_health "$container" || return 1
    proxy_reaches_api "$port"
}

# --- The desktop client ----------------------------------------------------
# A jar, not a container. The desktop client draws a window on this machine,
# so what Docker does for it is build it -- which keeps the promise the rest
# of this script makes, that Docker is the only thing anyone has to install.
DESKTOP_JAR="desktop/target/nl2sql-desktop.jar"
DESKTOP_CERT="nl2sql-api.crt"

javafx_platform() {
    # OpenJFX publishes its native code under one of five classifiers, and
    # the jar is built in a Linux container for whatever this machine is.
    # Anything unrecognised falls back to a 64-bit Linux build, which is what
    # the container would have assumed on its own.
    case "$(uname -s)" in
        Darwin) [[ "$(uname -m)" == "arm64" ]] && printf 'mac-aarch64' || printf 'mac' ;;
        Linux)
            case "$(uname -m)" in
                aarch64|arm64) printf 'linux-aarch64' ;;
                *) printf 'linux' ;;
            esac ;;
        MINGW*|MSYS*|CYGWIN*) printf 'win' ;;
        *) printf 'linux' ;;
    esac
}

desktop_jar_is_current() {  # desktop_jar_is_current PLATFORM
    [[ -f "$DESKTOP_JAR" ]] || return 1
    # A jar built for another machine will not start on this one, and the
    # only thing that says which it was built for is this file.
    [[ "$(cat desktop/target/.platform 2>/dev/null)" == "$1" ]] || return 1
    # Any source newer than the jar means the jar is not this checkout.
    [[ -z "$(find desktop/src desktop/pom.xml -newer "$DESKTOP_JAR" -print -quit 2>/dev/null)" ]]
}

desktop_image() {  # desktop_image PLATFORM -- what compose resolves for it
    printf '%s:%s-%s' \
        "$(compose_env DESKTOP_IMAGE_NAME nl2sql-desktop-build)" \
        "$(compose_env DESKTOP_IMAGE_TAG local)" "$1"
}

build_desktop_jar() {  # build_desktop_jar PLATFORM -- or take it from the image
    # Compose builds a service that has a `build:` section only when its
    # image is missing, so a `setup.sh --desktop` that pulled one turns this
    # into a copy. Nothing is pulled here: launch.sh is the fast path, and a
    # download in it would be a download on every start.
    JAVAFX_PLATFORM="$1" docker compose --profile desktop run --rm desktop >/dev/null 2>&1 || return 1
    printf '%s' "$1" > desktop/target/.platform
    [[ -f "$DESKTOP_JAR" ]]
}

if [[ $WITH_DESKTOP -eq 1 ]]; then
    step "Preparing the desktop client"
    desktop_platform=$(javafx_platform)
    mkdir -p desktop/target
    if desktop_jar_is_current "$desktop_platform"; then
        info "$DESKTOP_JAR is already built for $desktop_platform"
    else
        if docker image inspect "$(desktop_image "$desktop_platform")" >/dev/null 2>&1; then
            info "Taking it from $(desktop_image "$desktop_platform")"
        else
            info "Building it for $desktop_platform -- a few minutes the first time"
        fi
        if build_desktop_jar "$desktop_platform"; then
            info "Built $DESKTOP_JAR"
        else
            warn "the desktop client's jar could not be built."
            warn "Check what it said: JAVAFX_PLATFORM=$desktop_platform docker compose \\"
            warn "  --profile desktop run --rm desktop"
            WITH_DESKTOP=0
        fi
    fi
fi

if [[ $WITH_DESKTOP -eq 1 ]]; then
    # The client verifies the API's certificate rather than skipping the
    # check, so it needs the certificate. It is the same file the API writes
    # itself on first start, copied out of the volume it lives in.
    if docker compose --profile api cp api:/etc/nl2sql/tls/server.crt "./$DESKTOP_CERT" >/dev/null 2>&1; then
        info "Copied the API certificate to ./$DESKTOP_CERT"
    else
        warn "could not copy the API's certificate out of the container."
        warn "Without it the client has nothing to verify against; --insecure is"
        warn "the fallback, and it says so in the status bar for as long as it is on."
    fi
fi

# --- The web interface -----------------------------------------------------
# nginx serving the built page, and proxying /v1 to the API over TLS. It is
# started after the API because it waits on the API's health check, and it
# holds API_TOKEN so the browser never has to.
gui_port=$(compose_env GUI_PORT 8080)

start_gui() {
    docker compose --profile api --profile gui up -d gui >/dev/null 2>&1 || return 1
    local status=""
    for _ in $(seq 1 60); do
        status=$(docker inspect --format '{{.State.Health.Status}}' nl2sql-gui 2>/dev/null || echo starting)
        [[ "$status" == "healthy" ]] && return 0
        [[ "$(docker inspect --format '{{.State.Running}}' nl2sql-gui 2>/dev/null || echo true)" == "false" ]] && return 1
        sleep 2
    done
    return 1
}

if [[ $WITH_GUI -eq 1 ]]; then
    step "Starting the web interface"
    if start_gui; then
        if repair_proxy gui nl2sql-gui "$gui_port" --profile api --profile gui; then
            info "GUI is healthy at http://localhost:$gui_port"
        else
            warn "the GUI is up but cannot reach the API through its proxy."
            warn "Check what it said: docker compose --profile api --profile gui logs gui"
        fi
    else
        warn "the GUI container did not become healthy."
        warn "Check what it said: docker compose --profile api --profile gui logs gui"
    fi
fi

# --- Feedback --------------------------------------------------------------
# The staging database a verdict is written to, and the service that reviews
# what lands there.
#
# Two things are worth knowing about the order here. The API is told where
# the staging database is through API_FEEDBACK_DB_URL, which compose reads
# from the environment -- so the database has to exist before the API is
# started, which is why --feedback is handled before the API above would
# have been enough on its own. And the review service creates the schema and
# the API's INSERT-only role on its own start, so the API can be pointed at
# a database whose tables do not exist yet and will simply report feedback as
# unavailable until they do.
review_port=$(compose_env REVIEW_PORT 8444)
review_gui_port=$(compose_env REVIEW_GUI_PORT 8081)

start_feedbackdb() {
    docker compose --profile feedback up -d feedbackdb >/dev/null 2>&1 || return 1
    await_health nl2sql-feedbackdb
}

# The two stores a reviewer's fixes go into: corrections of wrong answers
# and completions of incomplete ones. Started, and waited on, before the
# review service so its start-up can create their schemas -- the same order
# the staging database gets for the same reason.
start_fixstores() {
    docker compose --profile feedback --profile review up -d correctionsdb completionsdb \
        >/dev/null 2>&1 || return 1
    await_health nl2sql-correctionsdb || return 1
    await_health nl2sql-completionsdb
}

start_review() {
    docker compose --profile feedback --profile review up -d review >/dev/null 2>&1 || return 1
    await_health nl2sql-review
}

start_reviewgui() {
    docker compose --profile feedback --profile review --profile reviewgui \
        up -d reviewgui >/dev/null 2>&1 || return 1
    await_health nl2sql-review-gui
}

if [[ $WITH_FEEDBACK -eq 1 ]]; then
    step "Starting the feedback staging database"
    if start_feedbackdb; then
        info "Staging database is healthy on port $(compose_env FEEDBACK_DB_PORT 5435)"
        if [[ -z "$(compose_env API_FEEDBACK_DB_URL "")" ]]; then
            warn "API_FEEDBACK_DB_URL is not set, so the API will not write verdicts to it."
            warn "setup.sh writes one into .env; add it there or export it before starting."
        fi
    else
        warn "the staging database did not become healthy."
        warn "Check what it said: docker compose --profile feedback logs feedbackdb"
    fi
fi

if [[ $WITH_REVIEW -eq 1 ]]; then
    step "Starting the corrections and completions stores"
    if start_fixstores; then
        info "Corrections store is healthy on port $(compose_env CORRECTIONS_DB_PORT 5436)"
        info "Completions store is healthy on port $(compose_env COMPLETIONS_DB_PORT 5437)"
    else
        warn "the corrections and completions stores did not both become healthy."
        warn "Fixes cannot be saved until they are; golden-set promotion still works."
        warn "Check what they said: docker compose --profile feedback --profile review logs correctionsdb completionsdb"
    fi

    step "Starting the review service"
    if start_review; then
        case "$(compose_env REVIEW_TLS_ENABLED true)" in
            0|false|no|off|FALSE|NO|OFF) review_scheme=http ;;
            *) review_scheme=https ;;
        esac
        info "Review service is healthy at $review_scheme://localhost:$review_port"
    else
        warn "the review service did not become healthy."
        warn "Check what it said: docker compose --profile feedback --profile review logs review"
    fi

    step "Starting the review interface"
    if start_reviewgui; then
        if repair_proxy reviewgui nl2sql-review-gui "$review_gui_port" \
            --profile feedback --profile review --profile reviewgui; then
            info "Review interface is healthy at http://localhost:$review_gui_port"
        else
            warn "the review interface is up but cannot reach the review service."
            warn "Check what it said: docker compose --profile reviewgui logs reviewgui"
        fi
    else
        warn "the review interface did not become healthy."
        warn "Check what it said: docker compose --profile feedback --profile review --profile reviewgui logs reviewgui"
    fi
fi

# --- The SQL console -------------------------------------------------------
# The agent's own image started a third way, and a page in front of it. After
# the API, because it presents the certificate the API writes -- and on the
# first start after an upgrade that is a certificate the API has just
# reissued, because API_TLS_HOSTNAMES has grown to name the console.
console_port=$(compose_env CONSOLE_PORT 8445)
console_gui_port=$(compose_env CONSOLE_GUI_PORT 8082)
console_bind=$(compose_env CONSOLE_BIND_ADDRESS 127.0.0.1)

start_console() {
    docker compose --profile console up -d console >/dev/null 2>&1 || return 1
    await_health nl2sql-console
}

start_consolegui() {
    docker compose --profile console --profile consolegui up -d consolegui >/dev/null 2>&1 || return 1
    await_health nl2sql-console-gui
}

if [[ $WITH_CONSOLE -eq 1 ]]; then
    step "Starting the SQL console"
    if start_console; then
        case "$(compose_env CONSOLE_TLS_ENABLED true)" in
            0|false|no|off|FALSE|NO|OFF) console_scheme=http ;;
            *) console_scheme=https ;;
        esac
        info "SQL console is healthy at $console_scheme://localhost:$console_port"
    else
        warn "the SQL console did not become healthy."
        if docker compose --profile console logs console 2>/dev/null | grep -q "No module named"; then
            warn "The pinned agent image has no SQL console in it -- it predates this"
            warn "checkout. Build it here instead: docker compose --profile console build console"
        else
            warn "Check what it said: docker compose --profile console logs console"
        fi
    fi

    step "Starting the SQL console's interface"
    if start_consolegui; then
        if repair_proxy consolegui nl2sql-console-gui "$console_gui_port" \
            --profile console --profile consolegui; then
            info "SQL console interface is healthy at http://localhost:$console_gui_port"
        else
            warn "the SQL console's interface is up but cannot reach the console."
            warn "Check what it said: docker compose --profile consolegui logs consolegui"
        fi
    else
        warn "the SQL console's interface did not become healthy."
        warn "Check what it said: docker compose --profile console --profile consolegui logs consolegui"
    fi

    # Loopback by default, which is what makes no token reasonable. Opened
    # to the network, it is a page that runs SQL for anyone who finds it.
    case "$console_bind" in
        127.0.0.1|localhost|::1) console_exposed=0 ;;
        *) console_exposed=1 ;;
    esac
    if [[ $console_exposed -eq 1 && -z "$(compose_env CONSOLE_TOKEN "")" ]]; then
        warn "the SQL console is published on $console_bind with no CONSOLE_TOKEN, so"
        warn "anything that can reach it may run SQL as the agent's database role."
    fi
fi

# --- Tracing ---------------------------------------------------------------
# MLflow, and the database it keeps traces in. Last, and not before the API,
# because nothing waits on it: the agent asks for the server on its first
# question, and again RETRY_SECONDS after one that went untraced, so a
# server that comes up after the API is found without restarting anything.
mlflow_port=$(compose_env MLFLOW_PORT 5001)
mlflow_bind=$(compose_env MLFLOW_BIND_ADDRESS 127.0.0.1)

start_mlflow() {
    docker compose --profile mlflow up -d mlflow >/dev/null 2>&1 || return 1
    await_health nl2sql-mlflow
}

if [[ $WITH_MLFLOW -eq 1 ]]; then
    step "Starting MLflow"
    info "The first start pulls MLflow's own image, about 370 MB."
    if start_mlflow; then
        info "MLflow is healthy at http://localhost:$mlflow_port"
        if [[ -z "$(compose_env MLFLOW_TRACKING_URI "")" ]]; then
            warn "MLFLOW_TRACKING_URI is not set, so the agent will not trace to it."
            warn "setup.sh writes one into .env; add it there or export it before starting."
        fi
    else
        warn "MLflow did not become healthy, so questions are answered untraced."
        warn "Check what it said: docker compose --profile mlflow logs mlflow mlflowdb"
    fi
    # Its interface has no login, and what it shows includes every row every
    # question returned -- so, like the console, it is this machine's unless
    # someone says otherwise, and saying otherwise is worth a warning.
    case "$mlflow_bind" in
        127.0.0.1|localhost|::1) mlflow_exposed=0 ;;
        *) mlflow_exposed=1 ;;
    esac
    if [[ $mlflow_exposed -eq 1 ]]; then
        warn "MLflow is published on $mlflow_bind with no login: anything that can"
        warn "reach it can read every question, query and result, and delete them."
    fi
fi

# --- Ready -----------------------------------------------------------------
if [[ $QUIET -eq 0 ]]; then
    cat <<EOF

==> Ready. Ask a question:

    docker compose run --rm agent "How many stores are there?"

    docker compose run --rm agent "What was the total gross profit for the Produce department in fiscal month 12 of FY2025?"
    docker compose run --rm agent --json "Which 3 promotions had the highest promo quantity sold?"

    The literal matcher means a question need not spell a value the way the
    database does, and the answer's numbers are checked against the rows:

    docker compose run --rm agent "total net sales for dairy and eggs in FY2025"

    ./launch.sh --help     other options
    docker compose down    stop the databases
EOF
    if [[ $WITH_API -eq 1 ]]; then
        cat <<EOF

==> The REST API is up. Point a GUI at it, or try it from here:

    # the development certificate is self-signed, so copy it out and trust it
    docker compose --profile api cp api:/etc/nl2sql/tls/server.crt ./nl2sql-api.crt

    curl --cacert ./nl2sql-api.crt "$api_scheme://localhost:$api_port/v1/meta"
    curl --cacert ./nl2sql-api.crt "$api_scheme://localhost:$api_port/v1/questions?wait=180" \\
         -H 'Content-Type: application/json' \\
         -d '{"question": "How many stores are there?"}'

    # or drive the whole API from an outside container, with nothing but curl
    docker compose --profile api run --rm apitest

    Browse it at $api_scheme://localhost:$api_port/docs
    agent/API.md is the contract a GUI is written against.
EOF
    fi
    if [[ $WITH_REVIEW -eq 1 ]]; then
        cat <<EOF

==> The review interface is up:

    open http://localhost:$review_gui_port

    Verdicts given in the web and desktop interfaces land in the staging
    database and wait here, one pane per verdict:

      Correct                  write the three fields a verdict cannot carry
                               -- keywords, reasoning target, expected result
                               -- and promote the golden pair.
      Wrong                    write the SQL that should have been generated,
                               validate it against the live retail database,
                               and add it to the corrections store.
      Correct but incomplete   the same, into the completions store.

    Promoting appends to context_questions/translated_questions.md in this
    checkout, so it shows up in \`git diff\` like any other edit and is
    committed the same way. The previous version is kept beside it as
    translated_questions.md.bak. Fixes go to their own databases instead --
    ports $(compose_env CORRECTIONS_DB_PORT 5436) and $(compose_env COMPLETIONS_DB_PORT 5437) -- never into the golden set.

    docker compose --profile feedback --profile review --profile reviewgui logs -f review
    review/README.md explains how it is put together.
EOF
    fi
    if [[ $WITH_CONSOLE -eq 1 ]]; then
        cat <<EOF

==> The SQL console is up:

    open http://localhost:$console_gui_port

    Paste the SQL an answer was built from, or pick a table, and run it one
    of three ways: Run returns the rows, Plan stops at the planner's
    estimate, Analyze times a real run. Beside each is what the agent would
    have made of it -- which of its gates would have refused it, in that
    gate's words, against which of its limits. It runs as the agent's
    read-only role, in a read-only transaction, under the agent's timeout.

    docker compose --profile console --profile consolegui logs -f console
    console/README.md explains how it is put together.
EOF
    fi
    if [[ $WITH_MLFLOW -eq 1 ]]; then
        cat <<EOF

==> MLflow is up:

    open http://localhost:$mlflow_port

    Every question the agent answers -- from a terminal, the API or the
    benchmark -- is a trace in the experiment $(compose_env MLFLOW_EXPERIMENT_NAME nl2sql-agent): one span per
    agent and per model call, each with what it read and what it wrote
    back. A verdict given in the web or desktop interface is recorded on
    the trace it judges, and python benchmarks/run_benchmark.py files each
    configuration it measures as a run of its own.

    docker compose --profile mlflow logs -f mlflow
EOF
    fi
    if [[ $WITH_DESKTOP -eq 1 ]]; then
        cat <<EOF

==> The desktop client is built:

    java -jar $DESKTOP_JAR --cacert ./$DESKTOP_CERT \\
         --url $api_scheme://localhost:$api_port

    It needs a Java runtime of 21 or later on this machine and nothing else;
    JavaFX is inside the jar. ./start.sh --desktop runs that line for you.

    Verdicts given in it take the same route as verdicts given in the web
    interface -- the same endpoint, the same staging table, the same review
    queue -- so a reviewer sees one queue whichever was used.

    desktop/README.md explains how it is put together.
EOF
    fi
    if [[ $WITH_GUI -eq 1 ]]; then
        cat <<EOF

==> The web interface is up:

    open http://localhost:$gui_port

    Ask a question and watch the pipeline work through it, then say whether
    the answer was right. nginx in that container holds the API token and
    verifies the API's certificate, so the browser sees neither.

    docker compose --profile api --profile gui logs -f gui
    gui/README.md explains how it is put together.
EOF
    fi
fi
