"""The feedback stack as compose resolves it.

Behind `--run-docker` for the same reason as the other compose tests:
`docker compose config` only parses and resolves, but it needs a working
`docker` CLI.

The properties that carry weight here are the ones no single file shows:

* **The review service presents the API's certificate.** That is two settings
  in two services -- `API_TLS_HOSTNAMES` has to cover `nl2sql-review`, and
  the review GUI's `proxy_ssl_name` has to be a name that certificate covers.
  Nothing but a test connects them.
* **The golden question document is bind-mounted writable from the checkout.**
  If it were not, a promotion would write a copy inside a container and the
  golden set would grow somewhere nobody can see.
* **The API and the review service reach the staging database as different
  roles.** The whole security story is that one of them is INSERT-only, and
  it is one compose line away from not being.
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

PROFILES = ("api", "gui", "feedback", "review", "reviewgui")


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
def feedbackdb(config: dict) -> dict:
    return config["services"]["feedbackdb"]


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


@pytest.mark.parametrize("service", ["feedbackdb", "correctionsdb", "completionsdb", "review", "reviewgui"])
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


@pytest.mark.parametrize(
    "service,profile",
    [
        ("feedbackdb", "feedback"),
        ("correctionsdb", "review"),
        ("completionsdb", "review"),
        ("review", "review"),
        ("reviewgui", "reviewgui"),
    ],
)
def test_each_service_is_in_the_profile_it_is_named_for(config: dict, service: str, profile: str):
    assert config["services"][service]["profiles"] == [profile]


# ---------------------------------------------------------------------------
# The staging database
# ---------------------------------------------------------------------------


def test_the_staging_database_keeps_its_data_in_its_own_volume(feedbackdb: dict, config: dict):
    """The one volume here holding data a person typed rather than data a
    build produced, which is what makes it the one worth backing up."""
    targets = {mount["target"]: mount for mount in feedbackdb["volumes"]}
    assert "/var/lib/postgresql/data" in targets
    assert targets["/var/lib/postgresql/data"]["source"] == "feedbackdata"
    assert "feedbackdata" in config["volumes"]


def test_the_staging_database_is_not_one_of_the_shipped_images(feedbackdb: dict):
    """A stock Postgres on purpose: the schema and the writer role are
    created by the review service on every start, so there is nothing to
    bake into an image."""
    assert feedbackdb["image"].startswith("postgres:")


def test_the_staging_database_is_health_checked(feedbackdb: dict):
    assert "pg_isready" in " ".join(feedbackdb["healthcheck"]["test"])


def test_the_staging_database_is_not_the_retail_one(feedbackdb: dict, config: dict):
    """Writing curation data into the database under test would pollute the
    thing being measured."""
    # The retail service takes its database name from a build ARG rather
    # than the environment, so the comparison is against the built-in
    # default rather than a key that is not there.
    retail = config["services"]["postgres"]
    assert feedbackdb["environment"]["POSTGRES_DB"] != retail["build"]["args"]["DB_NAME"]
    assert feedbackdb["container_name"] != retail["container_name"]
    assert feedbackdb["image"] != retail["image"]


def test_the_staging_database_is_on_its_own_port(config: dict):
    ports = {
        service: {p["published"] for p in spec.get("ports", [])}
        for service, spec in config["services"].items()
        if spec.get("ports")
    }
    feedback_ports = ports["feedbackdb"]
    for service, published in ports.items():
        if service != "feedbackdb":
            assert not (feedback_ports & published), f"feedbackdb collides with {service}"


# ---------------------------------------------------------------------------
# Who reaches it, and as whom
# ---------------------------------------------------------------------------


def test_the_api_reaches_the_staging_database_as_the_insert_only_role(tmp_path_factory):
    """The whole security story is that the public process holds a role that
    cannot read a submission back. It is one compose line away from not."""
    config = _compose_config(
        tmp_path_factory.mktemp("apifeedback"),
        env={"API_FEEDBACK_DB_URL": "postgresql://nl2sql_feedback_writer:pw@nl2sql-feedbackdb:5432/nl2sql_feedback"},
    )
    url = config["services"]["api"]["environment"]["API_FEEDBACK_DB_URL"]

    from nl2sql_review.store import WRITER_ROLE

    assert url.startswith(f"postgresql://{WRITER_ROLE}:")


def test_the_api_works_without_a_staging_database(api: dict):
    """Unset is the default, and it has to stay a working configuration:
    the feedback routes answer 503 with a reason and everything else runs."""
    assert api["environment"].get("API_FEEDBACK_DB_URL", "") == ""


def test_the_review_service_reaches_it_as_the_owner(review: dict):
    """It creates the schema and re-fences the writer role on every start,
    so it cannot be the writer."""
    from nl2sql_review.store import WRITER_ROLE

    url = review["environment"]["FEEDBACK_DB_URL"]
    assert WRITER_ROLE not in url
    assert url.startswith("postgresql://feedback:")


def test_the_review_service_waits_for_the_database(review: dict):
    assert review["depends_on"]["feedbackdb"]["condition"] == "service_healthy"


def test_the_review_service_can_reach_the_stores_it_reloads(review: dict):
    """Promotion runs the two RAG loaders, which write the context store and
    the vector store. Without these it would write the document and report a
    reload failure on every promotion."""
    assert "nl2sql-chunkdb" in review["environment"]["CHUNK_DB_URL"]
    assert "nl2sql-vectordb" in review["environment"]["VECTOR_DB_URL"]
    assert review["depends_on"]["chunkdb"]["condition"] == "service_healthy"
    assert review["depends_on"]["vectordb"]["condition"] == "service_healthy"


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


def test_the_api_certificate_covers_the_review_service(api: dict):
    """The review service presents the certificate the API generates rather
    than carrying a second copy of the code that makes one. The cost of that
    subtraction is this line, and nothing but a test connects the two."""
    assert "nl2sql-review" in api["environment"]["API_TLS_HOSTNAMES"].split(",")


def test_the_review_gui_verifies_a_name_that_certificate_covers(reviewgui: dict, api: dict):
    covered = api["environment"]["API_TLS_HOSTNAMES"].split(",")
    assert reviewgui["environment"]["REVIEW_SSL_NAME"] in covered


def test_the_review_gui_proxies_the_name_it_verifies(reviewgui: dict):
    upstream = reviewgui["environment"]["REVIEW_UPSTREAM"]
    assert reviewgui["environment"]["REVIEW_SSL_NAME"] in upstream


@pytest.mark.parametrize("service", ["review", "reviewgui"])
def test_the_certificate_is_mounted_read_only(config: dict, service: str):
    """Neither of these writes a certificate; only the API does."""
    mounts = {mount["target"]: mount for mount in config["services"][service]["volumes"]}
    assert mounts["/etc/nl2sql/tls"]["source"] == "apitls"
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
    """
    assert "REVIEW_TOKEN" in reviewgui["environment"]
    assert "REVIEW_TOKEN" in review["environment"]


