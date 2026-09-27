# Appended to chunkdb.Dockerfile or vectordb.Dockerfile by publish_db_image.sh,
# which writes the two into one build context -- so this is a fragment that
# continues the base image's stage, not a Dockerfile of its own. The base
# supplies everything but the data: the Postgres image, PGDATA outside the
# declared VOLUME, the directory's ownership and the labels.
#
# It restores a logical dump into a cluster initialised *here*, for whichever
# platform this build is for. What it replaces -- a tar of a stopped
# container's PGDATA -- was one machine's data directory, and Postgres does not
# promise a data directory moves between architectures, so every image built
# that way was arm64 only. A dump does move: the same SQL, restored natively
# under each platform of a `docker buildx build --platform ...`.
#
# cluster.sql is `pg_dumpall` of the store, minus the CREATE of the bootstrap
# role that initdb has just made; its ALTER ROLE still carries the password
# hash across. pg_hba.conf is the store's own, copied verbatim, so the image
# authenticates exactly as the one it replaces.

ARG DB_USER=ragproc

COPY cluster.sql pg_hba.conf /tmp/restore/

RUN set -eu; \
    gosu postgres initdb --username="$DB_USER" --encoding=UTF8 --locale=en_US.utf8 >/dev/null; \
    install -o postgres -g postgres -m 600 /tmp/restore/pg_hba.conf "$PGDATA/pg_hba.conf"; \
    gosu postgres pg_ctl -o "-c listen_addresses=''" -w start >/dev/null; \
    gosu postgres psql -X -q -v ON_ERROR_STOP=1 --username="$DB_USER" --dbname=postgres \
        --file=/tmp/restore/cluster.sql >/dev/null; \
    gosu postgres pg_ctl -m fast -w stop >/dev/null; \
    rm -rf /tmp/restore
