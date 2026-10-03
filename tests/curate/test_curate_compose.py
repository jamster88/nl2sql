"""The snippet store and the curation interface, as compose resolves docker-compose.yml.

Behind `--run-docker`, like the other compose tests. The properties no single
file shows:

* **Two roles into the snippet store.** The agent reads it as the role the
  loader creates, which can SELECT and nothing else; only the review service,
  which loads it, holds the owner. The two halves -- the agent's URL and the
  review service's reader name and password -- are two services that have to
  agree.
* **The curation page presents nothing of its own.** It proxies the review
  service, verifying a name the API's certificate covers, holding the review
  token so the browser never does.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.review.test_review_compose import PROFILES, _compose_config

pytestmark = pytest.mark.docker

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
EVERY = (*PROFILES, "curategui", "agent")


@pytest.fixture(scope="module")
def config(tmp_path_factory) -> dict:
    return _compose_config(tmp_path_factory.mktemp("curate"), *EVERY)


@pytest.fixture(scope="module")
def services(config: dict) -> dict:
    return config["services"]


# ---------------------------------------------------------------------------
# The snippet store
# ---------------------------------------------------------------------------


def test_the_snippet_store_is_pgvector_in_its_own_volume_started_with_the_other_stores(config: dict, services: dict):
    store = services["snippetsdb"]
    assert store["image"].startswith("pgvector/pgvector:")
    assert "profiles" not in store, "the agent reads it, so it starts with the agent"
    assert store["container_name"] == "nl2sql-snippetsdb"
    assert store["environment"]["POSTGRES_DB"] == "nl2sql_snippets"
    assert {m["target"]: m["source"] for m in store["volumes"]} == {"/var/lib/postgresql/data": "snippetsdata"}
    assert "snippetsdata" in config["volumes"]
    assert "pg_isready" in " ".join(store["healthcheck"]["test"])
    assert store["restart"] == "unless-stopped"


def test_the_snippet_store_has_a_port_of_its_own(services: dict):
    published = {name: {p["published"] for p in spec.get("ports", [])} for name, spec in services.items()}
    assert published["snippetsdb"] == {"5438"}
    for name, ports in published.items():
        if name != "snippetsdb":
            assert "5438" not in ports, name


@pytest.mark.parametrize("service", ["agent", "api", "review"])
def test_everything_that_reads_or_loads_it_waits_for_it(services: dict, service: str):
    assert services[service]["depends_on"]["snippetsdb"]["condition"] == "service_healthy"


@pytest.mark.parametrize("service", ["agent", "api"])
def test_the_agent_reads_it_as_the_role_the_loader_creates(services: dict, service: str):
    env = services[service]["environment"]
    url = env["SNIPPET_DB_URL"]
    assert url == "postgresql+psycopg://snippets_reader:snippets_reader@snippetsdb:5432/nl2sql_snippets"
    assert env["SNIPPETS_ENABLED"] == "true"
    review = services["review"]["environment"]
    assert (review["SNIPPETS_READER_USER"], review["SNIPPETS_READER_PASSWORD"]) == ("snippets_reader", "snippets_reader")


def test_a_reader_password_set_once_reaches_both_sides(tmp_path_factory):
    config = _compose_config(
        tmp_path_factory.mktemp("reader"), *EVERY, env={"SNIPPETS_READER_PASSWORD": "s3cret"}
    )
    assert ":s3cret@snippetsdb:" in config["services"]["agent"]["environment"]["SNIPPET_DB_URL"]
    assert config["services"]["review"]["environment"]["SNIPPETS_READER_PASSWORD"] == "s3cret"


def test_only_the_review_service_holds_the_store_owner(services: dict):
    owner = "snippets:snippets@nl2sql-snippetsdb"
    holding = {name for name, spec in services.items() if owner in json.dumps(spec.get("environment", {}))}
    assert holding == {"review"}


def test_the_review_service_writes_the_snippet_document_in_the_checkout(services: dict):
    review = services["review"]
    mounts = {m["target"]: m for m in review["volumes"]}
    assert mounts["/app/context_questions"]["source"].endswith("context_questions")
    assert not mounts["/app/context_questions"].get("read_only", False)
    assert review["environment"]["REVIEW_SNIPPETS_DOCUMENT"] == "/app/context_questions/sql_snippets.md"
    assert review["environment"]["SNIPPETS_DB_URL"] == (
        "postgresql://snippets:snippets@nl2sql-snippetsdb:5432/nl2sql_snippets"
    )


# ---------------------------------------------------------------------------
# The curation interface
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def curategui(services: dict) -> dict:
    return services["curategui"]


def test_it_is_not_started_unless_it_is_asked_for(tmp_path_factory):
    config = _compose_config(tmp_path_factory.mktemp("plain"), *PROFILES)
    assert "curategui" not in config["services"]


def test_it_is_its_own_image_in_its_own_profile_on_its_own_port(curategui: dict, services: dict):
    assert curategui["profiles"] == ["curategui"]
    assert curategui["build"]["dockerfile"] == "curate/Dockerfile"
    assert curategui["image"] != services["reviewgui"]["image"]
    assert [p["published"] for p in curategui["ports"]] == ["8083"]
    assert curategui["restart"] == "unless-stopped"


def test_it_waits_for_and_verifies_the_review_service(curategui: dict, services: dict):
    assert curategui["depends_on"]["review"]["condition"] == "service_healthy"
    env = curategui["environment"]
    assert env["CURATE_UPSTREAM"] == "https://nl2sql-review:8444"
    assert env["CURATE_SSL_NAME"] in services["api"]["environment"]["API_TLS_HOSTNAMES"].split(",")
    assert {m["target"]: m.get("read_only") for m in curategui["volumes"]} == {"/etc/nl2sql/tls": True}


def test_the_review_token_is_held_by_the_proxy(tmp_path_factory):
    config = _compose_config(tmp_path_factory.mktemp("token"), *EVERY, env={"REVIEW_TOKEN": "t0ken"})
    assert config["services"]["curategui"]["environment"]["CURATE_TOKEN"] == "t0ken"
    assert config["services"]["review"]["environment"]["REVIEW_TOKEN"] == "t0ken"


def test_the_proxy_outlasts_a_save(curategui: dict):
    from nl2sql_review.settings import ReviewSettings

    timeout = curategui["environment"]["CURATE_READ_TIMEOUT"]
    assert int(timeout.rstrip("s")) >= ReviewSettings().reload_timeout_seconds


def _proxy_variables() -> set[str]:
    gui = REPO_ROOT / "curate"
    sources = (gui / "nginx.conf.template").read_text() + (gui / "10-nl2sql-curate-config.envsh").read_text()
    return set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)", sources)) - {"CURATE_AUTH_HEADER", "NGINX_CURATE_UPSTREAM_TLS_CONF"}


def test_every_setting_the_proxy_reads_can_be_set_through_compose_and_nothing_else_is(curategui: dict):
    assert sorted(_proxy_variables() - set(curategui["environment"])) == []
    assert sorted(set(curategui["environment"]) - _proxy_variables()) == []
