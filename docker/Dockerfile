# syntax=docker/dockerfile:1

ARG PYTHON_IMAGE=python:3.12-slim
ARG POSTGRES_IMAGE=postgres:18

# --- Stage 1: generate the synthetic dataset as CSVs -------------------------
FROM ${PYTHON_IMAGE} AS generator

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
ARG DB_PASSWORD=nl2sql

# PGDATA must sit outside /var/lib/postgresql: that path is a VOLUME in the
# base image, and anything written to a volume path during build is discarded.
ENV PGDATA=/var/lib/pgdata

COPY data_gen/ddl.sql /opt/nl2sql/ddl.sql
COPY docker/init_db.sh /opt/nl2sql/init_db.sh
RUN mkdir -p "$PGDATA" \
 && chown -R postgres:postgres "$PGDATA" /opt/nl2sql \
 && chmod 700 "$PGDATA" \
 && chmod +x /opt/nl2sql/init_db.sh

# The CSVs are bind-mounted from the generator stage instead of COPYed, so they
# never become a layer in the published image.
USER postgres
RUN --mount=type=bind,from=generator,source=/out,target=/csv \
    /opt/nl2sql/init_db.sh
USER root

ENV POSTGRES_DB=${DB_NAME} \
    POSTGRES_USER=${DB_USER}

LABEL org.opencontainers.image.title="nl2sql synthetic retail Postgres" \
      org.opencontainers.image.description="Postgres preloaded with the nl2sql synthetic grocery-retail dataset"
