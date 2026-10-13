#!/usr/bin/env bash
#
# One-time setup for the NL2SQL agent.
#
# Brings up the whole stack in one call:
#
#   nl2sql-postgres    the retail dataset, already inside the image
#   nl2sql-vectordb    pgvector holding the embedded knowledge base
#   nl2sql-chunkdb     the golden question/SQL pairs and their BM25 statistics
#   nl2sql-stores      feedback, corrections, completions and the SQL
#                      snippets, four databases in one server
#   agent              the agent image, run on demand
#
# It pulls each image -- and the pages' image, MLflow and the desktop
# client's when their flags ask for them -- writes every password into
# secrets/, starts the databases, has the dbprep service prepare them, writes
# a .env so plain compose commands pick all of that up, checks that the chat and
# embedding models are reachable, and finally proves the agent container can
# actually retrieve from the knowledge base. When it finishes you can just run:
#
#     docker compose run --rm agent "your question"
#
set -euo pipefail

cd "$(dirname "$0")"

POSTGRES_IMAGE="mcfaddja/nl2sql-retail-postgres"
# v1_2 since 6.1: no password baked in, TLS on, and an entrypoint that sets
# the owner's and the reader's from .env on every start.
POSTGRES_TAG="v1_2"
AGENT_IMAGE="mcfaddja/nl2sql-agent"
AGENT_TAG="v7_0"
REVIEW_IMAGE="mcfaddja/nl2sql-review"
REVIEW_TAG="v7_0"
# Every page -- the web interface, and the review, curation, console and
# directory pages -- and MLflow's front door: one image since 6.3 (V6-37),
# each container told which page it serves. The console behind its page runs
# from the agent image above, started with a different command.
PROXY_IMAGE="mcfaddja/nl2sql-proxy"
PROXY_TAG="v7_0"
# MLflow, where the agent's runs are traced: its server and the Postgres it
# keeps traces in, both published with the release. --mlflow adds them.
MLFLOW_IMAGE="mcfaddja/nl2sql-mlflow"
MLFLOW_TAG="v7_0"
MLFLOW_DB_IMAGE="mcfaddja/nl2sql-mlflowdb"
MLFLOW_DB_TAG="v7_0"
# The desktop client's jar, one published tag per JavaFX platform. Nothing is
# pulled here: launch.sh --desktop is what fetches it, and only for the
# platform this machine turns out to be. Pinning it costs two lines of .env
# and saves everyone who asks for it a Maven build.
DESKTOP_IMAGE="mcfaddja/nl2sql-desktop-build"
DESKTOP_TAG="v7_0"
LDAP_IMAGE="mcfaddja/nl2sql-ldap"
LDAP_TAG="v7_0"
AUTH_IMAGE="mcfaddja/nl2sql-auth"
AUTH_TAG="v7_0"
# The release this checkout ships: the agent's tag before any flag changes
# it. Written into .env, so start.sh can tell a tag someone chose for this
# checkout from one an older checkout left behind.
RELEASE="$AGENT_TAG"
VECTOR_IMAGE="mcfaddja/nl2sql-rag-vectordb"
VECTOR_TAG="v3_2"
CONTEXT_IMAGE="mcfaddja/nl2sql-rag-chunkdb"
CONTEXT_TAG="v3_2"
OLLAMA_URL=""
OLLAMA_MODEL=""
EMBED_URL=""
EMBED_MODEL_NAME=""
POSTGRES_PORT=""
BUILD_POSTGRES=0
BUILD_AGENT=0
BUILD_ALL=0
# The pages are opt-in: most people ask questions from a terminal, and pulling
# an image for a container that is never started is a download nobody asked
# for. `./launch.sh --gui` still works without this -- it builds the image
# from this checkout instead, which is slower but needs no registry. Sign-in,
# on by default, has a page of its own (the directory's), so the pages' image
# is pulled with it.
WITH_GUI=0
WITH_REVIEW=0
WITH_CURATE=0
WITH_CONSOLE=0
WITH_MLFLOW=0
WITH_DESKTOP=0
WITH_RAG=1
WITH_SIGNIN=1
WITH_TOKENS=0
VERIFY=1
RESET=0

