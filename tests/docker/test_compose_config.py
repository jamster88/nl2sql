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

pytestmark = pytest.mark.docker

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _compose_config(tmp_path: Path, *, profile: str | None = None, env: dict | None = None) -> dict:
    empty_env_file = tmp_path / "empty.env"
    empty_env_file.write_text("")

    cmd = ["docker", "compose", "--env-file", str(empty_env_file)]
    if profile:
        cmd += ["--profile", profile]
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


def test_all_services_present_under_the_agent_profile(agent_profile_config: dict):
    assert set(agent_profile_config["services"]) == {
        "postgres", "vectordb", "chunkdb", "agent"
    }


def test_postgres_service_shape(agent_profile_config: dict):
    postgres = agent_profile_config["services"]["postgres"]
    assert postgres["build"]["dockerfile"] == "docker/Dockerfile"
    assert postgres["container_name"] == "nl2sql-postgres"
    assert postgres["restart"] == "unless-stopped"
    assert "pg_isready" in " ".join(postgres["healthcheck"]["test"])

    [volume] = postgres["volumes"]
    assert volume["source"] == "pgdata"
    # Must match ENV PGDATA in docker/Dockerfile, or the image's baked data
    # is invisible to the named volume that's supposed to seed from it.
    assert volume["target"] == "/var/lib/pgdata"

    [port] = postgres["ports"]
    assert port["target"] == 5432


def test_vectordb_service_shape(agent_profile_config: dict):
    vectordb = agent_profile_config["services"]["vectordb"]
    assert vectordb["container_name"] == "nl2sql-vectordb"
    assert vectordb["restart"] == "unless-stopped"
    assert "pg_isready" in " ".join(vectordb["healthcheck"]["test"])

    [volume] = vectordb["volumes"]
    assert volume["source"] == "vectordata"
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
    creates (docker/reader_role.sql), never as the owner that loaded the data.
    """
    postgres_args = agent_profile_config["services"]["postgres"]["build"]["args"]
    agent_env = agent_profile_config["services"]["agent"]["environment"]
    reader, password = postgres_args["DB_READER"], postgres_args["DB_READER_PASSWORD"]
    assert f"{reader}:{password}@postgres:5432/{postgres_args['DB_NAME']}" in agent_env["DATABASE_URL"]
    assert reader != postgres_args["DB_USER"]
    assert f"{postgres_args['DB_USER']}:" not in agent_env["DATABASE_URL"]


def test_the_owners_credentials_never_reach_the_agent_container(agent_profile_config: dict):
    """Least privilege at the compose level: the only Postgres identity in the
    agent's environment is the reader. The owner's password is a build arg
    of the postgres service and nothing else.
    """
    postgres_args = agent_profile_config["services"]["postgres"]["build"]["args"]
    agent_env = agent_profile_config["services"]["agent"]["environment"]
    owner, owner_password = postgres_args["DB_USER"], postgres_args["DB_PASSWORD"]
    for key, value in agent_env.items():
        assert f"{owner}:" not in str(value), f"{key} carries the owner's login"
        assert f":{owner_password}@" not in str(value), f"{key} carries the owner's password"


def test_the_reader_role_can_be_renamed_from_the_environment(tmp_path_factory):
    config = _compose_config(
        tmp_path_factory.mktemp("compose"),
        profile="agent",
        env={"POSTGRES_READER_USER": "ro", "POSTGRES_READER_PASSWORD": "secret"},
    )
    assert config["services"]["postgres"]["build"]["args"]["DB_READER"] == "ro"
    assert "ro:secret@postgres:5432/" in config["services"]["agent"]["environment"]["DATABASE_URL"]


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
        env={"VECTOR_DB_USER": "vuser", "VECTOR_DB_PASSWORD": "vpass", "VECTOR_DB_NAME": "vectors_db"},
    )
    healthcheck = " ".join(config["services"]["vectordb"]["healthcheck"]["test"])
    assert "-U vuser" in healthcheck
    assert "-d vectors_db" in healthcheck
    assert (
        config["services"]["agent"]["environment"]["VECTOR_DB_URL"]
        == "postgresql+psycopg://vuser:vpass@vectordb:5432/vectors_db"
    )


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
    config_py = (REPO_ROOT / "agent" / source).with_suffix(".py").read_text()
    for name in agent_profile_config["services"]["agent"]["environment"]:
        assert f'"{name}"' in config_py, f"compose sets {name}, but config.py never reads it"


# ---------------------------------------------------------------------------
# v3: the context store
# ---------------------------------------------------------------------------


def test_the_agent_is_pointed_at_the_context_store(agent_profile_config: dict):
    """The example retriever reads golden pairs and runs BM25 there, so the
    agent needs the URL as well as the dependency -- one without the other
    fails at the first question rather than at startup.
    """
    env = agent_profile_config["services"]["agent"]["environment"]
    assert env["CONTEXT_DB_URL"] == (
        "postgresql+psycopg://ragproc:ragproc@chunkdb:5432/nl2sql_chunks"
    )


def test_the_agent_waits_for_every_database_to_be_healthy(agent_profile_config: dict):
    depends = agent_profile_config["services"]["agent"]["depends_on"]
    assert set(depends) == {"postgres", "vectordb", "chunkdb"}
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
    ):
        assert flag in env, f"{flag} is not passed to the agent container"
    assert env["SCHEMA_RETRIEVAL"] == "vector"
    assert env["MAX_ATTEMPTS"] == "4"


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


def _settings_read() -> set[str]:
    """Every environment variable config.py reads, by name."""
    source = Path(REPO_ROOT / "agent" / "nl2sql_agent" / "config.py").read_text()
    return set(re.findall(r'_env(?:_str|_bool|_int|_float)\(\s*"([A-Z_]+)"', source))


def test_every_setting_the_agent_reads_can_be_set_through_compose(agent_profile_config: dict):
    """The mirror of the test above, and the one that was missing. A knob the
    README documents but compose never forwards cannot be set on the
    containerised agent at all, which is the way almost everyone runs it.
    """
    env = set(agent_profile_config["services"]["agent"]["environment"])
    missing = sorted(_settings_read() - env)
    assert missing == [], f"config.py reads these, but compose never passes them: {missing}"


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
    [volume] = [v for v in agent_profile_config["services"]["chunkdb"]["volumes"]]
    assert volume["source"] == "chunkdata"
    assert volume["target"] == "/var/lib/pgdata"


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
