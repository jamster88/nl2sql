# Connecting a GUI

*Part of the [nl2sql documentation](../README.md#documentation).*

The agent also answers over HTTPS, so a front end can be written in anything.
There is no client library here and there is not meant to be one: the
interface is JSON over HTTP with an OpenAPI document the server generates
itself, and a TypeScript, Python, Java or Go client is generated from that
rather than written by hand.

```bash
./launch.sh --api
docker compose --profile api cp api:/etc/nl2sql/tls/ca.crt ./nl2sql-ca.crt

curl --cacert ./nl2sql-ca.crt https://localhost:8443/v1/meta
curl --cacert ./nl2sql-ca.crt -X POST 'https://localhost:8443/v1/questions?wait=180' \
     -H 'Content-Type: application/json' \
     -d '{"question": "How many stores are there?"}'
```

It is the *same image* as the agent, started as a server instead of a command
(`python -m nl2sql_agent.api`), so the pipeline answering a GUI is the
pipeline that was benchmarked.

**A question is a resource, not a request.** Answering takes about a minute,
which no GUI can hold a connection open for while showing nothing. `POST
/v1/questions` returns a job immediately; the client polls it, streams its
progress, or asks the server to hold the connection with `?wait=`. All three
return the same document, so waiting is an optimisation rather than a second
contract.

**Progress is the pipeline, not an animation.** `GET
/v1/questions/{id}/events` is a Server-Sent Event stream carrying the graph's
own nodes as they happen -- screening, schema, literals, SQL, the plan gate,
execution, the narrator, the audit -- and it resumes from `Last-Event-ID`
after a dropped connection.

**TLS is on by default.** Under compose the API's certificate is its own,
issued by the stack's development CA before it starts (the `pki` service),
and kept in a volume so a restart presents the same one; started on its
own, the server writes itself a self-signed one instead. Either is a
development convenience, and `API_TLS_ALLOW_SELF_SIGNED=false` takes both
away: the server then refuses to start behind a development certificate at
all -- self-signed or issued by the stack's CA -- so a deployment meant to
have a real chain fails at startup instead of quietly serving the throwaway
one.

Sign-in is on by default, in the server's own settings as well as in
compose. `API_TOKEN` adds a service token for scripts (`setup.sh --tokens`
generates one); `API_CORS_ORIGINS` names a browser origin allowed to call
the API directly, and none is by default. At most `API_MAX_QUEUED` questions
wait behind those running and a signed-in person has at most
`API_MAX_PER_PERSON` in the air; past either, `POST /v1/questions` is `429`
with a `Retry-After`.

To try the whole thing from outside, with no Python and no shared code:

```bash
docker compose --profile api run --rm apitest
```

`apitest` is an Alpine image holding curl and jq. It verifies the
certificate, walks every endpoint, streams a real question's progress, and
exits `1` on a failed check or `2` when the API was never reachable -- so CI
can tell a retry apart from a defect. It is also the shortest complete
reference for writing a client.

[`agent/API.md`](../agent/API.md) is the contract: every endpoint, the response
shapes, the event stream, the error codes, the settings, and worked client
snippets for TypeScript/React, Python and Java. [`gui/`](../gui) is a complete
client written against it, if a working example is more useful than a
snippet.
