"""Sign-in as compose resolves it: the directory, the auth service, the
directory page, and what every other service is given to take part.

Behind `--run-docker` like the other compose tests: `docker compose config`
only parses and resolves, but it needs a working `docker` CLI.

What carries weight is what joins files that never mention each other:

* **Every setting is settable.** Each service reads its settings from the
  environment; one compose never passes is one that cannot be changed
  without rebuilding an image.
* **The directory is nobody's but the database's and the auth service's.**
  It publishes no port, and the name its certificate is issued for is the
  name both of them connect to.
* **One key.** The auth service writes the public half of its signing key
  into `authkeys`; the API, the console and the review service read it from
  there, at the path their guard looks for it by default.
* **On by default, off with one switch.** `AUTH_ENABLED` reaches every
  service and interface that has sign-in, from one variable.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.docker

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: The services and interfaces that take part in sign-in, and so take AUTH_ENABLED.
SIGNED_IN = ("api", "console", "review", "gui", "reviewgui", "curategui", "consolegui", "mlflowproxy")

#: The three that check a session themselves, against the auth service's public key.
CHECKERS = ("api", "console", "review")

#: Every profile, so every service resolves.
PROFILES = ("agent", "api", "gui", "feedback", "review", "reviewgui", "curategui", "console", "consolegui",
            "mlflow", "auth", "directorygui")


def _compose_config(tmp_path: Path, env: dict | None = None) -> dict:
    empty_env_file = tmp_path / "empty.env"
    empty_env_file.write_text("")
    cmd = ["docker", "compose", "--env-file", str(empty_env_file)]
    for profile in PROFILES:
        cmd += ["--profile", profile]
    cmd += ["config", "--format", "json"]
    substitutable = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)", (REPO_ROOT / "docker-compose.yml").read_text()))
    run_env = {k: v for k, v in os.environ.items() if k not in substitutable}
    run_env.update(env or {})
    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=60, env=run_env)
    if result.returncode != 0:
        pytest.fail(f"`docker compose config` failed:\n{result.stderr}")
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def config(tmp_path_factory) -> dict:
    return _compose_config(tmp_path_factory.mktemp("compose"))


@pytest.fixture(scope="module")
def services(config: dict) -> dict:
    return config["services"]


def _read(*paths: str, pattern: str = r'"((?:LDAP|AUTH|MLFLOW)_[A-Z_]+)"') -> set[str]:
    """Every quoted setting name in these source files."""
    return {name for path in paths for name in re.findall(pattern, (REPO_ROOT / path).read_text())}


def _volume(service: dict, target: str) -> dict:
    [found] = [volume for volume in service["volumes"] if volume["target"] == target]
    return found


# --- the directory ----------------------------------------------------------------


def test_the_directory_is_reached_on_the_stacks_network_by_the_name_its_certificate_names(services: dict):
    ldap = services["ldap"]
    assert ldap["container_name"] == "nl2sql-ldap"
    assert ldap["hostname"] == "nl2sql-ldap", "libldap checks the certificate against the name it was asked for"
    assert "ports" not in ldap, "only the database and the auth service talk to the directory"
    assert ldap["profiles"] == ["auth"]
    assert ldap["build"]["dockerfile"] == "ldap/Dockerfile"


def test_the_directory_keeps_its_people_and_writes_its_certificate_where_the_others_read_it(services: dict):
    ldap = services["ldap"]
    assert _volume(ldap, "/var/lib/openldap/openldap-data")["source"] == "ldapdata"
    tls = _volume(ldap, "/etc/nl2sql/ldap-tls")
    assert tls["source"] == "ldaptls" and not tls.get("read_only")
    seed = _volume(ldap, "/seed")
    assert seed["type"] == "bind" and seed["source"] == str(REPO_ROOT / "ldap" / "seed") and seed["read_only"]
    for reader in ("postgres", "auth"):
        mounted = _volume(services[reader], "/etc/nl2sql/ldap-tls")
        assert (mounted["source"], mounted["read_only"]) == ("ldaptls", True), reader


def test_every_setting_the_directory_reads_can_be_set_through_compose(services: dict):
    read = _read("ldap/nl2sql_ldap/settings.py", "ldap/nl2sql_ldap/replica.py", pattern=r'"(LDAP_[A-Z_]+)"')
    assert sorted(read - set(services["ldap"]["environment"])) == []
    assert sorted(set(services["ldap"]["environment"]) - read) == []


def test_the_directory_starts_standalone_with_its_defaults(services: dict):
    env = services["ldap"]["environment"]
    assert (env["LDAP_MODE"], env["LDAP_BASE_DN"], env["LDAP_ADMIN_USER"]) == ("standalone", "dc=nl2sql,dc=local", "admin")
    # Generated into .env by setup.sh; never a default anyone could guess.
    assert env["LDAP_SERVICE_PASSWORD"] == env["LDAP_ADMIN_PASSWORD"] == ""


# --- the auth service --------------------------------------------------------------


def test_the_auth_service_is_published_for_the_desktop_and_waits_for_what_it_needs(services: dict):
    auth = services["auth"]
    assert auth["container_name"] == "nl2sql-auth"
    [port] = auth["ports"]
    assert (port["published"], port["target"]) == ("8446", 8446)
    # The certificate it presents is the API's, so the API comes first.
    assert set(auth["depends_on"]) == {"postgres", "ldap", "api"}
    assert auth["profiles"] == ["auth"]


def test_every_setting_the_auth_service_reads_can_be_set_through_compose(services: dict):
    read = _read("auth/nl2sql_auth/settings.py")
    assert sorted(read - set(services["auth"]["environment"])) == []
    assert sorted(set(services["auth"]["environment"]) - read) == []


def test_the_role_sync_signs_in_to_the_retail_database_as_its_own_role(tmp_path_factory):
    config = _compose_config(
        tmp_path_factory.mktemp("rolesync"),
        env={"AUTH_ROLESYNC_PASSWORD": "generated", "POSTGRES_DB": "retail", "POSTGRES_READER_USER": "reader"},
    )
    env = config["services"]["auth"]["environment"]
    assert env["AUTH_ROLESYNC_DB_URL"] == "postgresql://nl2sql_rolesync:generated@nl2sql-postgres:5432/retail"
    assert (env["AUTH_DB_NAME"], env["AUTH_READER_ROLE"]) == ("retail", "reader")


def test_the_signing_key_is_the_auth_services_alone_and_its_public_half_is_everyones(services: dict):
    auth = services["auth"]
    assert _volume(auth, "/var/lib/nl2sql-auth")["source"] == "authdata"
    written = _volume(auth, "/etc/nl2sql/auth")
    assert written["source"] == "authkeys" and not written.get("read_only")
    for name, service in services.items():
        if name == "auth":
            continue
        mounted = {volume["source"] for volume in service.get("volumes", [])}
        assert "authdata" not in mounted, name
    for name in CHECKERS:
        read = _volume(services[name], "/etc/nl2sql/auth")
        assert (read["source"], read["read_only"]) == ("authkeys", True), name
        assert services[name]["environment"]["AUTH_PUBLIC_KEY_FILE"] == "/etc/nl2sql/auth/session.pub", name
    assert services["auth"]["environment"]["AUTH_PUBLIC_KEY_FILE"] == "/etc/nl2sql/auth/session.pub"


def test_the_apis_certificate_covers_the_auth_service(services: dict):
    names = services["api"]["environment"]["API_TLS_HOSTNAMES"].split(",")
    assert {"nl2sql-auth", "auth"} <= set(names)


# --- one switch --------------------------------------------------------------------


@pytest.mark.parametrize("name", SIGNED_IN)
def test_sign_in_is_on_by_default_everywhere_it_applies(services: dict, name: str):
    assert services[name]["environment"]["AUTH_ENABLED"] == "true"


def test_one_variable_turns_it_off_everywhere(tmp_path_factory):
    config = _compose_config(tmp_path_factory.mktemp("off"), env={"AUTH_ENABLED": "false"})
    assert {name: config["services"][name]["environment"]["AUTH_ENABLED"] for name in SIGNED_IN} == dict.fromkeys(
        SIGNED_IN, "false"
    )


@pytest.mark.parametrize("name", ["gui", "reviewgui", "curategui", "consolegui", "directorygui", "mlflowproxy"])
def test_every_interface_is_https_unless_one_variable_says_otherwise(services: dict, name: str, tmp_path_factory):
    [enabled] = [value for key, value in services[name]["environment"].items() if key.endswith("_TLS_ENABLED")]
    assert enabled == "true"
    cert = _volume(services[name], "/etc/nl2sql/tls")
    assert (cert["source"], cert["read_only"]) == ("apitls", True)


def test_gui_tls_enabled_switches_every_interface(tmp_path_factory):
    config = _compose_config(tmp_path_factory.mktemp("plain"), env={"GUI_TLS_ENABLED": "false"})
    for name in ("gui", "reviewgui", "curategui", "consolegui", "directorygui", "mlflowproxy"):
        env = config["services"][name]["environment"]
        assert [value for key, value in env.items() if key.endswith("_TLS_ENABLED")] == ["false"], name


# --- the directory page ------------------------------------------------------------


def test_the_directory_page_is_this_machines_and_waits_for_the_auth_service(services: dict):
    page = services["directorygui"]
    [port] = page["ports"]
    assert (port["host_ip"], port["published"], port["target"]) == ("127.0.0.1", "8084", 8084)
    assert set(page["depends_on"]) == {"auth"}
    assert page["profiles"] == ["directorygui"]
    # Told the mode so it can refuse to start beside a replica.
    assert page["environment"]["LDAP_MODE"] == "standalone"


def test_every_setting_the_directory_page_reads_can_be_set_through_compose_and_nothing_else_is(services: dict):
    gui = REPO_ROOT / "auth" / "gui"
    sources = (gui / "nginx.conf.template").read_text() + (gui / "10-nl2sql-directory-config.envsh").read_text()
    computed = {"DIRECTORY_AUTH_HEADER", "DIRECTORY_GUI_LISTEN_TLS", "NGINX_DIRECTORY_UPSTREAM_TLS_CONF",
                "NGINX_SERVER_TLS_CONF"}
    read = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)", sources)) - computed
    env = set(services["directorygui"]["environment"])
    assert sorted(read - env) == []
    assert sorted(env - read) == []


def test_no_two_services_publish_the_same_port(services: dict):
    published = [port["published"] for service in services.values() for port in service.get("ports", [])]
    assert len(published) == len(set(published)), sorted(published)
