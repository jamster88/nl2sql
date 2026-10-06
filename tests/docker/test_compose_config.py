"""docker-compose.yml, validated by actually asking `docker compose config`
to parse/resolve/render it (catches real YAML/interpolation errors, not just
"the file happens to look right"), plus structural assertions tying it back
to what docker/Dockerfile and agent/Dockerfile actually expect.

`docker compose config` only parses and resolves the compose file -- it
never builds, pulls, or starts anything -- so, unlike the tests in
test_dockerfiles.py, these don't strictly need --run-docker. They're grouped
under it anyway because they need a working `docker` CLI, and a repo clone
that lacks one shouldn't fail the offline (data_gen + agent) part of the
suite. Runs against a project-directory .env file are explicitly excluded
(via --env-file to an empty file) so the assertions reflect the compose
file's own defaults, not whatever a developer's local .env happens to hold.
"""

from __future__ import annotations

import json
import re
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from tests.settings_names import read_names, settable_names

pytestmark = pytest.mark.docker

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _compose_config(tmp_path: Path, *, profile: str | tuple[str, ...] | None = None, env: dict | None = None) -> dict:
    empty_env_file = tmp_path / "empty.env"
    empty_env_file.write_text("")

    cmd = ["docker", "compose", "--env-file", str(empty_env_file)]
    for name in (profile,) if isinstance(profile, str) else profile or ():
        cmd += ["--profile", name]
    cmd += ["config", "--format", "json"]

    # Start from an environment with nothing compose could substitute. The
    # helper passes an empty --env-file for the same reason, but compose also
    # reads the ambient environment, so a developer who exports EMBED_BASE_URL
    # or MAX_TABLES in their shell would otherwise see these tests assert
    # against their settings instead of the file's defaults.
    substitutable = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)", (REPO_ROOT / "docker-compose.yml").read_text()))
    run_env = {k: v for k, v in os.environ.items() if k not in substitutable}
    if env:
        run_env.update(env)

    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=30, env=run_env)
    if result.returncode != 0:
        pytest.fail(f"`docker compose config` failed:\n{result.stderr}")
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def default_config(tmp_path_factory) -> dict:
    return _compose_config(tmp_path_factory.mktemp("compose"))


@pytest.fixture(scope="module")
def agent_profile_config(tmp_path_factory) -> dict:
    return _compose_config(tmp_path_factory.mktemp("compose"), profile="agent")


def test_agent_service_is_hidden_without_the_agent_profile(default_config: dict):
    # profiles: ["agent"] means plain `docker compose up` never starts it.
    assert "agent" not in default_config["services"]
    assert "postgres" in default_config["services"]
    assert "vectordb" in default_config["services"]


@pytest.mark.parametrize("script", ["setup.sh", "launch.sh"])
def test_the_scripts_read_the_agents_settings_from_real_compose(script: str):
    """The fact above, as the scripts meet it.

    Both check the chat and embedding models by reading back what compose
    will hand the agent. Their fake `docker` answered for the agent whatever
    profile was named, and the real one does not -- so until v5.2 both read
    nothing, fell back to their own defaults, and checked the maintainer's
    host and model whatever .env said. This runs each script's own helper
    against the real compose file.
    """
    source = (REPO_ROOT / script).read_text()
    helper = re.search(r"^compose_value\(\) \{\n.*?^\}\n", source, re.MULTILINE | re.DOTALL)
    assert helper, f"{script} no longer defines compose_value"
    env = {
        **os.environ,
        "OLLAMA_BASE_URL": "http://chat.invalid:11434",
        "OLLAMA_MODEL": "some-chat-model",
        "EMBED_MODEL": "some-embedding-model",
    }
    result = subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + helper.group(0)
         + "compose_value OLLAMA_BASE_URL; echo; compose_value OLLAMA_MODEL; echo; "
           "compose_value EMBED_MODEL; echo"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=30, env=env,
    )
    assert result.stdout.split() == [
        "http://chat.invalid:11434", "some-chat-model", "some-embedding-model"], result.stderr


def test_all_services_present_under_the_agent_profile(agent_profile_config: dict):
    # pki has no profile: it is the one-shot that issues every TLS server
    # its certificate, and any of them may be what is started. Nor has
    # dbprep, the one-shot that prepares every database (6.3), nor the
    # runtime stores, one server since 6.3.
    assert set(agent_profile_config["services"]) == {
        "postgres", "vectordb", "chunkdb", "stores", "agent", "pki", "dbprep"
    }


