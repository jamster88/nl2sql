"""The GUI's configuration, checked for the things that rot quietly.

None of this needs npm, Docker or a network: it reads the files. What it
looks for is the class of mistake that does not show up until something is
deployed -- a path the dev server proxies and nginx does not, a dependency
that floated to a new major version between two clones, a coverage threshold
quietly lowered, an event stream that works locally and is buffered in
production.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from nl2sql_agent import __version__

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GUI = REPO_ROOT / "gui"


@pytest.fixture(scope="module")
def package_json() -> dict:
    return json.loads((GUI / "package.json").read_text())


@pytest.fixture(scope="module")
def tsconfig() -> dict:
    # tsconfig.json permits comments; this one has none, but strip trailing
    # commas defensively rather than depending on that staying true.
    text = (GUI / "tsconfig.json").read_text()
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", text))


@pytest.fixture(scope="module")
def vite_config() -> str:
    return (GUI / "vite.config.ts").read_text()


@pytest.fixture(scope="module")
def vitest_config() -> str:
    return (GUI / "vitest.config.ts").read_text()


@pytest.fixture(scope="module")
def nginx_template() -> str:
    """The page's server block in the one proxy image (V6-37)."""
    return (REPO_ROOT / "proxy" / "pages" / "gui.conf.template").read_text()


@pytest.fixture(scope="module")
def site_conf() -> str:
    """What every page serves of its own: the bundle, cached, from any path."""
    return (REPO_ROOT / "proxy" / "shared" / "site.conf").read_text()


@pytest.fixture(scope="module")
def client_ts() -> str:
    return (GUI / "src" / "api" / "client.ts").read_text()


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def test_every_dependency_is_pinned_exactly(package_json: dict):
    """A range makes two clones of this repository different programs.

    The agent's requirements.txt pins the same way and for the same reason:
    a bug that only appears on one machine is the most expensive kind.
    """
    floating = {
        name: spec
        for group in ("dependencies", "devDependencies")
        for name, spec in package_json.get(group, {}).items()
        if not re.fullmatch(r"\d+\.\d+\.\d+", spec)
    }
    assert floating == {}


def test_the_lockfile_is_committed():
    """`npm ci` needs it, and so does a reproducible image build."""
    assert (GUI / "package-lock.json").is_file()


def test_the_gui_version_follows_the_agent(package_json: dict):
    assert package_json["version"] == __version__


def test_the_scripts_a_contributor_needs_are_all_there(package_json: dict):
    assert {"dev", "build", "test", "typecheck"} <= set(package_json["scripts"])


def test_the_build_refuses_code_that_does_not_typecheck(package_json: dict):
    """`vite build` alone strips types without checking them."""
    assert "tsc --noEmit" in package_json["scripts"]["build"]


# ---------------------------------------------------------------------------
# TypeScript and the test suite
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "option",
    ["strict", "noUncheckedIndexedAccess", "exactOptionalPropertyTypes", "noUnusedLocals"],
)
def test_the_compiler_is_strict(tsconfig: dict, option: str):
    assert tsconfig["compilerOptions"][option] is True


def test_the_coverage_thresholds_are_all_a_hundred(vitest_config: str):
    """Matching the Python side. A threshold below 100 is a number nobody
    looks at; a failing build is read immediately."""
    thresholds = re.search(r"thresholds: \{(.*?)\}", vitest_config, re.DOTALL)
    assert thresholds
    measured = dict(re.findall(r"(\w+): (\d+)", thresholds.group(1)))
    assert measured == {
        "statements": "100",
        "branches": "100",
        "functions": "100",
        "lines": "100",
    }


def test_only_the_entry_point_is_left_out_of_coverage(vitest_config: str):
    exclude = re.search(r"exclude: \[([^\]]*)\]", vitest_config)
    assert exclude
    assert re.findall(r'"([^"]+)"', exclude.group(1)) == ["src/main.tsx"]


# ---------------------------------------------------------------------------
# The proxy, in both of the places it exists
# ---------------------------------------------------------------------------


def _client_paths(client_ts: str) -> set[str]:
    """The API paths the client actually asks for, as prefixes."""
    literal = set(re.findall(r'request<[^>]*>\(\s*[`"]([^`"$]+)', client_ts))
    # `eventsUrl` follows the link the server supplied, which is always under
    # /v1/questions; the client never builds it.
    literal.add("/v1/questions")
    return {"/" + path.lstrip("/").split("/")[0] for path in literal}


def test_the_dev_server_proxies_every_path_the_client_asks_for(vite_config: str, client_ts: str):
    proxied = set(re.findall(r'"(/[\w./]+)"', re.search(r"API_PATHS = \[([^\]]+)\]", vite_config).group(1)))
    missing = {path for path in _client_paths(client_ts) if path not in proxied}
    assert missing == set(), f"npm run dev would 404 on {sorted(missing)}"


