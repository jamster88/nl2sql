"""The review GUI's project, its nginx config, and the script nginx sources.

The same shape of test as `tests/gui/test_gui_project.py`, and mostly for the
same reasons. Two differences are specific to this application and are what
most of the file is about:

* **The token this proxy holds can rewrite the golden question set.** That it
  never reaches the browser is not a nicety here, and the empty case -- no
  token configured -- has to send no header rather than the word "Bearer".
* **There is no event stream**, so the buffering rules the web GUI needs are
  absent and a long read timeout on promotion is present instead. Promotion
  runs both RAG loaders; a proxy that gives up first cuts off a write that
  is still happening.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GUI = REPO_ROOT / "review" / "gui"


@pytest.fixture(scope="module")
def package_json() -> dict:
    return json.loads((GUI / "package.json").read_text())


@pytest.fixture(scope="module")
def tsconfig() -> dict:
    text = (GUI / "tsconfig.json").read_text()
    return json.loads(re.sub(r"//.*", "", text))


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
    """A range means two clones build different bundles from one lockfile
    bump, and the first sign is a bug that reproduces on one machine."""
    for section in ("dependencies", "devDependencies"):
        for name, version in package_json[section].items():
            assert re.fullmatch(r"\d+\.\d+\.\d+", version), f"{name} is {version}, not pinned"


def test_the_lockfile_is_committed():
    assert (GUI / "package-lock.json").is_file()


def test_it_is_a_separate_project_from_the_web_gui(package_json: dict):
    """Separate on purpose, and physically rather than by convention.

    Two Vite entry points in one project share a build, and the public GUI's
    image would then serve the interface that rewrites the golden question
    set to anyone who could reach it.
    """
    web = json.loads((REPO_ROOT / "gui" / "package.json").read_text())
    assert package_json["name"] != web["name"]
    assert not (REPO_ROOT / "gui" / "src" / "review").exists()


def test_the_scripts_a_contributor_needs_are_all_there(package_json: dict):
    assert {"dev", "build", "test", "typecheck"} <= set(package_json["scripts"])


def test_the_build_refuses_code_that_does_not_typecheck(package_json: dict):
    """`vite build` strips types without checking them."""
    assert "tsc --noEmit" in package_json["scripts"]["build"]


def test_the_test_script_measures_coverage(package_json: dict):
    assert "--coverage" in package_json["scripts"]["test"]


@pytest.mark.parametrize("option", ["strict", "noUncheckedIndexedAccess", "exactOptionalPropertyTypes"])
def test_the_compiler_is_strict(tsconfig: dict, option: str):
    assert tsconfig["compilerOptions"][option] is True


def test_the_coverage_thresholds_are_all_a_hundred(vitest_config: str):
    thresholds = re.search(r"thresholds: \{([^}]*)\}", vitest_config).group(1)
    found = dict(re.findall(r"(\w+): (\d+)", thresholds))
    assert found == {"statements": "100", "branches": "100", "functions": "100", "lines": "100"}


def test_only_the_entry_point_is_left_out_of_coverage(vitest_config: str):
    excluded = re.search(r"exclude: \[([^\]]*)\]", vitest_config).group(1)
    assert re.findall(r'"(src/[^"]+)"', excluded) == ["src/main.tsx"]


# ---------------------------------------------------------------------------
# The two proxies, which have to agree
# ---------------------------------------------------------------------------


def _api_paths(text: str) -> set[str]:
    return set(re.findall(r'"(/v1|/healthz|/readyz|/openapi\.json)"', text))


def test_the_dev_server_proxies_every_path_the_client_asks_for(vite_config: str, client_ts: str):
    """Anything the client requests and the dev server does not proxy is a
    request answered by Vite with the page, which parses as JSON never."""
    asked = {f"/{path.split('/')[1]}" for path in re.findall(r'"(/v1/[^"`$]*|/readyz)"', client_ts)}
    proxied = _api_paths(vite_config)
    assert asked <= proxied, f"the dev proxy does not cover {asked - proxied}"


def test_nginx_proxies_the_same_paths_the_dev_server_does(nginx_template: str, vite_config: str):
    """The two are the same three rules in two syntaxes, and a path proxied
    in development but not in the image is a bug nobody meets until deploy.
    """
    location = re.search(r"location ~ \^/\(([^)]*)\)", nginx_template).group(1)
    served = {part.replace("\\", "") for part in location.split("|")}
    assert served == {"v1", "healthz", "readyz", "openapi.json"}
    assert {f"/{name.split('.')[0]}" for name in served} <= {
        path.split(".")[0] for path in _api_paths(vite_config)
    }


def test_the_dev_proxy_adds_the_token_so_the_browser_never_holds_it(vite_config: str):
    assert "token: env.REVIEW_TOKEN" in vite_config and "devProxies({" in vite_config


def test_the_dev_proxy_does_not_verify_the_development_certificate(vite_config: str):
    """The default certificate is the one the API wrote for itself, and this
    hop is a developer's own machine."""
    assert re.search(r"NL2SQL_REVIEW_TLS_VERIFY.*?\"false\"", vite_config, re.S)


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
    """This hop crosses a container network, and a proxy that trusts anything
    at the far end is a proxy that will one day trust something else."""
    assert "include /tmp/nginx/upstream-tls.conf;" in api_location


def test_the_upstream_is_resolved_per_request(api_location: str):
    """nginx resolves a literal upstream while parsing its config and caches
    it for the life of the process: it refuses to start before the service is
    up, and keeps talking to a stale address after it restarts."""
    assert "resolver ${PROXY_RESOLVER}" in api_location
    assert "set $upstream ${UPSTREAM};" in api_location
    assert "proxy_pass $upstream$request_uri;" in api_location


def test_the_proxy_outlasts_a_promotion(api_location: str):
    """Promotion runs both RAG loaders and re-embeds. A proxy that gives up
    first cuts off a write that is still happening."""
    assert "proxy_read_timeout ${UPSTREAM_READ_TIMEOUT};" in api_location
    assert "proxy_send_timeout ${UPSTREAM_READ_TIMEOUT};" in api_location


def test_the_default_timeout_outlasts_the_services_own_cap():
    default = re.search(r"\${REVIEW_GUI_READ_TIMEOUT:-(\d+)s\}", (REPO_ROOT / "docker-compose.yml").read_text())
    assert default, "compose does not give the page a default read timeout"
    from nl2sql_review.settings import ReviewSettings

    assert int(default.group(1)) >= ReviewSettings().reload_timeout_seconds


def test_the_page_is_served_from_any_path_but_the_assets_are_immutable(nginx_template: str, site_conf: str):
    assert "include /etc/nginx/nl2sql/shared/site.conf;" in nginx_template
    assert "try_files $uri $uri/ /index.html;" in site_conf
    assert re.search(r"location /assets/ \{[^}]*immutable", site_conf, re.S)
    assert re.search(r"location = /index\.html \{[^}]*no-cache", site_conf, re.S)