def test_postgres_service_shape(agent_profile_config: dict):
    postgres = agent_profile_config["services"]["postgres"]
    assert postgres["build"]["dockerfile"] == "docker/Dockerfile"
    assert postgres["container_name"] == "nl2sql-postgres"
    assert postgres["restart"] == "unless-stopped"
    assert "pg_isready" in " ".join(postgres["healthcheck"]["test"])

    volume, pgtls, ldaptls, socket = postgres["volumes"]
    assert volume["source"] == "pgdata"
    # Must match ENV PGDATA in docker/Dockerfile, or the image's baked data
    # is invisible to the named volume that's supposed to seed from it.
    assert volume["target"] == "/var/lib/pgdata"
    # Its own certificate, written there on first start (V6-52): read-write.
    assert (pgtls["source"], pgtls["target"], pgtls.get("read_only", False)) == ("pgtls", "/etc/nl2sql/pg-tls", False)
    # Sign-in: pg_hba's `ldap` method verifies the directory's certificate,
    # which libldap finds through LDAPTLS_CACERT, in the volume the
    # directory writes it to -- read-only here.
    assert (ldaptls["source"], ldaptls["target"], ldaptls["read_only"]) == ("ldaptls", "/etc/nl2sql/ldap-tls", True)
    # Its socket, shared with the dbprep one-shot alone (6.3, V6-41).
    assert (socket["source"], socket["target"]) == ("pgsocket", "/var/run/postgresql")
    # The owner's password is a secret file (V6-38); the reader's is dbprep's
    # to set, so it is not here at all.
    assert postgres["environment"] == {
        "LDAPTLS_CACERT": "/etc/nl2sql/ldap-tls/ldap.crt",
        "POSTGRES_PASSWORD_FILE": "/run/secrets/postgres_password",
        "POSTGRES_READER_USER": "nl2sql_reader",
        "POSTGRES_TLS_HOSTNAMES": "",
        "POSTGRES_REQUIRE_TLS": "",
    }
    assert [secret["source"] for secret in postgres["secrets"]] == ["postgres_password"]
    # No password is a build argument any more (V6-07).
    assert not any("PASSWORD" in name for name in postgres["build"]["args"])

    [port] = postgres["ports"]
    assert port["target"] == 5432
    assert port["host_ip"] == "127.0.0.1", "this machine's unless DB_BIND_ADDRESS says otherwise (V6-06)"


def test_vectordb_service_shape(agent_profile_config: dict):
    vectordb = agent_profile_config["services"]["vectordb"]
    assert vectordb["container_name"] == "nl2sql-vectordb"
    assert vectordb["restart"] == "unless-stopped"
    assert "pg_isready" in " ".join(vectordb["healthcheck"]["test"])

    volume, socket = vectordb["volumes"]
    assert volume["source"] == "vectordata"
    assert (socket["source"], socket["target"]) == ("vectorsocket", "/var/run/postgresql")
    # Same PGDATA convention as the retail image: outside the base image's
    # declared VOLUME, so the embeddings baked into the image are visible.
    assert volume["target"] == "/var/lib/pgdata"

    [port] = vectordb["ports"]
    assert port["target"] == 5432


