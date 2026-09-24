"""The GUI as compose resolves it.

Behind `--run-docker` for the same reason as the other compose tests:
`docker compose config` only parses and resolves, but it needs a working
`docker` CLI.

The property that carries the weight here is the one that cannot be seen by
reading either file alone -- the proxy verifies the API's certificate against
a name, and that name has to be one the API's *generated* certificate covers.
Those two settings live in two services, and nothing but a test connects them.
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
GUI = REPO_ROOT / "gui"


def _compose_config(tmp_path: Path, *, env: dict | None = None) -> dict:
    empty_env_file = tmp_path / "empty.env"
    empty_env_file.write_text("")
    cmd = [
        "docker", "compose", "--env-file", str(empty_env_file),
        "--profile", "api", "--profile", "gui", "config", "--format", "json",
    ]
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
def gui(config: dict) -> dict:
    assert "gui" in config["services"], "the gui service is missing from docker-compose.yml"
    return config["services"]["gui"]


# ---------------------------------------------------------------------------
# It only exists when it is asked for
# ---------------------------------------------------------------------------


def test_the_gui_is_hidden_unless_it_is_asked_for(tmp_path_factory):
    """Most people ask questions from a terminal. A port nobody asked to be
    opened should not be."""
    tmp_path = tmp_path_factory.mktemp("noprofile")
    empty = tmp_path / "empty.env"
    empty.write_text("")
    result = subprocess.run(
        ["docker", "compose", "--env-file", str(empty), "config", "--format", "json"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "gui" not in json.loads(result.stdout)["services"]


def test_it_waits_for_the_api_to_be_healthy(gui: dict):
    """A GUI whose API is not up yet is a page that loads and then fails,
    which reads as the application being broken."""
    assert gui["depends_on"]["api"]["condition"] == "service_healthy"


# ---------------------------------------------------------------------------
# The certificate, across two services
# ---------------------------------------------------------------------------


def test_the_proxy_verifies_against_a_name_the_api_s_certificate_covers(config: dict, gui: dict):
    """The one thing neither file can be right about on its own.

    The API generates a certificate for API_TLS_HOSTNAMES; the GUI verifies
    the connection against API_SSL_NAME. If someone renames the service, or
    trims the hostname list, the two drift apart and every request through
    the proxy becomes a 502 that names neither setting.
    """
    covered = set(config["services"]["api"]["environment"]["API_TLS_HOSTNAMES"].split(","))
    verified = gui["environment"]["API_SSL_NAME"]
    assert verified in covered, (
        f"the GUI verifies the API as {verified!r}, which is not in the API's "
        f"generated certificate ({sorted(covered)})"
    )


def test_the_proxy_reaches_the_api_by_the_same_name_it_verifies(gui: dict):
    assert gui["environment"]["API_SSL_NAME"] in gui["environment"]["API_UPSTREAM"]


def test_it_can_read_the_certificate_the_api_wrote(gui: dict, config: dict):
    """From the volume the API writes it into -- the same arrangement the
    curl-only test client uses, and read-only, because it only reads it."""
    mounts = {mount["target"]: mount for mount in gui["volumes"]}
    certificate_dir = str(Path(gui["environment"]["API_CACERT"]).parent)

    assert certificate_dir in mounts
    assert mounts[certificate_dir]["read_only"] is True
    assert mounts[certificate_dir]["source"] == config["services"]["api"]["volumes"][0]["source"]


def test_the_api_writes_that_volume_rather_than_only_reading_it(config: dict):
    api_mounts = {mount["target"]: mount for mount in config["services"]["api"]["volumes"]}
    assert api_mounts["/etc/nl2sql/tls"].get("read_only") is not True


# ---------------------------------------------------------------------------
# The token
# ---------------------------------------------------------------------------


def test_the_gui_is_given_the_same_token_as_the_server(tmp_path_factory):
    """Holding it is the whole job. A GUI with a different token, or none,
    produces a page where every request is a 401."""
    config = _compose_config(
        tmp_path_factory.mktemp("token"), env={"API_TOKEN": "a-shared-secret"}
    )
    assert config["services"]["gui"]["environment"]["API_TOKEN"] == "a-shared-secret"
    assert config["services"]["api"]["environment"]["API_TOKEN"] == "a-shared-secret"


def test_no_token_is_the_default(gui: dict):
    assert gui["environment"]["API_TOKEN"] == ""


# ---------------------------------------------------------------------------
# The socket
# ---------------------------------------------------------------------------


def test_the_published_port_follows_the_configured_one(tmp_path_factory):
    """Both halves: nginx has to listen on what compose publishes, or the
    port is forwarded to nothing."""
    config = _compose_config(tmp_path_factory.mktemp("port"), env={"GUI_PORT": "9123"})
    gui = config["services"]["gui"]

    assert gui["environment"]["GUI_PORT"] == "9123"
    assert [(p["published"], p["target"]) for p in gui["ports"]] == [("9123", 9123)]


def test_the_default_port_is_the_documented_one(gui: dict):
    assert gui["environment"]["GUI_PORT"] == "8080"
    assert [(p["published"], p["target"]) for p in gui["ports"]] == [("8080", 8080)]


# ---------------------------------------------------------------------------
# Nothing set that is not read, nothing read that cannot be set
# ---------------------------------------------------------------------------


def _template_variables() -> set[str]:
    """Every ${NAME} the nginx template and its start-up script substitute."""
    sources = (GUI / "nginx.conf.template").read_text() + (GUI / "10-nl2sql-config.envsh").read_text()
    names = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)(?::-)?", sources))
    # Computed by the start-up script from API_TOKEN rather than set by
    # anyone, and deliberately not settable: a caller who set it directly
    # would bypass the empty-token handling.
    return names - {"API_AUTH_HEADER", "NGINX_UPSTREAM_TLS_CONF"}


def test_every_setting_the_proxy_reads_can_be_set_through_compose(gui: dict):
    unsettable = _template_variables() - set(gui["environment"])
    assert unsettable == set(), (
        f"the proxy reads {sorted(unsettable)}, which compose never passes, so "
        "they cannot be changed without rebuilding the image"
    )


def test_every_variable_compose_sets_is_one_the_proxy_reads(gui: dict):
    unread = set(gui["environment"]) - _template_variables()
    assert unread == set(), (
        f"compose sets {sorted(unread)} on the GUI, which nothing in it reads"
    )


def test_a_plain_http_api_can_be_configured_without_a_rebuild(tmp_path_factory):
    """API_TLS_ENABLED=false is a supported deployment, and the GUI has to be
    able to follow it -- the upstream scheme is what turns certificate
    verification off, so it has to be settable."""
    config = _compose_config(
        tmp_path_factory.mktemp("http"),
        env={"GUI_API_UPSTREAM": "http://nl2sql-api:8443"},
    )
    assert config["services"]["gui"]["environment"]["API_UPSTREAM"] == "http://nl2sql-api:8443"


def test_it_can_be_built_here_as_well_as_pulled(gui: dict):
    """Both, like the agent service: the image name is what `setup.sh --gui`
    pins to the published tag, and the build context is the fallback for a
    clone that cannot reach the registry -- or does not want to.
    """
    assert gui["build"]["dockerfile"].endswith("gui/Dockerfile")
    assert gui["image"] == "nl2sql-gui:latest"


def test_a_pinned_gui_image_is_what_compose_runs(tmp_path_factory):
    config = _compose_config(
        tmp_path_factory.mktemp("pinned"),
        env={"GUI_IMAGE_NAME": "mcfaddja/nl2sql-gui", "GUI_IMAGE_TAG": "v4_2"},
    )
    assert config["services"]["gui"]["image"] == "mcfaddja/nl2sql-gui:v4_2"
