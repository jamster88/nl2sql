"""The feedback stack as compose resolves it.

Behind `--run-docker` for the same reason as the other compose tests:
`docker compose config` only parses and resolves, but it needs a working
`docker` CLI.

The properties that carry weight here are the ones no single file shows:

* **The review service presents a certificate of its own.** That is two
  settings in two services -- the pki service's `REVIEW_TLS_HOSTNAMES` has to
  cover `nl2sql-review`, and the review GUI's `proxy_ssl_name` has to be a
  name that certificate covers.
  Nothing but a test connects them.
* **The golden question document is bind-mounted writable from the checkout.**
  If it were not, a promotion would write a copy inside a container and the
  golden set would grow somewhere nobody can see.
* **The API and the review service reach the staging database as different
  roles.** The whole security story is that one of them is INSERT-only, and
  it is one compose line away from not being.

Since 6.3 the staging database and the two fix stores are databases in one
server, `stores`, beside the snippets (V6-40), and the review page is the
proxy image's `review` page (V6-37).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from tests.settings_names import proxy_names, read_names, settable_names

pytestmark = pytest.mark.docker

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

PROFILES = ("api", "gui", "review", "reviewgui")


def _compose_config(tmp_path: Path, *profiles: str, env: dict | None = None) -> dict:
    empty_env_file = tmp_path / "empty.env"
    empty_env_file.write_text("")
    cmd = ["docker", "compose", "--env-file", str(empty_env_file)]
    for profile in (profiles or PROFILES):
        cmd += ["--profile", profile]
    cmd += ["config", "--format", "json"]

    # The host's own environment would otherwise substitute into the file and
    # the test would be reading this machine's .env rather than the defaults.
    substitutable = set(
        re.findall(r"\$\{([A-Z_][A-Z0-9_]*)", (REPO_ROOT / "docker-compose.yml").read_text())
    )
    run_env = {k: v for k, v in os.environ.items() if k not in substitutable}
    if env:
        run_env.update(env)
    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=60, env=run_env)
    if result.returncode != 0:
        pytest.fail(f"`docker compose config` failed:\n{result.stderr}")
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def config(tmp_path_factory) -> dict:
    return _compose_config(tmp_path_factory.mktemp("compose"))


@pytest.fixture(scope="module")
def stores(config: dict) -> dict:
    return config["services"]["stores"]


@pytest.fixture(scope="module")
def review(config: dict) -> dict:
    return config["services"]["review"]


@pytest.fixture(scope="module")
def reviewgui(config: dict) -> dict:
    return config["services"]["reviewgui"]


@pytest.fixture(scope="module")
def api(config: dict) -> dict:
    return config["services"]["api"]


# ---------------------------------------------------------------------------
# None of it exists unless it is asked for
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("service", ["review", "reviewgui"])
def test_nothing_is_started_unless_it_is_asked_for(tmp_path_factory, service: str):
    """Most people ask questions from a terminal and never review anything.

    A port nobody asked to be opened should not be -- and this stack opens
    the one that can rewrite the golden question set.
    """
    tmp_path = tmp_path_factory.mktemp("noprofile")
    empty = tmp_path / "empty.env"
    empty.write_text("")
    result = subprocess.run(
        ["docker", "compose", "--env-file", str(empty), "config", "--services"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
    )
    assert service not in result.stdout.split()


@pytest.mark.parametrize("service,profile", [("review", "review"), ("reviewgui", "reviewgui")])
def test_each_service_is_in_the_profile_it_is_named_for(config: dict, service: str, profile: str):
    assert config["services"][service]["profiles"] == [profile]


# ---------------------------------------------------------------------------
# The staging database: one of the runtime stores (6.3)
# ---------------------------------------------------------------------------


def test_the_runtime_stores_start_with_the_databases(stores: dict):
    """No profile: since 6.3 they are one server, and a verdict is staged
    whenever the API is up rather than only when --feedback was asked for."""
    assert "profiles" not in stores


def test_the_staging_database_keeps_its_data_in_the_stores_volume(stores: dict, config: dict):
    """The one volume here holding data people typed rather than data a
    build produced, which is what makes it the one worth backing up."""
    targets = {mount["target"]: mount for mount in stores["volumes"]}
    assert targets["/var/lib/postgresql/data"]["source"] == "storesdata"
    assert "storesdata" in config["volumes"]


def test_the_stores_are_not_one_of_the_shipped_images(stores: dict):
    """A stock pgvector on purpose, pinned by digest (V6-35): the schemas and
    the roles are made by dbprep and the review service on every start, so
    there is nothing to bake into an image."""
    assert stores["image"].startswith("pgvector/pgvector:pg18@sha256:")


def test_the_stores_are_health_checked(stores: dict):
    assert "pg_isready" in " ".join(stores["healthcheck"]["test"])


def test_the_staging_database_is_not_the_retail_one(stores: dict, config: dict):
    """Writing curation data into the database under test would pollute the
    thing being measured."""
    retail = config["services"]["postgres"]
    assert stores["container_name"] != retail["container_name"]
    assert stores["image"] != retail["image"]


def test_the_stores_are_on_a_port_of_their_own(config: dict):
    ports = {
        service: {p["published"] for p in spec.get("ports", [])}
        for service, spec in config["services"].items()
        if spec.get("ports")
    }
    assert ports["stores"] == {"5435"}
    for service, published in ports.items():
        if service != "stores":
            assert not (ports["stores"] & published), f"the stores collide with {service}"


# ---------------------------------------------------------------------------
# Who reaches it, and as whom
# ---------------------------------------------------------------------------


def test_the_api_reaches_the_staging_database_as_the_insert_only_role(tmp_path_factory):
    """The whole security story is that the public process holds a role that
    cannot read a submission back. It is one compose line away from not."""
    from nl2sql_review.store import WRITER_ROLE

    config = _compose_config(tmp_path_factory.mktemp("apifeedback"))
    env = config["services"]["api"]["environment"]
    assert env["API_FEEDBACK_DB_URL"] == f"postgresql://{WRITER_ROLE}@nl2sql-stores:5432/nl2sql_feedback"
    assert env["API_FEEDBACK_DB_PASSWORD_FILE"] == "/run/secrets/feedback_writer_password"


def test_the_api_works_without_a_staging_database(tmp_path_factory):
    """Set empty, it has to stay a working configuration: the feedback routes
    answer 503 with a reason and everything else runs. Empty is kept as
    empty (`${API_FEEDBACK_DB_URL-...}`), so it is a way to turn it off."""
    config = _compose_config(tmp_path_factory.mktemp("nofeedback"), env={"API_FEEDBACK_DB_URL": ""})
    assert config["services"]["api"]["environment"]["API_FEEDBACK_DB_URL"] == ""


def test_the_review_service_reaches_it_as_the_owner(review: dict):
    """It creates the schema and re-fences the writer role on every start,
    so it cannot be the writer."""
    from nl2sql_review.store import WRITER_ROLE

    url = review["environment"]["FEEDBACK_DB_URL"]
    assert WRITER_ROLE not in url
    assert url == "postgresql://feedback@nl2sql-stores:5432/nl2sql_feedback"
    assert review["environment"]["FEEDBACK_DB_PASSWORD_FILE"] == "/run/secrets/feedback_db_password"


def test_the_review_service_waits_for_the_database(review: dict):
    """Up, and prepared: the owner made, its password set (V6-41)."""
    assert review["depends_on"]["stores"]["condition"] == "service_healthy"
    assert review["depends_on"]["dbprep"]["condition"] == "service_completed_successfully"


def test_the_review_service_can_reach_the_stores_it_reloads(review: dict):
    """Promotion runs the two RAG loaders, which write the context store and
    the vector store. Without these it would write the document and report a
    reload failure on every promotion."""
    assert "nl2sql-chunkdb" in review["environment"]["CHUNK_DB_URL"]
    assert "nl2sql-vectordb" in review["environment"]["VECTOR_DB_URL"]
    assert review["depends_on"]["chunkdb"]["condition"] == "service_healthy"
    assert review["depends_on"]["vectordb"]["condition"] == "service_healthy"


@pytest.mark.parametrize("env", [{}, {"EMBED_BASE_URL": "http://gpu-box:11434", "EMBED_MODEL": "other-embedder"}])
def test_the_review_service_embeds_where_and_with_what_the_agent_does(tmp_path_factory, env: dict):
    """A promotion, and `launch.sh --load-golden`, embed the golden pairs the
    agent's questions are compared against. Until 5.5.1 the host was read from
    an `OLLAMA_URL` nothing wrote, so `setup.sh --embed-url` moved the agent's
    embedding host and left the review service's behind."""
    services = _compose_config(tmp_path_factory.mktemp("embed"), "agent", "review", env=env)["services"]
    review, agent = services["review"]["environment"], services["agent"]["environment"]
    assert (review["OLLAMA_URL"], review["EMBED_MODEL"]) == (agent["EMBED_BASE_URL"], agent["EMBED_MODEL"])