usage() {
    cat <<'EOF'
Usage: ./setup.sh [options]

  -t, --tag TAG          Postgres image tag to pull (default: v1_2)
  -i, --image NAME       Postgres image repository
                         (default: mcfaddja/nl2sql-retail-postgres)
  -u, --ollama-url URL   Ollama host serving the chat model
  -m, --model NAME       Ollama model the agent should use
  -p, --port PORT        Host port to publish Postgres on (default: 5432)
      --agent-image NAME Agent image repository
                         (default: mcfaddja/nl2sql-agent)
      --agent-tag TAG    Agent image tag to pull (default: v7_0)
      --build-agent      Build the agent image from source instead of pulling
      --gui              Also pull and pin the pages' image, so ./launch.sh
                         --gui starts the web interface instead of building it
      --review           Also pull and pin the feedback review service and
                         the pages' image (implies --gui)
      --review-image N   Review service image (default: mcfaddja/nl2sql-review)
      --review-tag TAG   Review service image tag (default: v7_0)
      --curate           Also pull and pin the pages' image for the curation
                         interface, where SQL snippets, golden pairs and fixes
                         are written directly, each run against the retail
                         database first
      --console          Also pull and pin the pages' image for the SQL
                         console's interface, where the retail database is
                         queried as the agent sees it (the console itself
                         runs from the agent image)
      --proxy-image N    The pages' image: every page and MLflow's front door
                         (default: mcfaddja/nl2sql-proxy)
      --proxy-tag TAG    The pages' image tag (default: v7_0)
      --mlflow           Also pull and pin MLflow -- its server and the
                         Postgres it keeps traces in -- so ./launch.sh
                         --mlflow starts it instead of building it here
      --mlflow-image N   MLflow server image (default: mcfaddja/nl2sql-mlflow)
      --mlflow-tag TAG   MLflow server image tag (default: v7_0)
      --mlflow-db-image N    MLflow store image
                         (default: mcfaddja/nl2sql-mlflowdb)
      --mlflow-db-tag TAG    MLflow store image tag (default: v7_0)
      --desktop          Also pull and pin the desktop client's jar, for this
                         machine's platform, so ./launch.sh --desktop takes it
                         from the image instead of building it here
      --desktop-image N  Desktop client image
                         (default: mcfaddja/nl2sql-desktop-build)
      --desktop-tag TAG  Desktop client image tag (default: v7_0). The JavaFX
                         platform is appended to it
      --vector-image N   Vector store image (default: mcfaddja/nl2sql-rag-vectordb)
      --vector-tag TAG   Vector store image tag (default: v3_2)
      --context-image N  Context store image (default: mcfaddja/nl2sql-rag-chunkdb)
      --context-tag TAG  Context store image tag (default: v3_2)
      --embed-url URL    Ollama host serving the embedding model
                         (default: http://host.docker.internal:11434, i.e. the
                         Ollama on this machine)
      --embed-model NAME Embedding model for retrieval (default: bge-m3)
      --no-rag           Skip the knowledge base; the agent answers from the
                         schema alone, like v1
      --no-auth          Turn sign-in off, in .env, for every later start: no
                         directory, no auth service, every page and port open
                         to whoever can reach it. Without it, the directory,
                         the auth service and the directory page are pulled
                         and pinned, and their passwords generated into
                         secrets/
      --tokens           Also generate the three service tokens, for scripts and
                         other services: secrets/api_token, review_token and
                         console_token.
                         Kept once made; without this none is set, and
                         signing in is the only way in
      --no-verify        Skip the end-of-setup retrieval check
      --build            Build the Postgres image locally instead of pulling
                         it (regenerates the dataset; takes a few minutes)
      --build-all        Build every image this checkout has a Dockerfile for
                         -- the dataset, the agent, the pages' image, the
                         review service, the directory, the auth service,
                         MLflow's two and the desktop client -- tagged local,
                         and pin those instead of pulling them (the two
                         knowledge-base stores are still pulled). How the
                         acceptance tier runs, and how to try a change as the
                         whole stack before publishing
      --reset            Delete the existing database volume first, so the
                         dataset comes from the image. DESTROYS local changes.
  -h, --help             Show this message
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -t|--tag) POSTGRES_TAG="$2"; shift 2 ;;
        -i|--image) POSTGRES_IMAGE="$2"; shift 2 ;;
        -u|--ollama-url) OLLAMA_URL="$2"; shift 2 ;;
        -m|--model) OLLAMA_MODEL="$2"; shift 2 ;;
        -p|--port) POSTGRES_PORT="$2"; shift 2 ;;
        --agent-image) AGENT_IMAGE="$2"; shift 2 ;;
        --agent-tag) AGENT_TAG="$2"; shift 2 ;;
        --build-agent) BUILD_AGENT=1; shift ;;
        --gui) WITH_GUI=1; shift ;;
        # The review interface is no use without the web interface in front
        # of it -- feedback has to be given before it can be reviewed -- so
        # asking for one pulls both.
        --review) WITH_REVIEW=1; WITH_GUI=1; shift ;;
        --review-image) REVIEW_IMAGE="$2"; WITH_REVIEW=1; WITH_GUI=1; shift 2 ;;
        --review-tag) REVIEW_TAG="$2"; WITH_REVIEW=1; WITH_GUI=1; shift 2 ;;
        # Its own page, not the review interface's: a curator writing
        # snippets needs neither the queue nor the page people vote in.
        --curate) WITH_CURATE=1; shift ;;
        # Not --gui as well: the console is for troubleshooting the agent's
        # answers, and those come from a terminal as often as from a page.
        --console) WITH_CONSOLE=1; shift ;;
        --proxy-image) PROXY_IMAGE="$2"; WITH_GUI=1; shift 2 ;;
        --proxy-tag) PROXY_TAG="$2"; WITH_GUI=1; shift 2 ;;
        # Both halves together: the server is no use without its store.
        --mlflow) WITH_MLFLOW=1; shift ;;
        --mlflow-image) MLFLOW_IMAGE="$2"; WITH_MLFLOW=1; shift 2 ;;
        --mlflow-tag) MLFLOW_TAG="$2"; WITH_MLFLOW=1; shift 2 ;;
        --mlflow-db-image) MLFLOW_DB_IMAGE="$2"; WITH_MLFLOW=1; shift 2 ;;
        --mlflow-db-tag) MLFLOW_DB_TAG="$2"; WITH_MLFLOW=1; shift 2 ;;
        --desktop) WITH_DESKTOP=1; shift ;;
        --desktop-image) DESKTOP_IMAGE="$2"; WITH_DESKTOP=1; shift 2 ;;
        --desktop-tag) DESKTOP_TAG="$2"; WITH_DESKTOP=1; shift 2 ;;
        --vector-image) VECTOR_IMAGE="$2"; shift 2 ;;
        --vector-tag) VECTOR_TAG="$2"; shift 2 ;;
        --context-image) CONTEXT_IMAGE="$2"; shift 2 ;;
        --context-tag) CONTEXT_TAG="$2"; shift 2 ;;
        --embed-url) EMBED_URL="$2"; shift 2 ;;
        --embed-model) EMBED_MODEL_NAME="$2"; shift 2 ;;
        --no-rag) WITH_RAG=0; shift ;;
        --no-auth) WITH_SIGNIN=0; shift ;;
        --tokens) WITH_TOKENS=1; shift ;;
        --no-verify) VERIFY=0; shift ;;
        --build) BUILD_POSTGRES=1; shift ;;
        --build-all) BUILD_ALL=1; BUILD_POSTGRES=1; shift ;;
        --reset) RESET=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown option: $1" >&2; usage >&2; exit 1 ;;
    esac
done

# With --build-all every image of ours is this checkout's, under its usual
# name and the tag `local`, which no registry has: compose builds them below
# instead of anything being pulled.
if [[ $BUILD_ALL -eq 1 ]]; then
    for local_image in AGENT:nl2sql-agent REVIEW:nl2sql-review PROXY:nl2sql-proxy \
        MLFLOW:nl2sql-mlflow MLFLOW_DB:nl2sql-mlflowdb DESKTOP:nl2sql-desktop-build \
        LDAP:nl2sql-ldap AUTH:nl2sql-auth; do
        printf -v "${local_image%%:*}_IMAGE" '%s' "${local_image#*:}"
        printf -v "${local_image%%:*}_TAG" '%s' local
    done
