"""What the stack exposes by default, read from the files that decide it.

The security tier's posture checks (V6-49, seeded in 6.1). Each is a
property a reviewer would otherwise have to re-derive by reading compose,
the dataset image and the start-up scripts, and each is a finding of the
v6_1 review that a later edit could quietly undo:

* every store is published on this machine only by default (V6-06);
* the dataset image bakes no password in, and its start refuses clear text
  and the superuser over the network (V6-07, V6-52);
* sign-in's rules are `hostssl`, and the auth service verifies the database
  (V6-53);
* every TLS server presents a key of its own, and no container holds
  another's (V6-36);
* sign-in is on by default in every service's code, not only in compose
  (V6-54);
* no secret is handed to a program on its command line (V6-55);
* every container is read-only, holds no capability it does not use, gains
  no privilege and has a ceiling on memory and processes (V6-34, 6.3);
* no password or token is in any service's environment, only the file
  beside it (V6-38, 6.3);
* a database's socket -- its superuser, over it -- is its own and the
  dbprep service's (V6-41, 6.3);
* no health check skips verifying the certificate it is answered with
  (V6-37, 6.3).

Offline: the files are read, not run. The compose tests under
`tests/docker` check the same file through `docker compose config`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
COMPOSE = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
SERVICES = COMPOSE["services"]

#: Every Postgres the stack publishes a port for.
STORES = ["postgres", "vectordb", "chunkdb", "stores"]

#: Every server that presents a TLS certificate, and the pki identity it is.
TLS_SERVERS = {
    "api": "api",
    "review": "review",
    "console": "console",
    "auth": "auth",
    "gui": "gui",
    "reviewgui": "reviewgui",
    "curategui": "curategui",
    "consolegui": "consolegui",
    "directorygui": "directorygui",
    "mlflowproxy": "mlflowproxy",
}


def _tls_volume(service: dict) -> str | None:
    for mount in service.get("volumes", []):
        source, _, rest = mount.partition(":")
        if rest.split(":")[0] == "/etc/nl2sql/tls":
            return source
    return None


# --- the stores ------------------------------------------------------------------


@pytest.mark.parametrize("name", STORES)
def test_every_store_is_this_machines_unless_someone_says_otherwise(name: str):
    [port] = SERVICES[name]["ports"]
    assert port.startswith("${DB_BIND_ADDRESS:-127.0.0.1}:"), port


def test_mlflows_store_is_not_published_at_all():
    assert "ports" not in SERVICES["mlflowdb"]


# --- the dataset image --------------------------------------------------------------


def test_the_dataset_image_bakes_in_no_password():
    dockerfile = (REPO_ROOT / "docker" / "Dockerfile").read_text()
    init_db = (REPO_ROOT / "docker" / "init_db.sh").read_text()
    assert not re.search(r"^ARG \w*PASSWORD", dockerfile, re.MULTILINE)
    assert "PASSWORD '" not in init_db and "ALTER ROLE postgres" not in init_db
    assert "hostssl all all all scram-sha-256" in init_db


def test_its_start_refuses_clear_text_and_the_superuser_over_the_network():
    entrypoint = (REPO_ROOT / "docker" / "entrypoint.sh").read_text()
    assert 'echo "hostnossl all all all reject"' in entrypoint
    assert 'echo "host all postgres all reject"' in entrypoint
    assert "ALTER ROLE postgres PASSWORD NULL;" in entrypoint
    assert '-c ssl=on -c "ssl_cert_file=$cert"' in entrypoint


def test_sign_ins_rules_are_tls_only():
    from nl2sql_ops.retail import signin_lines

    for tls in ("starttls", "ldaps", "none"):
        lines = signin_lines(reader="r", rolesync="s", database="d", host="h", port=389, tls=tls, base_dn="dc=x")
        assert {line.split()[0] for line in lines} == {"hostssl"}, tls


def test_the_auth_service_verifies_the_database_it_hands_passwords_to():
    from nl2sql_auth.settings import AuthSettings

    settings = AuthSettings()
    assert settings.db_sslmode == "verify-full"
    assert settings.db_ssl["sslrootcert"] == "/etc/nl2sql/pg-tls/server.crt"
    mounts = SERVICES["auth"]["volumes"]
    assert "pgtls:/etc/nl2sql/pg-tls:ro" in mounts


# --- TLS identities -----------------------------------------------------------------


@pytest.mark.parametrize(("name", "identity"), sorted(TLS_SERVERS.items()))
def test_every_tls_server_presents_its_own_identity(name: str, identity: str):
    volume = _tls_volume(SERVICES[name])
    assert volume == f"{identity}tls"
    assert f"{volume}:/etc/nl2sql/identities/{identity}" in SERVICES["pki"]["volumes"]
    assert any(arg.startswith(f"{identity}=/etc/nl2sql/identities/{identity}=") for arg in SERVICES["pki"]["command"])


def test_no_two_services_share_a_tls_identity():
    volumes = [_tls_volume(service) for service in SERVICES.values()]
    named = [volume for volume in volumes if volume]
    assert len(named) == len(set(named))


def test_the_cas_key_is_mounted_by_the_pki_service_alone():
    holders = [name for name, service in SERVICES.items() if any(m.startswith("pkica:") for m in service.get("volumes", []))]
    assert holders == ["pki"]


def test_a_client_that_only_verifies_is_given_only_the_cas_certificate():
    assert _tls_volume(SERVICES["apitest"]) == "tlstrust"


# --- sign-in by default ---------------------------------------------------------------


def test_sign_in_is_on_by_default_in_every_services_code(monkeypatch):
    from nl2sql_agent.api.settings import ApiSettings
    from nl2sql_agent.console.settings import ConsoleSettings
    from nl2sql_identity import GuardSettings
    from nl2sql_review.settings import ReviewSettings

    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    assert ApiSettings.from_env().auth_enabled
    assert ConsoleSettings.from_env().auth_enabled
    assert ReviewSettings.from_env().auth_enabled
    assert GuardSettings.from_env(token_variable="X", service_roles=()).enabled


def test_every_page_is_signed_in_unless_switched_off_by_name():
    """Every page's start-up, one script since 6.3 (V6-37): on unless a
    value it knows says off."""
    source = (REPO_ROOT / "proxy" / "10-nl2sql-proxy.envsh").read_text()
    assert 'off "${AUTH_ENABLED:-true}"' in source
    assert "AUTH_ENABLED:-false" not in source


# --- secrets ----------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["launch.sh", "setup.sh", "start.sh"])
def test_no_password_is_passed_to_psql_on_its_command_line(path: str):
    """V6-55: `psql -v name=$secret` is in `ps` for as long as it runs, on
    the host and in the container. Secrets go through the environment
    (`docker compose exec -e NAME`, read in SQL with `\\getenv`)."""
    source = (REPO_ROOT / path).read_text()
    assert not re.search(r"-v \w*password=", source), "a password as a psql -v argument"
    assert not re.search(r"-e \w*PASSWORD=", source), "a password as a docker -e NAME=value argument"


# --- every container (V6-34) ---------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_every_container_is_read_only_capped_and_gains_no_privilege(name: str):
    service = SERVICES[name]
    assert service.get("read_only") is True, name
    assert service.get("cap_drop") == ["ALL"], name
    assert "no-new-privileges:true" in service.get("security_opt", []), name
    assert service.get("mem_limit") and service.get("pids_limit"), name


#: What a service that starts as root to hand its volumes over and drop may
#: be given back, and nothing more; everything else holds none.
DROPS_ROOT = {"CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"}


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_a_capability_is_given_back_only_to_switch_users_or_hand_a_volume_over(name: str):
    assert set(SERVICES[name].get("cap_add", [])) <= DROPS_ROOT, name


# --- secrets (V6-38) -----------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_no_password_or_token_is_in_a_services_environment(name: str):
    """Only the file it is mounted at: `docker inspect` shows a container's
    environment to anyone who can run it."""
    environment = SERVICES[name].get("environment", {})
    for key, value in environment.items():
        if key.endswith(("PASSWORD", "TOKEN")):
            raise AssertionError(f"{name}: {key} is in its environment; {key}_FILE is what it reads")
        text = str(value)
        assert not re.search(r"\$\{\w*(PASSWORD|_TOKEN)\b", text), f"{name}: {key} interpolates a secret"
        if key.endswith("_FILE") and ("PASSWORD" in key or "TOKEN" in key):
            assert text.startswith("/run/secrets/"), f"{name}: {key}"


def test_every_secret_file_compose_mounts_is_one_setup_writes():
    setup = (REPO_ROOT / "setup.sh").read_text()
    for name, secret in COMPOSE["secrets"].items():
        assert secret == {"file": f"./secrets/{name}"}, name
        assert name in setup, f"setup.sh does not write secrets/{name}"


# --- the databases' sockets (V6-41) --------------------------------------------------------

SOCKETS = {"pgsocket": "postgres", "storessocket": "stores", "contextsocket": "chunkdb",
           "vectorsocket": "vectordb", "mlflowsocket": "mlflowdb"}


@pytest.mark.parametrize(("socket", "database"), sorted(SOCKETS.items()))
def test_a_databases_socket_is_its_own_and_the_one_shots(socket: str, database: str):
    """Over its own socket a Postgres container trusts whoever connects,
    superuser included: holding the volume is holding the database."""
    holders = sorted(name for name, service in SERVICES.items()
                     if any(str(mount).startswith(f"{socket}:") for mount in service.get("volumes", [])))
    allowed = {database, "dbprep"} | ({"storesmigrate"} if socket == "storessocket" else set())
    assert set(holders) == allowed, holders


# --- health checks that verify (V6-37) -------------------------------------------------------


def test_no_health_check_skips_verifying_the_certificate():
    """Twelve did until 6.3: a service presenting an expired certificate, or
    another's, answered `healthy`."""
    tracked = [REPO_ROOT / "docker-compose.yml", *REPO_ROOT.glob("**/Dockerfile")]
    for path in tracked:
        if "node_modules" in path.parts:
            continue
        text = path.read_text()
        assert "_create_unverified_context" not in text, path
        assert "--no-check-certificate" not in text, path
