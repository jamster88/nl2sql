"""The snippet store and the curation interface, as compose resolves docker-compose.yml.

Behind `--run-docker`, like the other compose tests. The properties no single
file shows:

* **Two roles into the snippet store.** The agent reads it as the role the
  loader creates, which can SELECT and nothing else; only the review service,
  which loads it, holds the owner. The two halves -- the agent's URL and the
  review service's reader name and password -- are two services that have to
  agree.
* **The curation page holds nothing of the review service's but its token.**
  It proxies the review service, verifying a name the review service's
  certificate covers, holding the review token so the browser never does.

Since 6.3 the snippet store is a database in the runtime stores' server
(V6-40), every password a secret file (V6-38), and the curation page the
proxy image's `curate` page (V6-37).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.review.test_review_compose import PROFILES, _compose_config
from tests.settings_names import proxy_names

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


def test_the_snippet_store_is_a_pgvector_database_in_the_runtime_stores(config: dict, services: dict):
    """Started with the other stores, since the agent reads it; pgvector,
    made in it by dbprep, which the owner cannot do (V6-41)."""
    from nl2sql_ops.settings import OpsSettings

    store = services["stores"]
    assert store["image"].startswith("pgvector/pgvector:")
    assert "profiles" not in store, "the agent reads it, so it starts with the agent"
    assert store["container_name"] == "nl2sql-stores"
    [snippets] = [s for s in OpsSettings.from_env().stores if s.key == "snippets"]
    assert (snippets.database, snippets.role, snippets.vectors, snippets.creates_roles) == (
        "nl2sql_snippets", "snippets", True, True,
    )
    assert {m["target"]: m["source"] for m in store["volumes"]}["/var/lib/postgresql/data"] == "storesdata"
    assert "pg_isready" in " ".join(store["healthcheck"]["test"])
    assert store["restart"] == "unless-stopped"


def test_the_stores_have_a_port_of_their_own(services: dict):
    published = {name: {p["published"] for p in spec.get("ports", [])} for name, spec in services.items()}
    assert published["stores"] == {"5435"}
    for name, ports in published.items():
        if name != "stores":
            assert "5435" not in ports, name


def test_the_stores_own_settings_reach_the_store_its_owner_and_its_reader(tmp_path_factory):
    """Every name compose reads for the snippets, set at once: the role and
    database dbprep creates are the ones the review service loads it as and
    the agent reads from, and the port and image move with them."""
    config = _compose_config(
        tmp_path_factory.mktemp("store"), *EVERY,
        env={
            "SNIPPETS_DB_USER": "owner", "SNIPPETS_DB_NAME": "snips", "SNIPPETS_READER_USER": "ro",
            "STORES_DB_PORT": "6435", "STORES_IMAGE": "example/pgvector:test",
        },
    )
    store, review, agent, dbprep = (config["services"][name] for name in ("stores", "review", "agent", "dbprep"))
    assert [p["published"] for p in store["ports"]] == ["6435"]
    assert store["image"] == "example/pgvector:test"
    assert (dbprep["environment"]["SNIPPETS_DB_USER"], dbprep["environment"]["SNIPPETS_DB_NAME"]) == ("owner", "snips")
    assert review["environment"]["SNIPPETS_DB_URL"] == "postgresql://owner@nl2sql-stores:5432/snips"
    assert review["environment"]["SNIPPETS_READER_USER"] == "ro"
    assert agent["environment"]["SNIPPET_DB_URL"] == "postgresql+psycopg://ro@nl2sql-stores:5432/snips"


@pytest.mark.parametrize("service", ["agent", "api", "review"])
def test_everything_that_reads_or_loads_it_waits_for_it(services: dict, service: str):
    """Up, and prepared: its database and owner made by dbprep."""
    assert services[service]["depends_on"]["stores"]["condition"] == "service_healthy"
    assert services[service]["depends_on"]["dbprep"]["condition"] == "service_completed_successfully"


@pytest.mark.parametrize("service", ["agent", "api"])
def test_the_agent_reads_it_as_the_role_the_loader_creates(services: dict, service: str):
    env = services[service]["environment"]
    assert env["SNIPPET_DB_URL"] == "postgresql+psycopg://snippets_reader@nl2sql-stores:5432/nl2sql_snippets"
    assert env["SNIPPET_DB_PASSWORD_FILE"] == "/run/secrets/snippets_reader_password"
    assert env["SNIPPETS_ENABLED"] == "true"
    review = services["review"]["environment"]
    assert review["SNIPPETS_READER_USER"] == "snippets_reader"


def test_a_reader_password_is_one_file_on_both_sides(services: dict):
    """The loader sets the reader's password from the file the agent reads
    it from, so the two cannot disagree."""
    assert services["agent"]["environment"]["SNIPPET_DB_PASSWORD_FILE"] == (
        services["review"]["environment"]["SNIPPETS_READER_PASSWORD_FILE"]
    )


def test_only_the_review_service_and_dbprep_hold_the_store_owner(services: dict):
    """The review service loads the store as its owner; dbprep makes it and
    sets the owner's password. Nothing else is handed the file."""
    holding = {name for name, spec in services.items()
               if "snippets_db_password" in json.dumps(spec.get("secrets", []))}
    assert holding == {"review", "dbprep"}