def test_agent_service_shape(agent_profile_config: dict):
    agent = agent_profile_config["services"]["agent"]
    assert agent["build"]["dockerfile"] == "agent/Dockerfile"
    assert agent["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert agent["profiles"] == ["agent"]
    env = agent["environment"]
    assert "DATABASE_URL" in env
    assert "OLLAMA_BASE_URL" in env
    assert "OLLAMA_MODEL" in env


def test_agent_waits_for_the_knowledge_base_to_be_healthy(agent_profile_config: dict):
    agent = agent_profile_config["services"]["agent"]
    assert agent["depends_on"]["vectordb"]["condition"] == "service_healthy"


def test_agent_vector_db_url_points_at_the_compose_vectordb_service(agent_profile_config: dict):
    env = agent_profile_config["services"]["agent"]["environment"]
    assert "@vectordb:5432/nl2sql_vectors" in env["VECTOR_DB_URL"]
    assert env["RAG_ENABLED"] == "true"


def test_agent_embeds_against_the_docker_host_not_the_compose_network(agent_profile_config: dict):
    """bge-m3 runs on the machine hosting Docker, so the container has to
    reach back out to it; extra_hosts is what makes that work on Linux, where
    host.docker.internal is not resolvable by default.
    """
    agent = agent_profile_config["services"]["agent"]
    assert agent["environment"]["EMBED_BASE_URL"].startswith("http://host.docker.internal")
    assert "host.docker.internal=host-gateway" in agent["extra_hosts"]


def test_agent_database_url_points_at_the_compose_postgres_service_as_the_read_only_role(
    agent_profile_config: dict,
):
    """The agent only ever reads, so it connects as the reader role the image
    creates (docker/reader_role.sql), never as the owner that loaded the data
    -- with the reader's password from its secret file, beside a URL that
    carries none (6.3, V6-38).
    """
    postgres_args = agent_profile_config["services"]["postgres"]["build"]["args"]
    agent = agent_profile_config["services"]["agent"]
    reader = postgres_args["DB_READER"]
    assert agent["environment"]["DATABASE_URL"] == (
        f"postgresql+psycopg://{reader}@postgres:5432/{postgres_args['DB_NAME']}"
    )
    assert agent["environment"]["DATABASE_PASSWORD_FILE"] == "/run/secrets/postgres_reader_password"
    assert reader != postgres_args["DB_USER"]


def test_the_owners_credentials_never_reach_the_agent_container(agent_profile_config: dict):
    """Least privilege at the compose level: the only Postgres identity in the
    agent's environment is the reader. The owner's password is a secret
    mounted into the postgres service and nothing else.
    """
    postgres_args = agent_profile_config["services"]["postgres"]["build"]["args"]
    agent = agent_profile_config["services"]["agent"]
    owner = postgres_args["DB_USER"]
    for key, value in agent["environment"].items():
        assert f"//{owner}@" not in str(value) and f"//{owner}:" not in str(value), f"{key} carries the owner's login"
    assert "postgres_password" not in {secret["source"] for secret in agent["secrets"]}


def test_the_reader_role_can_be_renamed_from_the_environment(tmp_path_factory):
    config = _compose_config(
        tmp_path_factory.mktemp("compose"),
        profile="agent",
        env={"POSTGRES_READER_USER": "ro"},
    )
    assert config["services"]["postgres"]["build"]["args"]["DB_READER"] == "ro"
    assert "//ro@postgres:5432/" in config["services"]["agent"]["environment"]["DATABASE_URL"]


def test_named_volumes_are_declared_persistent(agent_profile_config: dict):
    volumes = agent_profile_config["volumes"]
    assert volumes["pgdata"]["name"] == "nl2sql-pgdata"
    assert "vectordata" in volumes


def test_the_knowledge_base_volume_is_project_scoped(agent_profile_config: dict):
    """Adopting the RAG pipeline's volume (nl2sql-rag-vectordb-data) made
    compose warn about a cross-project volume on every command. The image
    ships the embeddings, so a project-scoped volume seeds from it instead.
    """
    name = agent_profile_config["volumes"]["vectordata"].get("name", "")
    assert name in ("", "nl2sql_vectordata")


def test_shell_env_vars_override_compose_defaults(tmp_path_factory):
    config = _compose_config(
        tmp_path_factory.mktemp("compose"),
        profile="agent",
        env={
            "POSTGRES_PORT": "15432",
            "IMAGE_NAME": "example.org/pg", "IMAGE_TAG": "v9",
            "AGENT_IMAGE_NAME": "example.org/agent", "AGENT_IMAGE_TAG": "v9",
        },
    )
    postgres = config["services"]["postgres"]
    assert postgres["ports"][0]["published"] == "15432"
    assert postgres["image"] == "example.org/pg:v9"
    assert config["services"]["agent"]["image"] == "example.org/agent:v9"


def test_postgres_and_agent_image_tags_are_independently_settable(tmp_path_factory):
    """Regression guard: the agent service must key off AGENT_IMAGE_TAG, not
    IMAGE_TAG -- otherwise pinning the Postgres dataset image to a version
    tag (IMAGE_TAG=v1) would also retag the unrelated agent image to v1.
    """
    config = _compose_config(
        tmp_path_factory.mktemp("compose"), profile="agent", env={"IMAGE_TAG": "v1"}
    )
    assert config["services"]["postgres"]["image"].endswith(":v1")
    assert not config["services"]["agent"]["image"].endswith(":v1")


# ---------------------------------------------------------------------------
# Knowledge-base overrides (v2)
# ---------------------------------------------------------------------------


def test_vector_image_and_port_are_overridable(tmp_path_factory):
    config = _compose_config(
        tmp_path_factory.mktemp("compose"),
        profile="agent",
        env={
            "VECTOR_IMAGE_NAME": "example.org/vectors",
            "VECTOR_IMAGE_TAG": "v7",
            "VECTOR_DB_PORT": "15434",
        },
    )
    vectordb = config["services"]["vectordb"]
    assert vectordb["image"] == "example.org/vectors:v7"
    assert vectordb["ports"][0]["published"] == "15434"


def test_vector_credentials_flow_into_both_the_healthcheck_and_the_agent_url(tmp_path_factory):
    """One set of variables has to drive both, or the database comes up with
    credentials the agent does not use.
    """
    config = _compose_config(
        tmp_path_factory.mktemp("compose"),
        profile="agent",
        env={"VECTOR_DB_USER": "vuser", "VECTOR_DB_NAME": "vectors_db"},
    )
    healthcheck = " ".join(config["services"]["vectordb"]["healthcheck"]["test"])
    assert "-U vuser" in healthcheck
    assert "-d vectors_db" in healthcheck
    agent = config["services"]["agent"]["environment"]
    assert agent["VECTOR_DB_URL"] == "postgresql+psycopg://vuser@vectordb:5432/vectors_db"
    # The password, one secret file that dbprep sets the login's from (6.3).
    assert agent["VECTOR_DB_PASSWORD_FILE"] == "/run/secrets/vector_db_password"
    assert "vector_db_password" in {secret["source"] for secret in config["services"]["dbprep"]["secrets"]}


def test_retrieval_can_be_turned_off_through_the_environment(tmp_path_factory):
    config = _compose_config(
        tmp_path_factory.mktemp("compose"), profile="agent", env={"RAG_ENABLED": "false"}
    )
    assert config["services"]["agent"]["environment"]["RAG_ENABLED"] == "false"


def test_embedding_host_model_and_top_k_are_overridable(tmp_path_factory):
    config = _compose_config(
        tmp_path_factory.mktemp("compose"),
        profile="agent",
        env={
            "EMBED_BASE_URL": "http://embed-host:11434",
            "EMBED_MODEL": "nomic-embed-text",
            "RAG_TOP_K": "9",
        },
    )
    env = config["services"]["agent"]["environment"]
    assert env["EMBED_BASE_URL"] == "http://embed-host:11434"
    assert env["EMBED_MODEL"] == "nomic-embed-text"
    assert env["RAG_TOP_K"] == "9"


def test_vectordb_gets_a_shutdown_grace_period_like_the_retail_database(agent_profile_config: dict):
    # Postgres needs time to checkpoint cleanly; a hard kill risks recovery on
    # the next start.
    assert agent_profile_config["services"]["vectordb"]["stop_grace_period"] == "1m0s"


def test_the_two_databases_do_not_collide_on_a_port_or_a_volume(agent_profile_config: dict):
    postgres = agent_profile_config["services"]["postgres"]
    vectordb = agent_profile_config["services"]["vectordb"]
    assert postgres["ports"][0]["published"] != vectordb["ports"][0]["published"]
    assert postgres["volumes"][0]["source"] != vectordb["volumes"][0]["source"]
    assert postgres["container_name"] != vectordb["container_name"]


def test_every_agent_environment_variable_is_one_the_agent_actually_reads(agent_profile_config: dict):
    """Guards against a compose variable that quietly does nothing because the
    agent reads a differently-spelled name.
    """
    from nl2sql_agent.config import Settings

    source = Path(Settings.__module__.replace(".", "/"))  # nl2sql_agent/config
    read = read_names((REPO_ROOT / "agent" / source).with_suffix(".py").read_text())
    for name in agent_profile_config["services"]["agent"]["environment"]:
        assert name in read, f"compose sets {name}, but config.py never reads it"


# ---------------------------------------------------------------------------
# v3: the context store
# ---------------------------------------------------------------------------


def test_the_agent_is_pointed_at_the_context_store(agent_profile_config: dict):
    """The example retriever reads golden pairs and runs BM25 there, so the
    agent needs the URL as well as the dependency -- one without the other
    fails at the first question rather than at startup.
    """
    env = agent_profile_config["services"]["agent"]["environment"]
    assert env["CONTEXT_DB_URL"] == "postgresql+psycopg://ragproc@chunkdb:5432/nl2sql_chunks"
    assert env["CONTEXT_DB_PASSWORD_FILE"] == "/run/secrets/context_db_password"


def test_the_agent_waits_for_every_database_to_be_healthy(agent_profile_config: dict):
    depends = agent_profile_config["services"]["agent"]["depends_on"]
    assert set(depends) == {"postgres", "vectordb", "chunkdb", "stores", "dbprep"}
    # Each database healthy, and prepared: the reader made and its password set.
    assert depends.pop("dbprep")["condition"] == "service_completed_successfully"
    assert all(d["condition"] == "service_healthy" for d in depends.values())


def test_the_context_store_keeps_its_data_outside_the_base_image_volume(agent_profile_config: dict):
    """PGDATA has to sit outside /var/lib/postgresql, which the base image
    declares as a VOLUME -- writes there are invisible to `docker commit`, so a
    cluster living there could never be published as an image.
    """
    mounts = agent_profile_config["services"]["chunkdb"]["volumes"]
    assert any(m["target"] == "/var/lib/pgdata" for m in mounts)


def test_multi_shot_and_the_rerank_are_on_by_default_in_compose(agent_profile_config: dict):
    """Both are the architecture now rather than experiments, so the image has
    to ship with them on -- a default that disagrees with the source default
    means the container behaves differently from everything the tests exercise.
    """
    env = agent_profile_config["services"]["agent"]["environment"]
    assert env["EXAMPLES_ENABLED"] == "true"
    assert env["MULTI_SHOT_ENABLED"] == "true"
    assert env["EXAMPLES_RERANK"] == "mmr"


def test_the_context_window_is_set_explicitly(agent_profile_config: dict):
    """Ollama caps num_ctx at a few thousand tokens whatever the model supports,
    and truncates past it silently. Multi-shot puts the schema, the knowledge
    block and three worked SQL queries in one window, so this is not optional.
    """
    env = agent_profile_config["services"]["agent"]["environment"]
    assert env["OLLAMA_NUM_CTX"] == "262144"
    assert env["OLLAMA_MODEL"] == "qwen3.8-256k"


def test_every_v4_pipeline_stage_is_toggleable_from_the_environment(agent_profile_config: dict):
    """The architecture's ablation plan is a set of environment flags, so a
    configuration comparison is a compose variable rather than a rebuild.
    """
    env = agent_profile_config["services"]["agent"]["environment"]
    for flag in (
        "SUPERVISOR_ENABLED", "LITERALS_ENABLED", "AUDIT_ENABLED",
        "NARRATE_ENABLED", "SCHEMA_RETRIEVAL", "MAX_ATTEMPTS", "MAX_PLAN_COST",
        "REVIEW_ENABLED", "REVIEW_REFLECTION_ENABLED",
    ):
        assert flag in env, f"{flag} is not passed to the agent container"
    assert env["SCHEMA_RETRIEVAL"] == "vector"
    assert env["MAX_ATTEMPTS"] == "7"
    assert env["REVIEW_ENABLED"] == "true"
    assert env["REVIEW_REFLECTION_ENABLED"] == "true"


def test_the_retry_budget_and_plan_ceiling_are_overridable(tmp_path_factory):
    config = _compose_config(
        tmp_path_factory.mktemp("compose"),
        profile="agent",
        env={"MAX_ATTEMPTS": "2", "MAX_PLAN_COST": "50000", "SCHEMA_RETRIEVAL": "llm"},
    )
    env = config["services"]["agent"]["environment"]
    assert env["MAX_ATTEMPTS"] == "2"
    assert env["MAX_PLAN_COST"] == "50000"
    assert env["SCHEMA_RETRIEVAL"] == "llm"


# ---------------------------------------------------------------------------
# The compose environment and config.py, in both directions
# ---------------------------------------------------------------------------


def _config_py() -> str:
    return (REPO_ROOT / "agent" / "nl2sql_agent" / "config.py").read_text()


def test_every_setting_the_agent_reads_can_be_set_through_compose(agent_profile_config: dict):
    """A knob the README documents but compose never forwards cannot be set
    on the containerised agent at all, which is the way almost everyone runs
    it.
    """
    env = set(agent_profile_config["services"]["agent"]["environment"])
    missing = sorted(settable_names(_config_py()) - env)
    assert missing == [], f"config.py reads these, but compose never passes them: {missing}"


def test_every_variable_compose_sets_on_the_agent_is_one_it_reads(agent_profile_config: dict):
    """The other direction, which every other service here already had. A
    variable compose forwards and nothing reads is a knob that does nothing
    -- usually a setting renamed in config.py and left behind in compose, so
    that setting it changes nothing and says nothing.
    """
    env = set(agent_profile_config["services"]["agent"]["environment"])
    unread = sorted(env - read_names(_config_py()))
    assert unread == [], f"compose passes these to the agent, and config.py never reads them: {unread}"


def test_a_forwarded_setting_the_host_has_not_set_arrives_empty(agent_profile_config: dict):
    """Which is the whole reason config.py treats empty as unset: compose has
    to forward a variable to make it settable, and forwarding an unset one
    yields an empty string.
    """
    env = agent_profile_config["services"]["agent"]["environment"]
    assert env["MAX_TABLES"] == ""
    assert env["SCHEMA_TOP_K"] == ""


def test_a_forwarded_setting_the_host_did_set_reaches_the_container(tmp_path_factory):
    config = _compose_config(
        tmp_path_factory.mktemp("compose"),
        profile="agent",
        env={"MAX_TABLES": "4", "LITERAL_MIN_SCORE": "0.8", "DB_SCHEMA": "analytics"},
    )
    env = config["services"]["agent"]["environment"]
    assert env["MAX_TABLES"] == "4"
    assert env["LITERAL_MIN_SCORE"] == "0.8"
    assert env["DB_SCHEMA"] == "analytics"


def test_forwarding_never_overrides_a_default(tmp_path_factory):
    """An empty forwarded value has to leave the image's own default
    standing, or every knob compose passes through would be silently reset.
    """
    from nl2sql_agent.config import Settings

    config = _compose_config(tmp_path_factory.mktemp("compose"), profile="agent")
    env = config["services"]["agent"]["environment"]
    defaults = Settings()
    for name, attribute in (
        ("MAX_TABLES", "max_tables"),
        ("SCHEMA_TOP_K", "schema_top_k"),
        ("LITERAL_MIN_SCORE", "literal_min_score"),
        ("MAX_ROWS", "max_rows"),
        ("DB_SCHEMA", "db_schema"),
    ):
        assert env[name] == "", f"{name} should be forwarded empty, not pinned in compose"
        assert getattr(defaults, attribute) is not None


def test_the_context_store_volume_is_declared_and_project_scoped(agent_profile_config: dict):
    """The third store arrived with v3 and its volume is the one least
    likely to be noticed missing: the agent still answers without it, just
    with no worked examples.
    """
    volumes = agent_profile_config["volumes"]
    assert "chunkdata" in volumes
    assert volumes["chunkdata"].get("name", "") in ("", "nl2sql_chunkdata")
    volume, socket = agent_profile_config["services"]["chunkdb"]["volumes"]
    assert volume["source"] == "chunkdata"
    assert volume["target"] == "/var/lib/pgdata"
    assert (socket["source"], socket["target"]) == ("contextsocket", "/var/run/postgresql")


def test_the_resolved_config_does_not_depend_on_the_developers_shell(tmp_path_factory):
    """Compose substitutes from the ambient environment as well as the env
    file, so a variable exported in a shell would quietly change what these
    tests assert against -- and pass or fail by accident on one machine.
    """
    with_ambient = _compose_config(
        tmp_path_factory.mktemp("compose"),
        profile="agent",
        env={},
    )
    import os as _os

    _os.environ["EMBED_BASE_URL"] = "http://somewhere-else:11434"
    _os.environ["MAX_TABLES"] = "99"
    try:
        clean = _compose_config(tmp_path_factory.mktemp("compose"), profile="agent")
    finally:
        del _os.environ["EMBED_BASE_URL"]
        del _os.environ["MAX_TABLES"]

    assert clean["services"]["agent"]["environment"] == with_ambient["services"]["agent"]["environment"]
    assert clean["services"]["agent"]["environment"]["EMBED_BASE_URL"].startswith(
        "http://host.docker.internal"
    )


# ---------------------------------------------------------------------------
# The RAG pipeline's own compose file
# ---------------------------------------------------------------------------


def test_the_rag_compose_file_resolves():
    """`rag/docker-compose.yml` is a second compose file, run by the RAG
    scripts from their own directory and never by the root one.

    Its contents are checked by reading the YAML in
    tests/rag/test_rag_images.py; this is the half that needs compose itself,
    and it is the only thing that catches a schema change.
    """
    result = subprocess.run(
        ["docker", "compose", "config", "--quiet"],
        cwd=REPO_ROOT / "rag", capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr


def test_the_two_compose_files_are_separate_projects():
    """They declare different `name:`s, so the RAG stores never appear in
    `docker compose ps` at the repository root and cannot be brought down by
    a `docker compose down` meant for the retail database.
    """
    root = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
    rag = yaml.safe_load((REPO_ROOT / "rag" / "docker-compose.yml").read_text())
    assert root["name"] != rag["name"]


# ---------------------------------------------------------------------------
# Every set of profiles a script runs or a document prints
# ---------------------------------------------------------------------------


def _profile_sets() -> list[str]:
    """Each distinct `docker compose --profile ...` run by a script or shown in
    a document, as its profile arguments. Not the changelogs: they are history,
    and quote the commands that were broken."""
    tracked = subprocess.run(
        ["git", "ls-files", "-z", "*.sh", "*.md", ":!CHANGELOG*.md"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.split("\0")
    found = set()
    for path in filter(None, tracked):
        # A file deleted in the working tree is still listed until the commit.
        if (REPO_ROOT / path).is_file():
            found |= set(re.findall(r"docker compose((?: --profile [a-z]+)+)", (REPO_ROOT / path).read_text()))
    return sorted(profiles.strip() for profiles in found)


def test_the_scripts_and_documents_name_profiles_to_check():
    assert {"--profile api --profile gui", "--profile review --profile reviewgui"} <= set(_profile_sets())


@pytest.mark.parametrize("profiles", _profile_sets())
def test_every_set_of_profiles_named_is_a_project_compose_accepts(profiles: str, tmp_path: Path):
    """A service whose dependencies sit behind another profile is a project
    compose rejects outright, before it lists a log line or runs a thing:
    `docker compose --profile reviewgui logs reviewgui`, printed by launch.sh
    when the review interface did not come up, failed that way -- as did
    5.5.1's `--load-golden` at first, which only a live run found, because the
    fake docker the script tests run against accepts whatever it is given."""
    empty_env_file = tmp_path / "empty.env"
    empty_env_file.write_text("")
    substitutable = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)", (REPO_ROOT / "docker-compose.yml").read_text()))
    result = subprocess.run(
        ["docker", "compose", "--env-file", str(empty_env_file), *profiles.split(), "config", "--quiet"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
        env={k: v for k, v in os.environ.items() if k not in substitutable},
    )
    assert result.returncode == 0, f"docker compose {profiles}: {result.stderr.strip()}"


# ---------------------------------------------------------------------------
# The stack's TLS identities (6.1, V6-36)
# ---------------------------------------------------------------------------


def _identities(config: dict) -> dict[str, tuple[str, list[str]]]:
    """Each identity's directory and names; its owner, the last part since
    6.2, is `test_unprivileged.py`'s to check."""
    found = {}
    for arg in config["services"]["pki"]["command"]:
        if "=" in arg and not arg.startswith("-"):
            name, directory, hosts = arg.split("=", 3)[:3]
            found[name] = (directory, hosts.split(","))
    return found


def test_the_pki_issues_an_identity_into_each_servers_own_volume(default_config: dict):
    pki = default_config["services"]["pki"]
    assert pki["command"][:3] == ["python", "-m", "nl2sql_identity.pki"]
    assert pki["image"] == "nl2sql-agent:latest", "the agent's image, which carries the package"
    mounts = {volume["target"]: volume["source"] for volume in pki["volumes"]}
    assert mounts["/etc/nl2sql/pki"] == "pkica"
    for name, (directory, hosts) in _identities(default_config).items():
        assert mounts[directory] == f"{name}tls"
        assert "localhost" in hosts and "127.0.0.1" in hosts


def test_names_given_to_the_stack_reach_every_identity(tmp_path_factory):
    """TLS_EXTRA_HOSTNAMES is this machine's name on the network, for a
    browser elsewhere; the API's own list is API_TLS_HOSTNAMES, as before."""
    config = _compose_config(
        tmp_path_factory.mktemp("names"),
        env={"TLS_EXTRA_HOSTNAMES": "nl2sql.lan", "API_TLS_HOSTNAMES": "localhost,nl2sql-api,api.example"},
    )
    assert "--also=nl2sql.lan" in config["services"]["pki"]["command"]
    assert _identities(config)["api"][1] == ["localhost", "nl2sql-api", "api.example"]


# ---------------------------------------------------------------------------
# Moving a port moves everything that proxies to it
# ---------------------------------------------------------------------------

#: (service, its variable naming an upstream, the port setting that moves
#: what it proxies to). A service listens on its own port setting inside
#: its container as well as publishing it, so a page whose upstream kept the
#: default answered 502 to every request once the port was moved -- which
#: USAGE_GUIDE.md says anyone may do in `.env`.
FOLLOWERS = [
    ("gui", "UPSTREAM", "API_PORT"), ("gui", "AUTH_UPSTREAM", "AUTH_PORT"),
    ("reviewgui", "UPSTREAM", "REVIEW_PORT"), ("reviewgui", "AUTH_UPSTREAM", "AUTH_PORT"),
    ("curategui", "UPSTREAM", "REVIEW_PORT"), ("curategui", "AUTH_UPSTREAM", "AUTH_PORT"),
    ("consolegui", "UPSTREAM", "CONSOLE_PORT"), ("consolegui", "AUTH_UPSTREAM", "AUTH_PORT"),
    ("directorygui", "AUTH_UPSTREAM", "AUTH_PORT"),
    ("directorygui", "UPSTREAM", "AUTH_DIRECTORY_PORT"),
    ("mlflowproxy", "AUTH_UPSTREAM", "AUTH_PORT"), ("apitest", "API_BASE_URL", "API_PORT"),
]
EVERY_PAGE = ("api", "gui", "review", "reviewgui", "curategui", "console", "consolegui",
              "auth", "directorygui", "mlflow")


@pytest.fixture(scope="module")
def moved(tmp_path_factory) -> dict:
    ports = {"API_PORT": "9443", "REVIEW_PORT": "9444", "CONSOLE_PORT": "9445", "AUTH_PORT": "9446",
             "AUTH_DIRECTORY_PORT": "9447"}
    return _compose_config(tmp_path_factory.mktemp("compose"), profile=EVERY_PAGE, env=ports)["services"]


@pytest.mark.parametrize("service,variable,port", FOLLOWERS)
def test_a_moved_port_is_followed_by_what_proxies_to_it(moved: dict, service: str, variable: str, port: str):
    target = {"API_PORT": "9443", "REVIEW_PORT": "9444", "CONSOLE_PORT": "9445", "AUTH_PORT": "9446",
              "AUTH_DIRECTORY_PORT": "9447"}[port]
    assert moved[service]["environment"][variable].endswith(f":{target}")


def test_no_upstream_names_a_port_of_its_own():
    """Every `https://nl2sql-<service>:` default ends in the setting that
    moves that service, so the list above is the whole of it."""
    text = (REPO_ROOT / "docker-compose.yml").read_text()
    assert re.findall(r"https://nl2sql-[a-z-]+:\d", text) == []
    assert len(re.findall(r":-https://nl2sql-", text)) == len(FOLLOWERS)


# ---------------------------------------------------------------------------
# Another instance beside this one (V6-67)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def instances(tmp_path_factory) -> tuple[dict, dict]:
    """The file resolved as the usual stack and as the acceptance tier's."""
    usual = _compose_config(tmp_path_factory.mktemp("compose"), profile=EVERY_PAGE)
    other = _compose_config(tmp_path_factory.mktemp("compose"), profile=EVERY_PAGE,
                            env={"NL2SQL_INSTANCE": "nl2sql-accept"})
    return usual, other


def test_the_usual_stack_is_named_as_it_always_was(instances):
    usual, _ = instances
    assert usual["name"] == "nl2sql"
    assert usual["volumes"]["pgdata"]["name"] == "nl2sql-pgdata"
    assert usual["services"]["api"]["container_name"] == "nl2sql-api"


def test_another_instance_shares_no_container_or_volume_name_with_it(instances):
    usual, other = instances
    assert other["name"] == "nl2sql-accept"
    assert other["volumes"]["pgdata"]["name"] == "nl2sql-accept-pgdata"
    named = {name: service["container_name"] for name, service in other["services"].items() if "container_name" in service}
    assert named and all(value.startswith("nl2sql-accept-") for value in named.values())
    assert not set(named.values()) & {s.get("container_name") for s in usual["services"].values()}


def test_every_container_is_reached_by_its_usual_name_in_either(instances):
    """The name the others reach it by, and the one its certificate covers:
    an alias on its own network, so it is the same in every instance. The
    runtime stores answer to the four names they had before 6.3 as well, so
    a URL written then still reaches its database."""
    legacy = ["nl2sql-feedbackdb", "nl2sql-correctionsdb", "nl2sql-completionsdb", "nl2sql-snippetsdb"]
    for config in instances:
        for name, service in config["services"].items():
            if "container_name" not in service:
                continue
            usual = service["container_name"].replace(config["name"], "nl2sql", 1)
            expected = [usual, *legacy] if name == "stores" else [usual]
            assert service["networks"]["default"]["aliases"] == expected, name


@pytest.mark.parametrize("sets", [(), ("--review", "--curate", "--console", "--mlflow", "--desktop"), ("--no-auth",)])
def test_the_profiles_setup_builds_with_resolve(run_setup, tmp_path, sets):
    """`setup.sh --build-all` names the profiles to build by what the run
    pins; compose refuses the lot if one names a service that depends on
    another in a profile left out -- which the fake `docker` its own tests
    run against cannot know, and the acceptance tier found."""
    [build] = [call for call in run_setup("--build-all", *sets).calls if call.endswith(" build")
               and "--profile api" in call]
    profiles = build.split("compose ", 1)[1].rsplit(" build", 1)[0].split()
    empty = tmp_path / "empty.env"
    empty.write_text("")
    resolved = subprocess.run(["docker", "compose", "--env-file", str(empty), *profiles, "config", "--quiet"],
                              cwd=REPO_ROOT, capture_output=True, text=True, timeout=60)
    assert resolved.returncode == 0, f"{' '.join(profiles)}: {resolved.stderr}"
