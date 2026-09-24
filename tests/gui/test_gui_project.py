"""The GUI's configuration, checked for the things that rot quietly.

None of this needs npm, Docker or a network: it reads the files. What it
looks for is the class of mistake that does not show up until something is
deployed -- a path the dev server proxies and nginx does not, a dependency
that floated to a new major version between two clones, a coverage threshold
quietly lowered, an event stream that works locally and is buffered in
production.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from nl2sql_agent import __version__

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GUI = REPO_ROOT / "gui"


@pytest.fixture(scope="module")
def package_json() -> dict:
    return json.loads((GUI / "package.json").read_text())


@pytest.fixture(scope="module")
def tsconfig() -> dict:
    # tsconfig.json permits comments; this one has none, but strip trailing
    # commas defensively rather than depending on that staying true.
    text = (GUI / "tsconfig.json").read_text()
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", text))


@pytest.fixture(scope="module")
def vite_config() -> str:
    return (GUI / "vite.config.ts").read_text()


@pytest.fixture(scope="module")
def vitest_config() -> str:
    return (GUI / "vitest.config.ts").read_text()


@pytest.fixture(scope="module")
def nginx_template() -> str:
    return (GUI / "nginx.conf.template").read_text()


@pytest.fixture(scope="module")
def dockerfile() -> str:
    return (GUI / "Dockerfile").read_text()


@pytest.fixture(scope="module")
def client_ts() -> str:
    return (GUI / "src" / "api" / "client.ts").read_text()


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def test_every_dependency_is_pinned_exactly(package_json: dict):
    """A range makes two clones of this repository different programs.

    The agent's requirements.txt pins the same way and for the same reason:
    a bug that only appears on one machine is the most expensive kind.
    """
    floating = {
        name: spec
        for group in ("dependencies", "devDependencies")
        for name, spec in package_json.get(group, {}).items()
        if not re.fullmatch(r"\d+\.\d+\.\d+", spec)
    }
    assert floating == {}


def test_the_lockfile_is_committed():
    """`npm ci` needs it, and so does a reproducible image build."""
    assert (GUI / "package-lock.json").is_file()


def test_the_gui_version_follows_the_agent(package_json: dict):
    assert package_json["version"] == __version__


def test_the_scripts_a_contributor_needs_are_all_there(package_json: dict):
    assert {"dev", "build", "test", "typecheck"} <= set(package_json["scripts"])


def test_the_build_refuses_code_that_does_not_typecheck(package_json: dict):
    """`vite build` alone strips types without checking them."""
    assert "tsc --noEmit" in package_json["scripts"]["build"]


# ---------------------------------------------------------------------------
# TypeScript and the test suite
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "option",
    ["strict", "noUncheckedIndexedAccess", "exactOptionalPropertyTypes", "noUnusedLocals"],
)
def test_the_compiler_is_strict(tsconfig: dict, option: str):
    assert tsconfig["compilerOptions"][option] is True


def test_the_coverage_thresholds_are_all_a_hundred(vitest_config: str):
    """Matching the Python side. A threshold below 100 is a number nobody
    looks at; a failing build is read immediately."""
    thresholds = re.search(r"thresholds: \{(.*?)\}", vitest_config, re.DOTALL)
    assert thresholds
    measured = dict(re.findall(r"(\w+): (\d+)", thresholds.group(1)))
    assert measured == {
        "statements": "100",
        "branches": "100",
        "functions": "100",
        "lines": "100",
    }


def test_only_the_entry_point_is_left_out_of_coverage(vitest_config: str):
    exclude = re.search(r"exclude: \[([^\]]*)\]", vitest_config)
    assert exclude
    assert re.findall(r'"([^"]+)"', exclude.group(1)) == ["src/main.tsx"]


# ---------------------------------------------------------------------------
# The proxy, in both of the places it exists
# ---------------------------------------------------------------------------


def _client_paths(client_ts: str) -> set[str]:
    """The API paths the client actually asks for, as prefixes."""
    literal = set(re.findall(r'request<[^>]*>\(\s*[`"]([^`"$]+)', client_ts))
    # `eventsUrl` follows the link the server supplied, which is always under
    # /v1/questions; the client never builds it.
    literal.add("/v1/questions")
    return {"/" + path.lstrip("/").split("/")[0] for path in literal}


def test_the_dev_server_proxies_every_path_the_client_asks_for(vite_config: str, client_ts: str):
    proxied = set(re.findall(r'"(/[\w./]+)"', re.search(r"API_PATHS = \[([^\]]+)\]", vite_config).group(1)))
    missing = {path for path in _client_paths(client_ts) if path not in proxied}
    assert missing == set(), f"npm run dev would 404 on {sorted(missing)}"


def test_nginx_proxies_every_path_the_client_asks_for(nginx_template: str, client_ts: str):
    """The failure this prevents is the worst kind: works in development,
    404s in the image, and only for the paths nobody clicked before release.
    """
    location = re.search(r"location ~ (\S+) \{", nginx_template)
    assert location, "the API location block has changed shape"
    pattern = re.compile(location.group(1).replace(r"\.", r"\."))

    for path in sorted(_client_paths(client_ts)):
        probe = "/openapi.json" if path == "/openapi.json" else f"{path}/anything"
        assert pattern.match(probe), f"nginx would not proxy {probe}"


def test_the_dev_proxy_and_nginx_agree_on_what_the_api_owns(vite_config: str, nginx_template: str):
    dev = set(re.findall(r'"(/[\w./]+)"', re.search(r"API_PATHS = \[([^\]]+)\]", vite_config).group(1)))
    served = re.search(r"location ~ \^/\(([^)]+)\)", nginx_template).group(1)
    nginx = {"/" + name.replace("\\", "") for name in served.split("|")}
    assert dev == nginx


def test_the_dev_proxy_does_not_verify_the_development_certificate(vite_config: str):
    """The self-signed certificate is the point of the development one, and
    this hop is a loopback on the developer's own machine."""
    assert 'NL2SQL_API_TLS_VERIFY ?? "false"' in vite_config