fi

step() { printf '\n==> %s\n' "$1"; }
info() { printf '    %s\n' "$1"; }
warn() { printf '    WARNING: %s\n' "$1" >&2; }
die() { printf '\nERROR: %s\n' "$1" >&2; exit 1; }

# What compose hands the agent: the shell's value, then .env's.
compose_env() {  # compose_env KEY DEFAULT
    local value="${!1:-}"
    if [[ -z "$value" && -f .env ]]; then
        value=$(grep -E "^$1=" .env | tail -1 | cut -d= -f2-)
    fi
    printf '%s' "${value:-$2}"
}

# --- Prerequisites ---------------------------------------------------------
step "Checking prerequisites"
command -v docker >/dev/null 2>&1 || die "docker is not installed or not on PATH."
docker info >/dev/null 2>&1 || die "the Docker daemon is not running. Start Docker and retry."
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required ('docker compose')."
info "Docker $(docker version --format '{{.Server.Version}}') with Compose $(docker compose version --short)"

# --- Existing data ---------------------------------------------------------
# The stack's name is `nl2sql` unless NL2SQL_INSTANCE -- in the shell or in
# .env, where compose reads it -- gives it another, so that a second stack
# (the acceptance tier's) can run beside this one. Its containers and its
# retail volume are named after it.
instance() {  # instance -- this stack's name
    local name="${NL2SQL_INSTANCE:-}"
    if [[ -z "$name" && -f .env ]]; then
        name=$(grep -E '^NL2SQL_INSTANCE=' .env | tail -1 | cut -d= -f2- || true)
    fi
    printf '%s' "${name:-nl2sql}"
}
VOLUME_NAME="$(instance)-pgdata"
if [[ $RESET -eq 1 ]]; then
    step "Removing the existing database volume"
    docker compose down -v >/dev/null 2>&1 || true
    info "volume $VOLUME_NAME deleted; the dataset will come from the image"
elif docker volume inspect "$VOLUME_NAME" >/dev/null 2>&1; then
    step "Existing database volume found"
    warn "volume $VOLUME_NAME already exists and takes precedence over the image,"
    warn "so its current contents are what you will query. Re-run with --reset"
    warn "to discard it and start from the image's dataset."
fi

# --- Postgres image --------------------------------------------------------
if [[ $BUILD_POSTGRES -eq 1 ]]; then
    step "Building the Postgres image locally (this regenerates the dataset)"
    POSTGRES_IMAGE="nl2sql-retail-postgres"
    POSTGRES_TAG="latest"
    IMAGE_NAME="$POSTGRES_IMAGE" IMAGE_TAG="$POSTGRES_TAG" docker compose build postgres
else
    step "Pulling $POSTGRES_IMAGE:$POSTGRES_TAG (Postgres with the test dataset)"
    info "about 290 MB to download, roughly 1.4 GB on disk"
    docker pull "$POSTGRES_IMAGE:$POSTGRES_TAG" ||
        die "could not pull $POSTGRES_IMAGE:$POSTGRES_TAG. Check the tag and your network."
fi

# --- Retrieval images ------------------------------------------------------
if [[ $WITH_RAG -eq 1 ]]; then
    step "Pulling $CONTEXT_IMAGE:$CONTEXT_TAG (context store: golden pairs and BM25 statistics)"
    if ! docker pull "$CONTEXT_IMAGE:$CONTEXT_TAG"; then
        die "could not pull $CONTEXT_IMAGE:$CONTEXT_TAG.
       If the repository is private, run 'docker login' first, or make it
       public on Docker Hub. To set up without retrieval, re-run with --no-rag."
    fi

    # Same story as the vector store below: the RAG pipeline may have left a
    # standalone container on this port and volume.
    if [[ -n "$(docker ps -q --filter name='^nl2sql-rag-chunkdb$')" ]]; then
        info "stopping the standalone nl2sql-rag-chunkdb container (compose takes over; data is kept)"
        docker stop nl2sql-rag-chunkdb >/dev/null
    fi

    step "Pulling $VECTOR_IMAGE:$VECTOR_TAG (pgvector with the knowledge base and golden-pair vectors)"
    if ! docker pull "$VECTOR_IMAGE:$VECTOR_TAG"; then
        die "could not pull $VECTOR_IMAGE:$VECTOR_TAG.
       If the repository is private, run 'docker login' first, or make it
       public on Docker Hub. To set up without retrieval, re-run with --no-rag."
    fi

    # The RAG pipeline may have left a standalone container bound to the same
    # port and data volume; compose manages it from here on.
    if [[ -n "$(docker ps -q --filter name='^nl2sql-rag-vectordb$')" ]]; then
        info "stopping the standalone nl2sql-rag-vectordb container (compose takes over; data is kept)"
        docker stop nl2sql-rag-vectordb >/dev/null
    fi
fi

# --- Environment file ------------------------------------------------------
# --- Carry over what the last run chose ------------------------------------
# .env is rewritten from scratch every time, and most of what goes into it is
# written only when a flag gave it a value. Without this, re-running the
# script to pick up a new image -- `./setup.sh --review` on a machine that was
# first set up with `--ollama-url` -- would produce a .env with no Ollama host
# in it, and the agent would start looking for a model on localhost.
#
# Nothing here overrides a flag: each value is taken only when the flag did
# not supply one. The old file is still moved to .env.bak below, so this is
# about not needing it rather than about being able to recover.
env_value() {  # env_value KEY -- what the existing .env says, or nothing
    [[ -f .env ]] || return 0
    grep -E "^$1=" .env | tail -1 | cut -d= -f2-
}

# Written as `if` rather than `[[ ... ]] && ...`: this script runs under
# `set -e`, where an AND-list whose test is false is a failing statement and
# takes the whole script down with it.
carry() {  # carry VARNAME ENV_KEY -- fill VARNAME from .env if it is empty
    local current="${!1}"
    if [[ -z "$current" ]]; then
        printf -v "$1" '%s' "$(env_value "$2")"
    fi
}

