"""The review GUI's project, its nginx config, and the script nginx sources.

The same shape of test as `tests/gui/test_gui_project.py`, and mostly for the
same reasons. Two differences are specific to this application and are what
most of the file is about:

* **The token this proxy holds can rewrite the golden question set.** That it
  never reaches the browser is not a nicety here, and the empty case -- no
  token configured -- has to send no header rather than the word "Bearer".
* **There is no event stream**, so the buffering rules the web GUI needs are
  absent and a long read timeout on promotion is present instead. Promotion
  runs both RAG loaders; a proxy that gives up first cuts off a write that
  is still happening.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GUI = REPO_ROOT / "review" / "gui"


@pytest.fixture(scope="module")
def package_json() -> dict:
    return json.loads((GUI / "package.json").read_text())


@pytest.fixture(scope="module")
def tsconfig() -> dict:
    text = (GUI / "tsconfig.json").read_text()
    return json.loads(re.sub(r"//.*", "", text))


@pytest.fixture(scope="module")
def vitest_config() -> str:
    return (GUI / "vitest.config.ts").read_text()


@pytest.fixture(scope="module")
def vite_config() -> str:
    return (GUI / "vite.config.ts").read_text()


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
# The project
# ---------------------------------------------------------------------------


def test_every_dependency_is_pinned_exactly(package_json: dict):
    """A range means two clones build different bundles from one lockfile
    bump, and the first sign is a bug that reproduces on one machine."""
    for section in ("dependencies", "devDependencies"):
        for name, version in package_json[section].items():
            assert re.fullmatch(r"\d+\.\d+\.\d+", version), f"{name} is {version}, not pinned"


def test_the_lockfile_is_committed():
    assert (GUI / "package-lock.json").is_file()


def test_it_is_a_separate_project_from_the_web_gui(package_json: dict):
    """Separate on purpose, and physically rather than by convention.

    Two Vite entry points in one project share a build, and the public GUI's
    image would then serve the interface that rewrites the golden question
    set to anyone who could reach it.
    """
    web = json.loads((REPO_ROOT / "gui" / "package.json").read_text())
    assert package_json["name"] != web["name"]
    assert not (REPO_ROOT / "gui" / "src" / "review").exists()


def test_the_scripts_a_contributor_needs_are_all_there(package_json: dict):
    assert {"dev", "build", "test", "typecheck"} <= set(package_json["scripts"])


def test_the_build_refuses_code_that_does_not_typecheck(package_json: dict):
    """`vite build` strips types without checking them."""
    assert "tsc --noEmit" in package_json["scripts"]["build"]


def test_the_test_script_measures_coverage(package_json: dict):
    assert "--coverage" in package_json["scripts"]["test"]


@pytest.mark.parametrize("option", ["strict", "noUncheckedIndexedAccess", "exactOptionalPropertyTypes"])
def test_the_compiler_is_strict(tsconfig: dict, option: str):
    assert tsconfig["compilerOptions"][option] is True


def test_the_coverage_thresholds_are_all_a_hundred(vitest_config: str):
    thresholds = re.search(r"thresholds: \{([^}]*)\}", vitest_config).group(1)
    found = dict(re.findall(r"(\w+): (\d+)", thresholds))
    assert found == {"statements": "100", "branches": "100", "functions": "100", "lines": "100"}


def test_only_the_entry_point_is_left_out_of_coverage(vitest_config: str):
    excluded = re.search(r"exclude: \[([^\]]*)\]", vitest_config).group(1)
    assert re.findall(r'"(src/[^"]+)"', excluded) == ["src/main.tsx"]


# ---------------------------------------------------------------------------
# The two proxies, which have to agree
# ---------------------------------------------------------------------------


def _api_paths(text: str) -> set[str]:
    return set(re.findall(r'"(/v1|/healthz|/readyz|/openapi\.json)"', text))


def test_the_dev_server_proxies_every_path_the_client_asks_for(vite_config: str, client_ts: str):
    """Anything the client requests and the dev server does not proxy is a
    request answered by Vite with the page, which parses as JSON never."""
    asked = {f"/{path.split('/')[1]}" for path in re.findall(r'"(/v1/[^"`$]*|/readyz)"', client_ts)}
    proxied = _api_paths(vite_config)
    assert asked <= proxied, f"the dev proxy does not cover {asked - proxied}"


def test_nginx_proxies_the_same_paths_the_dev_server_does(nginx_template: str, vite_config: str):
    """The two are the same three rules in two syntaxes, and a path proxied
    in development but not in the image is a bug nobody meets until deploy.
    """
    location = re.search(r"location ~ \^/\(([^)]*)\)", nginx_template).group(1)
    served = {part.replace("\\", "") for part in location.split("|")}
    assert served == {"v1", "healthz", "readyz", "openapi.json"}
    assert {f"/{name.split('.')[0]}" for name in served} <= {
        path.split(".")[0] for path in _api_paths(vite_config)
    }


def test_the_dev_proxy_adds_the_token_so_the_browser_never_holds_it(vite_config: str):
    assert "REVIEW_TOKEN" in vite_config
    assert 'setHeader("Authorization"' in vite_config


def test_the_dev_proxy_does_not_verify_the_development_certificate(vite_config: str):
    """The default certificate is the one the API wrote for itself, and this
    hop is a developer's own machine."""
    assert re.search(r"NL2SQL_REVIEW_TLS_VERIFY.*?\"false\"", vite_config, re.S)