def test_the_review_service_writes_the_snippet_document_in_the_checkout(services: dict):
    review = services["review"]
    mounts = {m["target"]: m for m in review["volumes"]}
    assert mounts["/app/context_questions"]["source"].endswith("context_questions")
    assert not mounts["/app/context_questions"].get("read_only", False)
    assert review["environment"]["REVIEW_SNIPPETS_DOCUMENT"] == "/app/context_questions/sql_snippets.md"
    assert review["environment"]["SNIPPETS_DB_URL"] == "postgresql://snippets@nl2sql-stores:5432/nl2sql_snippets"
    assert review["environment"]["SNIPPETS_DB_PASSWORD_FILE"] == "/run/secrets/snippets_db_password"


# ---------------------------------------------------------------------------
# The curation interface
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def curategui(services: dict) -> dict:
    return services["curategui"]


def test_it_is_not_started_unless_it_is_asked_for(tmp_path_factory):
    config = _compose_config(tmp_path_factory.mktemp("plain"), *PROFILES)
    assert "curategui" not in config["services"]


def test_it_is_its_own_page_in_its_own_profile_on_its_own_port(curategui: dict, services: dict):
    """The proxy image's `curate` page: the review page's image, its own page."""
    assert curategui["profiles"] == ["curategui"]
    assert curategui["build"]["dockerfile"] == "proxy/Dockerfile"
    assert curategui["image"] == services["reviewgui"]["image"]
    assert curategui["environment"]["NL2SQL_PAGE"] == "curate"
    assert [p["published"] for p in curategui["ports"]] == ["8083"]
    assert curategui["restart"] == "unless-stopped"


def test_it_waits_for_and_verifies_the_review_service(curategui: dict, services: dict):
    assert curategui["depends_on"]["review"]["condition"] == "service_healthy"
    env = curategui["environment"]
    assert env["UPSTREAM"] == "https://nl2sql-review:8444"
    assert env["UPSTREAM_SSL_NAME"] in _issued({"services": services}, "review")
    assert env["UPSTREAM_CACERT"] == "/etc/nl2sql/tls/ca.crt"
    assert [(m["source"], m["target"], m.get("read_only")) for m in curategui["volumes"]] == [
        ("curateguitls", "/etc/nl2sql/tls", True)
    ]


def test_the_review_token_is_held_by_the_proxy(curategui: dict, services: dict):
    """The review service's own token file, mounted (V6-38)."""
    assert curategui["environment"]["UPSTREAM_TOKEN_FILE"] == services["review"]["environment"]["REVIEW_TOKEN_FILE"]
    assert [secret["source"] for secret in curategui["secrets"]] == ["review_token"]


def test_the_proxy_outlasts_a_save(curategui: dict):
    from nl2sql_review.settings import ReviewSettings

    timeout = curategui["environment"]["UPSTREAM_READ_TIMEOUT"]
    assert int(timeout.rstrip("s")) >= ReviewSettings().reload_timeout_seconds


def _proxy_variables() -> set[str]:
    return proxy_names("curate")


def test_a_pinned_proxy_image_is_what_the_page_runs(tmp_path_factory):
    """Its settings by the names `.env` gives them are tests/docker/
    test_compose_config.py's, for every page at once."""
    config = _compose_config(
        tmp_path_factory.mktemp("page"), *EVERY,
        env={"PROXY_IMAGE_NAME": "example/proxy", "PROXY_IMAGE_TAG": "test"},
    )
    assert config["services"]["curategui"]["image"] == "example/proxy:test"


def test_every_setting_the_proxy_reads_can_be_set_through_compose_and_nothing_else_is(curategui: dict):
    assert sorted(_proxy_variables() - set(curategui["environment"])) == []
    assert sorted(set(curategui["environment"]) - _proxy_variables()) == []


def _issued(config: dict, identity: str) -> list[str]:
    """The names the pki service issues `identity`'s certificate for."""
    [spec] = [arg for arg in config["services"]["pki"]["command"] if arg.startswith(f"{identity}=")]
    return spec.split("=", 2)[2].split(",")