# ---------------------------------------------------------------------------
# The golden question document
# ---------------------------------------------------------------------------


def test_the_question_document_is_bind_mounted_from_the_checkout(review: dict):
    """The one writable bind mount in the project, and the point of the
    service. A promotion has to change the file in *this* checkout so it can
    be read, reviewed and committed like any other edit; written into a
    container's own copy it would vanish with the container.
    """
    mounts = {mount["target"]: mount for mount in review["volumes"]}
    document = mounts["/app/context_questions"]
    assert document["type"] == "bind"
    assert document["source"].endswith("/context_questions")
    assert document.get("read_only", False) is False


def test_the_service_is_pointed_at_the_mounted_document(review: dict):
    """A REVIEW_DOCUMENT outside the bind mount would write a promotion into
    the container's own filesystem, where nobody would ever see it."""
    assert review["environment"]["REVIEW_DOCUMENT"].startswith("/app/context_questions/")


def test_the_loaders_are_where_the_service_looks_for_them(review: dict):
    assert review["environment"]["REVIEW_RAG_DIR"] == "/app/rag"


# ---------------------------------------------------------------------------
# TLS: one certificate, two services
# ---------------------------------------------------------------------------


def test_the_review_service_is_issued_its_own_certificate(config: dict):
    """V6-36: its own key and certificate, from the pki service, rather than
    the API's. The name its page's proxy verifies is one the pki issues it."""
    assert "nl2sql-review" in _issued(config, "review")
    assert "nl2sql-review" not in config["services"]["api"]["environment"]["API_TLS_HOSTNAMES"].split(",")


