# Running the agent and asking questions

A practical guide to launching the NL2SQL agent and getting answers out of it.
For how it works internally and how to extend it, see
[`README.md`](README.md).

## Before you start

You need three things:

1. **Docker running**, with this repo as the working directory.
2. **The Postgres container**, which compose starts for you automatically.
3. **A reachable Ollama host.** The default is `http://192.168.44.129:11434`.
   Check it before debugging anything else:

   ```bash
   curl -s http://192.168.44.129:11434/api/tags | head -c 200
   ```

   That should print a JSON list of models. Note it is **http**, not https --
   port 11434 does not terminate TLS, and an `https://` URL fails with an SSL
   error.

The first run builds the agent image, which takes a minute or two. Later runs
start immediately.

## Launching

### Ask one question and exit

```bash
docker compose run --rm agent "How many stores are there?"
```

This is the normal way to use it. Compose starts Postgres, waits for its health
check, runs your question, and removes the agent container afterward. The
database keeps running.

### Interactive session

Leave the question off to get a prompt that keeps the connection open:

```bash
docker compose run --rm agent
```

```
Connected to qwen3.8:latest at http://192.168.44.129:11434.
Ask a question, or Ctrl-D to exit.

> How many vendors are there?
```

Each question is answered independently -- the agent does not remember the
previous one, so ask complete questions rather than follow-ups like "and by
store?".

Ctrl-D exits.

### Pipe questions in

```bash
echo "How many vendors are there?" | docker compose run --rm -T agent
```

The `-T` matters: without it, compose allocates a TTY and the pipe will not be
read.

## Reading the output

Progress goes to **stderr**, the answer goes to **stdout**:

```
[tables] fact_pos_retail_sales, dim_product, dim_date     <- tables it chose
[schema] 4,812 characters of context                      <- schema + samples it read
[sql] SELECT p.department_name, SUM(...)                  <- the query it wrote
[validation] valid                                        <- EXPLAIN + model review
[result] 5 row(s)

department_name | total_net_sales
----------------+----------------
Meat & Seafood  | 821785.92
```

Because the streams are separate, you can keep just the answer:

```bash
docker compose run --rm agent "How many stores are there?" 2>/dev/null
```

Or silence the progress lines entirely with `--quiet`.

### Structured output for scripts

`--json` prints the question, chosen tables, generated SQL, and rows as one
JSON object:

```bash
docker compose run --rm agent --json "Which 3 promotions had the highest total promo quantity sold?"
```

```json
{
  "question": "Which 3 promotions had the highest total promo quantity sold?",
  "selected_tables": ["fact_promo_performance", "dim_promotion"],
  "sql": "SELECT\n  p.promotion_name,\n  SUM(f.promo_quantity_sold) AS ...",
  "error": null,
  "result": {
    "columns": ["promotion_name", "total_promo_quantity_sold"],
    "rows": [["Back to School Flash Sale", "203812.674"]],
    "row_count": 3,
    "truncated": false
  }
}
```

The exit code is 0 on success and 1 when the agent could not answer, so it
works in a shell pipeline. Pull out just the SQL with
`... --json | jq -r .sql`.

## Choosing a model or host

```bash
docker compose run --rm agent --model gemma4:12b-mlx "How many stores are there?"
docker compose run --rm agent --base-url http://other-host:11434 "..."
```

List what a host has available with
`curl -s http://192.168.44.129:11434/api/tags | jq -r '.models[].name'`.
Pick a model with **tool support** -- table selection and validation use
structured output, which needs it.

For a permanent change, set `OLLAMA_MODEL` or `OLLAMA_BASE_URL` in the
environment or in `docker-compose.yml` instead of passing flags each time.

Add `--reasoning` for hard questions. It lets the model think before answering,
which costs noticeably more time but helps on questions involving several
joins or subtle grain.

## Example questions that work

These have all been run against the shipped dataset:

| Question | What it exercises |
|---|---|
| `How many stores are there?` | single table |
| `How many vendors are there?` | single table |
| `What were the top 5 product departments by net sales in fiscal year 2024?` | 3-table join, fiscal calendar, group + order |
| `Which 3 promotions had the highest total promo quantity sold?` | fact/dimension join on promotions |
| `Which 3 stores had the highest average net sales per basket?` | subquery to basket grain, then averaging |

### Writing questions that land

The dataset is a grocery retail star schema, so questions work best when they
name three things: **a metric**, **a grain**, and optionally **a time period**.

- Good: *"top 5 departments by net sales in fiscal year 2024"* -- metric
  (net sales), grain (department), period (FY2024).
- Harder: *"how are we doing?"* -- no metric or grain, so the agent has to
  guess at both.

Useful vocabulary from this schema: net sales, gross sales, markdown, quantity
sold, promo quantity sold, ad spend, impressions, competitor price, market
share, department, category, brand, store, banner, vendor, fiscal year, fiscal
week, promo cycle.

Remember that fiscal year `Y` starts April 1 of `Y-1`, so "fiscal year 2024" is
not the 2024 calendar year. Say "calendar 2024" if that is what you mean.

## When it cannot answer

The agent validates every query before running it and retries up to three
times, feeding the specific problems back to the model. If it still cannot
produce a valid query, it stops rather than executing anything:

```
failed: Could not produce a valid query in 3 attempts. Last problems: ...
```

That is the expected outcome for questions the data cannot answer, and for
anything that asks to modify data -- the agent is read-only by design, so
"delete every row from dim_store" is refused rather than attempted.

Raising `--max-attempts` helps when a question is answerable but awkward.
Rephrasing with an explicit metric and grain usually helps more.

## Troubleshooting

The agent checks the Ollama host and model before doing anything else, so a bad
URL or model name fails immediately with a one-line message and exit code 2:

```
error: Cannot reach Ollama at http://127.0.0.1:1. Check that the host is
running and reachable, and note that port 11434 serves http, not https.

error: Model `no-such-model:latest` not found in Ollama. Please pull the model
(using `ollama pull no-such-model:latest`) or specify a valid model name.
Available local models: gemma4:12b-mlx, qwen3.6:latest, qwen3.8:latest,
qwen3-coder-next:latest
```

| Symptom | Cause | Fix |
|---|---|---|
| `Cannot reach Ollama at ...` | host unreachable from the container, or `https://` was used | Check the URL and that Ollama is listening; confirm with the `curl` above |
| `Model ... not found in Ollama` | model not pulled on that host | `ollama pull <model>` on the host, or pick one from the list in the message |
| `does not provide tool support` or malformed table picks | model lacks tool/structured-output support | Choose a model whose `capabilities` include `tools` |
| Answers reference columns that do not exist | schema context truncated | Raise `OLLAMA_NUM_CTX` (default 16384) |
| Query times out | statement exceeded 30s | Narrow the question, or raise `STATEMENT_TIMEOUT_MS` |
| Results cut off with `... truncated at 50 rows` | row cap | Raise `--max-rows` |

Exit codes: `0` answered, `1` could not answer, `2` Ollama misconfigured.

## Stopping

The agent container removes itself after each question. The database keeps
running until you stop it:

```bash
docker compose stop postgres     # keeps data
docker compose down              # removes the container, keeps data
docker compose down -v           # also deletes the volume, resetting to the shipped dataset
```
