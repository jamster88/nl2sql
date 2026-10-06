"""The curation GUI's project, its nginx config, and the script nginx sources.

The review interface's tests, for the page that shares its service: the
token this proxy holds is the review service's, which can rewrite the golden
set and the snippets, so it never reaches the browser and "no token" sends
no header; and a save runs the loaders, so the proxy outlasts them.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GUI = REPO_ROOT / "curate"


@pytest.fixture(scope="module")
def package_json() -> dict:
    return json.loads((GUI / "package.json").read_text())


@pytest.fixture(scope="module")
def nginx_template() -> str:
    """The page's server block in the one proxy image (V6-37)."""
    return (REPO_ROOT / "proxy" / "pages" / "service.conf.template").read_text()


@pytest.fixture(scope="module")
def site_conf() -> str:
    """What every page serves of its own: the bundle, cached, from any path."""
    return (REPO_ROOT / "proxy" / "shared" / "site.conf").read_text()


@pytest.fixture(scope="module")
def vite_config() -> str:
    return (GUI / "vite.config.ts").read_text()


# ---------------------------------------------------------------------------
# The project
# ---------------------------------------------------------------------------


def test_every_dependency_is_pinned_exactly(package_json: dict):
    for section in ("dependencies", "devDependencies"):
        for name, version in package_json[section].items():
            assert re.fullmatch(r"\d+\.\d+\.\d+", version), f"{name} is {version}, not pinned"


def test_it_is_its_own_project_and_its_own_image(package_json: dict):
    """An entry point in another project would be served by that project's
    image -- the public web GUI's, to anyone who could reach it."""
    others = {json.loads((REPO_ROOT / p / "package.json").read_text())["name"] for p in ("gui", "review/gui", "console")}
    assert package_json["name"] == "nl2sql-curate-gui" and package_json["name"] not in others
    assert (GUI / "package-lock.json").is_file()


def test_the_scripts_build_typecheck_and_test_with_coverage(package_json: dict):
    scripts = package_json["scripts"]
    assert {"dev", "build", "test", "typecheck"} <= set(scripts)
    assert "tsc --noEmit" in scripts["build"]
    assert "--coverage" in scripts["test"]


@pytest.mark.parametrize("option", ["strict", "noUncheckedIndexedAccess", "exactOptionalPropertyTypes"])
def test_the_compiler_is_strict(option: str):
    tsconfig = json.loads((GUI / "tsconfig.json").read_text())
    assert tsconfig["compilerOptions"][option] is True


def test_coverage_is_held_to_a_hundred_with_only_the_entry_point_left_out():
    config = (GUI / "vitest.config.ts").read_text()
    thresholds = dict(re.findall(r"(\w+): (\d+)", re.search(r"thresholds: \{([^}]*)\}", config).group(1)))
    assert thresholds == {"statements": "100", "branches": "100", "functions": "100", "lines": "100"}
    excluded = re.search(r"exclude: \[([^\]]*)\]", config).group(1)
    assert re.findall(r'"(src/[^"]+)"', excluded) == ["src/main.tsx"]


# ---------------------------------------------------------------------------
# The two proxies, which have to agree
# ---------------------------------------------------------------------------


def test_the_dev_server_and_nginx_proxy_the_same_paths(vite_config: str, nginx_template: str):
    proxied = set(re.findall(r'"(/v1|/healthz|/readyz|/openapi\.json)"', vite_config))
    assert proxied == {"/v1", "/healthz", "/readyz", "/openapi.json"}
    location = re.search(r"location ~ \^/\(([^)]*)\)", nginx_template).group(1)
    assert {f"/{part.replace(chr(92), '')}" for part in location.split("|")} == proxied


def test_the_dev_proxy_adds_the_review_token_and_skips_the_development_certificate(vite_config: str):
    assert "token: env.REVIEW_TOKEN" in vite_config and "devProxies({" in vite_config
    assert re.search(r"NL2SQL_REVIEW_TLS_VERIFY.*?\"false\"", vite_config, re.S)


@pytest.fixture(scope="module")
def api_location(nginx_template: str) -> str:
    match = re.search(r"location ~ \^/\([^)]*\) \{(.*?)\n    \}", nginx_template, re.S)
    assert match, "the proxied location block is not where it was"
    return match.group(1)


def test_the_proxy_adds_the_token_verifies_the_service_and_resolves_it_per_request(api_location: str):
    assert 'proxy_set_header Authorization "${UPSTREAM_AUTH_HEADER}"' in api_location
    assert "include /tmp/nginx/upstream-tls.conf;" in api_location
    assert "resolver ${PROXY_RESOLVER}" in api_location
    assert "set $upstream ${UPSTREAM};" in api_location
    assert "proxy_pass $upstream$request_uri;" in api_location


def test_the_proxy_outlasts_a_save(api_location: str):
    """A save runs the loaders; a proxy that gives up first cuts off a write
    that is still happening."""
    assert "proxy_read_timeout ${UPSTREAM_READ_TIMEOUT};" in api_location
    default = re.search(r"\${CURATE_GUI_READ_TIMEOUT:-(\d+)s\}", (REPO_ROOT / "docker-compose.yml").read_text())
    from nl2sql_review.settings import ReviewSettings

    assert default and int(default.group(1)) >= ReviewSettings().reload_timeout_seconds


def test_the_page_is_served_from_any_path_but_the_assets_are_immutable(nginx_template: str, site_conf: str):
    assert "include /etc/nginx/nl2sql/shared/site.conf;" in nginx_template
    assert "try_files $uri $uri/ /index.html;" in site_conf
    assert re.search(r"location /assets/ \{[^}]*immutable", site_conf, re.S)
    assert re.search(r"location = /index\.html \{[^}]*no-cache", site_conf, re.S)