# ---------------------------------------------------------------------------
# What nginx is told to do
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def api_location(nginx_template: str) -> str:
    match = re.search(r"location ~ \^/\([^)]*\) \{(.*?)\n    \}", nginx_template, re.S)
    assert match, "the proxied location block is not where it was"
    return match.group(1)


def test_the_token_is_added_by_the_proxy_not_by_the_browser(api_location: str):
    assert 'proxy_set_header Authorization "${REVIEW_AUTH_HEADER}"' in api_location


def test_the_upstream_certificate_is_verified(api_location: str):
    """This hop crosses a container network, and a proxy that trusts anything
    at the far end is a proxy that will one day trust something else."""
    assert "include /etc/nginx/nl2sql-review-upstream-tls.conf;" in api_location


def test_the_upstream_is_resolved_per_request(api_location: str):
    """nginx resolves a literal upstream while parsing its config and caches
    it for the life of the process: it refuses to start before the service is
    up, and keeps talking to a stale address after it restarts."""
    assert "resolver ${REVIEW_GUI_RESOLVER}" in api_location
    assert "set $upstream ${REVIEW_UPSTREAM};" in api_location
    assert "proxy_pass $upstream$request_uri;" in api_location


def test_the_proxy_outlasts_a_promotion(api_location: str):
    """Promotion runs both RAG loaders and re-embeds. A proxy that gives up
    first cuts off a write that is still happening."""
    assert "proxy_read_timeout ${REVIEW_READ_TIMEOUT};" in api_location
    assert "proxy_send_timeout ${REVIEW_READ_TIMEOUT};" in api_location


def test_the_default_timeout_outlasts_the_services_own_cap(dockerfile: str):
    default = re.search(r"REVIEW_READ_TIMEOUT=(\d+)s", dockerfile)
    assert default, "the image does not set a default read timeout"
    from nl2sql_review.settings import ReviewSettings

    assert int(default.group(1)) >= ReviewSettings().reload_timeout_seconds


def test_the_page_is_served_from_any_path_but_the_assets_are_immutable(nginx_template: str):
    assert "try_files $uri $uri/ /index.html;" in nginx_template
    assert re.search(r"location /assets/ \{[^}]*immutable", nginx_template, re.S)
    assert re.search(r"location = /index\.html \{[^}]*no-cache", nginx_template, re.S)


# ---------------------------------------------------------------------------
# The image
# ---------------------------------------------------------------------------


def test_no_node_survives_into_the_served_image(dockerfile: str):
    stages = [line for line in dockerfile.splitlines() if line.startswith("FROM ")]
    assert len(stages) == 2
    assert "node:" in stages[0]
    assert "nginx:" in stages[1]


def test_the_builder_is_pinned_to_the_build_platform(dockerfile: str):
    """What the build produces is JavaScript: identical whatever it runs on.
    Without this, `npm ci` runs under emulation once per architecture."""
    assert "--platform=$BUILDPLATFORM" in dockerfile


def test_the_dependency_layer_is_cached_separately(dockerfile: str):
    lock = dockerfile.index("package-lock.json")
    source = dockerfile.index("review/gui/src/")
    assert lock < source, "the lockfile must be copied before the sources"


def test_no_token_is_baked_into_the_image(dockerfile: str):
    """A token in an ENV line is a token in every layer of the image -- and
    this one can rewrite the golden question set."""
    assert not re.search(r"^\s*ENV\s+.*REVIEW_TOKEN=", dockerfile, re.M)
    assert "REVIEW_TOKEN is deliberately not given a default" in dockerfile


def test_the_default_config_is_overwritten(dockerfile: str):
    """`default.conf` ships with the nginx image and wins on port 80."""
    assert "rm -f /etc/nginx/conf.d/default.conf" in dockerfile


def test_only_this_projects_variables_are_substituted(dockerfile: str):
    """Without the filter, envsubst also eats nginx's own $uri and $host."""
    assert 'NGINX_ENVSUBST_FILTER="^REVIEW_"' in dockerfile


