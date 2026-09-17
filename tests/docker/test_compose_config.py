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
    assert set(agent_profile_config["services"]) == {"postgres", "vectordb", "agent"}


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
    # Reuses the volume the RAG pipeline populated, so an existing knowledge
    # base is picked up instead of being re-seeded from the image.
    assert volumes["vectordata"]["name"] == "nl2sql-rag-vectordb-data"


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
