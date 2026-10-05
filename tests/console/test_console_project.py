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
    return (GUI / "nginx.conf.template").read_text()


@pytest.fixture(scope="module")
def dockerfile() -> str:
    return (GUI / "Dockerfile").read_text()


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
    assert "CONSOLE_TOKEN" in vite_config
    assert 'setHeader("Authorization"' in vite_config
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
    assert 'proxy_set_header Authorization "${CONSOLE_AUTH_HEADER}"' in api_location


def test_the_upstream_certificate_is_verified(api_location: str):
    assert "include /etc/nginx/nl2sql-console-upstream-tls.conf;" in api_location


def test_the_upstream_is_resolved_per_request(api_location: str):
    assert "resolver ${CONSOLE_GUI_RESOLVER}" in api_location
    assert "set $upstream ${CONSOLE_UPSTREAM};" in api_location
    assert "proxy_pass $upstream$request_uri;" in api_location


def test_the_proxy_waits_as_long_as_a_query_can_run(api_location: str, dockerfile: str):
    assert "proxy_read_timeout ${CONSOLE_READ_TIMEOUT};" in api_location
    assert "proxy_send_timeout ${CONSOLE_READ_TIMEOUT};" in api_location
    default = re.search(r"CONSOLE_READ_TIMEOUT=(\d+)s", dockerfile)
    assert default, "the image does not set a default read timeout"
    assert int(default.group(1)) * 1000 > Settings().statement_timeout_ms


def test_the_page_is_served_from_any_path_but_the_assets_are_immutable(nginx_template: str):
    assert "try_files $uri $uri/ /index.html;" in nginx_template
    assert re.search(r"location /assets/ \{[^}]*immutable", nginx_template, re.S)
    assert re.search(r"location = /index\.html \{[^}]*no-cache", nginx_template, re.S)


# ---------------------------------------------------------------------------
# The image
# ---------------------------------------------------------------------------


def test_no_node_survives_into_the_served_image(dockerfile: str):
    stages = [line for line in dockerfile.splitlines() if line.startswith("FROM ")]
    assert len(stages) == 2
    assert "node:" in stages[0] and "--platform=$BUILDPLATFORM" in stages[0]
    assert "nginx:" in stages[1]


def test_the_dependency_layer_is_cached_separately(dockerfile: str):
    assert dockerfile.index("package-lock.json") < dockerfile.index("console/src/")


def test_no_token_is_baked_into_the_image(dockerfile: str):
    assert not re.search(r"^\s*(ENV\s+)?.*CONSOLE_TOKEN=", dockerfile, re.M)
    assert "CONSOLE_TOKEN is deliberately not given a default" in dockerfile


def test_the_default_config_is_overwritten_and_only_this_projects_variables_substituted(dockerfile: str):
    assert "rm -f /etc/nginx/conf.d/default.conf" in dockerfile
    assert 'NGINX_ENVSUBST_FILTER="^(CONSOLE_|AUTH_)"' in dockerfile


def test_the_image_has_a_health_check_and_says_what_version_it_is(dockerfile: str, package_json: dict):
    assert "HEALTHCHECK" in dockerfile and "/index.html" in dockerfile
    label = re.search(r'org\.opencontainers\.image\.version="([^"]+)"', dockerfile)
    assert label.group(1) == package_json["version"] == __version__


def test_the_image_defaults_match_what_compose_passes(dockerfile: str):
    """The console's port and name are the compose service's; an image run
    on its own should find it where compose would have put it."""
    assert "CONSOLE_UPSTREAM=https://nl2sql-console:8445" in dockerfile
    assert "CONSOLE_SSL_NAME=nl2sql-console" in dockerfile
    assert "EXPOSE 8082" in dockerfile and "CONSOLE_GUI_PORT=8082" in dockerfile


def test_the_console_gui_is_published_at_the_agents_tag():
    """The page and the process behind it are built from one checkout and
    only tested together."""
    setup_sh = (REPO_ROOT / "setup.sh").read_text()
    agent = re.search(r'^AGENT_TAG="(\S+)"', setup_sh, re.M)
    console = re.search(r'^CONSOLE_GUI_TAG="(\S+)"', setup_sh, re.M)
    assert agent and console
    assert agent.group(1) == console.group(1)


