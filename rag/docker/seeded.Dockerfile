# Builds a publishable image with a populated cluster baked in.
# Used by publish_db_image.sh, which tars a stopped container's PGDATA volume
# into this build context first. ADD extracts the tar into the image layer.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

ENV PGDATA=/var/lib/pgdata

USER root
ADD pgdata.tar /var/lib/pgdata/
RUN chown -R postgres:postgres /var/lib/pgdata \
 && chmod 700 /var/lib/pgdata
