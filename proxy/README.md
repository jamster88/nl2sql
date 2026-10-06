# The proxy image

`mcfaddja/nl2sql-proxy` is every page of the stack, and MLflow's front door,
in one image (6.3). It is nginx with the five pages' bundles built into it
side by side -- the web interface, the review, curation and SQL console
interfaces, and the directory page -- one start-up script, one template per
kind of page and one health check. Each container serves the one page
`NL2SQL_PAGE` names and none of the others:

| `NL2SQL_PAGE` | Compose service | Serves | In front of |
|---|---|---|---|
| `gui` | `gui` | the web interface | the API |
| `review` | `reviewgui` | the review interface | the review service |
| `curate` | `curategui` | the curation interface | the review service |
| `console` | `consolegui` | the SQL console's interface | the console |
| `directory` | `directorygui` | the directory page; refused beside a replica directory | the auth service's directory port |
| `mlflow` | `mlflowproxy` | no page of its own: asks the auth service about every request | MLflow |

Until 6.3 each of these was an image of its own -- `nl2sql-gui`,
`nl2sql-review-gui`, `nl2sql-curate-gui`, `nl2sql-console-gui`,
`nl2sql-directory-gui` and `nl2sql-mlflow-proxy` -- each with its own copy
of the same start-up script, sign-in location and health check to keep in
step, and none of the six health checks verified the certificate it was
answered with. The pages' own sources are still where they were
([`gui/`](../gui), [`review/gui/`](../review/gui), [`curate/`](../curate),
[`console/`](../console), [`auth/gui/`](../auth/gui)), each its own npm
project; only how they are served moved here.

## What it does on start

[`10-nl2sql-proxy.envsh`](10-nl2sql-proxy.envsh) is sourced by the nginx
image's entrypoint before nginx starts. It decides, in order:

1. **Which page**, and whether it may be served: an unknown `NL2SQL_PAGE`
   stops the container saying which are known, and the directory page
   stops beside a replica directory, whose people are edited on its
   primary.
2. **What the upstream is sent as `Authorization`**: nothing with sign-in on,
   where each person's own session is what reaches the service and a token
   here would make every visitor the same somebody; with sign-in off, the
   service token in `UPSTREAM_TOKEN_FILE`, read from the secret compose
   mounts -- and no header at all when that file is empty.
3. **Whether each upstream is verified**: an `https://` one is, against
   `UPSTREAM_CACERT` under the name `UPSTREAM_SSL_NAME` (the auth service
   likewise, with `AUTH_CACERT` and `AUTH_SSL_NAME`), and a CA file that is
   not there stops the page with a message saying so; plain HTTP has
   nothing to verify.
4. **MLflow's sign-in**: with sign-in on, every request to MLflow is asked
   about first (`auth_request`), and the answer carries the request's
   method so the auth service can refuse a write from another site.
5. **The page's own listener**: HTTPS with its own certificate, from the
   pki service, unless `PROXY_TLS_ENABLED` says otherwise behind something
   that terminates TLS itself.
6. **The templates**, rendered with those settings and nothing else, so
   nginx's own `$variables` are left as they are.

Everything it writes goes under `/tmp/nginx`: the container's root is
read-only, and it runs as nginx's own account, 101, with no capabilities.

Every upstream is resolved per request, through `PROXY_RESOLVER` -- Docker's
DNS -- so a page starts before its service is up, and follows the service
across a restart onto a new address.

## Settings

Compose sets these from `.env` under each page's own names (`GUI_PORT`,
`REVIEW_GUI_UPSTREAM`, ...), which each page's README lists; these are the
names the image itself reads.

