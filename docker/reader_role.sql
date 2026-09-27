-- The agent's read-only role.
--
-- The agent only ever reads the retail dataset, so it should not connect as
-- the owner. This file creates a login role that can SELECT from every table
-- in public and do nothing else, and it is run in two places:
--
--   * at image build time, by init_db.sh, once the data is loaded;
--   * on every start, by launch.sh and setup.sh, because a volume created
--     from an image that predates the role keeps whatever roles it had.
--
-- Run it as the superuser (inside the container local connections are
-- trusted) with three psql variables. The database comes from the connection:
--
--   psql -U postgres -d nl2sql_retail \
--        -v reader=nl2sql_reader -v reader_password=nl2sql_reader -v owner=nl2sql \
--        -f docker/reader_role.sql
--
-- Idempotent: re-running resets the password to the one given and re-grants.
-- Nothing here drops anything, and the owner's rights on the data are
-- untouched. The two revokes at the end take from PUBLIC what reading the
-- dataset never needs: connecting to the cluster's other databases, and
-- signalling other sessions.
\set ON_ERROR_STOP on

SELECT format('CREATE ROLE %I', :'reader')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'reader') \gexec

ALTER ROLE :"reader" WITH LOGIN PASSWORD :'reader_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

-- Sessions start read-only, so a client that forgets SET TRANSACTION READ ONLY
-- still cannot write. This is a default a session can switch off, not a
-- boundary; the grants below are what actually stop writes.
ALTER ROLE :"reader" SET default_transaction_read_only = on;

GRANT CONNECT ON DATABASE :"DBNAME" TO :"reader";
GRANT USAGE ON SCHEMA public TO :"reader";
GRANT SELECT ON ALL TABLES IN SCHEMA public TO :"reader";

-- Tables the owner creates later (a regenerated dataset, a new dimension) are
-- readable too, without running this file again.
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner" IN SCHEMA public
    GRANT SELECT ON TABLES TO :"reader";

-- One database, not every database. CONNECT is PUBLIC's by default on every
-- database in the cluster, so the reader's password also opened `postgres` and
-- `template1` -- where the function revokes below do not apply, and from where
-- a backend in this database is just as reachable, since pids are
-- cluster-wide. Taken from PUBLIC on every database but this one: the reader
-- keeps CONNECT here by its own explicit privilege, and the superuser connects
-- anywhere. Enumerated on each run, so a database created later is closed on
-- the next start.
SELECT format('REVOKE CONNECT ON DATABASE %I FROM PUBLIC', datname)
FROM pg_database
WHERE datname <> :'DBNAME' AND datallowconn \gexec

-- Signalling other sessions. EXECUTE on these two is PUBLIC's by default, and
-- Postgres lets any role cancel or terminate backends of the *same* role --
-- and every reader session is the same role: the agent API's pool, each
-- `docker compose run --rm agent`, and the review service validating a
-- reviewer's SQL. With them, one SELECT could cancel or kill everyone else's:
--
--   SELECT pg_terminate_backend(pid) FROM pg_stat_activity
--   WHERE usename = current_user AND pid <> pg_backend_pid()
--
-- The agent's static validator refuses both names, but that is the agent
-- being careful, and the review service's validator is a second caller that
-- never learned the list. The server is the boundary, so it is revoked here.
-- Function privileges are per database: this changes the retail database
-- only, and the superuser keeps both.
REVOKE EXECUTE ON FUNCTION pg_catalog.pg_cancel_backend(integer) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION pg_catalog.pg_terminate_backend(integer, bigint) FROM PUBLIC;
