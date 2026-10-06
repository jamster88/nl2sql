"""The console interface's project, its nginx config, and the script nginx sources.

The same shape of test as the other two interfaces' project tests, read
offline. What is specific to this one:

* **The page runs SQL.** Its token never reaches the browser, the empty case
  sends no header at all, and its image is its own -- opting out of the
  console means it is not there, not that a route is hidden.
* **The proxy must outlast a query.** A query runs until the database
  cancels it at the agent's statement timeout; a proxy that gives up first
  turns the database's answer into a gateway error.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from nl2sql_agent import __version__
from nl2sql_agent.config import Settings

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GUI = REPO_ROOT / "console"


@pytest.fixture(scope="module")
def package_json() -> dict:
    return json.loads((GUI / "package.json").read_text())


@pytest.fixture(scope="module")
def tsconfig() -> dict:
    return json.loads(re.sub(r"//.*", "", (GUI / "tsconfig.json").read_text()))


@pytest.fixture(scope="module")
def vitest_config() -> str:
    return (GUI / "vitest.config.ts").read_text()


@pytest.fixture(scope="module")
def vite_config() -> str:
    return (GUI / "vite.config.ts").read_text()


@pytest.fixture(scope="module")
def nginx_template() -> str:
    """The page's server block in the one proxy image (V6-37)."""
    return (REPO_ROOT / "proxy" / "pages" / "service.conf.template").read_text()


@pytest.fixture(scope="module")
def site_conf() -> str:
    """What every page serves of its own: the bundle, cached, from any path."""
    return (REPO_ROOT / "proxy" / "shared" / "site.conf").read_text()


@pytest.fixture(scope="module")
def client_ts() -> str:
    return (GUI / "src" / "api" / "client.ts").read_text()


# ---------------------------------------------------------------------------
# The project
# ---------------------------------------------------------------------------


def test_every_dependency_is_pinned_exactly(package_json: dict):
    for section in ("dependencies", "devDependencies"):
        for name, version in package_json[section].items():
            assert re.fullmatch(r"\d+\.\d+\.\d+", version), f"{name} is {version}, not pinned"


def test_it_is_a_project_of_its_own(package_json: dict):
    """Separate from both other interfaces, physically: an entry point in
    either of their projects would be served by their image."""
    for other in ("gui/package.json", "review/gui/package.json"):
        assert package_json["name"] != json.loads((REPO_ROOT / other).read_text())["name"]
    assert not (REPO_ROOT / "gui" / "src" / "console").exists()


def test_the_scripts_a_contributor_needs_are_all_there(package_json: dict):
    assert {"dev", "build", "test", "typecheck"} <= set(package_json["scripts"])
    assert "tsc --noEmit" in package_json["scripts"]["build"]
    assert "--coverage" in package_json["scripts"]["test"]


@pytest.mark.parametrize("option", ["strict", "noUncheckedIndexedAccess", "exactOptionalPropertyTypes"])
def test_the_compiler_is_strict(tsconfig: dict, option: str):
    assert tsconfig["compilerOptions"][option] is True


def test_the_coverage_thresholds_are_all_a_hundred(vitest_config: str):
    thresholds = re.search(r"thresholds: \{([^}]*)\}", vitest_config).group(1)
    assert dict(re.findall(r"(\w+): (\d+)", thresholds)) == {
        "statements": "100", "branches": "100", "functions": "100", "lines": "100"
    }


def test_only_the_entry_point_is_left_out_of_coverage(vitest_config: str):
    excluded = re.search(r"exclude: \[([^\]]*)\]", vitest_config).group(1)
    assert re.findall(r'"(src/[^"]+)"', excluded) == ["src/main.tsx"]


# ---------------------------------------------------------------------------
# The two proxies, which have to agree
# ---------------------------------------------------------------------------


def _paths(text: str) -> set[str]:
    return set(re.findall(r'"(/v1|/healthz|/readyz|/openapi\.json)"', text))


def test_the_dev_server_proxies_every_path_the_client_asks_for(vite_config: str, client_ts: str):
    asked = {f"/{path.split('/')[1]}" for path in re.findall(r'[`"](/v1/[^"`$]*)', client_ts)}
    assert asked, "the client asks for nothing -- the pattern needs updating"
    assert asked <= _paths(vite_config)


def test_nginx_proxies_the_same_paths_the_dev_server_does(nginx_template: str, vite_config: str):
    location = re.search(r"location ~ \^/\(([^)]*)\)", nginx_template).group(1)
    served = {part.replace("\\", "") for part in location.split("|")}
    assert served == {"v1", "healthz", "readyz", "openapi.json"}
    assert {f"/{name}" for name in served} == _paths(vite_config)


def test_the_dev_proxy_adds_the_token_and_does_not_verify_the_development_certificate(vite_config: str):
    assert "token: env.CONSOLE_TOKEN" in vite_config and "devProxies({" in vite_config
    assert re.search(r"NL2SQL_CONSOLE_TLS_VERIFY.*?\"false\"", vite_config, re.S)


# ---------------------------------------------------------------------------
# What nginx is told to do
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def api_location(nginx_template: str) -> str:
    match = re.search(r"location ~ \^/\([^)]*\) \{(.*?)\n    \}", nginx_template, re.S)
    assert match, "the proxied location block is not where it was"
    return match.group(1)


def test_the_token_is_added_by_the_proxy_not_by_the_browser(api_location: str):
    assert 'proxy_set_header Authorization "${UPSTREAM_AUTH_HEADER}"' in api_location


def test_the_upstream_certificate_is_verified(api_location: str):
    assert "include /tmp/nginx/upstream-tls.conf;" in api_location


def test_the_upstream_is_resolved_per_request(api_location: str):
    assert "resolver ${PROXY_RESOLVER}" in api_location
    assert "set $upstream ${UPSTREAM};" in api_location
    assert "proxy_pass $upstream$request_uri;" in api_location


def test_the_proxy_waits_as_long_as_a_query_can_run(api_location: str):
    assert "proxy_read_timeout ${UPSTREAM_READ_TIMEOUT};" in api_location
    assert "proxy_send_timeout ${UPSTREAM_READ_TIMEOUT};" in api_location
    default = re.search(r"\${CONSOLE_GUI_READ_TIMEOUT:-(\d+)s\}", (REPO_ROOT / "docker-compose.yml").read_text())
    assert default, "compose does not give the page a default read timeout"
    assert int(default.group(1)) * 1000 > Settings().statement_timeout_ms


def test_the_page_is_served_from_any_path_but_the_assets_are_immutable(nginx_template: str, site_conf: str):
    assert "include /etc/nginx/nl2sql/shared/site.conf;" in nginx_template
    assert "try_files $uri $uri/ /index.html;" in site_conf
    assert re.search(r"location /assets/ \{[^}]*immutable", site_conf, re.S)
    assert re.search(r"location = /index\.html \{[^}]*no-cache", site_conf, re.S)