def test_the_two_interfaces_do_not_share_a_port(config: dict):
    gui_ports = {p["published"] for p in config["services"]["gui"]["ports"]}
    review_ports = {p["published"] for p in config["services"]["reviewgui"]["ports"]}
    assert not (gui_ports & review_ports)


def test_the_two_interfaces_are_built_from_different_images(config: dict):
    """Separate images from separate npm projects: two Vite entry points in
    one project would share a build, and the public GUI's image would serve
    the review interface to anyone who could reach it."""
    assert config["services"]["gui"]["image"] != config["services"]["reviewgui"]["image"]
    assert config["services"]["gui"]["build"]["dockerfile"] != config["services"]["reviewgui"]["build"]["dockerfile"]


def test_the_review_proxy_outlasts_a_promotion(reviewgui: dict, review: dict):
    """Promotion runs both RAG loaders. A proxy that gives up first cuts off
    a write that is still happening."""
    timeout = reviewgui["environment"]["REVIEW_READ_TIMEOUT"]
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


def _review_settings() -> set[str]:
    source = (REVIEW_DIR / "nl2sql_review" / "settings.py").read_text()
    read = set(re.findall(r'_env(?:_str|_bool|_int|_float|_tuple)?\(\s*"([A-Z_]+)"', source))
    assert read, "no environment variables found in settings.py -- the regex needs updating"
    return read


def test_every_setting_the_review_service_reads_can_be_set_through_compose(review: dict):
    missing = sorted(_review_settings() - set(review["environment"]))
    assert missing == [], f"the review service reads these, but compose never passes them: {missing}"


def test_every_variable_compose_sets_on_the_review_service_is_one_it_reads(review: dict):
    unread = sorted(set(review["environment"]) - _review_settings())
    assert unread == [], f"compose sets {unread} on the review service, which nothing in it reads"


