-- The roles that sign-in is built from, in the retail database's cluster.
--
-- People are not created here. The auth service's role sync makes one login
-- role per person in the directory's groups (nl2sql_auth/rolesync.py); this
-- file makes what those roles hang from, and the role that does the making:
--
--   nl2sql_ldap        a marker, no privileges. Every person the sync made is
--                      a member, and pg_hba sends exactly its members to the
--                      directory to check their password.
--   nl2sql_users       may read the retail tables, as the agent's reader can.
--                      Asking a question needs this.
--   nl2sql_reviewers   the review interface, the SQL console, MLflow;
--   nl2sql_curators    the curation interface, the SQL console;
--   nl2sql_admins      the directory's web interface. Each of these three is
--                      a member of nl2sql_users, so anyone who may review,
--                      curate or administer may also ask.
--   <rolesync>         logs in as the sync. CREATEROLE, and ADMIN on the five
--                      above and nothing else: Postgres lets a CREATEROLE role
--                      change only roles it holds ADMIN on, so the sync can
--                      make and remove people but cannot touch the owner, the
--                      reader, or any role it did not create.
--
-- Run it as the superuser on every start, after reader_role.sql, with four
-- psql variables:
--
--   psql -U postgres -d nl2sql_retail \
--        -v reader=nl2sql_reader -v owner=nl2sql \
--        -v rolesync=nl2sql_rolesync -v rolesync_password=... \
--        -f docker/auth_roles.sql
--
-- Idempotent. Nothing here drops anything, and a person's role is never
-- touched: what they hold is the sync's to decide.
\set ON_ERROR_STOP on
SET client_min_messages = warning;

SELECT format('CREATE ROLE %I NOLOGIN', name)
FROM unnest(ARRAY['nl2sql_ldap', 'nl2sql_users', 'nl2sql_reviewers', 'nl2sql_curators', 'nl2sql_admins']) AS name
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = name) \gexec

-- Group roles never log in, whatever someone did to them by hand.
ALTER ROLE nl2sql_ldap NOLOGIN;
ALTER ROLE nl2sql_users NOLOGIN;
ALTER ROLE nl2sql_reviewers NOLOGIN;
ALTER ROLE nl2sql_curators NOLOGIN;
ALTER ROLE nl2sql_admins NOLOGIN;

-- What a person reads the data with: the reader's own grants, on a role of
-- their own, so their SQL runs as them (SET LOCAL ROLE) and reads what the
-- reader would.
GRANT CONNECT ON DATABASE :"DBNAME" TO nl2sql_users;
GRANT USAGE ON SCHEMA public TO nl2sql_users;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO nl2sql_users;
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner" IN SCHEMA public
    GRANT SELECT ON TABLES TO nl2sql_users;

-- Reviewers, curators and administrators can ask too. Inherited, so the
-- SELECT reaches them; SET FALSE, so holding one of these is not a way to
-- become nl2sql_users with SET ROLE -- nothing needs to.
GRANT nl2sql_users TO nl2sql_reviewers, nl2sql_curators, nl2sql_admins
    WITH INHERIT TRUE, SET FALSE;

-- The sync's own login.
SELECT format('CREATE ROLE %I', :'rolesync')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'rolesync') \gexec

ALTER ROLE :"rolesync" WITH LOGIN PASSWORD :'rolesync_password'
    CREATEROLE NOSUPERUSER NOCREATEDB NOREPLICATION NOBYPASSRLS NOINHERIT;

-- ADMIN, to hand these out; neither inherited nor SET-able, so the sync's
-- login reads no data and cannot become any of them.
GRANT nl2sql_ldap, nl2sql_users, nl2sql_reviewers, nl2sql_curators, nl2sql_admins
    TO :"rolesync" WITH ADMIN TRUE, INHERIT FALSE, SET FALSE;

-- The roles the sync made before this start belong to the sync whatever the
-- grantor of record. A superuser recreating the sync role (a new volume for
-- the auth service, a renamed role) would otherwise leave every person made
-- by the old one beyond the new one's reach. Only where it is missing: a
-- grant the sync already holds, made again here, is a second membership
-- with the superuser as its grantor.
SELECT format('GRANT %I TO %I WITH ADMIN TRUE, INHERIT FALSE, SET FALSE', person.rolname, :'rolesync')
FROM pg_auth_members marker
JOIN pg_roles person ON person.oid = marker.member
WHERE marker.roleid = 'nl2sql_ldap'::regrole
  AND person.rolname <> :'rolesync'
  AND NOT EXISTS (
      SELECT 1 FROM pg_auth_members held
      WHERE held.roleid = person.oid AND held.member = :'rolesync'::regrole AND held.admin_option
  ) \gexec

-- And the agent's reader may become any of them for one transaction: that
-- is how a question runs as the person who asked it. The sync grants this
-- as it makes each person; this repairs it for a reader recreated since,
-- and only there, for the same reason.
SELECT format('GRANT %I TO %I WITH INHERIT FALSE, SET TRUE', person.rolname, :'reader')
FROM pg_auth_members marker
JOIN pg_roles person ON person.oid = marker.member
WHERE marker.roleid = 'nl2sql_ldap'::regrole
  AND person.rolname NOT IN (:'rolesync', :'reader')
  AND NOT EXISTS (
      SELECT 1 FROM pg_auth_members held
      WHERE held.roleid = person.oid AND held.member = :'reader'::regrole
  ) \gexec