def test_the_review_gui_verifies_a_name_that_certificate_covers(reviewgui: dict, config: dict):
    assert reviewgui["environment"]["UPSTREAM_SSL_NAME"] in _issued(config, "review")


def test_the_review_gui_proxies_the_name_it_verifies(reviewgui: dict):
    upstream = reviewgui["environment"]["UPSTREAM"]
    assert reviewgui["environment"]["UPSTREAM_SSL_NAME"] in upstream


@pytest.mark.parametrize(("service", "volume"), [("review", "reviewtls"), ("reviewgui", "reviewguitls")])
def test_each_has_its_own_certificate_mounted_read_only(config: dict, service: str, volume: str):
    """V6-36: each its own key, which only the pki service writes."""
    mounts = {mount["target"]: mount for mount in config["services"][service]["volumes"]}
    assert mounts["/etc/nl2sql/tls"]["source"] == volume
    assert mounts["/etc/nl2sql/tls"]["read_only"] is True


# ---------------------------------------------------------------------------
# The review interface
# ---------------------------------------------------------------------------


def test_the_review_gui_waits_for_the_service(reviewgui: dict):
    assert reviewgui["depends_on"]["review"]["condition"] == "service_healthy"


def test_the_review_token_never_reaches_the_browser(reviewgui: dict, review: dict):
    """It is held by nginx in the GUI container and by the service itself.

    This token can rewrite the golden question set; a browser that held it
    would be holding it in a bookmark, a screenshot and a support ticket.
    Since 6.3 both hold the one secret file, and neither has it in its
    environment (V6-38).
    """
    assert reviewgui["environment"]["UPSTREAM_TOKEN_FILE"] == review["environment"]["REVIEW_TOKEN_FILE"]
    assert review["environment"]["REVIEW_TOKEN_FILE"] == "/run/secrets/review_token"
    assert "REVIEW_TOKEN" not in reviewgui["environment"] and "REVIEW_TOKEN" not in review["environment"]