def test_the_image_has_a_health_check(dockerfile: str):
    assert "HEALTHCHECK" in dockerfile
    assert "/index.html" in dockerfile


# ---------------------------------------------------------------------------
# The start-up script nginx sources
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def config_envsh() -> Path:
    return GUI / "10-nl2sql-review-config.envsh"


def _env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    env = {
        "PATH": "/usr/bin:/bin",
        "NGINX_REVIEW_UPSTREAM_TLS_CONF": str(tmp_path / "upstream-tls.conf"),
        "REVIEW_UPSTREAM": "https://nl2sql-review:8444",
        "REVIEW_CACERT": "/etc/nl2sql/tls/server.crt",
        "REVIEW_SSL_NAME": "nl2sql-review",
    }
    env.update(overrides)
    return env


def _source(script: Path, env: dict[str, str], then: str = "true") -> subprocess.CompletedProcess:
    command = ["sh", "-c", f". {script}; {then}"]
    if os.environ.get("NL2SQL_SHELL_TRACE"):
        # See tests/shell_coverage.py. `sh -x` rather than `bash -x`: this is
        # sourced by the nginx image's entrypoint, which is not bash, and it
        # is run here the same way. The name is written in rather than
        # derived -- POSIX sh has no BASH_SOURCE and `$0` under `sh -c` is
        # the shell.
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
    assert "/docker-entrypoint.d/10-nl2sql-review-config.envsh" in dockerfile


def test_a_token_becomes_a_bearer_header(config_envsh: Path, tmp_path: Path):
    result = _source(
        config_envsh,
        _env(tmp_path, REVIEW_TOKEN="s3cret", REVIEW_CACERT=_a_certificate(tmp_path)),
        then='printf "%s" "$REVIEW_AUTH_HEADER"',
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "Bearer s3cret"


def test_no_token_becomes_an_empty_header_which_nginx_then_omits(
    config_envsh: Path, tmp_path: Path
):
    """The whole point of the indirection: a deployment with no token sends
    no Authorization header at all, rather than "Bearer " with nothing after
    it -- which the service would read as a presented token and refuse."""
    result = _source(
        config_envsh,
        _env(tmp_path, REVIEW_CACERT=_a_certificate(tmp_path)),
        then='printf "[%s]" "$REVIEW_AUTH_HEADER"',
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "[]"


def _a_certificate(tmp_path: Path) -> str:
    path = tmp_path / "server.crt"
    path.write_text("-----BEGIN CERTIFICATE-----\nnot a real one\n-----END CERTIFICATE-----\n")
    return str(path)


def test_an_https_upstream_writes_the_verification_block(config_envsh: Path, tmp_path: Path):
    written = tmp_path / "upstream-tls.conf"
    result = _source(config_envsh, _env(tmp_path, REVIEW_CACERT=_a_certificate(tmp_path)))

    assert result.returncode == 0, result.stderr
    text = written.read_text()
    assert "proxy_ssl_verify on;" in text
    assert "proxy_ssl_name nl2sql-review;" in text
    assert str(tmp_path / "server.crt") in text


def test_an_https_upstream_with_no_certificate_refuses_to_start(
    config_envsh: Path, tmp_path: Path
):
    """nginx would otherwise fail with a BIO error that says nothing about
    why, and the real cause is almost always that the API has not started
    yet and so has not written its certificate."""
    result = _source(config_envsh, _env(tmp_path, REVIEW_CACERT=str(tmp_path / "absent.crt")))

    assert result.returncode != 0
    # Whitespace-normalised: the message is wrapped for a terminal, so the
    # phrases it has to contain span line breaks.
    said = " ".join(result.stderr.split())
    assert "readable certificate" in said
    assert "apitls volume is not mounted here" in said


def test_a_plain_http_upstream_writes_no_verification_block(config_envsh: Path, tmp_path: Path):
    """The right outcome behind REVIEW_TLS_ENABLED=false, where there is no
    certificate anywhere to name."""
    written = tmp_path / "upstream-tls.conf"
    result = _source(config_envsh, _env(tmp_path, REVIEW_UPSTREAM="http://nl2sql-review:8444"))

    assert result.returncode == 0, result.stderr
    text = written.read_text()
    assert "proxy_ssl_verify" not in text
    assert "clear text" in text


def test_the_plain_http_note_says_what_is_at_stake(config_envsh: Path, tmp_path: Path):
    """It is not just questions crossing in clear text here: it is a
    reviewer's edits to the golden question set."""
    written = tmp_path / "upstream-tls.conf"
    _source(config_envsh, _env(tmp_path, REVIEW_UPSTREAM="http://nl2sql-review:8444"))
    assert "golden question set" in written.read_text()
