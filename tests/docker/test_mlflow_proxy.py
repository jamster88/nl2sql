"""MLflow's front door: the proxy image's `mlflow` page (V6-37).

Its start-up script and health check are every page's, in
tests/proxy/test_proxy_startup.py; run against MLflow, the directory and the
auth service, it is in tests/auth/test_auth_live.py -- a browser sent to sign
in, an MLflow client's Basic credentials, a write from another site refused.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATE = REPO_ROOT / "proxy" / "pages" / "mlflow.conf.template"


@pytest.fixture(scope="module")
def template() -> str:
    return TEMPLATE.read_text()


def test_every_request_to_mlflow_is_asked_about_and_the_question_carries_the_method(template: str):
    assert "include /tmp/nginx/mlflow-signin.conf;" in template
    verify = template[template.index("location = /_nl2sql_verify {") :]
    assert "internal;" in verify and "proxy_pass $auth_upstream/auth/verify;" in verify
    assert "proxy_set_header X-Original-Method $request_method;" in verify
    assert "proxy_pass_request_body off;" in verify


def test_a_browser_is_sent_to_sign_in_and_an_api_client_is_told_401(template: str):
    signin = template[template.index("location @signin {") :]
    assert "return 302 /auth/login?next=$request_uri;" in signin
    assert "if ($uri ~ ^/(api|ajax-api)/) {" in signin and "return 401;" in signin


def test_only_the_health_routes_are_open_and_mlflow_is_sent_no_credentials(template: str):
    health = template[template.index("location = /health {") : template.index("location / {")]
    assert "auth_request" not in health and "signin.conf" not in health
    after = template[template.index("location / {") :]
    assert 'proxy_set_header Authorization "";' in after
    assert "proxy_set_header Host" not in after, "MLflow's allowed hosts know the upstream's name"
    assert "include /etc/nginx/nl2sql/shared/health.conf;" in template


def test_it_serves_no_page_of_its_own():
    """Everything it answers is MLflow's, or sign-in's."""
    assert "site.conf" not in TEMPLATE.read_text() and "root " not in TEMPLATE.read_text()


def test_compose_runs_it_from_the_one_image_in_mlflows_profile():
    service = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())["services"]["mlflowproxy"]
    assert service["environment"]["NL2SQL_PAGE"] == "mlflow"
    assert service["image"] == "${PROXY_IMAGE_NAME:-nl2sql-proxy}:${PROXY_IMAGE_TAG:-latest}"
    assert service["profiles"] == ["mlflow"]
    assert service["environment"]["UPSTREAM"] == "${MLFLOW_PROXY_UPSTREAM:-http://nl2sql-mlflow:5000}"
    assert service["ports"] == ["${MLFLOW_BIND_ADDRESS:-127.0.0.1}:${MLFLOW_PORT:-5001}:${MLFLOW_PROXY_PORT:-5001}"]
