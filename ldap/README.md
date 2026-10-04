# The directory

Who may sign in, and to what. OpenLDAP 2.6 in a container of its own
(`nl2sql-ldap`), holding people and the four groups every service grants
access by. The retail database checks a person's password against it
(pg_hba's `ldap` method), and the auth service turns its groups into
database roles -- see [`auth/README.md`](../auth/README.md) for that half.

It runs one of two ways, chosen by `LDAP_MODE` on start:

| Mode | Where people come from | Passwords | Edited |
| --- | --- | --- | --- |
| `standalone` (default) | here: a seed file on first start, then the directory page | hashed here (Argon2) | on the directory page, by `nl2sql-admins` |
| `replica` | copied from another directory -- Active Directory, another OpenLDAP, anything that answers an LDAP search -- every `LDAP_REPLICA_INTERVAL` seconds | never copied: each sign-in is passed through to the primary | on the primary, and only there |

Nothing publishes its port. Only the database and the auth service talk to
it, on the stack's own network, over StartTLS with a certificate the
directory writes into the `ldaptls` volume on its first start.

## The four groups

| Group | Database role | What it opens |
| --- | --- | --- |
| `nl2sql-users` | `nl2sql_users` | asking questions: the web interface, the desktop client, the API |
| `nl2sql-reviewers` | `nl2sql_reviewers` | the review interface, the SQL console, MLflow |
| `nl2sql-curators` | `nl2sql_curators` | the curation interface, the SQL console |
| `nl2sql-admins` | `nl2sql_admins` | the directory page; MLflow |

Every group can ask questions. The names are `LDAP_GROUPS` here and
`AUTH_GROUP_ROLES` on the auth service; which roles each service lets in is
that service's own setting (`REVIEW_REVIEWER_ROLES`, `CONSOLE_ALLOWED_ROLES`,
`MLFLOW_ALLOWED_ROLES`, ...).

## Standalone

The first start makes the base entry, the four groups, the auth service's
account (`cn=nl2sql-auth,ou=services`, password `LDAP_SERVICE_PASSWORD`) and
the first administrator: `LDAP_ADMIN_USER` (default `admin`), in every
group, with the password `LDAP_ADMIN_PASSWORD`. `setup.sh` -- or `launch.sh`,
for a `.env` written before sign-in existed -- generates both passwords into
`.env`, which only its owner can read:

```bash
grep LDAP_ADMIN_PASSWORD .env
```

Then, also on the first start only, `LDAP_SEED_FILE` is loaded, if set. Put
the file in `ldap/seed/` -- which git ignores, apart from the example -- and
name it as the container sees it:

```bash
cp ldap/seed/people.example.csv ldap/seed/people.csv    # and edit it
echo 'LDAP_SEED_FILE=/seed/people.csv' >> .env
```

After that, people are added, edited, put in groups, given passwords,
unlocked and removed on the directory page (`https://localhost:8084`, for
`nl2sql-admins`), whose import takes the same two formats. A directory that
already has people is never re-seeded: changing the file afterwards changes
nothing, which is what keeps a restart from undoing the page's edits.

### The file

**CSV**, with a header row naming its columns, in any order and any case:

```csv
uid,given_name,surname,display_name,mail,groups,password,password_hash
alice,Alice,Smith,,alice@example.com,nl2sql-users;nl2sql-reviewers,,
```

Only `uid` is required (`username` and `login` are read as it, `email` as
`mail`, `first_name`/`last_name` as the two names). `groups` is separated by
`;`. A person can be given a `password`, which the directory hashes as it
stores it, or a `password_hash` another directory already made (`{ARGON2}`,
`{SSHA512}`, `{SSHA384}`, `{SSHA256}`, `{SSHA}` or `{CRYPT}`); with neither
they exist but cannot sign in until someone sets one on the page.

A user name is lower case, starts with a letter or a digit, and is at most 63
characters of letters, digits, `.`, `_` and `-` -- it becomes a database role
name, and Postgres would fold or reject anything else.

