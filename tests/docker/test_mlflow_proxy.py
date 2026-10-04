"""MLflow's front door (`docker/mlflow-proxy`): the template, the start-up
script, the image.

Run, against MLflow, the directory and the auth service, it is in
tests/auth/test_auth_live.py: a browser sent to sign in, an MLflow client's
Basic credentials, a write from another site refused.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from nl2sql_agent import __version__

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PROXY = REPO_ROOT / "docker" / "mlflow-proxy"
ENVSH = PROXY / "10-nl2sql-mlflow-proxy.envsh"


@pytest.fixture(scope="module")
def template() -> str:
    return (PROXY / "nginx.conf.template").read_text()


@pytest.fixture(scope="module")
def dockerfile() -> str:
    return (PROXY / "Dockerfile").read_text()


def test_every_request_to_mlflow_is_asked_about_and_the_question_carries_the_method(template: str):
    assert "include /etc/nginx/nl2sql-mlflow-signin.conf;" in template
    verify = template[template.index("location = /_nl2sql_verify {") :]
    assert "internal;" in verify and "proxy_pass $auth_upstream/auth/verify;" in verify
    assert "proxy_set_header X-Original-Method $request_method;" in verify
    assert "proxy_pass_request_body off;" in verify


def test_a_browser_is_sent_to_sign_in_and_an_api_client_is_told_401(template: str):
    signin = template[template.index("location @signin {") :]
    assert "return 302 /auth/login?next=$request_uri;" in signin
    assert "if ($uri ~ ^/(api|ajax-api)/) {" in signin and "return 401;" in signin


def test_only_the_health_route_is_open_and_mlflow_is_sent_no_credentials(template: str):
    health = template[template.index("location = /health {") : template.index("location / {")]
    assert "auth_request" not in health and "signin.conf" not in health
    assert 'proxy_set_header Authorization "${MLFLOW_AUTH_HEADER}";' in template
    assert "proxy_set_header Host" not in template[template.index("location / {") :], "MLflow's allowed hosts know the upstream's name"


def test_the_image(dockerfile: str):
    assert re.search(r"^FROM nginx:1\.\d+-alpine$", dockerfile, re.MULTILINE)
    assert 'NGINX_ENVSUBST_FILTER="^(MLFLOW_|AUTH_)"' in dockerfile
    assert "MLFLOW_UPSTREAM=http://nl2sql-mlflow:5000" in dockerfile and "EXPOSE 5001" in dockerfile
    assert "/health" in dockerfile and "HEALTHCHECK" in dockerfile
    assert f'org.opencontainers.image.version="{__version__}"' in dockerfile


# --- the start-up script -----------------------------------------------------------


def _env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    env = {
        "PATH": "/usr/bin:/bin",
        "NGINX_UPSTREAM_TLS_CONF": str(tmp_path / "upstream-tls.conf"),
        "NGINX_AUTH_TLS_CONF": str(tmp_path / "auth-tls.conf"),
        "NGINX_SIGNIN_CONF": str(tmp_path / "signin.conf"),
        "NGINX_SERVER_TLS_CONF": str(tmp_path / "server-tls.conf"),
        "MLFLOW_UPSTREAM": "http://nl2sql-mlflow:5000",
        "AUTH_UPSTREAM": "http://nl2sql-auth:8446",
        "AUTH_CACERT": "/etc/nl2sql/tls/server.crt",
        "AUTH_SSL_NAME": "nl2sql-auth",
        "MLFLOW_PROXY_TLS_ENABLED": "false",
        "MLFLOW_PROXY_TLS_CERT_FILE": "/etc/nl2sql/tls/server.crt",
        "MLFLOW_PROXY_TLS_KEY_FILE": "/etc/nl2sql/tls/server.key",
    }
    env.update(overrides)
    return env


def _source(env: dict[str, str], then: str = "true") -> subprocess.CompletedProcess:
    command = ["sh", "-c", f". {ENVSH}; {then}"]
    if os.environ.get("NL2SQL_SHELL_TRACE"):
        env = {**env, "PS4": f"+@{ENVSH.name}@${{LINENO}}@ "}
        command = ["sh", "-x", "-c", f". {ENVSH}; {then}"]
    result = subprocess.run(command, capture_output=True, text=True, env=env)
    directory = os.environ.get("NL2SQL_SHELL_TRACE")
    if directory:
        with open(os.path.join(directory, "trace.log"), "a") as handle:
            handle.write(result.stderr)
    return result


def test_the_start_up_script_is_valid_executable_shell():
    assert subprocess.run(["sh", "-n", str(ENVSH)]).returncode == 0
    assert ENVSH.stat().st_mode & 0o111 and ENVSH.suffix == ".envsh"


@pytest.mark.parametrize("enabled", ["true", "1", "yes", "on"])
def test_with_sign_in_on_every_request_is_asked_about(tmp_path: Path, enabled):
    result = _source(_env(tmp_path, AUTH_ENABLED=enabled), 'printf "[%s]" "$MLFLOW_AUTH_HEADER"')
    assert result.returncode == 0, result.stderr
    assert result.stdout == "[]", "MLflow is never sent a credential"
    written = (tmp_path / "signin.conf").read_text()
    assert "auth_request /_nl2sql_verify;" in written
    assert "error_page 401 = @signin;" in written and "error_page 403 = @forbidden;" in written


def test_with_sign_in_off_nothing_is_asked_and_it_says_so(tmp_path: Path):
    assert _source(_env(tmp_path)).returncode == 0
    written = (tmp_path / "signin.conf").read_text()
    assert "auth_request" not in written and "anyone who can reach this port reaches MLflow" in written
    assert "inside\n# the stack's own network" in (tmp_path / "upstream-tls.conf").read_text()


def test_an_https_sign_in_hop_is_verified(tmp_path: Path):
    cacert = tmp_path / "server.crt"
    cacert.write_text("readable")
    assert _source(_env(tmp_path, AUTH_UPSTREAM="https://nl2sql-auth:8446", AUTH_CACERT=str(cacert))).returncode == 0
    written = (tmp_path / "auth-tls.conf").read_text()
    assert "proxy_ssl_verify on;" in written and "proxy_ssl_name nl2sql-auth;" in written


def test_an_https_hop_with_no_certificate_refuses_to_start(tmp_path: Path):
    result = _source(_env(tmp_path, AUTH_UPSTREAM="https://nl2sql-auth:8446", AUTH_CACERT=str(tmp_path / "x")))
    assert result.returncode != 0
    assert "nl2sql-mlflow-proxy: AUTH_UPSTREAM is https://nl2sql-auth:8446 but there is no" in result.stderr
    assert "readable certificate at AUTH_CACERT=" in result.stderr
    assert "The auth service presents the certificate the agent API generates," in result.stderr
    assert "so this usually means the API has not started yet, or the apitls" in result.stderr
    assert "volume is not mounted here." in result.stderr


def test_the_proxy_is_https_with_the_apis_certificate(tmp_path: Path):
    cert, key = tmp_path / "server.crt", tmp_path / "server.key"
    cert.write_text("c")
    key.write_text("k")
    env = _env(tmp_path, MLFLOW_PROXY_TLS_ENABLED="true", MLFLOW_PROXY_TLS_CERT_FILE=str(cert), MLFLOW_PROXY_TLS_KEY_FILE=str(key))
    result = _source(env, 'printf "[%s]" "$MLFLOW_PROXY_LISTEN_TLS"')
    assert result.returncode == 0 and result.stdout == "[ ssl]"
    assert f"ssl_certificate_key {key};" in (tmp_path / "server-tls.conf").read_text()


def test_an_https_proxy_with_no_certificate_refuses_to_start(tmp_path: Path):
    result = _source(_env(tmp_path, MLFLOW_PROXY_TLS_ENABLED="true", MLFLOW_PROXY_TLS_CERT_FILE=str(tmp_path / "x")))
    assert result.returncode != 0
    assert "nl2sql-mlflow-proxy: MLFLOW_PROXY_TLS_ENABLED is on but" in result.stderr
    assert "is not readable. They come from the apitls volume" in result.stderr
    assert "the API writes on its first start; mount it, or set MLFLOW_PROXY_TLS_ENABLED=false" in result.stderr
    assert "behind something that terminates TLS itself." in result.stderr


@pytest.mark.parametrize("off", ["false", "0", "no", "off"])
def test_a_plain_proxy_is_said_to_be_one(tmp_path: Path, off):
    result = _source(_env(tmp_path, MLFLOW_PROXY_TLS_ENABLED=off), 'printf "[%s]" "$MLFLOW_PROXY_LISTEN_TLS"')
    assert result.stdout == "[]"
    assert "every password typed into the sign-in page" in (tmp_path / "server-tls.conf").read_text()
