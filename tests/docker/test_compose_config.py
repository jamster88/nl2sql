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
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.docker

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _compose_config(tmp_path: Path, *, profile: str | None = None, env: dict | None = None) -> dict:
    empty_env_file = tmp_path / "empty.env"
    empty_env_file.write_text("")

    cmd = ["docker", "compose", "--env-file", str(empty_env_file)]
    if profile:
        cmd += ["--profile", profile]
    cmd += ["config", "--format", "json"]

    run_env = dict(os.environ)
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


def test_agent_database_url_points_at_the_compose_postgres_service_and_matches_its_credentials(
    agent_profile_config: dict,
):
    postgres_args = agent_profile_config["services"]["postgres"]["build"]["args"]
    agent_env = agent_profile_config["services"]["agent"]["environment"]
    user, password, name = postgres_args["DB_USER"], postgres_args["DB_PASSWORD"], postgres_args["DB_NAME"]
    assert f"{user}:{password}@postgres:5432/{name}" in agent_env["DATABASE_URL"]


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


def test_multi_shot_defaults_to_off_in_compose(agent_profile_config: dict):
    """Retrieval on, prompting off: the examples are fetched and inspectable
    but do not steer generation until the next step turns this on.
    """
    env = agent_profile_config["services"]["agent"]["environment"]
    assert env["EXAMPLES_ENABLED"] == "true"
    assert env["MULTI_SHOT_ENABLED"] == "false"