carry OLLAMA_URL OLLAMA_BASE_URL
carry OLLAMA_MODEL OLLAMA_MODEL
carry EMBED_URL EMBED_BASE_URL
carry EMBED_MODEL_NAME EMBED_MODEL
carry POSTGRES_PORT POSTGRES_PORT

# The optional image sets are pinned only when asked for, so "was it asked
# for last time" is the same question as "is it pinned in .env". A .env from
# before 6.3 pinned a page's image of its own; any of them is the pages' now.
for page_pin in PROXY_IMAGE_NAME GUI_IMAGE_NAME REVIEW_GUI_IMAGE_NAME CURATE_GUI_IMAGE_NAME CONSOLE_GUI_IMAGE_NAME; do
    if [[ $WITH_GUI -eq 0 && -n "$(env_value "$page_pin")" ]]; then
        WITH_GUI=1
    fi
done
# The review service's image: for its own pages, and -- with retrieval on --
# because it carries the loader that fills the snippet store from its
# document, which launch.sh runs on start and this script at its end.
WITH_REVIEW_SERVICE=0
if [[ $WITH_REVIEW -eq 1 || $WITH_CURATE -eq 1 || $WITH_RAG -eq 1 ]]; then
    WITH_REVIEW_SERVICE=1
fi
if [[ $WITH_MLFLOW -eq 0 && -n "$(env_value MLFLOW_IMAGE_NAME)" ]]; then
    WITH_MLFLOW=1
fi
if [[ $WITH_DESKTOP -eq 0 && -n "$(env_value DESKTOP_IMAGE_NAME)" ]]; then
    WITH_DESKTOP=1
fi
# Sign-in turned off before stays off: the line is kept from the old file
# below, and nothing it would need is pulled.
case "$(env_value AUTH_ENABLED)" in
    0|false|no|off|FALSE|NO|OFF) WITH_SIGNIN=0 ;;
esac
# The pages' image, for any page: the ones asked for, the directory's with
# sign-in, MLflow's front door with MLflow.
WITH_PROXY=0
if [[ $WITH_GUI -eq 1 || $WITH_REVIEW -eq 1 || $WITH_CURATE -eq 1 || $WITH_CONSOLE -eq 1 ||
      $WITH_MLFLOW -eq 1 || $WITH_SIGNIN -eq 1 ]]; then
    WITH_PROXY=1
fi

# --- Passwords and tokens ------------------------------------------------------
# Every one a file in secrets/ (6.3, V6-38), which compose mounts into the
# services that need it and nowhere else -- never an environment variable,
# which `docker inspect` shows to anyone who can run it. Generated once and
# never replaced: a store, the directory and the database keep the ones they
# were given, so a new one here would be a password nothing accepts. A .env
# from before 6.3 held them; each is moved from there once, and the line
# leaves .env.
#
#   ldap_admin_password     the first person, admin, in every group -- what
#                           signs in to the directory page the first time
#   ldap_service_password   the auth service's account in the directory
#   auth_rolesync_password  the role that keeps the database's people in
#                           step with the directory's
#   and every store's owner and the roles that read them, and the runtime
#   stores' superuser. The three service tokens only when asked for
#   (--tokens) or made before; a replica's bind password is the one you give
#   it, in secrets/ldap_upstream_bind_password. Both are empty files otherwise.
#
# Hex, so each can sit in a URL and a shell line as it is. 24 bytes from
# /dev/urandom; `od -N` reads exactly that many, so nothing is cut short.
SECRETS_DIR="secrets"
SECRET_NAMES=(ldap_admin_password ldap_service_password auth_rolesync_password
    postgres_password postgres_reader_password context_db_password vector_db_password
    stores_db_password snippets_db_password snippets_reader_password feedback_db_password
    feedback_writer_password corrections_db_password completions_db_password mlflow_db_password)
TOKEN_NAMES=(api_token review_token console_token)
GIVEN_NAMES=(ldap_upstream_bind_password)

new_secret() {
    od -An -N24 -tx1 /dev/urandom | tr -d ' \n'
}

# The directory is its owner's alone; the files in it are readable, because
# the account each container runs as -- 10001, nginx's 101, postgres's 999 --
# is not the person who owns this checkout, and on Linux a bind-mounted file
# keeps the mode it has here.
ensure_secret_files() {  # ensure_secret_files -- print how many were made new
    local name key value made=0
    mkdir -p "$SECRETS_DIR"
    chmod 700 "$SECRETS_DIR"
    for name in "${SECRET_NAMES[@]}" "${TOKEN_NAMES[@]}" "${GIVEN_NAMES[@]}"; do
        [[ -s "$SECRETS_DIR/$name" ]] && continue
        # Its key in a .env from before 6.3: the shell's first, as compose read it.
        key=$(printf '%s' "$name" | tr '[:lower:]' '[:upper:]')
        value="${!key:-}"
        if [[ -z "$value" ]]; then
            value="$(env_value "$key")"
        fi
        if [[ -z "$value" ]]; then
            case " ${SECRET_NAMES[*]} " in
                *" $name "*) value="$(new_secret)"; made=$((made + 1)) ;;
            esac
        fi
        if [[ -z "$value" && $WITH_TOKENS -eq 1 ]]; then
            case " ${TOKEN_NAMES[*]} " in
                *" $name "*) value="$(new_secret)"; made=$((made + 1)) ;;
            esac
        fi
        (umask 022 && printf '%s' "$value" > "$SECRETS_DIR/$name")
    done
    printf '%s' "$made"
}

made=$(ensure_secret_files)
if [[ "$made" -gt 0 ]]; then
    step "Generated $made password(s) and token(s) into $SECRETS_DIR/, which only you can open"
fi

step "Writing .env"
had_env=0
if [[ -f .env ]]; then
    mv .env .env.bak
    had_env=1
    info "existing .env moved to .env.bak"
