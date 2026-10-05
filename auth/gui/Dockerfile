# The directory's web interface: built with node, served by nginx, with no
# node in the result.
#
# Its own npm project and image, like every page of this stack: it decides who
# may sign in to all the others, and an entry point in another project would
# be served by that project's image to anyone who could reach it. Its backend
# is the auth service.
#
# Only for a standalone directory: beside a replica it refuses to start (see
# 10-nl2sql-directory-config.envsh), and the auth service has no directory
# routes to serve it anyway.

FROM --platform=$BUILDPLATFORM node:26-alpine AS build

WORKDIR /build

# The lockfile alone first, so the dependency layer is only rebuilt when the
# dependencies actually change rather than on every source edit.
COPY auth/gui/package.json auth/gui/package-lock.json ./
RUN npm ci

COPY auth/gui/tsconfig.json auth/gui/vite.config.ts auth/gui/index.html ./
COPY auth/gui/src/ ./src/
# `npm run build` is `tsc --noEmit && vite build`, so an image cannot be
# produced from code that does not compile.
RUN npm run build


FROM nginx:1.29-alpine

# `default.conf` ships with the image and would otherwise win on port 80.
RUN rm -f /etc/nginx/conf.d/default.conf

COPY --from=build /build/dist/ /usr/share/nginx/html/
COPY auth/gui/nginx.conf.template /etc/nginx/templates/nl2sql-directory.conf.template
COPY auth/gui/10-nl2sql-directory-config.envsh /docker-entrypoint.d/10-nl2sql-directory-config.envsh
RUN chmod +x /docker-entrypoint.d/10-nl2sql-directory-config.envsh

# Substituted into the template at start-up. Only these names are touched --
# without the filter, envsubst would also eat nginx's own $uri and $host.
ENV NGINX_ENVSUBST_FILTER="^DIRECTORY_" \
    DIRECTORY_GUI_PORT=8084 \
    DIRECTORY_GUI_TLS_ENABLED=true \
    DIRECTORY_GUI_TLS_CERT_FILE=/etc/nl2sql/tls/server.crt \
    DIRECTORY_GUI_TLS_KEY_FILE=/etc/nl2sql/tls/server.key \
    DIRECTORY_UPSTREAM=https://nl2sql-auth:8446 \
    DIRECTORY_API_UPSTREAM=https://nl2sql-auth:8447 \
    DIRECTORY_CACERT=/etc/nl2sql/tls/ca.crt \
    DIRECTORY_SSL_NAME=nl2sql-auth \
    DIRECTORY_READ_TIMEOUT=120s \
    DIRECTORY_GUI_RESOLVER=127.0.0.11

# Where this page's own certificate and the stack's CA are expected to
# appear, mounted by compose from the volume the pki service issues them into.
RUN mkdir -p /etc/nl2sql/tls

EXPOSE 8084

# HTTPS unless the page was told otherwise. Unverified, and only here: this is
# the container checking its own socket, where the certificate's name proves
# nothing.
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD case "$DIRECTORY_GUI_TLS_ENABLED" in false|0|no|off) s=http ;; *) s=https ;; esac; \
        wget -q --no-check-certificate -O /dev/null "$s://127.0.0.1:${DIRECTORY_GUI_PORT}/index.html" || exit 1

LABEL org.opencontainers.image.title="nl2sql directory GUI" \
      org.opencontainers.image.description="React/TypeScript web interface for a standalone nl2sql directory: people, groups, passwords" \
      org.opencontainers.image.version="6.1.0"
