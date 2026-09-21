#!/usr/bin/env bash
#
# Drive the NL2SQL REST API from outside, with nothing but curl.
#
#     docker compose --profile api run --rm apitest
#     docker compose --profile api run --rm apitest "your question here"
#
# Every request here is one a GUI makes: discover the service, check it is
# ready, read the limits, ask a question, watch the progress stream, collect
# the answer. If one of them needs something that is not in this file, the
# API is not usable by a client that does not import this project.
#
# Exit codes: 0 all checks passed, 1 a check failed, 2 the API was never
# reachable at all -- told apart so a CI job can retry the second and not the
# first.
set -uo pipefail

BASE_URL="${API_BASE_URL:-https://nl2sql-api:8443}"
QUESTION="${1:-${APITEST_QUESTION:-How many stores are there?}}"
WAIT_SECONDS="${APITEST_WAIT_SECONDS:-240}"

PASS=0
FAIL=0

pass() { printf '  ok    %s\n' "$1"; PASS=$((PASS + 1)); }
fail() { printf '  FAIL  %s\n' "$1" >&2; FAIL=$((FAIL + 1)); }
step() { printf '\n==> %s\n' "$1"; }
die()  { printf '\nERROR: %s\n' "$1" >&2; exit 2; }

# --- How this client trusts the server -------------------------------------
# Three ways, in the order a deployment grows through them: a CA file that
# was mounted in, the development certificate the server generated, or
# --insecure because someone said so. Trusting the generated certificate as a
# CA file is better than --insecure even in development -- it still proves
# the connection reached the server holding that key.
# Assigned by exactly one of the branches below, never declared empty: an
# empty array expanded under `set -u` aborts on bash 3.2, and a variable that
# is only ever set to something real cannot be expanded empty by mistake.
if [[ -n "${API_CACERT:-}" && -f "${API_CACERT}" ]]; then
    CURL_TLS=(--cacert "${API_CACERT}")
    TRUST="the CA file at ${API_CACERT}"
elif [[ -f /etc/nl2sql/tls/server.crt ]]; then
    CURL_TLS=(--cacert /etc/nl2sql/tls/server.crt)
    TRUST="the server's own development certificate, pinned as a CA"
elif [[ "${API_INSECURE:-false}" =~ ^(1|true|yes|on)$ ]]; then
    CURL_TLS=(--insecure)
    TRUST="nothing -- certificate verification is off (API_INSECURE)"
else
    die "no way to verify the server: mount a CA at API_CACERT, mount its certificate at /etc/nl2sql/tls/server.crt, or set API_INSECURE=true"
fi

# Seeded with a header rather than left empty: under `set -u`, bash 3.2 --
# which is what macOS still ships -- treats "${AUTH[@]}" on an empty array as
# an unbound variable and aborts. The container has bash 5 and would never
# have shown it, and the header earns its place by naming this client in the
# API's access log.
AUTH=(-H "X-Client: nl2sql-apitest")
[[ -n "${API_TOKEN:-}" ]] && AUTH+=(-H "Authorization: Bearer ${API_TOKEN}")

api() {  # api METHOD PATH [curl args...]
    local method="$1" path="$2"; shift 2
    curl -sS --max-time 30 "${CURL_TLS[@]}" "${AUTH[@]}" \
        -X "$method" "${BASE_URL}${path}" "$@"
}

status_of() {  # status_of METHOD PATH [curl args...]
    local method="$1" path="$2"; shift 2
    curl -sS --max-time 30 -o /dev/null -w '%{http_code}' "${CURL_TLS[@]}" "${AUTH[@]}" \
        -X "$method" "${BASE_URL}${path}" "$@"
}

printf 'NL2SQL API smoke test\n'
printf '  target  %s\n' "$BASE_URL"
printf '  trust   %s\n' "$TRUST"
printf '  auth    %s\n' "${API_TOKEN:+bearer token}${API_TOKEN:-none}"

# --- 1. Is it there, and is TLS actually in use? ---------------------------
step "Reaching the service"
for attempt in $(seq 1 30); do
    if health=$(api GET /healthz 2>/dev/null) && [[ -n "$health" ]]; then
        break
    fi
    sleep 2
done
[[ -n "${health:-}" ]] || die "no response from ${BASE_URL}/healthz after 60s"