fi
{
    echo "# Written by setup.sh -- delete this file to go back to building locally."
    # Which release's setup.sh wrote this file. start.sh brings a file an
    # older checkout wrote up to date, and leaves alone a tag chosen since.
    echo "SETUP_RELEASE=$RELEASE"
    echo "IMAGE_NAME=$POSTGRES_IMAGE"
    echo "IMAGE_TAG=$POSTGRES_TAG"
    echo "AGENT_IMAGE_NAME=$AGENT_IMAGE"
    echo "AGENT_IMAGE_TAG=$AGENT_TAG"
    # Pinned only when it was asked for, so compose does not go looking for
    # an image nobody wanted -- except that the review service is wanted
    # whenever retrieval is on, for its loader. Left unset, compose falls
    # back to a local tag and builds it from this checkout on first start.
    if [[ $WITH_REVIEW_SERVICE -eq 1 ]]; then
        echo "REVIEW_IMAGE_NAME=$REVIEW_IMAGE"
        echo "REVIEW_IMAGE_TAG=$REVIEW_TAG"
    fi
    # The same again for MLflow's two. Unpinned, compose builds both here,
    # which is a pull of MLflow's and Postgres's own images.
    if [[ $WITH_MLFLOW -eq 1 ]]; then
        echo "MLFLOW_IMAGE_NAME=$MLFLOW_IMAGE"
        echo "MLFLOW_IMAGE_TAG=$MLFLOW_TAG"
        echo "MLFLOW_DB_IMAGE_NAME=$MLFLOW_DB_IMAGE"
        echo "MLFLOW_DB_IMAGE_TAG=$MLFLOW_DB_TAG"
    fi
    # Same reasoning again. Unpinned, compose resolves the desktop service to
    # a local tag with nowhere to be pulled from, and launch.sh builds the
    # jar here instead.
    if [[ $WITH_DESKTOP -eq 1 ]]; then
        echo "DESKTOP_IMAGE_NAME=$DESKTOP_IMAGE"
        echo "DESKTOP_IMAGE_TAG=$DESKTOP_TAG"
    fi
    # Sign-in's three: the directory, the auth service and the directory
    # page. Pinned whenever sign-in is on, which is by default, because
    # every page and port waits on them. MLflow's front door goes with
    # MLflow.
    if [[ $WITH_SIGNIN -eq 1 ]]; then
        echo "LDAP_IMAGE_NAME=$LDAP_IMAGE"
        echo "LDAP_IMAGE_TAG=$LDAP_TAG"
        echo "AUTH_IMAGE_NAME=$AUTH_IMAGE"
        echo "AUTH_IMAGE_TAG=$AUTH_TAG"
    else
        echo "AUTH_ENABLED=false"
    fi
    # Every page's image, and MLflow's front door's: one since 6.3.
    if [[ $WITH_PROXY -eq 1 ]]; then
        echo "PROXY_IMAGE_NAME=$PROXY_IMAGE"
        echo "PROXY_IMAGE_TAG=$PROXY_TAG"
    fi
    echo "VECTOR_IMAGE_NAME=$VECTOR_IMAGE"
    echo "VECTOR_IMAGE_TAG=$VECTOR_TAG"
    echo "CONTEXT_IMAGE_NAME=$CONTEXT_IMAGE"
    echo "CONTEXT_IMAGE_TAG=$CONTEXT_TAG"
    echo "RAG_ENABLED=$([[ $WITH_RAG -eq 1 ]] && echo true || echo false)"
    # Where the agent sends its traces: the mlflow service, which
    # `./launch.sh --mlflow` starts. Harmless when it is not up, the same
    # way: a run that finds no server is answered untraced, and the next is
    # traced once one answers. A previous .env's own line is written back
    # as it was -- another server, or nothing at all, which is how tracing
    # is turned off -- because that line is a choice, not a default.
    if [[ $had_env -eq 1 ]] && grep -q '^MLFLOW_TRACKING_URI=' .env.bak; then
        grep '^MLFLOW_TRACKING_URI=' .env.bak | tail -1
    else
        echo "MLFLOW_TRACKING_URI=http://nl2sql-mlflow:5000"
    fi
    if [[ -n "$OLLAMA_URL" ]]; then echo "OLLAMA_BASE_URL=$OLLAMA_URL"; fi
    if [[ -n "$OLLAMA_MODEL" ]]; then echo "OLLAMA_MODEL=$OLLAMA_MODEL"; fi
    if [[ -n "$EMBED_URL" ]]; then echo "EMBED_BASE_URL=$EMBED_URL"; fi
    if [[ -n "$EMBED_MODEL_NAME" ]]; then echo "EMBED_MODEL=$EMBED_MODEL_NAME"; fi
    if [[ -n "$POSTGRES_PORT" ]]; then echo "POSTGRES_PORT=$POSTGRES_PORT"; fi
} > .env
# No password is in either since 6.3, but they are a map of what runs where:
# their owner's alone, as they always were.
chmod 600 .env
if [[ $had_env -eq 1 ]]; then
    chmod 600 .env.bak
fi

# Everything else the previous file held -- a port, an API token, a setting
# added by hand -- is kept as it was. This script writes the keys above and
# has no opinion about the rest, and dropping them would make re-running it,
# which start.sh now does when a checkout ships newer images, quietly lose
# whatever someone had added since. Only from the file just moved aside: an
# older .env.bak is a backup, not a source.
# What 6.3 no longer keeps in .env: every password and token, which are in
# secrets/ now, the API's feedback URL, which named one and compose now
# writes itself, and the six page images', which are one image.
RETIRED_KEYS=(LDAP_ADMIN_PASSWORD LDAP_SERVICE_PASSWORD AUTH_ROLESYNC_PASSWORD POSTGRES_PASSWORD
    POSTGRES_READER_PASSWORD CONTEXT_DB_PASSWORD VECTOR_DB_PASSWORD SNIPPETS_DB_PASSWORD
    SNIPPETS_READER_PASSWORD FEEDBACK_DB_PASSWORD FEEDBACK_WRITER_PASSWORD CORRECTIONS_DB_PASSWORD
    COMPLETIONS_DB_PASSWORD MLFLOW_DB_PASSWORD STORES_DB_PASSWORD LDAP_UPSTREAM_BIND_PASSWORD
    API_TOKEN REVIEW_TOKEN CONSOLE_TOKEN API_FEEDBACK_DB_URL
    GUI_IMAGE_NAME GUI_IMAGE_TAG REVIEW_GUI_IMAGE_NAME REVIEW_GUI_IMAGE_TAG
    CURATE_GUI_IMAGE_NAME CURATE_GUI_IMAGE_TAG CONSOLE_GUI_IMAGE_NAME CONSOLE_GUI_IMAGE_TAG
    DIRECTORY_GUI_IMAGE_NAME DIRECTORY_GUI_IMAGE_TAG MLFLOW_PROXY_IMAGE_NAME MLFLOW_PROXY_IMAGE_TAG)

