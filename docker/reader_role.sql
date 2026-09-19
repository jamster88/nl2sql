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
-- Nothing here drops or revokes anything, and the owner role is untouched.
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