def test_the_two_interfaces_do_not_share_a_port(config: dict):
    gui_ports = {p["published"] for p in config["services"]["gui"]["ports"]}
    review_ports = {p["published"] for p in config["services"]["reviewgui"]["ports"]}
    assert not (gui_ports & review_ports)


def test_the_two_interfaces_are_two_pages_of_one_image(config: dict):
    """Separate npm projects, still: two Vite entry points in one project
    would share a build. Since 6.3 both bundles are in one image (V6-37),
    and each container serves only the page NL2SQL_PAGE names, from that
    page's own root -- the public GUI's container answers nothing with the
    review interface's files (tests/docker/test_gui_container.py)."""
    gui, reviewgui = config["services"]["gui"], config["services"]["reviewgui"]
    assert gui["image"] == reviewgui["image"]
    assert (gui["environment"]["NL2SQL_PAGE"], reviewgui["environment"]["NL2SQL_PAGE"]) == ("gui", "review")


def test_the_review_proxy_outlasts_a_promotion(reviewgui: dict, review: dict):
    """Promotion runs both RAG loaders. A proxy that gives up first cuts off
    a write that is still happening."""
    timeout = reviewgui["environment"]["UPSTREAM_READ_TIMEOUT"]
    assert timeout.endswith("s")
    from nl2sql_review.settings import ReviewSettings

    assert int(timeout.rstrip("s")) >= ReviewSettings().reload_timeout_seconds


# ---------------------------------------------------------------------------
# Nothing set that is not read, nothing read that cannot be set
#
# The check the agent, the API and the GUI's proxy already have. A setting
# the service reads that compose never passes can only be changed by
# rebuilding the image; one compose passes that nothing reads is a knob that
# silently does nothing.
# ---------------------------------------------------------------------------

REVIEW_DIR = REPO_ROOT / "review"


def _review_source() -> str:
    source = (REVIEW_DIR / "nl2sql_review" / "settings.py").read_text()
    assert "REVIEW_TOKEN" in read_names(source), "no environment variables found in settings.py -- the regex needs updating"
    return source


def test_every_setting_the_review_service_reads_can_be_set_through_compose(review: dict):
    missing = sorted(settable_names(_review_source()) - set(review["environment"]))
    assert missing == [], f"the review service reads these, but compose never passes them: {missing}"


def test_every_variable_compose_sets_on_the_review_service_is_one_it_reads(review: dict):
    unread = sorted(set(review["environment"]) - read_names(_review_source()))
    assert unread == [], f"compose sets {unread} on the review service, which nothing in it reads"


def _review_proxy_variables() -> set[str]:
    """Every setting the proxy image reads for the review page."""
    return proxy_names("review")


def test_every_setting_the_review_proxy_reads_can_be_set_through_compose(reviewgui: dict):
    unsettable = sorted(_review_proxy_variables() - set(reviewgui["environment"]))
    assert unsettable == [], (
        f"the review proxy reads {unsettable}, which compose never passes, so "
        "they cannot be changed without rebuilding the image"
    )


def test_every_variable_compose_sets_is_one_the_review_proxy_reads(reviewgui: dict):
    unread = sorted(set(reviewgui["environment"]) - _review_proxy_variables())
    assert unread == [], f"compose sets {unread} on the review GUI, which nothing in it reads"


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def test_the_review_service_is_health_checked_on_the_scheme_it_serves(review: dict):
    """The check follows REVIEW_TLS_ENABLED rather than assuming https, so
    turning TLS off does not make a working container look unhealthy; and on
    https it verifies the certificate it is answered with (6.3, V6-37)."""
    assert review["healthcheck"]["test"] == ["CMD", "python", "-m", "nl2sql_common.health", "REVIEW"]
    health = (REPO_ROOT / "common" / "nl2sql_common" / "health.py").read_text()
    assert '_TLS_ENABLED"' in health and "/healthz" in health and '"REVIEW": 8444' in health


def test_every_new_service_restarts_unless_stopped(config: dict):
    for name in ("stores", "review", "reviewgui"):
        assert config["services"][name]["restart"] == "unless-stopped"


