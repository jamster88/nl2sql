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

**A question asked several ways has a lane for every run.** Since 7.0 the
agent asks a question as it was typed and reworded, and runs the whole
pipeline once per wording ([asking it several
ways](../agent/README.md#asking-it-several-ways-arch7)). Every event on the
stream says which run it came from, so the progress panel draws a lane per
run, each with the pipeline's steps, between the ensemble's own: above the
lanes, what it did before the runs -- screening the question, writing the
rewordings, checking each one still asks the same thing -- and below them,
what it did with their answers -- comparing them, the Judge, the vote,
fusion. `/v1/meta` says how many runs to expect and which steps are whose,
so the lanes are there, waiting, before the first event. On a host that
serves one model call at a time they fill one after another, and side by
side when the deployer has allowed more (`OLLAMA_PARALLEL_CALLS`); either
way it is the order things happened in. With the ensemble off it is the one
row of steps it always was.

**The answer arrives with its working.** The sentence, then the chart the
Visual Formatter asked for, then the rows. Folded away beneath: the SQL and
how many attempts it took, the phrases matched to database values, and how
long each node took. Hovering a claim highlights the exact cells it was read
from -- the audit ties every sentence to the rows that support it, and this
is the clearest thing that makes possible.

**An answer several runs gave says how they agreed.** Beside the sentence
is a badge: the level in a word -- all agree, most agree, the Judge's
choice, contested, one answer -- the count, and the agent's own line about
the vote, which is the server's sentence rather than one this page composed
from the numbers. An answer that lost is among the notes, with how its
query differs from the one delivered; so is a column joined on from another
run that agreed, named with its run, because the SQL shown does not return
it. And folded away with the SQL is "The question, asked 4 ways": every
wording, what was changed to make it, how its run ended, which answer it
gave and the SQL it wrote; what the Judge said of each answer; and the
rewordings that were written and never run, each with the check it failed.
A rewording and a Judge's reason are a model's words, and the page renders
them as text, never as markup. Feedback is unchanged: three buttons, on the
answer that was delivered.

**The questions asked are a conversation.** Since 8.0 a follow-up such as
"and in each state?" is answered as the complete question it means, read
with the answered questions before it ([remembering the
conversation](../agent/README.md#remembering-the-conversation-arch8)). The
page makes a conversation id when it opens and sends it with every
question, while `/v1/meta` says the server remembers anything; the status
bar says how much -- "remembers the last 5 turns", or "each question asked
alone" -- and names the pipeline answering. When the agent restated a
follow-up, the answer says so under the words typed: "Read as: *How many
stores are there in each state?* · turn 2, 1 remembered", rendered as text
since a model wrote it. Nothing is shown for a question read as it was
typed. **New conversation**, beside the Ask button, takes a new id, and the
next question is read without the ones before it. The list of earlier
questions is not the conversation: it is the questions answered this
session, and a new conversation leaves it as it was. Marking an answer
wrong also takes its turn out of the conversation, so the next question is
not read in the light of a wrong one. A question sent while the last is
still being answered would be refused, but the ask box is closed until the
answer is on screen; if a refusal ever arrives, it is shown under the box
like any other question that could not be sent.

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
