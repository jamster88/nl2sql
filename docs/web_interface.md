# The web interface

*Part of the [nl2sql documentation](../README.md#documentation).*

```bash
./start.sh
```

A React and TypeScript front end, built to static files and served by nginx.
Ask a question, watch the pipeline work through it, read the answer with its
chart and its rows, and say whether it was right.

It imports nothing from the agent. It speaks the same JSON over HTTPS that
any other client would, which is the point: it is a demonstration that the
API is framework-agnostic rather than a privileged special case. Everything
it does, the four snippets in [`agent/API.md`](../agent/API.md) do too.

**The minute a question takes is filled with what the agent is doing.** The
progress stream carries the graph's own node names -- screening the question,
matching literals, reading the schema, writing SQL, checking the plan,
running it, narrating, auditing -- so the user watches work rather than an
animation, and the nodes still to come are listed greyed because `/v1/meta`
says which ones this server runs.

**The answer arrives with its working.** The sentence, then the chart the
Visual Formatter asked for, then the rows. Folded away beneath: the SQL and
how many attempts it took, the phrases matched to database values, and how
long each node took. Hovering a claim highlights the exact cells it was read
from -- the audit ties every sentence to the rows that support it, and this
is the clearest thing that makes possible.

**A refusal is rendered as a refusal.** `verdict` is not `proceed` for an
out-of-scope or unsafe question, and there is no table because no query ran;
showing an empty grid would report a failure that did not happen. An
ambiguous question comes back with a clarification, which is a question for
the user, so it goes where the answer would.

**Feedback is three buttons.** Correct, wrong, or correct but incomplete, per
answer, shown back in the answer and in the session list. The third is for an
answer whose SQL was right and which still left out something a reader
needed -- a name beside an id, the figure a ranking was ranked by -- because
that calls for fleshing out rather than correcting, and a plain "no" cannot
say which. Once the review service has started once (`--review` or
`--curate`), making the staging table, the verdict is also sent to the API
and waits to be reviewed; on a server without one it is kept in the browser
and says so, because a button
whose every click fails is worse than no button. See [Feedback](feedback.md#feedback).

The container verifies the API's certificate against the stack's CA, so the
browser never has to,
and with sign-in off it holds the API token as well; with sign-in on, the
session the page signed in with is what reaches the API, and no token is
added. Neither is a requirement of the API -- it answers
a browser directly when `API_CORS_ORIGINS` names the origin (none does by
default) -- but an untrusted certificate blocks `EventSource` with no
warning to click, and
[`gui/README.md`](../gui/README.md) explains the three problems one same-origin
hop removes.

`start.sh` waits until the page actually answers before opening it. That is
not the same as waiting for the container to call itself healthy: nginx
reports healthy as soon as it is up, which is a moment before it has read the
configuration written for it at start-up, and a browser opened on the health
check alone lands on a connection error often enough to matter. On a machine
with no desktop it prints the URL and carries on, which is not a failure --
`--no-browser` asks for that deliberately, and `BROWSER` picks what opens it.

For development against a running API:

```bash
./launch.sh --api
cd gui && npm install && npm run dev      # http://localhost:5173
```