**LDIF**, as any directory exports it (`ldapsearch -LLL`, AD's `ldifde`):
entries with a `person` object class become people, `groupOfNames` and
`groupOfUniqueNames` become groups whose members are those people. Only
additions are read.

A row that cannot be used is reported, by line, and skipped: one typo does
not stop the other two hundred people.

### Passwords

Hashed with Argon2. At least `LDAP_MIN_PASSWORD_LENGTH` characters (12).
`LDAP_LOCKOUT_FAILURES` wrong ones in a row (5) lock the account for
`LDAP_LOCKOUT_SECONDS` (900), which the directory page can also clear early.
A person changes their own with `POST /auth/password` on the auth service,
which every web interface offers beside its sign-out.

Over the network, a password is only accepted on an encrypted connection
(`LDAP_REQUIRE_TLS`, on); the auth service and the database both use
StartTLS and verify the directory's certificate.

## Replica

```bash
# .env
LDAP_MODE=replica
LDAP_UPSTREAM_URI=ldaps://dc1.example.com:636
LDAP_UPSTREAM_FLAVOUR=ad
LDAP_UPSTREAM_BASE_DN=dc=example,dc=com
LDAP_UPSTREAM_BIND_DN=CN=svc-nl2sql,OU=Service Accounts,DC=example,DC=com
LDAP_UPSTREAM_BIND_PASSWORD=...
LDAP_UPSTREAM_CACERT=/seed/example-ca.pem
LDAP_REPLICA_GROUPS=CN=NL2SQL Users,OU=Groups,DC=example,DC=com=nl2sql-users;CN=NL2SQL Reviewers,OU=Groups,DC=example,DC=com=nl2sql-reviewers
```

Every `LDAP_REPLICA_INTERVAL` seconds (60) it searches the primary, page by
page, for people (`LDAP_UPSTREAM_USER_FILTER`) and groups
(`LDAP_UPSTREAM_GROUP_FILTER`), resolves nested groups, and makes this
directory match: people added, changed and removed, group memberships set.
`LDAP_UPSTREAM_FLAVOUR` picks the filters and the login attribute that suit
the primary -- `ad` (`sAMAccountName`, disabled accounts left out),
`openldap`, or `generic`, the default -- and any of them can be set on its
own.

What it never copies is a password. A sign-in against the replica is passed
through to the primary (slapd's `remoteauth` overlay), so a password changed
or an account disabled there is in force here at once, and the primary keeps
its own lockout count.

By default only people in one of the mapped groups are copied
(`LDAP_REPLICA_ONLY_GROUP_MEMBERS`). `LDAP_REPLICA_GROUPS` maps the
primary's groups onto the four, `upstream=local;upstream=local`, split on the
*last* `=` so an upstream group can be named by its DN; unset, a group of the
same name is looked for. A copy that would empty a directory that has people
in it is refused, and logged, unless `LDAP_REPLICA_ALLOW_EMPTY=true`: a
primary that answered a search with nothing is more often broken than empty.

A replica is read-only. It has no directory page -- the page's container
refuses to start beside one -- and the auth service answers every directory
route with `404 replica_read_only`, and a password change with `409`. When it last copied, and whether that
worked, is on the auth service's `/readyz`.

## Settings

Read from the environment; compose passes each from `.env`. A `_FILE`
variant of each password (`LDAP_ADMIN_PASSWORD_FILE`, ...) reads it from a
file instead, for Docker secrets.

| Variable | Default | |
| --- | --- | --- |
| `LDAP_MODE` | `standalone` | or `replica` |
| `LDAP_BASE_DN` | `dc=nl2sql,dc=local` | this directory's own base |
| `LDAP_ORGANISATION` | `nl2sql` | |
| `LDAP_SERVICE_PASSWORD` | -- | the auth service's account; generated into `.env` |
| `LDAP_ADMIN_USER` | `admin` | the first administrator (standalone) |
| `LDAP_ADMIN_PASSWORD` | -- | their password; generated into `.env` |
| `LDAP_ADMIN_NAME` | `Directory administrator` | |
| `LDAP_SEED_FILE` | -- | CSV or LDIF loaded on the first start (standalone) |
| `LDAP_GROUPS` | the four above | comma-separated |
| `LDAP_TLS_CERT_FILE` | `/etc/nl2sql/ldap-tls/ldap.crt` | |
| `LDAP_TLS_KEY_FILE` | `/etc/nl2sql/ldap-tls/ldap.key` | |
| `LDAP_TLS_GENERATE` | `true` | write a self-signed certificate when there is none |
| `LDAP_TLS_HOSTNAMES` | `nl2sql-ldap,ldap,localhost` | the names it is issued for |
| `LDAP_TLS_DAYS` | `825` | |
| `LDAP_REQUIRE_TLS` | `true` | refuse a password over an unencrypted connection |
| `LDAP_LOCKOUT_FAILURES` | `5` | wrong passwords before a lockout; 0 never locks (standalone) |
| `LDAP_LOCKOUT_SECONDS` | `900` | |
| `LDAP_MIN_PASSWORD_LENGTH` | `12` | |
| `LDAP_LOG_LEVEL` | `stats` | slapd's |
| `LDAP_UPSTREAM_URI` | -- | the primary (replica) |
| `LDAP_UPSTREAM_FLAVOUR` | `generic` | `ad`, `openldap` or `generic` |
| `LDAP_UPSTREAM_BIND_DN` | -- | an account that can read people and groups there |
| `LDAP_UPSTREAM_BIND_PASSWORD` | -- | |
| `LDAP_UPSTREAM_BASE_DN` | -- | |
| `LDAP_UPSTREAM_USER_BASE` | the base | |
| `LDAP_UPSTREAM_GROUP_BASE` | the base | |
| `LDAP_UPSTREAM_USER_FILTER` | the flavour's | |
| `LDAP_UPSTREAM_GROUP_FILTER` | the flavour's | |
| `LDAP_UPSTREAM_LOGIN_ATTRIBUTE` | the flavour's | `uid`, or `sAMAccountName` for AD |
| `LDAP_UPSTREAM_MEMBER_ATTRIBUTE` | `member` | |
| `LDAP_UPSTREAM_STARTTLS` | `false` | for an `ldap://` primary |
| `LDAP_UPSTREAM_CACERT` | -- | the primary's CA, as a path inside the container (`/seed/...`) |
| `LDAP_UPSTREAM_TLS_VERIFY` | `true` | |
| `LDAP_UPSTREAM_PAGE_SIZE` | `500` | |
| `LDAP_UPSTREAM_TIMEOUT` | `10` | seconds |
| `LDAP_REPLICA_GROUPS` | the four, by name | `upstream=local;...` |
| `LDAP_REPLICA_INTERVAL` | `60` | seconds between copies |
| `LDAP_REPLICA_ONLY_GROUP_MEMBERS` | `true` | |
| `LDAP_REPLICA_ALLOW_EMPTY` | `false` | |

## Inside the container

`python3 -m nl2sql_ldap` with a command:

| Command | |
| --- | --- |
| `serve` | the default: render `slapd.conf`, start slapd, prepare the directory, and -- for a replica -- copy on the interval |
| `health` | exit 0 when the directory answers on its local socket (the health check) |
| `import FILE` | load a CSV or LDIF now (standalone) |
| `sync` | copy from the primary now (replica) |
| `config` | print the `slapd.conf` these settings produce |

```bash
docker compose --profile api --profile auth exec ldap python3 -m nl2sql_ldap sync
```

Nothing runs as root for longer than it takes to start. The entry point
begins as root only to hand the `ldap` user the directories it writes -- a
named volume another container mounted first is created owned by root, which
under compose is what happens to the certificate's -- and then becomes
`ldap` before it does anything else, so slapd and everything it starts run
as that user. A `docker exec` of `python3 -m nl2sql_ldap ...`, and the
health check, start as root and give it up the same way; run OpenLDAP's own
tools with `docker compose exec -u ldap`. The `ldap` user is also the
directory's root -- on the local socket only, by its peer credentials -- so
there is no root password to keep.

## Tests

`tests/ldap/` -- the settings, the file formats, the directory's operations,
`slapd.conf`, the replica's copy and the supervisor, offline and at 100%; and
`tests/ldap/test_ldap_image.py` (`--run-docker`), which builds the image,
checks both modes' `slapd.conf` with slaptest, runs a replica against a
second directory end to end, and repeats compose's order of creation -- the
directory's container, then one whose image owns the certificate's folder as
root, on the same volume -- to show the directory still writes its
certificate and that slapd and the supervisor run as `ldap`. 6.0.0's image
fails that last one, which is what the published stack found.