@pytest.mark.parametrize("artefact", ["node_modules", "dist", "coverage"])
def test_build_artefacts_are_not_committed(artefact: str):
    assert f"console/{artefact}/" in (REPO_ROOT / ".gitignore").read_text()


# ---------------------------------------------------------------------------
# The start-up script nginx sources
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def config_envsh() -> Path:
    return GUI / "10-nl2sql-console-config.envsh"


def _env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    env = {
        "PATH": "/usr/bin:/bin",
        "NGINX_CONSOLE_UPSTREAM_TLS_CONF": str(tmp_path / "upstream-tls.conf"),
        "CONSOLE_UPSTREAM": "https://nl2sql-console:8445",
        "CONSOLE_CACERT": "/etc/nl2sql/tls/server.crt",
        "CONSOLE_SSL_NAME": "nl2sql-console",
        "NGINX_AUTH_TLS_CONF": str(tmp_path / "auth-tls.conf"),
        "NGINX_SERVER_TLS_CONF": str(tmp_path / "server-tls.conf"),
        # Plain unless a test says otherwise, so each decision is tested on
        # its own rather than every test needing every certificate.
        "AUTH_UPSTREAM": "http://nl2sql-auth:8446",
        "AUTH_CACERT": "/etc/nl2sql/tls/server.crt",
        "AUTH_SSL_NAME": "nl2sql-auth",
        "CONSOLE_GUI_TLS_ENABLED": "false",
        "CONSOLE_GUI_TLS_CERT_FILE": "/etc/nl2sql/tls/server.crt",
        "CONSOLE_GUI_TLS_KEY_FILE": "/etc/nl2sql/tls/server.key",
    }
    env.update(overrides)
    return env


def _source(script: Path, env: dict[str, str], then: str = "true") -> subprocess.CompletedProcess:
    command = ["sh", "-c", f". {script}; {then}"]
    if os.environ.get("NL2SQL_SHELL_TRACE"):
        # See tests/shell_coverage.py: traced the way it runs, by `sh`, with
        # the name written in because POSIX sh has no BASH_SOURCE.
        env = {**env, "PS4": f"+@{script.name}@${{LINENO}}@ "}
        command = ["sh", "-x", "-c", f". {script}; {then}"]
    result = subprocess.run(command, capture_output=True, text=True, env=env)
    directory = os.environ.get("NL2SQL_SHELL_TRACE")
    if directory:
        with open(os.path.join(directory, "trace.log"), "a") as handle:
            handle.write(result.stderr)
    return result


def _a_certificate(tmp_path: Path) -> str:
    path = tmp_path / "server.crt"
    path.write_text("-----BEGIN CERTIFICATE-----\nnot a real one\n-----END CERTIFICATE-----\n")
    return str(path)


def test_the_start_up_script_is_valid_executable_shell(config_envsh: Path):
    assert subprocess.run(["sh", "-n", str(config_envsh)], capture_output=True).returncode == 0
    assert config_envsh.stat().st_mode & 0o111


def test_it_is_sourced_rather_than_run(config_envsh: Path, dockerfile: str):
    assert config_envsh.suffix == ".envsh"
    assert "/docker-entrypoint.d/10-nl2sql-console-config.envsh" in dockerfile