retired() {  # retired KEY -- one of those
    local key
    for key in "${RETIRED_KEYS[@]}"; do
        [[ "$key" == "$1" ]] && return 0
    done
    return 1
}

keep_settings() {  # keep_settings -- append each KEY=value on stdin .env lacks; print how many
    local line kept=0
    while IFS= read -r line; do
        if grep -q "^${line%%=*}=" .env; then continue; fi
        if retired "${line%%=*}"; then continue; fi
        if [[ $kept -eq 0 ]]; then echo "# Kept from the previous .env" >> .env; fi
        printf '%s\n' "$line" >> .env
        kept=$((kept + 1))
    done
    printf '%s' "$kept"
}

# The backup keeps what the last .env said, less its secrets: every one of
# them is in secrets/, and a second copy of each, in a file nothing reads, is
# one more place to take them from.
scrub_backup() {
    local line key work
    work="$(mktemp .env.bak.XXXXXX)"
    while IFS= read -r line || [[ -n "$line" ]]; do
        key="${line%%=*}"
        if [[ "$line" == *=* && "$key" =~ (PASSWORD|TOKEN|SECRET) ]]; then
            printf '# %s: in secrets/, not kept here\n' "$key"
        else
            printf '%s\n' "$line"
        fi
    done < .env.bak > "$work"
    chmod 600 "$work"
    mv "$work" .env.bak
}

if [[ $had_env -eq 1 ]]; then
    # Settings only: comments and blank lines are this script's to write.
    # grep also ends the last line, which an editor may have left unended.
    kept=$( (grep -E '^[A-Za-z_][A-Za-z0-9_]*=' .env.bak || true) | keep_settings)
    if [[ $kept -gt 0 ]]; then
        info "kept $kept other setting(s) from the previous .env"
    fi
    scrub_backup
fi
info "compose will use $POSTGRES_IMAGE:$POSTGRES_TAG"

# --- Agent image -----------------------------------------------------------
if [[ $BUILD_ALL -eq 1 ]]; then
    : # built with everything else below
elif [[ $BUILD_AGENT -eq 1 ]]; then
    step "Building the agent image from source"
    docker compose build agent
else
    step "Pulling $AGENT_IMAGE:$AGENT_TAG (the RAG agent)"
    if ! docker pull "$AGENT_IMAGE:$AGENT_TAG"; then
        warn "could not pull $AGENT_IMAGE:$AGENT_TAG (private repo, or not logged in);"
        warn "building it from source instead, which needs no registry access."
        docker compose build agent
    fi
fi

# --- The pages' image ----------------------------------------------------------
# Pulled rather than built when asked for, the same way as the agent. Compose
# builds a service that has a `build:` section whenever its image is missing,
# so pulling it here is what makes the published image the one that runs.
# One image for every page and MLflow's front door (V6-37).
if [[ $BUILD_ALL -eq 0 && $WITH_PROXY -eq 1 ]]; then
    step "Pulling $PROXY_IMAGE:$PROXY_TAG (every page, and MLflow's front door)"
    if ! docker pull "$PROXY_IMAGE:$PROXY_TAG"; then
        warn "could not pull $PROXY_IMAGE:$PROXY_TAG (private repo, or not logged in);"
        warn "./launch.sh will build it from source the first time a page starts."
    fi
fi

# The review service: a Python service that can rewrite the golden question
# set and the snippets, and what loads the snippet store.
if [[ $BUILD_ALL -eq 0 && $WITH_REVIEW_SERVICE -eq 1 ]]; then
    step "Pulling $REVIEW_IMAGE:$REVIEW_TAG (the review service, which loads the SQL snippets)"
    if ! docker pull "$REVIEW_IMAGE:$REVIEW_TAG"; then
        warn "could not pull $REVIEW_IMAGE:$REVIEW_TAG (private repo, or not logged in);"
        warn "compose will build it from source the first time it is needed."
    fi
fi

# MLflow's two, the server and its store: pulled together, as they run.
if [[ $BUILD_ALL -eq 0 && $WITH_MLFLOW -eq 1 ]]; then
    for pair in "$MLFLOW_IMAGE:$MLFLOW_TAG" "$MLFLOW_DB_IMAGE:$MLFLOW_DB_TAG"; do
        step "Pulling $pair (MLflow)"
        if ! docker pull "$pair"; then
            warn "could not pull $pair (private repo, or not logged in);"
            warn "./launch.sh --mlflow will build it from source instead."
        fi
    done
fi

