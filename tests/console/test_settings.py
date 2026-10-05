"""The console's settings: its own `CONSOLE_*`, and the agent's it borrows.

The second half is the one that matters. The console exists to show what
the agent sees, so it reads the agent's database, schema and limits through
the agent's own `Settings` -- and exactly the ones `AGENT_SETTINGS` names,
which is the list compose passes and the documentation describes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from nl2sql_agent.console.settings import (
    AGENT_SETTINGS,
    DEFAULT_MAX_ROWS,
    DEFAULT_PORT,
    SETTING_FIELDS,
    ConsoleSettings,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
AGENT = REPO_ROOT / "agent" / "nl2sql_agent"
CONSOLE = AGENT / "console"

CONSOLE_VARIABLES = {
    "CONSOLE_HOST": "host",
    "CONSOLE_PORT": "port",
    "CONSOLE_ROOT_PATH": "root_path",
    "CONSOLE_TLS_ENABLED": "tls_enabled",
    "CONSOLE_TLS_CERT_FILE": "tls_cert_file",
    "CONSOLE_TLS_KEY_FILE": "tls_key_file",
    "CONSOLE_TOKEN": "token",
    "CONSOLE_TOKEN_NAME": "token_name",
    "CONSOLE_TOKEN_ROLES": "token_roles",
    "CONSOLE_CORS_ORIGINS": "cors_origins",
    "CONSOLE_MAX_ROWS": "max_rows",
    "CONSOLE_DOCS_ENABLED": "docs_enabled",
    "CONSOLE_LOG_LEVEL": "log_level",
    # Sign-in's: the first three are every service's, the last the console's own.
    "AUTH_ENABLED": "auth_enabled",
    "AUTH_PUBLIC_KEY_FILE": "auth_public_key_file",
    "AUTH_COOKIE_NAME": "auth_cookie_name",
    "CONSOLE_ALLOWED_ROLES": "allowed_roles",
}


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    for name in CONSOLE_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def test_the_defaults_are_a_local_https_console_with_nothing_opened_up():
    settings = ConsoleSettings.from_env()
    assert settings.port == DEFAULT_PORT == 8445
    assert settings.tls_enabled is True
    assert settings.tls_cert_file == "/etc/nl2sql/tls/server.crt"
    assert settings.token is None
    assert settings.cors_origins == ()
    assert settings.max_rows == DEFAULT_MAX_ROWS == 1000
    assert settings.source == "environment"


def test_every_variable_is_read(monkeypatch):
    values = {
        "CONSOLE_HOST": "127.0.0.1",
        "CONSOLE_PORT": "9000",
        "CONSOLE_ROOT_PATH": "/console",
        "CONSOLE_TLS_ENABLED": "false",
        "CONSOLE_TLS_CERT_FILE": "/c.crt",
        "CONSOLE_TLS_KEY_FILE": "/c.key",
        "CONSOLE_TOKEN": "s3cret",
        "CONSOLE_TOKEN_NAME": "reports",
        "CONSOLE_TOKEN_ROLES": "nl2sql_curators",
        "CONSOLE_CORS_ORIGINS": "https://a.example, https://b.example,",
        "CONSOLE_MAX_ROWS": "25",
        "CONSOLE_DOCS_ENABLED": "no",
        "CONSOLE_LOG_LEVEL": "debug",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    settings = ConsoleSettings.from_env()
    assert settings == ConsoleSettings(
        host="127.0.0.1",
        port=9000,
        root_path="/console",
        tls_enabled=False,
        tls_cert_file="/c.crt",
        tls_key_file="/c.key",
        token="s3cret",
        token_name="reports",
        token_roles=("nl2sql_curators",),
        cors_origins=("https://a.example", "https://b.example"),
        max_rows=25,
        docs_enabled=False,
        log_level="debug",
    )


def test_an_empty_variable_is_unset(monkeypatch):
    """Compose forwards an unset variable as an empty string."""
    for name in CONSOLE_VARIABLES:
        monkeypatch.setenv(name, "")
    assert ConsoleSettings.from_env() == ConsoleSettings()


def test_every_field_has_a_variable_to_set_it_with():
    source = (CONSOLE / "settings.py").read_text()
    read = set(re.findall(r'_env(?:_str|_bool|_int|_float|_tuple)?\(\s*"([A-Z_]+)"', source))
    assert read == set(CONSOLE_VARIABLES)
    assert set(SETTING_FIELDS) == set(CONSOLE_VARIABLES.values())


@pytest.mark.parametrize(
    ("host", "shown"), [("0.0.0.0", "localhost"), ("::", "localhost"), ("", "localhost"), ("10.0.0.5", "10.0.0.5")]
)
def test_the_public_url_is_one_a_client_can_use(host, shown):
    settings = ConsoleSettings(host=host, root_path="/c")
    assert settings.public_url() == f"https://{shown}:8445/c"
    assert ConsoleSettings(tls_enabled=False).scheme == "http"


def test_a_careful_configuration_has_nothing_to_warn_about():
    assert ConsoleSettings(token="s3cret", cors_origins=("https://gui.example",)).warnings() == []


def test_the_three_configurations_worth_a_warning():
    assert any("clear text" in note for note in ConsoleSettings(token="t", tls_enabled=False).warnings())
    assert ConsoleSettings(auth_enabled=False).warnings() == [
        "This console is OPEN: sign-in is off (AUTH_ENABLED=false) and no CONSOLE_TOKEN "
        "is set, so anyone who can reach the port can run SQL as the agent's database role."
    ]
    assert ConsoleSettings().warnings() == [], "sign-in is on by default, and is the control then"
    assert any("wildcard origin" in note for note in ConsoleSettings(token="t", cors_origins=("*",)).warnings())


# ---------------------------------------------------------------------------
# The agent's settings it borrows
# ---------------------------------------------------------------------------


def _agent_field_variables() -> dict[str, str]:
    """`config.Settings` field -> the variable `from_env` reads it from."""
    source = (AGENT / "config.py").read_text()
    found = dict(re.findall(r'(\w+)=_env(?:_str|_bool|_int|_float)\(\s*"([A-Z_]+)"', source))
    assert found, "no settings found in config.py -- the regex needs updating"
    return found


def test_the_console_reads_exactly_the_agent_settings_it_names():
    """Both directions. A setting the console used and did not name would
    not be passed by compose, and would silently take the image's default
    rather than the agent's value; one it named and did not use would be a
    knob that does nothing.
    """
    source = "".join(path.read_text() for path in CONSOLE.glob("*.py"))
    used = set(re.findall(r"\bagent\.(\w+)", source))
    variables = _agent_field_variables()
    assert used <= set(variables), f"not agent settings: {sorted(used - set(variables))}"
    assert {variables[name] for name in used} == set(AGENT_SETTINGS)
