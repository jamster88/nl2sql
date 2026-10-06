# Every page of the stack, and MLflow's front door, in one image (V6-37).
#
# Until 6.3 there were six nginx images -- the web interface, the review,
# curation, console and directory pages, and MLflow's front door -- that
# differed by their upstream and their paths, each with its own copy of the
# same start-up script, sign-in location and health check that did not
# verify. This is all of them: the five pages' bundles side by side, one
# start-up script (`10-nl2sql-proxy.envsh`), one template per page and one
# health check that verifies the certificate the container serves. Each
# container serves the one page `NL2SQL_PAGE` names and none of the others:
# the review page's files are in the web interface's container, but nothing
# there answers with them.
#
# The bundles are built on the build platform, as before: what a build
# produces is JavaScript, CSS and HTML -- the same whatever the image runs
# on -- so a multi-arch publish builds them once, natively, rather than
# running `npm ci` under emulation for every architecture. Each page's type
# check runs here as well (`npm run build` is `tsc --noEmit && vite build`).
# The shared web package (V6-25) sits beside each page at `/web`, where its
# configuration looks for it (`../web` or `../../web` from `/build`).

FROM --platform=$BUILDPLATFORM node:26-alpine@sha256:0b36e8c136b94cd4fcf02188228e76c31ad5872eef3fec8cbd2eee500cfd9e80 AS gui
WORKDIR /build
COPY gui/package.json gui/package-lock.json ./
RUN npm ci
COPY web/package.json /web/package.json
COPY web/src/ /web/src/
COPY gui/tsconfig.json gui/vite.config.ts gui/index.html ./
COPY gui/src/ ./src/
RUN npm run build

FROM --platform=$BUILDPLATFORM node:26-alpine@sha256:0b36e8c136b94cd4fcf02188228e76c31ad5872eef3fec8cbd2eee500cfd9e80 AS review
WORKDIR /build
COPY review/gui/package.json review/gui/package-lock.json ./
RUN npm ci
COPY web/package.json /web/package.json
COPY web/src/ /web/src/
COPY review/gui/tsconfig.json review/gui/vite.config.ts review/gui/index.html ./
COPY review/gui/src/ ./src/
RUN npm run build

FROM --platform=$BUILDPLATFORM node:26-alpine@sha256:0b36e8c136b94cd4fcf02188228e76c31ad5872eef3fec8cbd2eee500cfd9e80 AS curate
WORKDIR /build
COPY curate/package.json curate/package-lock.json ./
RUN npm ci
COPY web/package.json /web/package.json
COPY web/src/ /web/src/
COPY curate/tsconfig.json curate/vite.config.ts curate/index.html ./
COPY curate/src/ ./src/
RUN npm run build

FROM --platform=$BUILDPLATFORM node:26-alpine@sha256:0b36e8c136b94cd4fcf02188228e76c31ad5872eef3fec8cbd2eee500cfd9e80 AS console
WORKDIR /build
COPY console/package.json console/package-lock.json ./
RUN npm ci
COPY web/package.json /web/package.json
COPY web/src/ /web/src/
COPY console/tsconfig.json console/vite.config.ts console/index.html ./
COPY console/src/ ./src/
RUN npm run build

FROM --platform=$BUILDPLATFORM node:26-alpine@sha256:0b36e8c136b94cd4fcf02188228e76c31ad5872eef3fec8cbd2eee500cfd9e80 AS directory
WORKDIR /build
COPY auth/gui/package.json auth/gui/package-lock.json ./
RUN npm ci
COPY web/package.json /web/package.json
COPY web/src/ /web/src/
COPY auth/gui/tsconfig.json auth/gui/vite.config.ts auth/gui/index.html ./
COPY auth/gui/src/ ./src/
RUN npm run build


FROM nginx:1.29-alpine@sha256:5616878291a2eed594aee8db4dade5878cf7edcb475e59193904b198d9b830de

COPY --from=gui /build/dist/ /usr/share/nginx/html/gui/
COPY --from=review /build/dist/ /usr/share/nginx/html/review/
COPY --from=curate /build/dist/ /usr/share/nginx/html/curate/
COPY --from=console /build/dist/ /usr/share/nginx/html/console/
COPY --from=directory /build/dist/ /usr/share/nginx/html/directory/

# nginx's own configuration, replaced: it reads what the start-up script
# writes under /tmp, which is the one place a read-only container can write
# (V6-34), and it runs as nginx's account with no `user` directive to switch.
# nginx's own welcome and error pages go too: what this image serves is the
# five pages' bundles, each from its own root, and nothing else.
RUN rm -f /etc/nginx/conf.d/default.conf /usr/share/nginx/html/index.html /usr/share/nginx/html/50x.html
COPY proxy/nginx.conf /etc/nginx/nginx.conf
COPY proxy/shared/ /etc/nginx/nl2sql/shared/
COPY proxy/pages/ /etc/nginx/nl2sql/pages/
COPY proxy/10-nl2sql-proxy.envsh /docker-entrypoint.d/10-nl2sql-proxy.envsh
COPY proxy/health.sh /usr/local/bin/nl2sql-proxy-health
RUN chmod 0755 /docker-entrypoint.d/10-nl2sql-proxy.envsh /usr/local/bin/nl2sql-proxy-health

# Where this page's certificate and the stack's CA are mounted, from the
# volume the pki service issues them into. The directory exists in the image
# so a certificate can also be put here with `docker cp`.
RUN mkdir -p /etc/nl2sql/tls

ENV PROXY_TLS_ENABLED=true \
    PROXY_TLS_CERT_FILE=/etc/nl2sql/tls/server.crt \
    PROXY_TLS_KEY_FILE=/etc/nl2sql/tls/server.key \
    PROXY_RESOLVER=127.0.0.11 \
    UPSTREAM_CACERT=/etc/nl2sql/tls/ca.crt \
    UPSTREAM_READ_TIMEOUT=120s \
    AUTH_UPSTREAM=https://nl2sql-auth:8446 \
    AUTH_CACERT=/etc/nl2sql/tls/ca.crt \
    AUTH_SSL_NAME=nl2sql-auth

# Unprivileged (V6-31): nginx's own account, uid 101, which the pki service
# gives each page's key to.
USER 101:101

# The page's own certificate, verified against the stack's CA under the name
# `localhost`, which every page's certificate covers: a page serving an
# expired certificate, or another page's, is unhealthy.
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 CMD ["/usr/local/bin/nl2sql-proxy-health"]

LABEL org.opencontainers.image.title="nl2sql proxy" \
      org.opencontainers.image.description="nginx serving one nl2sql page -- the web interface, review, curation, console, directory -- or MLflow's front door, as NL2SQL_PAGE says" \
      org.opencontainers.image.version="6.3.0"
