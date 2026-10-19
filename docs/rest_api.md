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

**An answer several runs gave carries its runs.** Since 7.0 the agent asks
a question several ways and runs the pipeline once per wording ([asking it
several ways](../agent/README.md#asking-it-several-ways-arch7)), and the
wire says so in three places, each of which a client written for 6.3 can
ignore:

- **`Answer.ensemble`**: how the runs agreed -- the counts, the level, and
  the sentence the answer opens with -- which run was delivered, the answers
  that lost and how their queries differ, the Judge's verdict on each
  answer, every wording's run with its SQL, and the rewordings that were
  not run. `null` with the ensemble off, so the answer is the one it always
  was.
- **`ProgressEvent.candidate`**: which run a step came from -- `0` the
  question as asked, `1` and up a rewording, `null` one of the ensemble's
  own steps. A client that ignores it sees every run's steps in one list.
- **`Pipeline.ensemble`** in `/v1/meta`: whether this server asks several
  ways, how many rewordings it runs, and which of `nodes` are the ensemble's
  own, so a client can draw a lane per run before the first event.

**A follow-up names its conversation.** Since 8.0 a question may continue
a conversation, and the client names it: `"conversation"` on the request,
an id it makes up -- a UUID for one, 1 to 64 letters, digits, `.`, `_` or
`-` -- with no call to create it, since the first question with an id
starts it. Every later question with the id is read with the answered
questions before it, the last `CONVERSATION_MEMORY` (five by default), so
"and by store?" is answered as the complete question it stands for, and
`Answer.conversation` says which turn it was and how it was read. A
question asked while the conversation's last is still running is `409
conversation_busy` with a `Retry-After`; `DELETE
/v1/conversations/{conversation_id}` forgets one, and so does an hour of
silence (`API_CONVERSATION_TTL_SECONDS`) or a restart -- nothing is written
to disk. A `no` on an answer forgets its turn, so the next question is not
read in the light of a wrong one. A request without the field is asked
alone, as before 8.0, and with `CONVERSATION_MEMORY=0` every question is.
[`agent/API.md`](../agent/API.md#in-a-conversation-conversation-80) has
the rest.

Two fields of `Answer.conversation` are the question twice: `asked` is the
words typed, and `read_as` the complete question the agent answered when it
restated a follow-up, with `resolved` true. Show both, as the web interface
does -- the words typed as the heading, "Read as: ..." under it -- so a
wrong reading is seen rather than believed, and show `read_as` as text: it
is a model's sentence. Send the id only while `/v1/meta`'s
`pipeline.memory.turns` is above zero: a server that remembers nothing
echoes it and changes nothing, and one older than 8.0 refuses a field it
does not know. Make a new id for a new conversation; the old one is
forgotten once it has been idle long enough.

**Watch the job; do not hold the connection for it.** A question asked
several ways takes several runs' time -- minutes, where one run took about
one. `?wait=` still works and is still bounded by `API_MAX_WAIT_SECONDS`
(900), and a job that outlasts the wait comes back still running, with the
links to follow. But a proxy, a load balancer or a browser between the
client and the API usually gives up on a silent connection long before
that. The event stream is never silent for long, resumes after a drop, and
is what both clients here use; polling the job is the other answer.

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
