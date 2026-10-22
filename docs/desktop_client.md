# The desktop client

*Part of the [nl2sql documentation](../README.md#documentation).*

```bash
./start.sh --desktop
```

A JavaFX application in a window on this machine, rather than a page in a
browser. The same questions, the same progress stream, the same charts and
the same three-way verdict -- correct, wrong, correct but incomplete -- as the web interface --
[`desktop/README.md`](../desktop/README.md) is the whole of it.

**It exists because a contract only one implementation has ever met is a
contract nobody has checked.** [`agent/API.md`](../agent/API.md) claims the
interface is framework-agnostic, and until this was written the only things
that had ever read it were a React application and a curl script. Writing the
second client found something immediately: `/v1/meta` published the limits
that describe the *answer* and not the two that describe the *request*,
because a browser discovers those from a 422 in its network tab and a desktop
application shows the user whatever it was handed. Both are now in `limits`,
and this client reads them before it will let a question be sent.

**The runs behind an answer are shown as the web interface shows them.**
Since 7.0 the agent asks a question several ways and runs the pipeline once
per wording, and this client draws what the web interface draws, by the
same rules, from the same fields: a lane for every run in the progress
panel, between the steps the ensemble takes before the runs and the ones it
takes with their answers; a badge with the sentence, saying how the runs
agreed in the server's own words; the answers that lost among the notes;
and a folded pane, "The question, asked 4 ways", with every wording, its
outcome and its SQL, the Judge's verdict on each answer, and the rewordings
that were not run. Everything a model wrote is drawn by a label or a text
area, which draw a string as text. [The web
interface](web_interface.md#the-web-interface) describes each part.

**Each window is a conversation.** Since 8.0 a follow-up is answered as
the complete question it means, and the window does what the web page does
([the web interface](web_interface.md#the-web-interface)): it makes a
conversation id when it opens -- a random UUID, so two windows are two
conversations -- sends it with every question while `/v1/meta` says the
server remembers anything, and makes a new one on **New conversation**,
beside the Ask button. Under the words typed, a restated follow-up says how
it was read: "Read as: ..." with its turn and how many turns it was read
with, drawn as text. The status bar names the pipeline and says how much of
a conversation the server remembers, or that each question is asked alone.
The session list stays as it was when a new conversation begins: it is the
questions answered, not the turns.

**Verdicts take the same route as the web interface's.** The same endpoint,
the same staging table, the same row-level security fence, the same review
queue. A reviewer sees one queue because there is only one -- not because two
writers were made to agree -- which is what the API's design buys: the client
sends an opinion and nothing else, and the server reads the snapshot off the
job it still has. There is no Java half of the review interface and there
should not be: the thing that can rewrite the golden question set is one
service with one token.

**It has to decide for itself whether to believe the server**, which a
browser never does here. The web interface is served by the nginx that
proxies the API, so it talks to its own origin and the proxy holds both the
token and the trust decision. This one opens the connection itself, so
`./launch.sh --desktop` copies the stack's CA certificate out and the client
is run with `--cacert`, which covers the API and the sign-in service alike.
`--fingerprint` -- both servers', comma-separated -- and `--insecure` are
the other two answers, and the status bar says which of them is in force
for as long as it is true.

**Docker fetches it; Java runs it.** Nothing runs a desktop application in a
container, so what the `desktop` image does is *carry* a jar -- and that keeps
the promise the rest of this repository makes, that Docker is the only thing
anyone has to install. Running it needs a Java runtime of 21 or later and
nothing else; JavaFX is inside the jar.

**The window outlives the command that opened it**, which took two things
rather than one. `nohup` is what survives the terminal being closed
afterwards; the subshell it is started in is what survives `start.sh` itself
exiting, because a process backgrounded directly is a job of that shell and
is reaped with its process group moments later. `start.sh` also waits a beat
and checks the window is still there before it claims to have opened one --
the same promise its browser half makes by waiting for the page to answer --
and prints what the client said if it stopped. Run it again and it says the
client is already open rather than putting a second window onto the same
API.

The jar is built in a Linux container for a machine that is not the
container, so `launch.sh` reads `uname`, pulls the tag for what it finds, and
builds locally only when there is nothing to pull:

```bash
./launch.sh --desktop        # fetch it and copy the CA certificate out
java -jar desktop/target/nl2sql-desktop.jar --cacert ./nl2sql-ca.crt
```

One jar is one platform. The same native library file names are used on macOS
x86-64 and arm64, so a jar carrying both would carry one of them twice under
one name and load whichever came first. That is why there is a published tag
per platform --

| Tag | For |
|---|---|
| `mcfaddja/nl2sql-desktop-build:v8_0_1-mac-aarch64` | Apple silicon |
| `mcfaddja/nl2sql-desktop-build:v8_0_1-mac` | Intel Macs |
| `mcfaddja/nl2sql-desktop-build:v8_0_1-linux` | x86-64 Linux |
| `mcfaddja/nl2sql-desktop-build:v8_0_1-linux-aarch64` | arm64 Linux |
| `mcfaddja/nl2sql-desktop-build:v8_0_1-win` | Windows |

-- and why `launch.sh` records which platform the jar beside it was built
for, and fetches again when that or a source file changes.