# Reporting the transport, not testing it: the request above already went
# through this same handshake under these same trust settings, and a failure
# there exits 2 long before here. A second probe could only ever agree with
# it, so this says what happened rather than pretending to check again.
[[ "$BASE_URL" == https://* ]] && pass "TLS handshake completed"

jq -e '.status == "ok"' <<<"$health" >/dev/null \
    && pass "GET /healthz -> $(jq -r '.version' <<<"$health")" \
    || fail "GET /healthz did not report ok: $health"

# --- 2. Readiness, which is the one that fails in real life ----------------
step "Checking readiness"
ready_body=$(api GET /readyz)
if jq -e '.ready == true' <<<"$ready_body" >/dev/null; then
    pass "GET /readyz -> ready"
else
    fail "GET /readyz says not ready: $(jq -c '.checks' <<<"$ready_body" 2>/dev/null || echo "$ready_body")"
fi

# --- 3. Self-description: the whole framework-agnostic claim ---------------
step "Reading the service description"
openapi_status=$(status_of GET /openapi.json)
[[ "$openapi_status" == "200" ]] \
    && pass "GET /openapi.json -> 200 (a client can be generated from this)" \
    || fail "GET /openapi.json -> $openapi_status"

meta=$(api GET /v1/meta)
# `length > 0`, not `length`: jq counts a missing `.tables` as 0 and reports
# success, so an error body would have been announced as "ok -- 0 tables".
# A server with nothing in scope is not one a client can use either way.
if table_count=$(jq -e '.tables | length > 0 and length' <<<"$meta" 2>/dev/null); then
    pass "GET /v1/meta -> ${table_count} tables, model $(jq -r '.model' <<<"$meta")"
    jq -e '.limits.max_rows > 0' <<<"$meta" >/dev/null \
        && pass "the limits a client has to respect are published" \
        || fail "/v1/meta has no usable limits"
else
    fail "GET /v1/meta did not return a description: $meta"
fi

# --- 4. Rejecting what it should reject ------------------------------------
step "Checking the error contract"
bad=$(api POST /v1/questions -H 'Content-Type: application/json' -d '{"question": ""}')
jq -e '.error.code' <<<"$bad" >/dev/null \
    && pass "an invalid question returns the documented error shape ($(jq -r '.error.code' <<<"$bad"))" \
    || fail "an empty question did not return an error object: $bad"

missing=$(status_of GET /v1/questions/does-not-exist)
[[ "$missing" == "404" ]] \
    && pass "an unknown job -> 404" \
    || fail "an unknown job -> $missing, expected 404"

# --- 5. Ask a question -----------------------------------------------------
step "Asking: $QUESTION"
created=$(api POST /v1/questions \
    -H 'Content-Type: application/json' \
    -d "$(jq -nc --arg q "$QUESTION" '{question: $q, metadata: {client: "apitest"}}')")

job_id=$(jq -r '.id // empty' <<<"$created")
if [[ -z "$job_id" ]]; then
    # A failed check, not a dead service: the API answered, it just would not
    # take this question -- a missing token is the usual reason. Exiting 2
    # here would tell a CI job to retry something that will never succeed.
    fail "POST /v1/questions returned no job: $(jq -c '.error // .' <<<"$created" 2>/dev/null || printf '%s' "$created")"
else
    pass "POST /v1/questions -> job $job_id ($(jq -r '.status' <<<"$created"))"

    # --- 6. Watch it happen ------------------------------------------------
    # The progress stream is what a GUI draws while the minute passes, so it
    # is checked the way a GUI consumes it: read events until one is `done`.
    step "Streaming progress"
    events=$(curl -sS --max-time "$WAIT_SECONDS" --no-buffer "${CURL_TLS[@]}" "${AUTH[@]}" \
        -H 'Accept: text/event-stream' \
        "${BASE_URL}/v1/questions/${job_id}/events" 2>/dev/null |
        while IFS= read -r line; do
            printf '%s\n' "$line"
            [[ "$line" == "event: done" ]] && break
        done)

    steps=$(grep -c '^event: progress$' <<<"$events" || true)
    if [[ "${steps:-0}" -gt 0 ]]; then
        pass "the stream carried ${steps} pipeline step(s)"
        # The `data: ` prefix has to come off before jq sees it; an SSE frame
        # is not JSON, the field it carries is.
        printf '%s\n' "$events" | sed -n 's/^data: \({"seq".*\)/\1/p' |
            jq -r '"        " + (.seq|tostring) + ". " + .label + " -- " + (.detail | split("\n")[0])' |
            head -20
    else
        fail "the event stream carried no progress events"
    fi
    grep -q '^event: done$' <<<"$events" \
        && pass "the stream ended with a done event" \
        || fail "the stream never reported the job finished"

    # --- 7. Collect the answer ---------------------------------------------
    step "Collecting the answer"
    job=$(api GET "/v1/questions/${job_id}?wait=${WAIT_SECONDS}" --max-time $((WAIT_SECONDS + 30)))
    state=$(jq -r '.status' <<<"$job")
    case "$state" in
        succeeded)
            pass "the job succeeded in $(jq -r '.duration_ms' <<<"$job") ms"
            jq -e '.answer.sql | length > 0' <<<"$job" >/dev/null \
                && pass "the answer carries the SQL that produced it" \
                || fail "the answer has no SQL"
            jq -e '.answer.result.columns | length > 0' <<<"$job" >/dev/null \
                && pass "the answer carries $(jq -r '.answer.result.row_count' <<<"$job") row(s)" \
                || fail "the answer has no rows"
            printf '\n    question: %s\n' "$(jq -r '.question' <<<"$job")"
            printf '    sql:      %s\n' "$(jq -r '.answer.sql' <<<"$job" | tr '\n' ' ' | cut -c1-160)"
            printf '    answer:   %s\n' "$(jq -r '.answer.narrative // .answer.answer' <<<"$job" | head -3)"
            ;;
        failed)
            fail "the job failed: $(jq -r '.error' <<<"$job")"
            ;;
        *)
            fail "the job is still $state after ${WAIT_SECONDS}s"
            ;;
    esac

    # --- 8. Housekeeping ---------------------------------------------------
    step "Cleaning up"
    deleted=$(status_of DELETE "/v1/questions/${job_id}")
    [[ "$deleted" == "204" ]] \
        && pass "DELETE /v1/questions/${job_id} -> 204" \
        || fail "DELETE /v1/questions/${job_id} -> $deleted"
fi

printf '\n==> %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]] || exit 1
