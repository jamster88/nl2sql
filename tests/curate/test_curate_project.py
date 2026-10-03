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

from tests.review.test_review_project import _a_certificate, _source

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GUI = REPO_ROOT / "curate"


@pytest.fixture(scope="module")
def package_json() -> dict:
    return json.loads((GUI / "package.json").read_text())


@pytest.fixture(scope="module")
def nginx_template() -> str:
    return (GUI / "nginx.conf.template").read_text()


@pytest.fixture(scope="module")
def dockerfile() -> str:
    return (GUI / "Dockerfile").read_text()


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
    assert "REVIEW_TOKEN" in vite_config and 'setHeader("Authorization"' in vite_config
    assert re.search(r"NL2SQL_REVIEW_TLS_VERIFY.*?\"false\"", vite_config, re.S)


@pytest.fixture(scope="module")
def api_location(nginx_template: str) -> str:
    match = re.search(r"location ~ \^/\([^)]*\) \{(.*?)\n    \}", nginx_template, re.S)
    assert match, "the proxied location block is not where it was"
    return match.group(1)


def test_the_proxy_adds_the_token_verifies_the_service_and_resolves_it_per_request(api_location: str):
    assert 'proxy_set_header Authorization "${CURATE_AUTH_HEADER}"' in api_location
    assert "include /etc/nginx/nl2sql-curate-upstream-tls.conf;" in api_location
    assert "resolver ${CURATE_GUI_RESOLVER}" in api_location
    assert "set $upstream ${CURATE_UPSTREAM};" in api_location
    assert "proxy_pass $upstream$request_uri;" in api_location


def test_the_proxy_outlasts_a_save(api_location: str, dockerfile: str):
    """A save runs the loaders; a proxy that gives up first cuts off a write
    that is still happening."""
    assert "proxy_read_timeout ${CURATE_READ_TIMEOUT};" in api_location
    default = re.search(r"CURATE_READ_TIMEOUT=(\d+)s", dockerfile)
    from nl2sql_review.settings import ReviewSettings

    assert default and int(default.group(1)) >= ReviewSettings().reload_timeout_seconds


def test_the_page_is_served_from_any_path_but_the_assets_are_immutable(nginx_template: str):
    assert "try_files $uri $uri/ /index.html;" in nginx_template
    assert re.search(r"location /assets/ \{[^}]*immutable", nginx_template, re.S)
    assert re.search(r"location = /index\.html \{[^}]*no-cache", nginx_template, re.S)


# ---------------------------------------------------------------------------
# The image
# ---------------------------------------------------------------------------


def test_no_node_survives_into_the_served_image(dockerfile: str):
    stages = [line for line in dockerfile.splitlines() if line.startswith("FROM ")]
    assert len(stages) == 2 and "node:" in stages[0] and "nginx:" in stages[1]
    assert "--platform=$BUILDPLATFORM" in stages[0]


def test_the_dependency_layer_is_cached_separately(dockerfile: str):
    assert dockerfile.index("package-lock.json") < dockerfile.index("curate/src/")


def test_no_token_is_baked_into_the_image(dockerfile: str):
    assert not re.search(r"^\s*ENV\s+.*CURATE_TOKEN=", dockerfile, re.M)
    assert "CURATE_TOKEN is deliberately not given a default" in dockerfile


def test_the_image_serves_only_this_page_on_its_own_port(dockerfile: str):
    assert "rm -f /etc/nginx/conf.d/default.conf" in dockerfile
    assert 'NGINX_ENVSUBST_FILTER="^CURATE_"' in dockerfile
    assert "CURATE_GUI_PORT=8083" in dockerfile and "EXPOSE 8083" in dockerfile
    assert "HEALTHCHECK" in dockerfile and "/index.html" in dockerfile
    assert "/docker-entrypoint.d/10-nl2sql-curate-config.envsh" in dockerfile


# ---------------------------------------------------------------------------
# The start-up script nginx sources
# ---------------------------------------------------------------------------

ENVSH = GUI / "10-nl2sql-curate-config.envsh"


def _env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    env = {
        "PATH": "/usr/bin:/bin",
        "NGINX_CURATE_UPSTREAM_TLS_CONF": str(tmp_path / "upstream-tls.conf"),
        "CURATE_UPSTREAM": "https://nl2sql-review:8444",
        "CURATE_CACERT": "/etc/nl2sql/tls/server.crt",
        "CURATE_SSL_NAME": "nl2sql-review",
    }
    env.update(overrides)
    return env


def test_the_start_up_script_is_valid_executable_shell():
    result = subprocess.run(["sh", "-n", str(ENVSH)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert ENVSH.stat().st_mode & 0o111 and ENVSH.suffix == ".envsh"


def test_a_token_becomes_a_bearer_header_and_none_an_empty_one(tmp_path: Path):
    cert = _a_certificate(tmp_path)
    named = _source(ENVSH, _env(tmp_path, CURATE_TOKEN="s3cret", CURATE_CACERT=cert), then='printf "%s" "$CURATE_AUTH_HEADER"')
    assert named.returncode == 0 and named.stdout == "Bearer s3cret"
    empty = _source(ENVSH, _env(tmp_path, CURATE_CACERT=cert), then='printf "[%s]" "$CURATE_AUTH_HEADER"')
    assert empty.returncode == 0 and empty.stdout == "[]"


def test_an_https_upstream_writes_the_verification_block(tmp_path: Path):
    result = _source(ENVSH, _env(tmp_path, CURATE_CACERT=_a_certificate(tmp_path)))
    assert result.returncode == 0, result.stderr
    text = (tmp_path / "upstream-tls.conf").read_text()
    assert "proxy_ssl_verify on;" in text and "proxy_ssl_name nl2sql-review;" in text


def test_an_https_upstream_with_no_certificate_refuses_to_start(tmp_path: Path):
    result = _source(ENVSH, _env(tmp_path, CURATE_CACERT=str(tmp_path / "absent.crt")))
    assert result.returncode != 0
    said = " ".join(result.stderr.split())
    assert "readable certificate" in said and "apitls volume is not mounted here" in said


def test_a_plain_http_upstream_writes_no_verification_block_and_says_what_is_at_stake(tmp_path: Path):
    result = _source(ENVSH, _env(tmp_path, CURATE_UPSTREAM="http://nl2sql-review:8444"))
    assert result.returncode == 0, result.stderr
    text = (tmp_path / "upstream-tls.conf").read_text()
    assert "proxy_ssl_verify" not in text and "clear text" in text and "golden set and the snippets" in text