def test_the_dev_proxy_adds_the_token_so_the_browser_never_holds_it(vite_config: str):
    assert 'setHeader("Authorization", `Bearer ${token}`)' in vite_config


# ---------------------------------------------------------------------------
# nginx
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def api_location(nginx_template: str) -> str:
    block = re.search(r"location ~ [^\n]+\{(.*?)\n    \}", nginx_template, re.DOTALL)
    assert block
    return block.group(1)


def test_the_event_stream_is_not_buffered(api_location: str):
    """Three settings, and every one of them fails silently.

    nginx buffers a proxied response by default and compresses it if it can,
    either of which holds the whole progress stream until the answer is
    finished -- which is the one thing the stream exists to avoid, and it
    looks like a spinner that never moves rather than like an error.
    """
    assert "proxy_buffering off;" in api_location
    assert "proxy_cache off;" in api_location
    assert "gzip off;" in api_location


def test_a_reconnecting_browser_can_resume_where_it_left_off(api_location: str):
    """`Last-Event-ID` is how the server knows to resume rather than replay,
    and a proxy that drops it turns every reconnect into a redraw."""
    assert "proxy_set_header Last-Event-ID $http_last_event_id;" in api_location


def test_the_proxy_outlasts_a_question(api_location: str):
    assert "proxy_read_timeout ${API_READ_TIMEOUT};" in api_location


def test_the_upstream_is_resolved_per_request(api_location: str):
    """A literal upstream is resolved once, while nginx parses its config.

    That stops the GUI starting when the API is not up yet, and leaves it
    talking to a stale address after the API is restarted onto a new one.
    """
    assert "resolver ${GUI_RESOLVER}" in api_location
    assert "set $upstream ${API_UPSTREAM};" in api_location
    assert "proxy_pass $upstream$request_uri;" in api_location


def test_the_certificate_block_is_written_at_start_up(api_location: str):
    """It has to be, because it is wrong when the upstream is plain HTTP."""
    assert "include /etc/nginx/nl2sql-upstream-tls.conf;" in api_location
    assert "proxy_ssl_verify" not in api_location


def test_the_page_is_served_from_any_path_but_the_assets_are_immutable(nginx_template: str):
    assert "try_files $uri $uri/ /index.html;" in nginx_template
    assert 'add_header Cache-Control "public, immutable";' in nginx_template
    assert 'add_header Cache-Control "no-cache";' in nginx_template


# ---------------------------------------------------------------------------
# The start-up script nginx sources
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def config_envsh() -> Path:
    return GUI / "10-nl2sql-config.envsh"


def _env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    """The environment nginx would source this script in, redirected to a
    temporary directory so the test does not write to /etc."""
    env = {
        "PATH": "/usr/bin:/bin",
        "NGINX_UPSTREAM_TLS_CONF": str(tmp_path / "upstream-tls.conf"),
        "API_UPSTREAM": "https://nl2sql-api:8443",
        "API_CACERT": "/etc/nl2sql/tls/server.crt",
        "API_SSL_NAME": "nl2sql-api",
    }
    env.update(overrides)
    return env


