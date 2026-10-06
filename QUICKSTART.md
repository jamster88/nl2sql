# Quick start

Ask questions about a retail database in plain English, and get back the
answer, a chart, the rows and the SQL that produced them. This page gets you
from nothing to your first answer. [`USAGE_GUIDE.md`](USAGE_GUIDE.md) covers
everything else.

## You need

- **Docker** with Compose v2 (Docker Desktop on macOS and Windows), and about
  3 GB of disk for the images.
- **An Ollama host serving a chat model with tool support** -- this machine
  or another on your network.
- **Ollama on this machine** (<https://ollama.com>), for the embedding model.
  `start.sh` starts it and pulls `bge-m3` into it for you.
- **Optional:** a Java runtime of 21 or later, for the desktop client.

On Windows, run the scripts from WSL.

## 1. Get it

```bash
git clone https://github.com/jamster88/nl2sql.git
cd nl2sql
```

## 2. Point it at your Ollama host

Check the host answers, and pick a model from the list it prints (http, not
https):

```bash
curl -s http://<your-ollama-host>:11434/api/tags
```

Then set the stack up against it -- once, a few minutes, mostly downloads:

```bash
./setup.sh --gui --ollama-url http://<your-ollama-host>:11434 --model <model>
```

## 3. Start it

```bash
./start.sh
```

It starts every container, checks each one is ready, and opens
<https://localhost:8080> in your browser. From now on, `./start.sh` is all it
takes: seconds rather than minutes.

The page is HTTPS with a certificate from a CA the stack made itself, so the
browser warns about it the first time; accept it, or trust `nl2sql-ca.crt` --
once, for every page -- as [`USAGE_GUIDE.md`](USAGE_GUIDE.md#signing-in) shows. Then sign in as
`admin`, with the password the first run generated into `secrets/`:

```bash
cat secrets/ldap_admin_password
```

Everyone else gets their own account on the directory page,
<https://localhost:8084>, where `admin` can add them. `./start.sh --no-auth`
starts without sign-in.

## 4. Ask

Type a question and press Enter. Name a measure, a grain and a period:

```text
What were the top 5 product departments by net sales in fiscal year 2024?
Which 3 stores had the highest average net sales per basket?
total net sales for dairy and eggs in FY2025
```

An answer takes about a minute; the steps show as they happen. The fiscal
year starts on 1 April. Under each answer, say whether it was **Correct**,
**Wrong** or **Correct but incomplete**.

From a terminal instead:

```bash
docker compose run --rm agent "How many stores are there?"
```

## More

```bash
./start.sh --desktop                      # the desktop client instead of the browser
./start.sh --review                       # and review the verdicts people gave
./start.sh --console                      # and the SQL console, for working out a wrong answer
./start.sh --mlflow                       # and MLflow: everything the agent did, per question
./start.sh --curate                       # and write what it learns from: SQL snippets, golden pairs, fixes
./start.sh --review --curate --console --mlflow   # every page
```

| Page | Address |
|---|---|
| Web interface | <https://localhost:8080> |
| Review interface | <https://localhost:8081> |
| Curation interface | <https://localhost:8083> |
| SQL console | <https://localhost:8082> |
| MLflow | <https://localhost:5001> |
| Directory page | <https://localhost:8084> |

## Stop

```bash
docker compose --profile '*' down         # everything; your data is kept
```

## If something is wrong

`start.sh` checks the stack before you ask anything, and says what is wrong
and what to do. The usual ones:

- **`could not reach the chat host`** -- the Ollama host is down or the URL
  is wrong. Re-run `./setup.sh --ollama-url ...`.
- **`... does not have <model>`** -- pull it on that host
  (`ollama pull <model>`), or re-run `./setup.sh --model` with one it has.
- **`does not have bge-m3`** -- run `ollama pull bge-m3` on this machine.
  The agent still answers without it, from the schema alone.
- **A page never answered** -- the warning names the logs to read, such as
  `docker compose --profile api --profile gui logs gui`.

[`USAGE_GUIDE.md`](USAGE_GUIDE.md#troubleshooting) has the rest.
