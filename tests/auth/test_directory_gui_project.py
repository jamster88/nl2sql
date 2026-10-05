"""The directory page as a project: its package, its proxy, its image, and
the start-up script that refuses to serve it beside a replica.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from nl2sql_agent import __version__

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GUI = REPO_ROOT / "auth" / "gui"
ENVSH = GUI / "10-nl2sql-directory-config.envsh"


@pytest.fixture(scope="module")
def package_json() -> dict:
    return json.loads((GUI / "package.json").read_text())


@pytest.fixture(scope="module")
def dockerfile() -> str:
    return (GUI / "Dockerfile").read_text()


@pytest.fixture(scope="module")
def template() -> str:
    return (GUI / "nginx.conf.template").read_text()


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
    assert "location /auth/ {" in template and "set $upstream ${DIRECTORY_UPSTREAM};" in template
    # V6-58: the directory's own API on the auth service's unpublished port.
    assert "location /directory/ {" in template and "set $upstream ${DIRECTORY_API_UPSTREAM};" in template
    assert 'proxy_set_header Authorization "${DIRECTORY_AUTH_HEADER}";' in template
    assert "proxy_set_header X-Forwarded-Host $http_host;" in template
    assert "client_max_body_size 6m;" in template


def test_the_image(dockerfile: str):
    assert "COPY auth/gui/package.json auth/gui/package-lock.json ./" in dockerfile
    assert re.search(r"^FROM nginx:1\.\d+-alpine$", dockerfile, re.MULTILINE)
    assert re.search(r"^FROM --platform=\$BUILDPLATFORM node:\d+-alpine AS build$", dockerfile, re.MULTILINE)
    assert 'NGINX_ENVSUBST_FILTER="^DIRECTORY_"' in dockerfile
    assert "DIRECTORY_GUI_PORT=8084" in dockerfile and "EXPOSE 8084" in dockerfile
    assert "HEALTHCHECK" in dockerfile and "--no-check-certificate" in dockerfile
    assert "/docker-entrypoint.d/10-nl2sql-directory-config.envsh" in dockerfile
    assert f'org.opencontainers.image.version="{__version__}"' in dockerfile
    assert not re.search(r"TOKEN=", dockerfile)


def test_build_artefacts_are_ignored():
    for path in ("auth/gui/node_modules", "auth/gui/dist", "auth/gui/coverage"):
        assert subprocess.run(["git", "check-ignore", "-q", f"{path}/"], cwd=REPO_ROOT).returncode == 0, path
        assert path in (REPO_ROOT / ".dockerignore").read_text(), path


# --- the start-up script -----------------------------------------------------------


def _env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    env = {
        "PATH": "/usr/bin:/bin",
        "NGINX_DIRECTORY_UPSTREAM_TLS_CONF": str(tmp_path / "upstream-tls.conf"),
        "NGINX_SERVER_TLS_CONF": str(tmp_path / "server-tls.conf"),
        "DIRECTORY_UPSTREAM": "http://nl2sql-auth:8446",
        "DIRECTORY_CACERT": "/etc/nl2sql/tls/server.crt",
        "DIRECTORY_SSL_NAME": "nl2sql-auth",
        "DIRECTORY_GUI_TLS_ENABLED": "false",
        "DIRECTORY_GUI_TLS_CERT_FILE": "/etc/nl2sql/tls/server.crt",
        "DIRECTORY_GUI_TLS_KEY_FILE": "/etc/nl2sql/tls/server.key",
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


def test_beside_a_replica_the_page_refuses_to_start(tmp_path: Path):
    result = _source(_env(tmp_path, LDAP_MODE="replica"))
    assert result.returncode != 0
    assert "nl2sql-directory-gui: LDAP_MODE=replica. A replica's people are edited on its" in result.stderr
    assert "primary directory and copied here, so this page is not served beside one." in result.stderr
    assert not (tmp_path / "upstream-tls.conf").exists()


@pytest.mark.parametrize("mode", [None, "standalone"])
def test_beside_a_standalone_directory_it_starts_and_holds_no_token(tmp_path: Path, mode):
    env = _env(tmp_path, DIRECTORY_TOKEN="ignored")
    if mode:
        env["LDAP_MODE"] = mode
    result = _source(env, 'printf "[%s]" "$DIRECTORY_AUTH_HEADER"')
    assert result.returncode == 0, result.stderr
    assert result.stdout == "[]"
    assert "passwords cross this hop in clear" in (tmp_path / "upstream-tls.conf").read_text()


def test_an_https_upstream_is_verified(tmp_path: Path):
    cacert = tmp_path / "server.crt"
    cacert.write_text("readable")
    assert _source(_env(tmp_path, DIRECTORY_UPSTREAM="https://nl2sql-auth:8446", DIRECTORY_CACERT=str(cacert))).returncode == 0
    written = (tmp_path / "upstream-tls.conf").read_text()
    assert "proxy_ssl_verify on;" in written and "proxy_ssl_name nl2sql-auth;" in written


def test_an_https_upstream_with_no_certificate_refuses_to_start(tmp_path: Path):
    result = _source(_env(tmp_path, DIRECTORY_UPSTREAM="https://nl2sql-auth:8446", DIRECTORY_CACERT=str(tmp_path / "x")))
    assert result.returncode != 0
    assert "nl2sql-directory-gui: DIRECTORY_UPSTREAM is https://nl2sql-auth:8446 but there is no" in result.stderr
    assert "readable certificate at DIRECTORY_CACERT=" in result.stderr
    assert "It is the stack's CA certificate, which the pki service writes beside this" in result.stderr
    assert "page's own certificate -- so this usually means that volume is not mounted here." in result.stderr
    assert "volume is not mounted here." in result.stderr


def test_the_page_is_https_with_the_apis_certificate(tmp_path: Path):
    cert, key = tmp_path / "server.crt", tmp_path / "server.key"
    cert.write_text("c")
    key.write_text("k")
    env = _env(tmp_path, DIRECTORY_GUI_TLS_ENABLED="true", DIRECTORY_GUI_TLS_CERT_FILE=str(cert), DIRECTORY_GUI_TLS_KEY_FILE=str(key))
    result = _source(env, 'printf "[%s]" "$DIRECTORY_GUI_LISTEN_TLS"')
    assert result.returncode == 0 and result.stdout == "[ ssl]"
    assert f"ssl_certificate {cert};" in (tmp_path / "server-tls.conf").read_text()


def test_an_https_page_with_no_certificate_refuses_to_start(tmp_path: Path):
    result = _source(_env(tmp_path, DIRECTORY_GUI_TLS_ENABLED="true", DIRECTORY_GUI_TLS_CERT_FILE=str(tmp_path / "x")))
    assert result.returncode != 0
    assert "nl2sql-directory-gui: DIRECTORY_GUI_TLS_ENABLED is on but" in result.stderr
    assert "is not readable. They are this page's own, from the pki service" in result.stderr
    assert "(its TLS volume, mounted here); mount it, or set DIRECTORY_GUI_TLS_ENABLED=false" in result.stderr
    assert "behind something that terminates TLS itself." in result.stderr


@pytest.mark.parametrize("off", ["false", "0", "no", "off"])
def test_a_plain_page_is_said_to_be_one(tmp_path: Path, off):
    result = _source(_env(tmp_path, DIRECTORY_GUI_TLS_ENABLED=off), 'printf "[%s]" "$DIRECTORY_GUI_LISTEN_TLS"')
    assert result.stdout == "[]"
    assert "every password typed into it" in (tmp_path / "server-tls.conf").read_text()
