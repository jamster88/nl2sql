"""The proxy image's start-up script and health check (V6-37), run as nginx runs them.

`proxy/10-nl2sql-proxy.envsh` is sourced by the nginx image's entrypoint, by
`sh`, before nginx reads its configuration; here it is sourced the same way,
writing into a temporary directory, for each page and each decision it makes.
`proxy/health.sh` is run against a `curl` that says what it was asked. The
image itself, serving a page read-only with a certificate from the stack's
CA in front of a real API, is tests/docker/test_gui_container.py.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PROXY = REPO_ROOT / "proxy"
ENVSH = PROXY / "10-nl2sql-proxy.envsh"
HEALTH = PROXY / "health.sh"
PAGES = ("gui", "review", "curate", "console", "directory", "mlflow")


def _env(tmp_path: Path, page: str = "gui", **overrides: str) -> dict[str, str]:
    """What compose gives a page, with its output in a temporary directory.
    Plain HTTP everywhere unless a test says otherwise, so each decision is
    tested on its own."""
    env = {
        "PATH": "/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin",
        "NL2SQL_PAGE": page,
        "NL2SQL_PROXY_OUT": str(tmp_path / "out"),
        "NL2SQL_PROXY_TEMPLATES": str(PROXY),
        "PROXY_PORT": "8080",
        "PROXY_TLS_ENABLED": "false",
        "PROXY_TLS_CERT_FILE": str(tmp_path / "server.crt"),
        "PROXY_TLS_KEY_FILE": str(tmp_path / "server.key"),
        "UPSTREAM": "http://nl2sql-api:8443",
        "UPSTREAM_CACERT": str(tmp_path / "ca.crt"),
        "UPSTREAM_SSL_NAME": "nl2sql-api",
        "AUTH_UPSTREAM": "http://nl2sql-auth:8446",
        "AUTH_CACERT": str(tmp_path / "ca.crt"),
        "AUTH_SSL_NAME": "nl2sql-auth",
    }
    env.update(overrides)
    return env


def _source(env: dict[str, str], then: str = "true") -> subprocess.CompletedProcess:
    command = ["sh", "-c", f". {ENVSH}; {then}"]
    if os.environ.get("NL2SQL_SHELL_TRACE"):
        # tests/shell_coverage.py. `sh -x`, as nginx's entrypoint sources it.
        env = {**env, "PS4": f"+@{ENVSH.name}@${{LINENO}}@ "}
        command = ["sh", "-x", "-c", f". {ENVSH}; {then}"]
    result = subprocess.run(command, capture_output=True, text=True, env=env)
    directory = os.environ.get("NL2SQL_SHELL_TRACE")
    if directory:
        with open(os.path.join(directory, "trace.log"), "a") as handle:
            handle.write(result.stderr)
    return result


def _out(tmp_path: Path, name: str) -> str:
    return (tmp_path / "out" / name).read_text()


# --- the script ----------------------------------------------------------------------


def test_it_is_valid_shell_and_sourced_by_the_image():
    assert subprocess.run(["sh", "-n", str(ENVSH)]).returncode == 0
    assert ENVSH.suffix == ".envsh", "the .envsh suffix is what makes nginx's entrypoint source it"
    dockerfile = (PROXY / "Dockerfile").read_text()
    assert "/docker-entrypoint.d/10-nl2sql-proxy.envsh" in dockerfile
    assert "/usr/local/bin/nl2sql-proxy-health" in dockerfile


@pytest.mark.parametrize("page", PAGES)
def test_each_page_renders_its_own_server_and_leaves_nginx_variables_alone(page, tmp_path):
    result = _source(_env(tmp_path, page))
    assert result.returncode == 0, result.stderr
    conf = _out(tmp_path, "conf.d/nl2sql.conf")
    assert "listen 8080;" in conf
    assert "${" not in conf, "every setting was substituted"
    assert "$request_uri" in conf and "$http_host" in conf or page == "mlflow", "nginx's own are kept"
    assert "include /etc/nginx/nl2sql/shared/health.conf;" in conf
    assert "include /tmp/nginx/auth.conf;" in conf
    if page != "mlflow":
        assert f"root /usr/share/nginx/html/{page};" in conf
        assert "include /etc/nginx/nl2sql/shared/site.conf;" in conf
    auth = _out(tmp_path, "auth.conf")
    assert "set $auth_upstream http://nl2sql-auth:8446;" in auth and "resolver 127.0.0.11" in auth


def test_a_page_it_does_not_know_is_refused(tmp_path):
    result = _source(_env(tmp_path, "admin"))
    assert result.returncode != 0
    assert "NL2SQL_PAGE must be gui, review, curate, console, directory or mlflow, not 'admin'" in result.stderr


def test_the_directory_page_is_not_served_beside_a_replica(tmp_path):
    result = _source(_env(tmp_path, "directory", LDAP_MODE="replica"))
    assert result.returncode != 0 and "edited on its primary" in result.stderr
    assert _source(_env(tmp_path, "directory", LDAP_MODE="standalone")).returncode == 0


@pytest.mark.parametrize("page", PAGES)
def test_with_sign_in_on_no_token_crosses_whatever_the_secret_holds(page, tmp_path):
    token = tmp_path / "token"
    token.write_text("s3cret\n")
    result = _source(_env(tmp_path, page, UPSTREAM_TOKEN_FILE=str(token)), 'printf "[%s]" "$UPSTREAM_AUTH_HEADER"')
    assert result.stdout == "[]"


@pytest.mark.parametrize(("page", "said"), [
    ("gui", "can ask questions"), ("review", "can review and promote"),
    ("curate", "can rewrite what the agent learns from"), ("console", "can run SQL"),
    ("mlflow", "reaches MLflow"),
])
def test_with_sign_in_off_the_page_says_so_and_sends_its_token(page, said, tmp_path):
    token = tmp_path / "token"
    token.write_text("s3cret\r\n")
    result = _source(_env(tmp_path, page, AUTH_ENABLED="false", UPSTREAM_TOKEN_FILE=str(token)),
                     'printf "[%s]" "$UPSTREAM_AUTH_HEADER"')
    assert result.stdout == "[Bearer s3cret]"
    assert f"sign-in is off (AUTH_ENABLED=false): " in result.stderr and said in result.stderr


@pytest.mark.parametrize("token", ["", None, "missing"])
def test_no_token_sends_no_header_at_all(token, tmp_path):
    extra = {}
    if token == "":
        (tmp_path / "token").write_text("")
        extra["UPSTREAM_TOKEN_FILE"] = str(tmp_path / "token")
    elif token == "missing":
        extra["UPSTREAM_TOKEN_FILE"] = str(tmp_path / "nowhere")
    result = _source(_env(tmp_path, "gui", AUTH_ENABLED="off", **extra), 'printf "[%s]" "$UPSTREAM_AUTH_HEADER"')
    assert result.stdout == "[]"


def test_the_directory_page_says_nothing_with_sign_in_off(tmp_path):
    """It has no service of its own to open: sign-in off, the auth service is
    not started at all."""
    result = _source(_env(tmp_path, "directory", AUTH_ENABLED="0"))
    assert result.returncode == 0 and "sign-in is off" not in result.stderr


# --- verifying the upstreams ----------------------------------------------------------------


def test_an_https_upstream_is_verified_against_the_stacks_ca(tmp_path):
    (tmp_path / "ca.crt").write_text("CA")
    env = _env(tmp_path, UPSTREAM="https://nl2sql-api:8443", AUTH_UPSTREAM="https://nl2sql-auth:8446")
    assert _source(env).returncode == 0
    upstream, auth = _out(tmp_path, "upstream-tls.conf"), _out(tmp_path, "auth-tls.conf")
    for block, name in ((upstream, "nl2sql-api"), (auth, "nl2sql-auth")):
        assert "proxy_ssl_verify on;" in block
        assert f"proxy_ssl_trusted_certificate {tmp_path / 'ca.crt'};" in block
        assert f"proxy_ssl_name {name};" in block


@pytest.mark.parametrize("which", ["UPSTREAM", "AUTH_UPSTREAM"])
def test_an_https_upstream_with_no_ca_to_verify_it_against_stops_the_page(which, tmp_path):
    result = _source(_env(tmp_path, **{which: "https://somewhere:1"}))
    assert result.returncode != 0
    assert f"{which} is https://somewhere:1 but there is no readable certificate" in result.stderr
    assert "the pki service writes beside this" in result.stderr


def test_a_plain_upstream_is_said_to_be_one(tmp_path):
    assert _source(_env(tmp_path)).returncode == 0
    assert "plain HTTP, so there is" in _out(tmp_path, "upstream-tls.conf")
    assert "proxy_ssl_verify" not in _out(tmp_path, "auth-tls.conf")


@pytest.mark.parametrize("missing", ["UPSTREAM", "AUTH_UPSTREAM", "PROXY_PORT"])
def test_what_a_page_cannot_run_without_stops_it(missing, tmp_path):
    env = _env(tmp_path)
    del env[missing]
    result = _source(env)
    assert result.returncode != 0 and f"{missing} is not set" in result.stderr


# --- MLflow's sign-in -------------------------------------------------------------------------


def test_with_sign_in_on_every_mlflow_request_is_asked_about(tmp_path):
    assert _source(_env(tmp_path, "mlflow")).returncode == 0
    signin = _out(tmp_path, "mlflow-signin.conf")
    assert "auth_request /_nl2sql_verify;" in signin and "error_page 401 = @signin;" in signin


def test_with_sign_in_off_nothing_is_asked(tmp_path):
    assert _source(_env(tmp_path, "mlflow", AUTH_ENABLED="no")).returncode == 0
    assert _out(tmp_path, "mlflow-signin.conf") == ""


# --- the page's own listener -------------------------------------------------------------------


def test_an_https_page_serves_its_own_certificate(tmp_path):
    (tmp_path / "server.crt").write_text("cert")
    (tmp_path / "server.key").write_text("key")
    result = _source(_env(tmp_path, PROXY_TLS_ENABLED="true"), 'printf "[%s]" "$PROXY_LISTEN_TLS"')
    assert result.stdout == "[ ssl]"
    conf = _out(tmp_path, "server-tls.conf")
    assert f"ssl_certificate {tmp_path / 'server.crt'};" in conf and "TLSv1.2 TLSv1.3" in conf
    assert "listen 8080 ssl;" in _out(tmp_path, "conf.d/nl2sql.conf")


def test_an_https_page_without_its_certificate_refuses_to_start(tmp_path):
    result = _source(_env(tmp_path, PROXY_TLS_ENABLED="true"))
    assert result.returncode != 0 and "is not readable. They are this page's own" in result.stderr


def test_a_plain_page_says_so(tmp_path):
    result = _source(_env(tmp_path), 'printf "[%s]" "$PROXY_LISTEN_TLS"')
    assert result.stdout == "[]"
    assert "so is every password typed into it" in _out(tmp_path, "server-tls.conf")


# --- what the templates may name -----------------------------------------------------------------


def test_the_templates_name_only_what_the_script_substitutes():
    """A setting a template names and the script's list leaves out would reach
    nginx as `${NAME}`, which it reads as a variable of its own, unset."""
    listed = set(re.search(r"names='([^']*)'", ENVSH.read_text())[1].replace("$", "").replace("{", "").replace("}", "").split())
    for template in [*PROXY.glob("pages/*.template"), *PROXY.glob("shared/*.template")]:
        named = set(re.findall(r"\$\{([A-Z_]+)\}", template.read_text()))
        assert named <= listed, f"{template.name} names {sorted(named - listed)}"


def test_every_page_has_a_template_the_script_chooses():
    chosen = set(re.findall(r"template=(\w+)", ENVSH.read_text()))
    assert chosen == {path.name.split(".")[0] for path in PROXY.glob("pages/*.conf.template")}


# --- the health check ---------------------------------------------------------------------------


FAKE_CURL = """#!/bin/sh
printf '%s\\n' "$*" > "$CURL_LOG"
"""


def _health(tmp_path: Path, **env: str) -> str:
    (tmp_path / "bin").mkdir(exist_ok=True)
    curl = tmp_path / "bin" / "curl"
    curl.write_text(FAKE_CURL)
    curl.chmod(0o755)
    log = tmp_path / "curl.log"
    environment = {"PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin", "CURL_LOG": str(log), "PROXY_PORT": "8443", **env}
    command = ["sh", str(HEALTH)]
    if os.environ.get("NL2SQL_SHELL_TRACE"):
        environment["PS4"] = f"+@{HEALTH.name}@${{LINENO}}@ "
        command = ["sh", "-x", str(HEALTH)]
    result = subprocess.run(command, capture_output=True, text=True, env=environment)
    directory = os.environ.get("NL2SQL_SHELL_TRACE")
    if directory:
        with open(os.path.join(directory, "trace.log"), "a") as handle:
            handle.write(result.stderr)
    assert result.returncode == 0, result.stderr
    return log.read_text().strip()


def test_the_health_check_verifies_the_page_under_the_name_its_certificate_covers(tmp_path):
    asked = _health(tmp_path, PROXY_TLS_CERT_FILE="/etc/nl2sql/tls/server.crt")
    assert asked == ("-fsS -o /dev/null --max-time 4 --cacert /etc/nl2sql/tls/ca.crt "
                     "--resolve localhost:8443:127.0.0.1 https://localhost:8443/_proxy/health")
    assert "--insecure" not in asked and " -k " not in asked


def test_the_health_checks_ca_can_be_named(tmp_path):
    assert "--cacert /other/ca.pem " in _health(tmp_path, PROXY_HEALTH_CACERT="/other/ca.pem")


def test_a_plain_page_is_asked_over_plain_http(tmp_path):
    assert _health(tmp_path, PROXY_TLS_ENABLED="off").endswith("http://127.0.0.1:8443/_proxy/health")


def test_the_health_check_needs_the_port(tmp_path):
    result = subprocess.run(["sh", str(HEALTH)], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})
    assert result.returncode != 0 and "PROXY_PORT is not set" in result.stderr
