# Sign-in

*Part of the [nl2sql documentation](../README.md#documentation).*

```bash
./start.sh                       # sign-in is on: every page asks who you are
cat secrets/ldap_admin_password  # the first person, admin
open https://localhost:8084      # add everyone else
```

Who may use what is decided by the retail database and a directory beside
it. A person signs in with the user name and password the directory holds;
**Postgres checks that password itself** -- pg_hba's `ldap` method, which
the `dbprep` one-shot writes on every start -- and the groups they are in become the database roles
they hold. Every page, the desktop client, the REST API, the SQL console,
the review service and MLflow then believe the session that sign-in issued,
and what a person asks or runs, runs as their own database role.

| Group | Lets in |
|---|---|
| `nl2sql-users` | the web interface, the desktop client, the API |
| `nl2sql-reviewers` | the review interface, the SQL console, MLflow |
| `nl2sql-curators` | the curation interface, the SQL console |
| `nl2sql-admins` | the directory page, MLflow |

Everyone in a group can ask questions. Three containers make it work, all
started with the API:

| Container | Port | |
|---|---|---|
| `nl2sql-ldap` | none | the directory: OpenLDAP, **standalone** -- people loaded from a file on its first start and edited on the directory page -- or a **read-only replica** of another directory, Active Directory or any LDAP server, copied on an interval with every password passed through to it. See [`ldap/README.md`](../ldap/README.md) |
| `nl2sql-auth` | 8446 | signs people in, issues the session, keeps the database's roles in step with the directory, and answers the directory page and MLflow's front door. See [`auth/README.md`](../auth/README.md) |
| `nl2sql-directory-gui` | 8084 | the directory page, for `nl2sql-admins`; standalone only |

Every page is HTTPS, so a password is never typed into a page served in
clear, and since 6.1 every server has a certificate of its own: a one-shot
container, `nl2sql-pki`, keeps a development CA for the stack and issues
each one a key that no other container holds. A browser warns until this
machine is told to trust that CA, once, for every page --
`docker compose --profile api cp api:/etc/nl2sql/tls/ca.crt
./nl2sql-ca.crt`, then add it to the system's trust store (Keychain Access
on macOS, `update-ca-certificates` on Debian and Ubuntu) -- or until real
certificates are mounted. `TLS_EXTRA_HOSTNAMES` adds this machine's name to
every certificate, for a browser elsewhere. Behind something that terminates
TLS itself, `GUI_TLS_ENABLED=false` makes every page plain again; the
terminator must send `X-Forwarded-Proto: https`, which the pages pass on so
the session cookie stays `Secure`.

A person's password reaches the database only over TLS: the retail
database serves TLS and refuses everything over the network without it,
and the auth service verifies its certificate (`verify-full`). The
directory's own API -- the routes that make people -- answers on the auth
service's port 8447, which is not published: only the directory page
reaches it. [`SECURITY.md`](SECURITY.md) says what each of these protects,
from whom, and what is still open.

Off with `--no-auth` for one run of `start.sh` or `launch.sh`, or
`./setup.sh --no-auth` -- `AUTH_ENABLED=false` in `.env` -- for good: no
directory, no auth service, and every page and port as it was before, open
to whoever can reach it unless a static token is set.

The directory GUI: 61 tests, at 100% of statements, branches, functions and
lines.
