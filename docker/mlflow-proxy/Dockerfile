# MLflow's front door: nginx, asking the auth service about every request.
#
# MLflow's own server has no login. Rather than add one inside MLflow -- whose
# basic-auth app keeps a user table of its own, a second list of people to
# keep in step with the directory -- this proxy is the only way in from
# outside the stack, and the auth service decides, by the same session every
# other page uses. See nginx.conf.template.
#
# Nothing is built: a template and a start-up script on the nginx image.
FROM nginx:1.29-alpine

RUN rm -f /etc/nginx/conf.d/default.conf

COPY docker/mlflow-proxy/nginx.conf.template /etc/nginx/templates/nl2sql-mlflow.conf.template
COPY docker/mlflow-proxy/10-nl2sql-mlflow-proxy.envsh /docker-entrypoint.d/10-nl2sql-mlflow-proxy.envsh
RUN chmod +x /docker-entrypoint.d/10-nl2sql-mlflow-proxy.envsh

# Substituted into the template at start-up. Only these names are touched --
# without the filter, envsubst would also eat nginx's own $uri and $host.
ENV NGINX_ENVSUBST_FILTER="^(MLFLOW_|AUTH_)" \
    MLFLOW_PROXY_PORT=5001 \
    MLFLOW_PROXY_TLS_ENABLED=true \
    MLFLOW_PROXY_TLS_CERT_FILE=/etc/nl2sql/tls/server.crt \
    MLFLOW_PROXY_TLS_KEY_FILE=/etc/nl2sql/tls/server.key \
    MLFLOW_UPSTREAM=http://nl2sql-mlflow:5000 \
    MLFLOW_READ_TIMEOUT=300s \
    AUTH_UPSTREAM=https://nl2sql-auth:8446 \
    AUTH_CACERT=/etc/nl2sql/tls/ca.crt \
    AUTH_SSL_NAME=nl2sql-auth \
    MLFLOW_PROXY_RESOLVER=127.0.0.11

RUN mkdir -p /etc/nl2sql/tls

EXPOSE 5001

# MLflow's own health route, through the proxy and without sign-in: it says
# the proxy is up and MLflow answers it. Unverified, on the container's own
# socket only.
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD case "$MLFLOW_PROXY_TLS_ENABLED" in false|0|no|off) s=http ;; *) s=https ;; esac; \
        wget -q --no-check-certificate -O /dev/null "$s://127.0.0.1:${MLFLOW_PROXY_PORT}/health" || exit 1

LABEL org.opencontainers.image.title="nl2sql MLflow proxy" \
      org.opencontainers.image.description="nginx in front of the nl2sql MLflow server: sign-in through the auth service for every request" \
      org.opencontainers.image.version="6.1.0"