def _source(script: Path, env: dict[str, str], then: str = "true") -> subprocess.CompletedProcess:
    command = ["sh", "-c", f". {script}; {then}"]
    if os.environ.get("NL2SQL_SHELL_TRACE"):
        # See tests/shell_coverage.py. `sh -x` rather than `bash -x`: this
        # script is sourced by the nginx image's entrypoint, which is not
        # bash, and it is run here the same way.
        #
        # The name is written in rather than derived: POSIX sh has no
        # BASH_SOURCE, and `$0` under `sh -c` is the shell. Only one script
        # is sourced here, so the label is known without asking.
        env = {**env, "PS4": f"+@{script.name}@${{LINENO}}@ "}
        command = ["sh", "-x", "-c", f". {script}; {then}"]
    result = subprocess.run(command, capture_output=True, text=True, env=env)
    directory = os.environ.get("NL2SQL_SHELL_TRACE")
    if directory:
        with open(os.path.join(directory, "trace.log"), "a") as handle:
            handle.write(result.stderr)
    return result


def test_the_start_up_script_is_valid_shell(config_envsh: Path):
    result = subprocess.run(["sh", "-n", str(config_envsh)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_the_start_up_script_is_executable(config_envsh: Path):
    assert config_envsh.stat().st_mode & 0o111


def test_it_is_sourced_rather_than_run(config_envsh: Path, dockerfile: str):
    """The `.envsh` extension is what tells the nginx entrypoint to source it,
    and an exported variable has to survive into the envsubst step after."""
    assert config_envsh.suffix == ".envsh"
    assert "/docker-entrypoint.d/10-nl2sql-config.envsh" in dockerfile


@pytest.mark.parametrize(
    ("token", "expected"),
    [("s3cret", "Bearer s3cret"), ("", ""), (None, "")],
)
def test_no_token_means_no_authorization_header(config_envsh: Path, token, expected, tmp_path):
    """nginx omits a header whose value is empty, so "no token" has to
    produce an empty string rather than the word "Bearer" on its own."""
    env = _env(tmp_path, API_UPSTREAM="http://api:8443")
    if token is not None:
        env["API_TOKEN"] = token
    result = _source(config_envsh, env, 'printf "%s" "$API_AUTH_HEADER"')
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected


def test_an_https_upstream_is_verified_against_the_certificate(config_envsh: Path, tmp_path: Path):
    cacert = tmp_path / "server.crt"
    cacert.write_text("not really a certificate, but it is readable")
    env = _env(
        tmp_path,
        API_UPSTREAM="https://nl2sql-api:8443",
        API_CACERT=str(cacert),
        API_SSL_NAME="nl2sql-api",
    )

    assert _source(config_envsh, env).returncode == 0
    written = (tmp_path / "upstream-tls.conf").read_text()
    assert "proxy_ssl_verify on;" in written
    assert f"proxy_ssl_trusted_certificate {cacert};" in written
    assert "proxy_ssl_name nl2sql-api;" in written


def test_an_https_upstream_with_no_certificate_refuses_to_start(config_envsh: Path, tmp_path: Path):
    """Rather than quietly proxying without verifying, or dying with nginx's
    own BIO error, which says nothing about what to do."""
    env = _env(tmp_path, API_UPSTREAM="https://nl2sql-api:8443", API_CACERT=str(tmp_path / "absent.crt"))
    result = _source(config_envsh, env)

    assert result.returncode != 0
    assert "there is no readable" in result.stderr
    assert "certificate at API_CACERT" in result.stderr
    # The likeliest cause, named: under compose the file comes from the
    # volume the API writes on its first start.
    assert "comes from the apitls volume, which the API" in result.stderr
    assert "started yet, or the volume is not mounted" in result.stderr


def test_a_plain_http_upstream_needs_no_certificate(config_envsh: Path, tmp_path: Path):
    """API_TLS_ENABLED=false is a supported deployment -- behind something
    that terminates TLS itself -- and there is no certificate anywhere in it."""
    env = _env(tmp_path, API_UPSTREAM="http://nl2sql-api:8443", API_CACERT=str(tmp_path / "absent.crt"))

    assert _source(config_envsh, env).returncode == 0
    written = (tmp_path / "upstream-tls.conf").read_text()
    assert "proxy_ssl_verify" not in written
    assert "clear text" in written


# ---------------------------------------------------------------------------
# The image
# ---------------------------------------------------------------------------


def _stages(dockerfile: str) -> list[tuple[str, str]]:
    """(image, stage name) per FROM, with any `--platform=` flag skipped."""
    return re.findall(r"^FROM (?:--platform=\S+ )?(\S+)(?: AS (\S+))?", dockerfile, re.MULTILINE)


def test_the_toolchain_does_not_ship(dockerfile: str):
    """Two stages exist so the result has no node, no npm and no
    node_modules in it -- only the few hundred kilobytes they produced."""
    stages = _stages(dockerfile)
    assert len(stages) == 2
    assert stages[0][0].startswith("node:")
    assert stages[1][0].startswith("nginx:")


def test_both_base_images_are_pinned_to_a_version(dockerfile: str):
    for image, _ in _stages(dockerfile):
        assert ":" in image and not image.endswith(":latest")


def test_the_build_stage_is_not_emulated(dockerfile: str):
    """Its output is JavaScript, CSS and HTML -- the same bytes whatever the
    image runs on. Without `$BUILDPLATFORM` a multi-arch publish runs
    `npm ci` under QEMU once per architecture, for an identical result.
    """
    assert re.search(r"^FROM --platform=\$BUILDPLATFORM node:", dockerfile, re.MULTILINE)


def test_the_serving_stage_is_built_for_the_target(dockerfile: str):
    """nginx is a binary, so that half must not be pinned to the builder."""
    nginx = re.search(r"^FROM (?:--platform=\S+ )?(nginx:\S+)", dockerfile, re.MULTILINE)
    assert nginx, "the serving stage is missing"
    assert "--platform" not in nginx.group(0)


def test_the_dependency_layer_is_cached_on_the_lockfile(dockerfile: str):
    """Copying the sources first would rebuild the whole tree on every edit."""
    lock = dockerfile.index("package-lock.json")
    sources = dockerfile.index("gui/src/")
    assert lock < sources


def test_envsubst_is_filtered_to_this_project_s_variables(dockerfile: str):
    """Unfiltered, it would also substitute nginx's own $uri and $host."""
    assert 'NGINX_ENVSUBST_FILTER="^(API_|GUI_)"' in dockerfile


def test_no_token_is_baked_into_the_image(dockerfile: str):
    """A token in an ENV is a token in every layer of the image."""
    assert not re.search(r"^\s*API_TOKEN=", dockerfile, re.MULTILINE)


def test_the_image_says_when_it_is_ready(dockerfile: str):
    assert "HEALTHCHECK" in dockerfile


def test_the_image_label_says_the_version_this_actually_is(dockerfile: str, package_json: dict):
    """The same rule the agent image is held to: `__version__`, the image
    label and the published tag all say the same thing, because a bump that
    misses one ships an image that lies about itself.
    """
    label = re.search(r'org\.opencontainers\.image\.version="([^"]+)"', dockerfile)
    assert label, "the GUI image no longer labels its version"
    assert label.group(1) == package_json["version"] == __version__


def test_the_published_gui_tag_names_a_version_this_actually_is():
    """`setup.sh` pins the tag it pulls. A tag that is not a prefix of the
    version pulls an image that is not this checkout."""
    setup_sh = (REPO_ROOT / "setup.sh").read_text()
    match = re.search(r'^GUI_TAG="v([\d_]+)"', setup_sh, re.MULTILINE)
    assert match, "setup.sh no longer pins a GUI tag of the form vN or vN_M"
    tagged = match.group(1).split("_")
    assert tagged == __version__.split(".")[: len(tagged)], (
        f"setup.sh pulls the GUI at v{match.group(1)}, but this package is {__version__}"
    )


def test_the_agent_and_the_gui_are_published_at_the_same_tag():
    """They are built from one checkout and only tested together. Two tags
    that can drift apart is two versions of "which GUI goes with which API".
    """
    setup_sh = (REPO_ROOT / "setup.sh").read_text()
    agent = re.search(r'^AGENT_TAG="(\S+)"', setup_sh, re.MULTILINE)
    gui = re.search(r'^GUI_TAG="(\S+)"', setup_sh, re.MULTILINE)
    assert agent and gui
    assert agent.group(1) == gui.group(1)


# ---------------------------------------------------------------------------
# Everything around it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["gui/node_modules", "gui/dist"])
def test_build_artefacts_are_not_committed_or_sent_to_the_daemon(path: str):
    """node_modules is tens of thousands of files. Sending it as build
    context is slow, and committing it is worse."""
    assert f"{path}/" in (REPO_ROOT / ".gitignore").read_text()
    assert path in (REPO_ROOT / ".dockerignore").read_text()
