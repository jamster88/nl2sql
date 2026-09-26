# The desktop client

A JavaFX front end for the agent's REST API. Same questions, same answers,
same charts and the same yes/no verdict as [`gui/`](../gui) — in a window
rather than a browser tab, on a machine rather than a server.

```bash
./start.sh --desktop
```

That builds it, copies the API's certificate out and opens it. Everything
below is what that command does and why.

---

## Contents

- [Why a second client](#why-a-second-client)
- [Running it](#running-it)
- [Trusting the server](#trusting-the-server)
- [What is on screen](#what-is-on-screen)
- [Feedback](#feedback)
- [How it is put together](#how-it-is-put-together)
- [Building it](#building-it)
- [Tests](#tests)

---

## Why a second client

Because a contract only one implementation has ever met is a contract nobody
has checked. [`agent/API.md`](../agent/API.md) claims to be framework
agnostic — "a TypeScript, Python, Java or Go client is generated from it
rather than written against by hand" — and until this existed, the only
things that had ever read it were a React application and a curl script.

Writing the second one found things. `/v1/meta` published the row limit and
the wait ceiling but not the two limits that describe the *request*, because
a browser discovers those from a 422 in its network tab and a desktop
application shows the user whatever it was handed. Both are now in
`limits`, and this client reads them before it will let a question be sent.

Nothing here imports anything from this repository. The wire contract is
mirrored by hand in one file — [`Models.java`](src/main/java/org/nl2sql/desktop/api/Models.java)
— for the reason `gui/src/api/types.ts` is: a generated file is not something
anyone reads, and this is the one somebody writing a third client opens
first. `tests/java/test_desktop_contract.py` compares every record component
against the pydantic field it mirrors, so the hand-written copy cannot drift.

---

## Running it

```bash
./start.sh --desktop                 # everything, then the window
./start.sh --desktop --review        # and the review interface in a browser
./launch.sh --desktop                # build it and stop there
java -jar desktop/target/nl2sql-desktop.jar --cacert ./nl2sql-api.crt
```

`--desktop` is an interface, not an addition to one: the web interface is not
started and no browser is opened for it. `--review` still opens the review
page, because there is no desktop equivalent of it and there is not going to
be one — curation happens in one place.

The window is left running when the command returns, and is still there when
the terminal is closed. Those are two different problems: a process
backgrounded directly is a job of the shell that started it and is reaped
with that shell's process group, which `start.sh` is about to end, so the
client is started inside a subshell; and `nohup` is what ignores the hangup a
closing terminal sends afterwards. `start.sh` waits a moment and checks the
window is still there before saying it opened one, and repeats what the
client said if it is not. Running it again reports the client as already open
rather than putting a second window onto the same API — the pid is kept
beside the jar and checked against the operating system rather than trusted,
because the file outlives the process it names.

What the machine needs is **a Java runtime of 21 or later**, and nothing
else. JavaFX is inside the jar. Docker builds that jar, so Maven is a
developer's tool here rather than a user's.

### Options

Every one has an environment variable, and the flag wins.

| Flag | Variable | What |
| --- | --- | --- |
| `--url URL` | `NL2SQL_API_URL` | The API to talk to. Default `https://localhost:8443` |
| `--token TOKEN` | `NL2SQL_API_TOKEN` | Bearer token, when the server requires one |
| `--cacert FILE` | `NL2SQL_API_CACERT` | PEM certificate to verify the server against |
| `--fingerprint HEX` | `NL2SQL_API_FINGERPRINT` | Accept exactly the certificate with this SHA-256 |
| `--insecure` | `NL2SQL_API_INSECURE` | Do not verify the certificate at all |
| `--wait SECONDS` | `NL2SQL_API_WAIT_SECONDS` | How long to let the server hold a question open |
| | `NL2SQL_FEEDBACK_FILE` | Where verdicts are mirrored. `none` for no file |
| | `NL2SQL_POLL_INTERVAL_MS` | How often to poll once streaming has been given up on |
| | `NL2SQL_HISTORY_LIMIT` | How many answered questions stay in the session list |

An empty variable is an absent one, because compose passes an unset variable
through as an empty string and this client is started from a compose-shaped
environment often enough for the difference to matter.

---

## Trusting the server

A browser gets this for nothing. `gui/` is served by the same nginx that
proxies the API, so the page talks to its own origin, the proxy holds the
token and the operating system has already decided which certificates to
trust. A desktop application has none of that: it opens the connection
itself, from a machine that has never heard of this server.

The API writes itself a self-signed certificate on first start, which every
client that checks will refuse — and that refusal is the feature. So there
are three ways to say *which* server you meant, in the order `agent/API.md`
recommends them:

```bash
# 1. the certificate itself, which is what ./launch.sh --desktop copies out
docker compose --profile api cp api:/etc/nl2sql/tls/server.crt ./nl2sql-api.crt
java -jar desktop/target/nl2sql-desktop.jar --cacert ./nl2sql-api.crt

# 2. its fingerprint, which the server prints at start-up and /v1/meta carries
java -jar desktop/target/nl2sql-desktop.jar --fingerprint 3f2c...

# 3. nothing at all, for a throwaway experiment
java -jar desktop/target/nl2sql-desktop.jar --insecure
```

The status bar says which of the four it is doing — `verified against
nl2sql-api.crt`, `pinned to 3f2c8a91b0de`, `NOT VERIFIED`, `not encrypted` —
for as long as it is true. An application that stops verifying quietly is how
it ends up doing it in production.

`--insecure` cannot be combined with `--cacert` or `--fingerprint`: one of
them says to check the certificate and the other says not to bother, and
guessing which was meant is how a client ends up not checking on the run
where it mattered.

The second and third build a trust manager by hand, which is normally a
mistake worth failing a review over. It is not one here: neither is
*weakening* a chain of trust, because there is no chain — a self-signed
certificate has no issuer to check against, so the only question that can be
asked is whether this is the one the user named.

---

## What is on screen

The same order the web interface uses, because a reader wants the same
things in the same order: the sentence, then the picture, then the rows, then
— folded away — the SQL, the matched values and the per-node cost.

* **The steps, while they happen.** The pipeline's own graph node names,
  streamed as they finish, with the ones still to come greyed out. It is a
  report rather than an animation: a user watching "matching literals" go by
  learns something about why the answer took a minute.
* **The sentence, traceable.** The audit ties each sentence of the narrative
  to the cells it was read from, so pointing at one lights up its cells in
  the table below. That is the clearest thing this API makes possible.
* **The chart the pipeline asked for**, drawn with JavaFX's own charts. The
  shaping is a port of `gui/src/charts/values.ts` rather than a second
  design: two clients that shape one result differently are two clients that
  disagree about what the agent said. One number is drawn as one number, and
  `kind: "table"` means the rows are the presentation.
* **The rows, always.** A chart is a reading of the result; the table is the
  result. Cells are shown exactly as they arrived, because a `numeric`
  crosses JSON as a string so it does not lose precision.
* **A refusal is a success.** The agent decided a question was out of scope
  and said so; there is no table, because no query ran.

Everything that is a sentence reflows as the window is resized, and that took
more than turning wrapping on. Two things in JavaFX make prose run off the
edge rather than wrap, and the interface had both. A `TextFlow` breaks the
`Text` nodes it is given across lines and lays anything else out at its
preferred width as one unbreakable box -- so the answer, which is one
hoverable span per claim, has to be made of `Text` and not of `Label`. And
`setWrapText(true)` on its own only permits wrapping: the label still asks
for the width its text wants and its container still gives it, so the height
that comes back is one line. What makes the height depend on the width is a
ceiling, read from the container it sits in. The session list had a third
version of the same problem: a `ListCell` sizes itself to its text and the
virtual flow behind it sizes itself to the widest cell, so one long question
made the whole panel six hundred pixels wide inside a two-hundred-pixel pane
and spilled out over the answer beside it. The session list takes a share of the
window rather than a fixed strip -- 260 pixels is a quarter of a wide window
and nearly half a narrow one -- and the Ask button is the one control that
refuses to shrink, because a button that gives way becomes an ellipsis and an
ellipsis is not a label anyone can guess at. The window also declines to be
dragged narrower than it can lay out, rather than letting itself be cut in
half.

The last of those was the one that mattered, and it was neither of the panes
it appeared to be in. A label reports the width its text wants as its
*minimum*, a `BorderPane` honours a minimum, and `/v1/meta` describes this
database in a sentence four hundred characters long -- so the status bar's
floor was two thousand pixels, the root laid itself out that wide inside a
window half the size, and the screen clipped everything that did not fit.
Both panes looked broken; only the bar was. Nothing in the window may set a
floor under the window, and a test asserts exactly that.

Two habits came out of finding it three tries late. Layout defects are found
by *drawing* the window rather than measuring it -- `Node.snapshot` works
under Monocle, so a throwaway test can render the whole window to a PNG at
any size and a person can look at it. And the fixtures say what the server
really says: the scope sentence in `Fakes` is the real one, all three hundred
and ninety characters, because the tidy twenty-six character version had no
opinion about how wide the window had to be and that is precisely the opinion
that was wrong.

---

## Feedback

Identical to the web interface's, deliberately: `POST
/v1/questions/{id}/feedback`, the same staging table, the same row-level
security fence, the same review queue. **A reviewer sees one queue because
there is only one**, not because two writers were made to agree — which is
the whole point of the client sending an opinion and nothing else and the
server reading the snapshot off the job it still has.

The behaviour is the web interface's too. The verdict is recorded on the
click and the request goes out behind it, because a verdict is a courtesy the
user is doing us and making them watch a spinner for it is how a feedback
system stops collecting feedback. What happened to it is shown rather than
hidden — `sending…`, `sent for review`, `not sent` with a Retry beside it —
because a verdict that silently failed to send is worse than one that was
never offered: the user believes they have reported the problem.

Clicking the verdict already recorded withdraws it. Verdicts are also
mirrored to `~/.nl2sql/feedback.json`, so one that could not be sent is still
on screen after a restart, still marked unsent, and still the user's own
record of what they thought. Set `NL2SQL_FEEDBACK_FILE=none` for no file.

Review itself happens in [`review/`](../review) and only there. There is no
Java half of it and there should not be: the thing that can rewrite the
golden question set is one service with one token, and a second client of it
would be a second way in.

---

## How it is put together

```
src/main/java/org/nl2sql/desktop/
  Main.java              the entry point, which does not extend Application
  Settings.java          where the API is and how it may be believed
  api/
    Models.java          the wire contract, mirroring models.py
    ApiClient.java       what this application needs from the server
    HttpApiClient.java   eight calls and one error shape
    Tls.java             deciding whether to believe the server
    EventStream.java     server-sent events, taken apart
    JobWatcher.java      streaming with a fallback to polling
    Json.java            Jackson, configured once
  chart/                 rows to series, a port of gui/src/charts/values.ts
  feedback/              the verdict, where it is kept and where it goes
  ui/                    the window, and every part of it
    Markup.java          undoing the escaping the pipeline does for markdown
```

Three decisions are worth knowing because they look arbitrary otherwise:

* **`Main` does not extend `Application`.** A class that does, launched from
  the class path, makes JavaFX refuse to start with a message about missing
  runtime components. A separate launcher is the documented way round it, and
  it is what lets this ship as one jar that runs with `java -jar`.
* **`MainWindow` takes both of its threads as parameters.** In the
  application `background` is a pool and `foreground` is
  `Platform::runLater`; in a test both are "run it here", which is what lets
  the whole window be driven against a fake client on one thread, in order,
  with nothing to wait for. The rule they enforce is the one rule of a
  JavaFX application: nothing that can block touches the toolkit's thread,
  and nothing that touches the scene graph happens off it.
* **`JobWatcher` is a `Runnable`, not something that starts a thread.** Same
  reason. The stream, the reconnect, the fall back to polling and the
  recovery are all assertable without a sleep anywhere.

---

## Building it

```bash
./launch.sh --desktop                       # fetch it, or build it
docker compose --profile desktop run --rm desktop      # take the jar out
cd desktop && mvn package                   # with Maven, if you have it
```

There is a published tag per platform -- `mcfaddja/nl2sql-desktop-build:v4_5-mac-aarch64`
and four siblings -- so the usual path is a 33 MB pull rather than a Maven
build. The image carries the jar and nothing that could have produced it: the
builder stage is Maven, a JDK and half a gigabyte of dependency cache, and
the stage that ships is alpine and one file. `launch.sh` builds locally only
when there is nothing to pull, which is what an unpinned checkout and an
offline machine have in common.

The jar is built **in a container for a machine that is not the container**.
OpenJFX publishes its native code under one of five classifiers and picks the
host's automatically, which is right for a developer and wrong for an
artefact built in Linux and run on macOS — so `launch.sh` reads `uname` and
passes the answer in as `-Djavafx.platform=`.

One jar is one platform. The same library file names are used on macOS
x86-64 and on arm64, so a jar carrying both would carry one of them twice
under one name and load whichever came first. `launch.sh` records which
platform the jar was built for beside it, and fetches again when that changes
or when a source file is newer.

Every one of those tags is itself multi-architecture, which is a second axis
and an easy one to confuse with the first: that is the machine the *image*
runs on to copy the jar out, while the platform in the tag is the machine the
*jar* will draw on.

JavaFX **21** rather than the newest: it is the long-term-support line and
runs on every JDK from 17 upwards, while 25 refuses to load on anything below
25 — which would make the floor for *running* this the newest JDK rather than
the oldest supported one.

One warning is expected on start-up:

```
WARNING: Unsupported JavaFX configuration: classes were loaded from 'unnamed module'
```

That is JavaFX noticing it is on the class path rather than the module path,
which is what makes a single runnable jar possible at all. The alternative is
a directory of module jars and a launcher that works out which platform's
subdirectory to use.

---

## Tests

```bash
cd desktop && mvn test          # directly
pytest tests/java --run-java    # the same thing, from the Python suite
pytest tests/java              # the parts that need no JDK: the contract
```

376 tests, 100% of lines and branches, enforced by JaCoCo — a threshold below
100 is a number nobody looks at, while a failing build is read immediately.
`Main` is the one exclusion: it calls `Application.launch()`, which does not
return until the window is closed.

The interface is tested for real, through the toolkit, on the toolkit's own
thread — a button is pressed with `fire()` and the scene graph is read
afterwards, which exercises the same handlers a click would with nothing to
wait for and nothing to flake. The toolkit runs headless through **Monocle**,
which is a dependency because of macOS and useful everywhere: AppKit insists
on owning the process's first thread, and under a test runner that thread is
the one running the tests, so the real back end comes up and then delivers
nothing — a hang rather than a failure, and an intermittent one.

`HttpApiClientTest` drives the client against a real `HttpsServer` on a
loopback port, with a certificate generated by `keytool` at test time. Both
halves of that matter: the encrypted path is where a client's mistakes stay
invisible until deployment, and a private key committed to a repository is a
private key in every clone and every secret scanner's report.

From the Python side, `tests/java/` checks the things a Java test cannot see
— that every wire model has a record, that the components are in the same
order as the pydantic fields, that the pom pins what it depends on and gates
coverage at 100%, and that `launch.sh` can name all five platforms OpenJFX
publishes for.