# ---------------------------------------------------------------------------
# The corrections and completions stores (5.1), databases in the stores (6.3)
# ---------------------------------------------------------------------------

FIX_STORES = {
    "corrections": ("nl2sql_corrections", "CORRECTIONS_DB_URL"),
    "completions": ("nl2sql_completions", "COMPLETIONS_DB_URL"),
}


@pytest.mark.parametrize("store", sorted(FIX_STORES))
def test_each_fix_store_is_a_pgvector_database_of_its_own(review: dict, store: str):
    """Records and RAG side by side, so the database has to have pgvector,
    which dbprep makes in it; and data a person typed, kept in the stores'
    volume with its own owner."""
    from nl2sql_ops.settings import OpsSettings

    database, variable = FIX_STORES[store]
    [prepared] = [s for s in OpsSettings.from_env().stores if s.key == store]
    assert (prepared.database, prepared.role, prepared.vectors) == (database, store, True)
    assert review["environment"][variable] == f"postgresql://{store}@nl2sql-stores:5432/{database}"


def test_the_fix_stores_are_apart_from_the_golden_set_and_from_each_other(review: dict):
    """The golden set is what the agent is measured against; a record of its
    mistakes is not a benchmark answer. And a wrong join and a missing label
    are different lessons, kept apart for whatever reads them next: a
    database and an owner each, and none of them the golden set's server."""
    urls = {variable: review["environment"][variable] for _, variable in FIX_STORES.values()}
    assert len(set(urls.values())) == 2
    for url in urls.values():
        assert "nl2sql-chunkdb" not in url and "nl2sql-vectordb" not in url
    assert review["environment"]["CORRECTIONS_DB_PASSWORD_FILE"] != review["environment"]["COMPLETIONS_DB_PASSWORD_FILE"]


@pytest.mark.parametrize("store", sorted(FIX_STORES))
def test_the_review_service_waits_for_and_reaches_each_store(review: dict, store: str):
    database, variable = FIX_STORES[store]
    assert review["depends_on"]["stores"]["condition"] == "service_healthy"
    assert f"@nl2sql-stores:5432/{database}" in review["environment"][variable]


def test_only_the_review_service_is_given_a_way_into_the_fix_stores(tmp_path_factory):
    """Least access, one level up from the database roles. The agent reads
    as `nl2sql_reader`; the API writes verdicts as an INSERT-only role; and
    neither is handed the stores' names or credentials, so no SQL either of
    them runs can reach a fix. dbprep makes them, and holds their owners'
    passwords to set them. Rendered over every profile -- the CLI agent's and
    the desktop build's included -- because a service in a profile nobody
    tested is still a service somebody starts.
    """
    everything = _compose_config(tmp_path_factory.mktemp("all"), *PROFILES, "agent", "desktop", "migrate")
    assert "agent" in everything["services"]
    markers = ("nl2sql_corrections", "nl2sql_completions", "corrections_db_password", "completions_db_password")
    reaching = {
        name
        for name, spec in everything["services"].items()
        if any(marker in json.dumps([spec.get("environment", {}), spec.get("secrets", [])]) for marker in markers)
    }
    assert reaching == {"review", "dbprep"}


def test_a_fix_is_validated_on_the_retail_database_as_the_reader(review: dict):
    """It runs SQL a person typed, so it runs it as the role that can only read."""
    assert review["depends_on"]["postgres"]["condition"] == "service_healthy"
    url = review["environment"]["RETAIL_DB_URL"]
    assert url == "postgresql://nl2sql_reader@nl2sql-postgres:5432/nl2sql_retail"
    assert review["environment"]["RETAIL_DB_PASSWORD_FILE"] == "/run/secrets/postgres_reader_password"
    for knob in ("REVIEW_VALIDATE_TIMEOUT_MS", "REVIEW_VALIDATE_MAX_ROWS", "REVIEW_EMBED_FIXES"):
        assert knob in review["environment"]


def _issued(config: dict, identity: str) -> list[str]:
    """The names the pki service issues `identity`'s certificate for."""
    [spec] = [arg for arg in config["services"]["pki"]["command"] if arg.startswith(f"{identity}=")]
    return spec.split("=", 2)[2].split(",")