def test_nginx_proxies_every_path_the_client_asks_for(nginx_template: str, client_ts: str):
    """The failure this prevents is the worst kind: works in development,
    404s in the image, and only for the paths nobody clicked before release.
    """
    location = re.search(r"location ~ (\S+) \{", nginx_template)
    assert location, "the API location block has changed shape"
    pattern = re.compile(location.group(1).replace(r"\.", r"\."))

    for path in sorted(_client_paths(client_ts)):
        probe = "/openapi.json" if path == "/openapi.json" else f"{path}/anything"
        assert pattern.match(probe), f"nginx would not proxy {probe}"


def test_the_dev_proxy_and_nginx_agree_on_what_the_api_owns(vite_config: str, nginx_template: str):
    dev = set(re.findall(r'"(/[\w./]+)"', re.search(r"API_PATHS = \[([^\]]+)\]", vite_config).group(1)))
    served = re.search(r"location ~ \^/\(([^)]+)\)", nginx_template).group(1)
    nginx = {"/" + name.replace("\\", "") for name in served.split("|")}
    assert dev == nginx


def test_the_dev_proxy_does_not_verify_the_development_certificate(vite_config: str):
    """The self-signed certificate is the point of the development one, and
    this hop is a loopback on the developer's own machine."""
    assert 'NL2SQL_API_TLS_VERIFY ?? "false"' in vite_config


def test_the_dev_proxy_adds_the_token_so_the_browser_never_holds_it(vite_config: str):
    """The proxies every page builds the same way (web/src/vite.ts, V6-25):
    this one with the API token, streaming."""
    assert "devProxies({" in vite_config and "token: env.API_TOKEN" in vite_config and "stream: true" in vite_config
    shared = (Path(__file__).resolve().parent.parent.parent / "web" / "src" / "vite.ts").read_text()
    assert 'setHeader("Authorization", `Bearer ${token}`)' in shared


# ---------------------------------------------------------------------------
# nginx
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def api_location(nginx_template: str) -> str:
    block = re.search(r"location ~ [^\n]+\{(.*?)\n    \}", nginx_template, re.DOTALL)
    assert block
    return block.group(1)


def test_the_event_stream_is_not_buffered(api_location: str):
    """Three settings, and every one of them fails silently.

    nginx buffers a proxied response by default and compresses it if it can,
    either of which holds the whole progress stream until the answer is
    finished -- which is the one thing the stream exists to avoid, and it
    looks like a spinner that never moves rather than like an error.
    """
    assert "proxy_buffering off;" in api_location
    assert "proxy_cache off;" in api_location
    assert "gzip off;" in api_location


def test_a_reconnecting_browser_can_resume_where_it_left_off(api_location: str):
    """`Last-Event-ID` is how the server knows to resume rather than replay,
    and a proxy that drops it turns every reconnect into a redraw."""
    assert "proxy_set_header Last-Event-ID $http_last_event_id;" in api_location


def test_the_proxy_outlasts_a_question(api_location: str):
    assert "proxy_read_timeout ${UPSTREAM_READ_TIMEOUT};" in api_location


def test_the_upstream_is_resolved_per_request(api_location: str):
    """A literal upstream is resolved once, while nginx parses its config.

    That stops the GUI starting when the API is not up yet, and leaves it
    talking to a stale address after the API is restarted onto a new one.
    """
    assert "resolver ${PROXY_RESOLVER}" in api_location
    assert "set $upstream ${UPSTREAM};" in api_location
    assert "proxy_pass $upstream$request_uri;" in api_location


def test_the_certificate_block_is_written_at_start_up(api_location: str):
    """It has to be, because it is wrong when the upstream is plain HTTP."""
    assert "include /tmp/nginx/upstream-tls.conf;" in api_location
    assert "proxy_ssl_verify" not in api_location


def test_the_page_is_served_from_any_path_but_the_assets_are_immutable(nginx_template: str, site_conf: str):
    assert "include /etc/nginx/nl2sql/shared/site.conf;" in nginx_template
    assert "try_files $uri $uri/ /index.html;" in site_conf
    assert 'add_header Cache-Control "public, immutable";' in site_conf
    assert 'add_header Cache-Control "no-cache";' in site_conf


# ---------------------------------------------------------------------------
# Everything around it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["gui/node_modules", "gui/dist"])
def test_build_artefacts_are_not_committed_or_sent_to_the_daemon(path: str):
    """node_modules is tens of thousands of files. Sending it as build
    context is slow, and committing it is worse."""
    assert f"{path}/" in (REPO_ROOT / ".gitignore").read_text()
    assert path in (REPO_ROOT / ".dockerignore").read_text()
