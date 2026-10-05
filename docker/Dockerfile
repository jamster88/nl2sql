# syntax=docker/dockerfile:1

ARG PYTHON_IMAGE=python:3.12-slim
ARG POSTGRES_IMAGE=postgres:18

# --- Stage 1: generate the synthetic dataset as CSVs -------------------------
# Pinned to the build machine's own platform: on a multi-platform build every
# target architecture then loads these same CSVs, so the data is identical
# across images instead of being regenerated per architecture (and pandas
# never runs under emulation).
FROM --platform=$BUILDPLATFORM ${PYTHON_IMAGE} AS generator

WORKDIR /app/data_gen
COPY data_gen/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY data_gen/ ./
COPY docker/emit_load_sql.py /opt/emit_load_sql.py

# Extra generate_data.py flags, e.g. GEN_ARGS="--scale 3 --seed 7".
ARG GEN_ARGS=""
ENV PYTHONPATH=/app/data_gen
RUN python generate_data.py --no-sqlite --output-dir /out ${GEN_ARGS} \
 && python /opt/emit_load_sql.py /csv /out/_load.sql

# --- Stage 2: Postgres with that dataset already loaded ----------------------
FROM ${POSTGRES_IMAGE} AS db

ARG DB_NAME=nl2sql_retail
ARG DB_USER=nl2sql
# The agent's read-only role (docker/reader_role.sql). DB_USER above owns the
# data and is what loads it; the agent never connects as that.
#
# No password is an argument any more (v1_2). Before, the superuser's and the
# owner's were `nl2sql` inside every copy of the image, and anyone who could
# reach the port could become the superuser with them. Now the image carries
# none: the entrypoint sets the owner's and the reader's from the environment
# on every start (POSTGRES_PASSWORD, POSTGRES_READER_PASSWORD), and the
# superuser has none at all.
ARG DB_READER=nl2sql_reader

# PGDATA must sit outside /var/lib/postgresql: that path is a VOLUME in the
# base image, and anything written to a volume path during build is discarded.
ENV PGDATA=/var/lib/pgdata

COPY data_gen/ddl.sql /opt/nl2sql/ddl.sql
COPY docker/reader_role.sql /opt/nl2sql/reader_role.sql
COPY docker/init_db.sh /opt/nl2sql/init_db.sh
COPY docker/entrypoint.sh /usr/local/bin/nl2sql-entrypoint.sh
RUN mkdir -p "$PGDATA" /etc/nl2sql/pg-tls \
 && chown -R postgres:postgres "$PGDATA" /opt/nl2sql /etc/nl2sql/pg-tls \
 && chmod 700 "$PGDATA" \
 && chmod +x /opt/nl2sql/init_db.sh /usr/local/bin/nl2sql-entrypoint.sh

# The CSVs are bind-mounted from the generator stage instead of COPYed, so they
# never become a layer in the published image.
USER postgres
RUN --mount=type=bind,from=generator,source=/out,target=/csv \
    /opt/nl2sql/init_db.sh
USER root

ENV POSTGRES_DB=${DB_NAME} \
    POSTGRES_USER=${DB_USER} \
    POSTGRES_READER_USER=${DB_READER} \
    POSTGRES_TLS_DIR=/etc/nl2sql/pg-tls

# Its certificate, its transport rules and its passwords, then Postgres --
# on every start, so a volume an older image made is brought up to date too.
# See docker/entrypoint.sh.
ENTRYPOINT ["nl2sql-entrypoint.sh"]
CMD ["postgres"]

LABEL org.opencontainers.image.title="nl2sql synthetic retail Postgres" \
      org.opencontainers.image.description="Postgres preloaded with the nl2sql synthetic grocery-retail dataset; TLS, and no password baked in" \
      org.opencontainers.image.version="1.2.0"