def test_with_sign_in_off_a_token_becomes_a_bearer_header_and_it_says_so(config_envsh: Path, tmp_path: Path):
    result = _source(
        config_envsh,
        _env(tmp_path, AUTH_ENABLED="false", CONSOLE_TOKEN="s3cret", CONSOLE_CACERT=_a_certificate(tmp_path)),
        then='printf "%s" "$CONSOLE_AUTH_HEADER"',
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "Bearer s3cret"
    assert "nl2sql-console-gui: sign-in is off (AUTH_ENABLED=false)" in result.stderr


def test_with_sign_in_off_and_no_token_the_proxy_adds_no_header(config_envsh: Path, tmp_path: Path):
    env = _env(tmp_path, AUTH_ENABLED="false", CONSOLE_CACERT=_a_certificate(tmp_path))
    env.pop("CONSOLE_TOKEN", None)
    result = _source(config_envsh, env, then='printf "[%s]" "$CONSOLE_AUTH_HEADER"')
    assert result.returncode == 0 and result.stdout == "[]"
    assert "nl2sql-console-gui: sign-in is off (AUTH_ENABLED=false)" in result.stderr


@pytest.mark.parametrize("enabled", [None, "", "true", "maybe"])
def test_sign_in_is_on_unless_switched_off_by_name(config_envsh: Path, tmp_path: Path, enabled):
    """V6-54: unset, empty or misspelt is sign-in on, and the proxy's token
    is never added for a visitor."""
    env = _env(tmp_path, CONSOLE_TOKEN="s3cret", CONSOLE_CACERT=_a_certificate(tmp_path))
    env.pop("AUTH_ENABLED", None)
    if enabled is not None:
        env["AUTH_ENABLED"] = enabled
    result = _source(config_envsh, env, then='printf "[%s]" "$CONSOLE_AUTH_HEADER"')
    assert result.returncode == 0, result.stderr
    assert result.stdout == "[]" and "sign-in is off" not in result.stderr


def test_no_token_becomes_an_empty_header_which_nginx_then_omits(config_envsh: Path, tmp_path: Path):
    result = _source(
        config_envsh,
        _env(tmp_path, CONSOLE_CACERT=_a_certificate(tmp_path)),
        then='printf "[%s]" "$CONSOLE_AUTH_HEADER"',
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "[]"


def test_an_https_upstream_writes_the_verification_block(config_envsh: Path, tmp_path: Path):
    result = _source(config_envsh, _env(tmp_path, CONSOLE_CACERT=_a_certificate(tmp_path)))
    assert result.returncode == 0, result.stderr
    text = (tmp_path / "upstream-tls.conf").read_text()
    assert "proxy_ssl_verify on;" in text
    assert "proxy_ssl_name nl2sql-console;" in text
    assert str(tmp_path / "server.crt") in text


def test_an_https_upstream_with_no_certificate_refuses_to_start(config_envsh: Path, tmp_path: Path):
    """nginx would otherwise fail with a BIO error that says nothing about
    why; the cause is almost always that the API has not written it yet."""
    result = _source(config_envsh, _env(tmp_path, CONSOLE_CACERT=str(tmp_path / "absent.crt")))
    assert result.returncode != 0
    said = " ".join(result.stderr.split())
    assert "nl2sql-console-gui: CONSOLE_UPSTREAM is https://nl2sql-console:8445 but there is no" in said
    assert "readable certificate at CONSOLE_CACERT=" in said
    assert "that volume is not mounted here" in said


def test_a_plain_http_upstream_writes_no_verification_block(config_envsh: Path, tmp_path: Path):
    """The right outcome behind CONSOLE_TLS_ENABLED=false, where there is no
    certificate anywhere to name -- and it says what crosses in clear text."""
    result = _source(config_envsh, _env(tmp_path, CONSOLE_UPSTREAM="http://nl2sql-console:8445"))
    assert result.returncode == 0, result.stderr
    text = (tmp_path / "upstream-tls.conf").read_text()
    assert "proxy_ssl_verify" not in text
    assert "Every query and every row the browser is sent crosses in clear text." in text


# --- sign-in --------------------------------------------------------------------


@pytest.mark.parametrize("enabled", ["true", "1", "yes", "on"])
def test_with_sign_in_on_the_proxy_adds_no_token(config_envsh: Path, enabled, tmp_path: Path):
    """The browser's session goes through instead; a token here would let
    every visitor act as the service."""
    env = _env(tmp_path, CONSOLE_UPSTREAM="http://upstream:1", CONSOLE_TOKEN="s3cret", AUTH_ENABLED=enabled)
    result = _source(config_envsh, env, 'printf "%s" "$CONSOLE_AUTH_HEADER"')
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_the_sign_in_hop_is_verified(config_envsh: Path, tmp_path: Path):
    cacert = tmp_path / "server.crt"
    cacert.write_text("readable")
    env = _env(tmp_path, CONSOLE_UPSTREAM="http://upstream:1", AUTH_UPSTREAM="https://nl2sql-auth:8446", AUTH_CACERT=str(cacert))
    assert _source(config_envsh, env).returncode == 0
    written = (tmp_path / "auth-tls.conf").read_text()
    assert "proxy_ssl_verify on;" in written and "proxy_ssl_name nl2sql-auth;" in written


def test_a_plain_sign_in_hop_says_what_is_at_stake(config_envsh: Path, tmp_path: Path):
    env = _env(tmp_path, CONSOLE_UPSTREAM="http://upstream:1")
    assert _source(config_envsh, env).returncode == 0
    assert "passwords cross this hop in clear text" in (tmp_path / "auth-tls.conf").read_text()


def test_a_sign_in_hop_with_no_certificate_refuses_to_start(config_envsh: Path, tmp_path: Path):
    env = _env(tmp_path, CONSOLE_UPSTREAM="http://upstream:1", AUTH_UPSTREAM="https://nl2sql-auth:8446",
               AUTH_CACERT=str(tmp_path / "absent.crt"))
    result = _source(config_envsh, env)
    assert result.returncode != 0
    assert "nl2sql-console-gui: AUTH_UPSTREAM is https://nl2sql-auth:8446 but there is no" in result.stderr
    assert "readable certificate at AUTH_CACERT=" in result.stderr
    assert "It is the stack's CA certificate, which the pki service writes beside this" in result.stderr
    assert "page's own certificate -- so this usually means that volume is not mounted here." in result.stderr
    assert "volume is not mounted here." in result.stderr


def test_the_page_is_https_with_the_apis_certificate(config_envsh: Path, tmp_path: Path):
    cert, key = tmp_path / "server.crt", tmp_path / "server.key"
    cert.write_text("c")
    key.write_text("k")
    env = _env(tmp_path, CONSOLE_UPSTREAM="http://upstream:1", CONSOLE_GUI_TLS_ENABLED="true",
               CONSOLE_GUI_TLS_CERT_FILE=str(cert), CONSOLE_GUI_TLS_KEY_FILE=str(key))
    result = _source(config_envsh, env, 'printf "[%s]" "$CONSOLE_GUI_LISTEN_TLS"')
    assert result.returncode == 0, result.stderr
    assert result.stdout == "[ ssl]"
    written = (tmp_path / "server-tls.conf").read_text()
    assert f"ssl_certificate {cert};" in written and f"ssl_certificate_key {key};" in written


def test_an_https_page_with_no_certificate_refuses_to_start(config_envsh: Path, tmp_path: Path):
    env = _env(tmp_path, CONSOLE_UPSTREAM="http://upstream:1", CONSOLE_GUI_TLS_ENABLED="true",
               CONSOLE_GUI_TLS_CERT_FILE=str(tmp_path / "absent.crt"))
    result = _source(config_envsh, env)
    assert result.returncode != 0
    assert "nl2sql-console-gui: CONSOLE_GUI_TLS_ENABLED is on but" in result.stderr
    assert "is not readable. They are this page's own, from the pki service" in result.stderr
    assert "(its TLS volume, mounted here); mount it, or set CONSOLE_GUI_TLS_ENABLED=false" in result.stderr
    assert "behind something that terminates TLS itself." in result.stderr


@pytest.mark.parametrize("off", ["false", "0", "no", "off"])
def test_a_plain_page_is_said_to_be_one(config_envsh: Path, off, tmp_path: Path):
    env = _env(tmp_path, CONSOLE_UPSTREAM="http://upstream:1", CONSOLE_GUI_TLS_ENABLED=off)
    result = _source(config_envsh, env, 'printf "[%s]" "$CONSOLE_GUI_LISTEN_TLS"')
    assert result.stdout == "[]"
    assert "every password typed into it" in (tmp_path / "server-tls.conf").read_text()

