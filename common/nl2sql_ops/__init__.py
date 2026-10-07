"""The stack's databases, prepared by a one-shot rather than by shell (V6-41).

Until 6.3 `launch.sh` and `setup.sh` did this with `docker compose exec ...
psql` as each database's superuser: the agent's reader, the roles sign-in
hangs from, the sign-in lines in pg_hba.conf, every store's password, and
the checks that say what each database holds. 3,200 lines of shell, tested
against a fake `docker` that accepted an `exec` with a missing variable --
which is how the first of 6.0's four shipped defects got through.

Now compose runs `python -m nl2sql_ops prepare` as the `dbprep` service
before anything that needs a prepared database, the way it runs the pki
service before anything that serves TLS. It reaches each database through
that database's own socket, shared with it in a volume nothing else mounts:
inside a Postgres container the socket trusts whoever connects, so holding
it is holding the superuser, and the one-shot is the only thing that does.

* `retail`  -- the reader, pg_trgm, sign-in's roles and its pg_hba lines.
* `stores`  -- the four runtime stores in their one server (V6-40):
  each its own database, owner and password.
* `passwords` -- the knowledge stores' and MLflow's, from their secrets.
* `report`  -- what each database holds, for the scripts to show.
* `hba`     -- pg_hba.conf rewritten through SQL, checked before it is used.

Run as `python -m nl2sql_ops prepare | report | snippets`.
"""
