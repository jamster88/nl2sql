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
# This is the every-time script. setup.sh is the first-time one: it pulls the
# images and writes the .env that pins them. launch.sh assumes that has already
# happened and just starts what is down, then checks that each piece actually
# holds what the agent expects -- the dataset, the knowledge base, the golden
# pairs, and both models.
#
# The distinction matters because the failures are different. Setup fails when
# an image will not pull; launch fails when a container is up but empty, when
# the chat host moved, or when the embedding model is not the one the vectors
# were built with. Those are invisible until a question is asked, and then they
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
                   turned into golden questions (implies --feedback)
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
compose_value() {
    docker compose config 2>/dev/null |
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

repair_proxy() {  # repair_proxy NAME PORT PROFILES...
    local name="$1" port="$2"
    shift 2
    proxy_reaches_api "$port" && return 0
    info "$name cannot reach the API through its proxy -- restarting it to pick up"
    info "the current certificate (the API reissues one when a service name is added)"
    docker compose "$@" restart "$name" >/dev/null 2>&1 || return 1
    await_health "nl2sql-${name/reviewgui/review-gui}" || return 1
    proxy_reaches_api "$port"
}

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
        if repair_proxy gui "$gui_port" --profile api --profile gui; then
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
        if repair_proxy reviewgui "$review_gui_port" \
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

    Verdicts given in the web interface land in the staging database and wait
    here. A reviewer reads what was asked, what the agent answered and what
    the user thought of it, writes the three fields a thumbs-up cannot carry
    -- keywords, reasoning target, expected result -- and promotes the pair.

    Promoting appends it to context_questions/translated_questions.md in this
    checkout, so it shows up in \`git diff\` like any other edit and is
    committed the same way. The previous version is kept beside it as
    translated_questions.md.bak.

    docker compose --profile feedback --profile review --profile reviewgui logs -f review
    review/README.md explains how it is put together.
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