def _review_proxy_variables() -> set[str]:
    """Every ${NAME} the review GUI's nginx template and start-up script substitute."""
    gui = REVIEW_DIR / "gui"
    sources = (gui / "nginx.conf.template").read_text() + (gui / "10-nl2sql-review-config.envsh").read_text()
    names = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)", sources))
    # Worked out by the start-up script rather than set by anyone: the header
    # from REVIEW_TOKEN, and where it writes the upstream's TLS settings.
    return names - {"REVIEW_AUTH_HEADER", "NGINX_REVIEW_UPSTREAM_TLS_CONF"}


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
    turning TLS off does not make a working container look unhealthy."""
    test = " ".join(review["healthcheck"]["test"])
    assert "/healthz" in test
    assert "REVIEW_TLS_ENABLED" in test


def test_every_new_service_restarts_unless_stopped(config: dict):
    for name in ("feedbackdb", "correctionsdb", "completionsdb", "review", "reviewgui"):
        assert config["services"][name]["restart"] == "unless-stopped"


# ---------------------------------------------------------------------------
# The corrections and completions stores (5.1)
# ---------------------------------------------------------------------------

FIX_STORES = {
    "correctionsdb": ("correctionsdata", "nl2sql_corrections", "CORRECTIONS_DB_URL"),
    "completionsdb": ("completionsdata", "nl2sql_completions", "COMPLETIONS_DB_URL"),
}


@pytest.mark.parametrize("service", sorted(FIX_STORES))
def test_each_fix_store_is_pgvector_in_its_own_volume(config: dict, service: str):
    """Records and RAG side by side, so the image has to carry pgvector;
    and data a person typed, so it keeps it in a volume of its own."""
    spec = config["services"][service]
    volume, database, _ = FIX_STORES[service]
    assert spec["image"].startswith("pgvector/pgvector:")
    assert spec["environment"]["POSTGRES_DB"] == database
    assert {m["target"]: m["source"] for m in spec["volumes"]} == {"/var/lib/postgresql/data": volume}
    assert volume in config["volumes"]
    assert "pg_isready" in " ".join(spec["healthcheck"]["test"])


def test_the_fix_stores_are_apart_from_the_golden_set_and_from_each_other(config: dict):
    """The golden set is what the agent is measured against; a record of its
    mistakes is not a benchmark answer. And a wrong join and a missing label
    are different lessons, kept apart for whatever reads them next."""
    services = config["services"]
    names = {services[s]["container_name"] for s in ("correctionsdb", "completionsdb", "chunkdb", "vectordb", "feedbackdb")}
    assert len(names) == 5
    volumes = {services[s]["volumes"][0]["source"] for s in ("correctionsdb", "completionsdb", "chunkdb", "vectordb", "feedbackdb")}
    assert len(volumes) == 5
    published = {
        service: {p["published"] for p in services[service].get("ports", [])} for service in services
    }
    for store in FIX_STORES:
        for other, ports in published.items():
            if other != store:
                assert published[store].isdisjoint(ports), f"{store} shares a port with {other}"


@pytest.mark.parametrize("service", sorted(FIX_STORES))
def test_the_review_service_waits_for_and_reaches_each_store(review: dict, service: str):
    _, database, variable = FIX_STORES[service]
    assert review["depends_on"][service]["condition"] == "service_healthy"
    assert f"nl2sql-{service}:5432/{database}" in review["environment"][variable]


def test_only_the_review_service_is_given_a_way_into_the_fix_stores(tmp_path_factory):
    """Least access, one level up from the database roles. The agent reads
    as `nl2sql_reader`; the API writes verdicts as an INSERT-only role; and
    neither is handed the stores' address or credentials, so no SQL either
    of them runs can reach a fix. Rendered over every profile -- the CLI
    agent's and the desktop build's included -- because a service in a
    profile nobody tested is still a service somebody starts.
    """
    everything = _compose_config(tmp_path_factory.mktemp("all"), *PROFILES, "agent", "desktop")
    assert "agent" in everything["services"]
    markers = ("nl2sql-correctionsdb", "nl2sql-completionsdb", "nl2sql_corrections", "nl2sql_completions")
    reaching = {
        name
        for name, spec in everything["services"].items()
        if any(marker in json.dumps(spec.get("environment", {})) for marker in markers)
    }
    assert reaching - set(FIX_STORES) == {"review"}


def test_a_fix_is_validated_on_the_retail_database_as_the_reader(review: dict):
    """It runs SQL a person typed, so it runs it as the role that can only read."""
    assert review["depends_on"]["postgres"]["condition"] == "service_healthy"
    url = review["environment"]["RETAIL_DB_URL"]
    assert url.startswith("postgresql://nl2sql_reader:") and "@nl2sql-postgres:5432/nl2sql_retail" in url
    for knob in ("REVIEW_VALIDATE_TIMEOUT_MS", "REVIEW_VALIDATE_MAX_ROWS", "REVIEW_EMBED_FIXES"):
        assert knob in review["environment"]
