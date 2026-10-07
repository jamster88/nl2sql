"""The directory page as a project: its package and its proxy.

Its image is the one every page is served from (proxy/, V6-37), and the
start-up script that refuses to serve it beside a replica is that image's:
tests/proxy/test_proxy_startup.py.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from nl2sql_agent import __version__

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GUI = REPO_ROOT / "auth" / "gui"


@pytest.fixture(scope="module")
def package_json() -> dict:
    return json.loads((GUI / "package.json").read_text())


@pytest.fixture(scope="module")
def template() -> str:
    return (REPO_ROOT / "proxy" / "pages" / "directory.conf.template").read_text()


def test_every_dependency_is_pinned_exactly_and_the_lockfile_is_committed(package_json: dict):
    for section in ("dependencies", "devDependencies"):
        for name, version in package_json[section].items():
            assert re.fullmatch(r"\d+\.\d+\.\d+", version), f"{name} is {version}"
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", "auth/gui/package-lock.json"], cwd=REPO_ROOT, capture_output=True)
    assert tracked.returncode == 0


def test_the_version_follows_the_agent_and_the_scripts_are_the_usual(package_json: dict):
    assert package_json["version"] == __version__
    assert package_json["scripts"]["build"] == "tsc --noEmit && vite build"
    assert package_json["scripts"]["test"] == "vitest run --coverage"


def test_the_coverage_thresholds_are_all_a_hundred():
    config = (GUI / "vitest.config.ts").read_text()
    assert "thresholds: { statements: 100, branches: 100, functions: 100, lines: 100 }" in config
    assert 'exclude: ["src/main.tsx"]' in config


def test_the_page_is_for_administrators_only():
    main = (GUI / "src" / "main.tsx").read_text()
    assert 'needs={["nl2sql_admins"]}' in main


def test_the_dev_proxy_and_nginx_both_send_the_page_to_the_auth_service(template: str):
    vite = (GUI / "vite.config.ts").read_text()
    assert 'paths: ["/directory"]' in vite and "auth: target" in vite and "https://localhost:8446" in vite
    assert "include /tmp/nginx/auth.conf;" in template
    # V6-58: the directory's own API on the auth service's unpublished port.
    assert "location /directory/ {" in template and "set $upstream ${UPSTREAM};" in template
    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    assert "UPSTREAM: ${DIRECTORY_GUI_API_UPSTREAM:-https://nl2sql-auth:${AUTH_DIRECTORY_PORT:-8447}}" in compose
    assert 'proxy_set_header Authorization "";' in template
    assert "proxy_set_header X-Forwarded-Host $http_host;" in template
    assert "client_max_body_size 6m;" in template


def test_build_artefacts_are_ignored():
    for path in ("auth/gui/node_modules", "auth/gui/dist", "auth/gui/coverage"):
        assert subprocess.run(["git", "check-ignore", "-q", f"{path}/"], cwd=REPO_ROOT).returncode == 0, path
        assert path in (REPO_ROOT / ".dockerignore").read_text(), path