| Setting | Default | |
|---|---|---|
| `NL2SQL_PAGE` | *(none)* | Which page this container serves: `gui`, `review`, `curate`, `console`, `directory` or `mlflow` |
| `PROXY_PORT` | *(none)* | The port it listens on, inside its container |
| `PROXY_TLS_ENABLED` | `true` | HTTPS on that port, with the page's own certificate. `false` only behind something that terminates TLS, which must send `X-Forwarded-Proto: https` |
| `PROXY_TLS_CERT_FILE` | `/etc/nl2sql/tls/server.crt` | The page's certificate, from the pki service |
| `PROXY_TLS_KEY_FILE` | `/etc/nl2sql/tls/server.key` | Its key, readable by account 101 alone |
| `PROXY_RESOLVER` | `127.0.0.11` | The DNS server upstreams are resolved through, per request |
| `UPSTREAM` | *(none)* | The service this page is in front of |
| `UPSTREAM_SSL_NAME` | *(none)* | The name its certificate is checked for, when it is `https://` |
| `UPSTREAM_CACERT` | `/etc/nl2sql/tls/ca.crt` | The CA it is verified against: the stack's, which the pki service puts beside the page's own certificate |
| `UPSTREAM_READ_TIMEOUT` | `120s` | How long a request may wait for an answer -- longer for the API, a question, and the review service, a promotion |
| `UPSTREAM_TOKEN_FILE` | *(none)* | The file holding the service's token, sent only with sign-in off |
| `AUTH_ENABLED` | `true` | Sign-in. Anything but `false`, `0`, `no` or `off` is on |
| `AUTH_UPSTREAM` | `https://nl2sql-auth:8446` | Where the sign-in form posts, and what MLflow's front door asks |
| `AUTH_SSL_NAME` | `nl2sql-auth` | The name the auth service's certificate is checked for |
| `AUTH_CACERT` | `/etc/nl2sql/tls/ca.crt` | The CA it is verified against |
| `LDAP_MODE` | `standalone` | Read by the directory page only, to refuse beside a `replica` |
| `PROXY_HEALTH_CACERT` | `ca.crt` beside `PROXY_TLS_CERT_FILE` | The CA the health check verifies the page's certificate against |
| `NL2SQL_PROXY_OUT` | `/tmp/nginx` | Where the rendered configuration is written. For the tests |
| `NL2SQL_PROXY_TEMPLATES` | `/etc/nginx/nl2sql` | Where the templates are read from. For the tests |

## Its health check

[`health.sh`](health.sh) asks the page's own listener for `/_proxy/health`,
under the name `localhost` -- which every page's certificate covers -- and
verifies the answer against the stack's CA. A page serving an expired
certificate, or another page's, is unhealthy; the `wget
--no-check-certificate` the six images used called both healthy.

## Layout

| Path | What |
|---|---|
| [`Dockerfile`](Dockerfile) | Five build stages, one per page, on the build platform -- the bundles are the same bytes whatever the image runs on -- then nginx, every base pinned by digest |
| [`nginx.conf`](nginx.conf) | nginx's own configuration, replaced: no `user` directive, a pid file and temporary paths under `/tmp`, and the rendered page included |
| [`10-nl2sql-proxy.envsh`](10-nl2sql-proxy.envsh) | The start-up script above |
| [`health.sh`](health.sh) | The health check |
| [`pages/`](pages) | One template per kind of page: `gui`, `service` (the review, curation and console pages, which differ only in their upstream), `directory` and `mlflow` |
| [`shared/`](shared) | What every page has: the sign-in location, the static site's headers and the health route |

## Building and testing it

```bash
docker build -f proxy/Dockerfile -t nl2sql-proxy .
docker buildx build --platform linux/amd64,linux/arm64 \
  -f proxy/Dockerfile --push -t mcfaddja/nl2sql-proxy:v6_3 .
```

[`tests/proxy/test_proxy_startup.py`](../tests/proxy/test_proxy_startup.py)
runs the start-up script and the health check for every page against fake
files, `curl` and `envsubst`, through each decision above and each way one
can stop the page, and is what the shell coverage measurement counts for
both. Each page's own project tests check its template; with
`--run-docker`,
[`tests/docker/test_gui_container.py`](../tests/docker/test_gui_container.py)
builds the image and serves the web interface from it -- read-only, with no
capabilities, as compose runs it -- in front of a real API, and checks that
its health check fails against a CA that did not issue its certificate.
