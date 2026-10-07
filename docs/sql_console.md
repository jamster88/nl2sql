# The SQL console

*Part of the [nl2sql documentation](../README.md#documentation).*

```bash
./start.sh --console
```

When the agent answers a question wrongly, the questions worth asking next
are about the database it read and the gates in front of it -- would the
validator have passed this query, what did the planner estimate, did it
finish inside the timeout, what do the rows actually say -- and each has an
exact answer that only running SQL can give. The SQL console is where that
SQL is run, at <https://localhost:8082>, as the agent would run it: as
`nl2sql_reader`, inside a read-only transaction, under the agent's statement
timeout, through the agent's own static validator and planner gate.

**Beside every result, what the agent would have done with it.** Which gate
would have refused the query, in that gate's own words -- the message its
Repair Agent would have been handed -- and the estimated cost against the
`MAX_PLAN_COST` ceiling. A result longer than the agent reads says so,
because the agent would have seen the first 50 rows and been told the rest
were cut off.

**Three ways to run it.** *Run* returns the rows, each column headed by its
type, with NULL shown as NULL. *Plan* stops at `EXPLAIN`, which is exactly
the agent's planner gate. *Analyze* runs the query to time it, and shows what
really happened beside each of the planner's estimates.

**The schema is the agent's.** The browser on the left is the introspection
the agent's prompt is built from, comments and keys included, and *Agent's
view* on a table shows the block of the prompt that describes it, sample rows
and all -- when the agent picked the wrong column, that text is usually why.

It runs from the agent's image, `python -m nl2sql_agent.console`, so every
one of those answers is the agent's code rather than a copy of it; and it is
its own process, on its own port, behind its own token, because the API runs
SQL the pipeline wrote and this runs SQL a person typed. Both of its ports
are published on this machine only unless `CONSOLE_BIND_ADDRESS` says
otherwise, and the database refuses what the validator does not catch:
`SELECT lo_create(0)` passes the one and is stopped by the other.

[`console/README.md`](../console/README.md) has the whole of it.