# The desktop client, whose image is tagged by JavaFX platform rather than by
# architecture: it carries a jar, and a jar carries native code for the
# machine it will draw on. Only this machine's is pulled -- the other four
# are 33 MB each of no use here.
javafx_platform() {
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

# Sign-in's images: the directory and the auth service. Its page is the
# pages' image, pulled above.
if [[ $BUILD_ALL -eq 0 && $WITH_SIGNIN -eq 1 ]]; then
    for pair in "$LDAP_IMAGE:$LDAP_TAG" "$AUTH_IMAGE:$AUTH_TAG"; do
        step "Pulling $pair (sign-in)"
        if ! docker pull "$pair"; then
            warn "could not pull $pair (private repo, or not logged in);"
            warn "./launch.sh will build it from source the first time sign-in starts."
        fi
    done
fi

if [[ $BUILD_ALL -eq 0 && $WITH_DESKTOP -eq 1 ]]; then
    desktop_pair="$DESKTOP_IMAGE:$DESKTOP_TAG-$(javafx_platform)"
    step "Pulling $desktop_pair (the desktop client)"
    if ! docker pull "$desktop_pair"; then
        warn "could not pull $desktop_pair (private repo, not logged in, or no"
        warn "tag for this platform); ./launch.sh --desktop will build it instead."
    fi
fi

# Everything this run pins, built from this checkout: one build, which
# compose runs in parallel and its cache makes quick the second time.
if [[ $BUILD_ALL -eq 1 ]]; then
    step "Building every image from this checkout (--build-all)"
    info "tagged local and pinned in .env; nothing of ours is pulled"
    build_profiles=(--profile api)
    # The pages' image, once, through the one page that names it: the others
    # are the same image and would only build it again.
    if [[ $WITH_PROXY -eq 1 ]]; then build_profiles+=(--profile gui); fi
    if [[ $WITH_REVIEW_SERVICE -eq 1 ]]; then build_profiles+=(--profile review); fi
    if [[ $WITH_MLFLOW -eq 1 ]]; then build_profiles+=(--profile mlflow); fi
    if [[ $WITH_SIGNIN -eq 1 ]]; then build_profiles+=(--profile auth); fi
    if [[ $WITH_DESKTOP -eq 1 ]]; then build_profiles+=(--profile desktop); fi
    JAVAFX_PLATFORM="$(javafx_platform)" docker compose "${build_profiles[@]}" build ||
        die "an image did not build from this checkout. What compose said is above."
fi

# --- Start the databases ---------------------------------------------------
# The retail database, the runtime stores and, with retrieval, the two
# knowledge stores -- then the dbprep service, which makes each what .env and
# secrets/ say over its own socket (V6-41): the agent's reader, sign-in's
# roles and its pg_hba lines, the stores' databases and owners, every
# password. Nothing here runs SQL itself.
# A stack set up before 6.3 still runs the four stores' own containers, the
# feedback store's on the port the runtime stores publish now. Each is stopped
# cleanly and removed, its volume kept: launch.sh moves what it holds into the
# runtime stores on its next start.
retired=()
for store in feedbackdb correctionsdb completionsdb snippetsdb; do
    docker container inspect "$(instance)-$store" >/dev/null 2>&1 || continue
    docker stop -t 60 "$(instance)-$store" >/dev/null 2>&1 || true
    docker rm "$(instance)-$store" >/dev/null 2>&1 || true
    retired+=("$store")
done
if [[ ${#retired[@]} -gt 0 ]]; then
    step "Retiring the stores' containers from before 6.3: ${retired[*]}"
    info "stopped and removed; their volumes are kept, and launch.sh moves what they hold into the runtime stores"
fi

db_services=(postgres stores)
if [[ $WITH_RAG -eq 1 ]]; then db_services+=(vectordb chunkdb); fi
step "Starting the databases: ${db_services[*]}"
docker compose up -d "${db_services[@]}"

info "waiting for them to become healthy..."
for db_service in "${db_services[@]}"; do
    status=""
    for _ in $(seq 1 60); do
        status=$(docker inspect --format '{{.State.Health.Status}}' "$(instance)-$db_service" 2>/dev/null || echo starting)
        [[ "$status" == "healthy" ]] && break
        sleep 2
    done
    [[ "$status" == "healthy" ]] || die "$db_service did not become healthy. Check 'docker compose logs $db_service'."
done

# The one-shot's lines, as it said them: STEP, INFO and WARN are this
# script's step, info and warn; STATE is for the script to read.
relay() {  # relay < LINES
    local kind text
    while IFS=' ' read -r kind text; do
        case "$kind" in
            STEP) step "$text" ;;
            INFO) info "$text" ;;
            WARN) warn "$text" ;;
        esac
    done
}

step "Preparing the databases"
if prepared=$(docker compose run --rm --no-deps -T dbprep 2>&1); then
    printf '%s\n' "$prepared" | tr -d '\r' | relay
else
    printf '%s\n' "$prepared" | tail -5 >&2
    die "the databases could not be prepared, so nothing could use them. What it said is above."
fi

# --- The SQL snippets --------------------------------------------------------
# The one store no image ships: it is built from context_questions/
# sql_snippets.md, so it is loaded here, after the images and before the
# models are checked -- by the loader in the review service's image, as its
# account, which needs the embedding model for the meanings and loads the
# rows without it. The store's URL and password are the service's own.
if [[ $WITH_RAG -eq 1 ]]; then
    step "Loading the SQL snippets"
    if loaded=$(docker compose --profile review run --rm --no-deps -T --user 10001:10001 --entrypoint sh review -c '
        cd "$REVIEW_RAG_DIR" &&
        python 07_load_snippets.py "$REVIEW_SNIPPETS_DOCUMENT" \
            --ollama-url "$OLLAMA_URL" --model "$EMBED_MODEL"' 2>&1); then
        while IFS= read -r line; do info "$line"; done < <(printf '%s\n' "$loaded" | grep -E 'rows written|vectors ->' | sed 's/^ *//')
    else
        warn "the SQL snippets did not load completely; ./launch.sh tries again on every start. It said:"
        while IFS= read -r said; do warn "  $said"; done < <(printf '%s\n' "$loaded" | tail -3)
    fi
fi

# --- What each database holds --------------------------------------------------
reported=$(docker compose run --rm --no-deps -T dbprep python -m nl2sql_ops report 2>&1 | tr -d '\r' || true)
printf '%s\n' "$reported" | relay
retail_rows=$(printf '%s\n' "$reported" | sed -n 's/^STATE retail_rows=//p')
if [[ -z "$retail_rows" || "$retail_rows" == 0 ]]; then
    die "the database is up but the dataset is missing. Try --reset."
fi

# --- Ollama ----------------------------------------------------------------
step "Checking Ollama"

# Read back whatever compose will actually hand the agent. awk consumes the
# whole stream rather than exiting on the first match: under `set -o pipefail`
# an early exit can take the pipeline down with SIGPIPE (141) if the producer
# is still writing. `--profile agent`, because the agent is behind a profile of
# its own and `compose config` leaves out a service whose profile is not named:
# without it, the checks below read the defaults, not what .env was given.
compose_value() {
    docker compose --profile agent config 2>/dev/null |
        awk -v key="$1:" '$1 == key && !seen { print $2; seen = 1 }'
}

effective_url=$(compose_value OLLAMA_BASE_URL)
effective_model=$(compose_value OLLAMA_MODEL)
effective_url=${effective_url:-http://192.168.10.82:11434}
effective_model=${effective_model:-qwen3.8-256k}

if tags=$(curl -sf --max-time 5 "$effective_url/api/tags" 2>/dev/null); then
# Ollama reports a model the user asked for as `name:latest` when they gave no
# tag, so an exact match on the configured name reports a model that is
# present and working as missing. The embedding check below has always matched
# on the prefix for this reason; this one did not, and warned that "every
# question will fail" about a host the benchmark had just scored 15/15 against.
    if printf '%s' "$tags" | grep -q "\"$effective_model\(\"\|:\)"; then
        info "$effective_model is available at $effective_url"
    else
        warn "$effective_url is reachable but does not have $effective_model."
        warn "Pull it there, or re-run with --model <name>."
    fi
else
    warn "could not reach Ollama at $effective_url from this machine."
    warn "The agent will fail until it is reachable; re-run with --ollama-url URL."
fi

# --- Embedding model -------------------------------------------------------
if [[ $WITH_RAG -eq 1 ]]; then
    step "Checking the embedding model"
    effective_embed_url=$(compose_value EMBED_BASE_URL)
    effective_embed_model=$(compose_value EMBED_MODEL)
    effective_embed_url=${effective_embed_url:-http://host.docker.internal:11434}
    effective_embed_model=${effective_embed_model:-bge-m3}

    # host.docker.internal is how the container reaches this machine; from the
    # shell running setup.sh that same Ollama is on localhost.
    probe_url=${effective_embed_url/host.docker.internal/localhost}
    if tags=$(curl -sf --max-time 5 "$probe_url/api/tags" 2>/dev/null); then
        if printf '%s' "$tags" | grep -q "\"$effective_embed_model"; then
            info "$effective_embed_model is available at $effective_embed_url"
        else
            warn "$probe_url is reachable but does not have $effective_embed_model."
            warn "Run 'ollama pull $effective_embed_model', or re-run with --embed-model NAME."
            warn "Without it the agent still answers, but without knowledge retrieval."
        fi
    else
        warn "could not reach an Ollama at $probe_url to serve $effective_embed_model."
        warn "Retrieval will be skipped; re-run with --embed-url URL to point elsewhere."
    fi
fi

# --- End-to-end check ------------------------------------------------------
# Everything above proves each piece is up; this proves they are wired to each
# other, which is what the next command the user runs actually depends on:
# the agent container resolving the vectordb service and reaching the
# embedding host. Failing here is a warning, not an error -- the agent still
# answers without retrieval.
if [[ $VERIFY -eq 1 && $WITH_RAG -eq 1 ]]; then
    step "Checking the agent can reach the knowledge base"
    probe='
import json
from nl2sql_agent.config import Settings
from nl2sql_agent.retrieval import KnowledgeBase, build_embedder
s = Settings.from_env()
kb = KnowledgeBase(s.vector_db_url, build_embedder(s), top_k=1)
print("PROBE " + json.dumps({"chunks": len(kb.search("market share")),
                             "collections": len(kb.collections())}))
'
    probe_out=$(docker compose run --rm --entrypoint python agent -c "$probe" 2>&1 || true)
    probe_line=$(printf '%s' "$probe_out" | grep '^PROBE ' || true)
    if [[ -n "$probe_line" ]]; then
        found=$(printf '%s' "$probe_line" | sed 's/.*"chunks": *\([0-9]*\).*/\1/')
        collections=$(printf '%s' "$probe_line" | sed 's/.*"collections": *\([0-9]*\).*/\1/')
        if [[ "${found:-0}" -gt 0 ]]; then
            info "retrieval works end to end ($collections collections searched)"
        else
            warn "the agent reached the knowledge base but it returned nothing."
            warn "Queries will still run, just without retrieved context."
        fi
    else
        warn "the agent container could not retrieve from the knowledge base:"
        printf '%s\n' "$probe_out" | tail -3 >&2
        warn "The agent will still answer, but without knowledge context."
    fi
fi

# --- Done ------------------------------------------------------------------
# What is running is what was started above: the two retrieval stores only
# when the knowledge base was asked for.
cat <<EOF

==> Setup complete. Running now:

    nl2sql-postgres    the retail dataset
    nl2sql-stores      feedback, corrections, completions and SQL snippets
EOF
if [[ $WITH_RAG -eq 1 ]]; then
    cat <<EOF
    nl2sql-vectordb    the embedded knowledge base
    nl2sql-chunkdb     the golden pairs and their BM25 index
EOF
fi
cat <<EOF

    The agent runs on demand, in a container of its own:

    docker compose run --rm agent "How many stores are there?"

    docker compose run --rm agent            # interactive session
    docker compose down                      # stop the databases (data kept)

    A question that needs the knowledge base to get right:

    docker compose run --rm agent "What is our overall market share in fiscal year 2024?"

    Rather use a browser? There is a web interface:

    ./launch.sh --gui                        # https://localhost:8080

    Teaching it this database's pieces -- how two tables join, what a phrase
    filters to, how a measure is calculated? The curation interface writes
    SQL snippets, golden pairs and fixes, each run on the database first:

    ./launch.sh --curate                     # https://localhost:8083

    Working out why an answer was wrong? The SQL console runs a query the way
    the agent runs its own, and says which of its gates would have stopped it:

    ./launch.sh --console                    # https://localhost:8082

    Want to see what the agent did with a question, agent by agent and
    model call by model call? MLflow traces every one:

    ./launch.sh --mlflow                     # https://localhost:5001

    Or connect a GUI of your own: the same image serves a REST API over TLS,
    and agent/API.md is the contract a client is written against:

    ./launch.sh --api
    docker compose --profile api run --rm apitest

EOF
if [[ $WITH_SIGNIN -eq 1 ]]; then
    cat <<EOF
    Every page asks who you are. The first person is $(compose_env LDAP_ADMIN_USER admin), whose password
    is in secrets/ldap_admin_password; they add everyone else on the directory
    page, which ./launch.sh --api starts at https://localhost:8084.

EOF
else
    cat <<EOF
    Sign-in is off (AUTH_ENABLED=false in .env): every page and port is open
    to whoever can reach it.

EOF
fi
