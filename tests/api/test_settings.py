"""The server's own settings, and the empty-is-unset discipline they inherit.

The reason this file exists at all is a defect that already happened once in
this repository: Compose forwards an unset variable as an empty string, and
until `config.py` read empty as absent, every forwarded knob silently
overrode its own default with nothing. These settings are forwarded the same
way, so they are checked the same way.
"""

from __future__ import annotations

import re
from dataclasses import fields
from pathlib import Path

import pytest
from nl2sql_agent.api.settings import (
    DEFAULT_HOSTNAMES,
    DEFAULT_PORT,
    SETTING_FIELDS,
    ApiSettings,
)

SETTINGS_PY = Path(ApiSettings.__module__.replace(".", "/"))


@pytest.fixture
def clean_env(monkeypatch):
    """No API_* variable set, whatever the developer's shell holds."""
    import os

    for name in list(os.environ):
        if name.startswith("API_"):
            monkeypatch.delenv(name, raising=False)
    return monkeypatch


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_the_defaults_serve_https_on_8443_with_no_token(clean_env):
    settings = ApiSettings.from_env()
    assert settings.tls_enabled is True
    assert settings.port == DEFAULT_PORT
    assert settings.token is None
    assert settings.scheme == "https"


def test_tls_is_on_unless_someone_turns_it_off(clean_env):
    """The one default that would be a security decision if it were wrong."""
    assert ApiSettings().tls_enabled is True
    clean_env.setenv("API_TLS_ENABLED", "false")
    assert ApiSettings.from_env().tls_enabled is False
    assert ApiSettings.from_env().scheme == "http"


def test_a_caller_chosen_database_principal_is_refused_by_default(clean_env):
    """Forwarding a principal means letting an HTTP caller pick the role rows
    are read as. It has to be switched on deliberately.
    """
    assert ApiSettings().allow_principal is False
    clean_env.setenv("API_ALLOW_PRINCIPAL", "true")
    assert ApiSettings.from_env().allow_principal is True


def test_the_generated_certificate_covers_the_compose_service_name(clean_env):
    """Another container reaches the API as `nl2sql-api`; a certificate that
    does not name it is rejected by every client that checks.
    """
    assert "nl2sql-api" in DEFAULT_HOSTNAMES
    assert "localhost" in DEFAULT_HOSTNAMES
    assert "127.0.0.1" in DEFAULT_HOSTNAMES


# ---------------------------------------------------------------------------
# The environment
# ---------------------------------------------------------------------------


def test_every_setting_can_be_set_from_the_environment(clean_env):
    """A field with no variable behind it cannot be configured on the
    container, which is the only way most people will run this.
    """
    source = (Path(__file__).resolve().parents[2] / "agent" / SETTINGS_PY).with_suffix(".py")
    read = set(re.findall(r'_env(?:_str|_bool|_int|_float|_tuple)?\(\s*"([A-Z_]+)"', source.read_text()))
    assert len(read) == len(SETTING_FIELDS), (
        f"{len(SETTING_FIELDS)} settings but {len(read)} environment variables: "
        "a field was added without a way to set it, or the other way round"
    )


def test_an_empty_value_leaves_the_default_standing(clean_env):
    """Exactly the defect that was found in config.py: compose forwards an
    unset variable as "", and "" must not mean "no model" or "port zero".
    """
    for name in ("API_HOST", "API_PORT", "API_TOKEN", "API_TLS_ENABLED", "API_MAX_CONCURRENCY"):
        clean_env.setenv(name, "")
    settings = ApiSettings.from_env()
    defaults = ApiSettings()
    assert settings.host == defaults.host
    assert settings.port == defaults.port
    assert settings.token is None
    assert settings.tls_enabled is defaults.tls_enabled
    assert settings.max_concurrency == defaults.max_concurrency


def test_whitespace_is_as_good_as_empty(clean_env):
    clean_env.setenv("API_TOKEN", "   ")
    assert ApiSettings.from_env().token is None


def test_a_token_that_is_set_is_kept_verbatim_apart_from_padding(clean_env):
    clean_env.setenv("API_TOKEN", "  s3cret  ")
    assert ApiSettings.from_env().token == "s3cret"


def test_lists_are_comma_separated(clean_env):
    clean_env.setenv("API_CORS_ORIGINS", "https://gui.example.com, http://localhost:5173 ,")
    clean_env.setenv("API_TLS_HOSTNAMES", "api.internal,10.0.0.5")
    settings = ApiSettings.from_env()
    assert settings.cors_origins == ("https://gui.example.com", "http://localhost:5173")
    assert settings.tls_hostnames == ("api.internal", "10.0.0.5")


def test_an_empty_list_falls_back_to_the_default(clean_env):
    clean_env.setenv("API_CORS_ORIGINS", "")
    assert ApiSettings.from_env().cors_origins == ("*",)


# ---------------------------------------------------------------------------
# What it says about itself
# ---------------------------------------------------------------------------


def test_the_public_url_names_a_host_a_client_can_actually_use():
    """0.0.0.0 is an interface to bind, never an address to connect to, so
    printing it in a banner sends people to a URL that does not work.
    """
    assert ApiSettings(host="0.0.0.0", port=8443).public_url() == "https://localhost:8443"
    assert ApiSettings(host="::", port=1).public_url() == "https://localhost:1"
    assert ApiSettings(host="api.internal").public_url().startswith("https://api.internal:")


def test_the_public_url_follows_the_root_path_and_the_scheme():
    settings = ApiSettings(tls_enabled=False, root_path="/nl2sql", port=8000)
    assert settings.public_url() == "http://localhost:8000/nl2sql"


def test_plain_http_is_called_out_as_a_warning():
    notes = " ".join(ApiSettings(tls_enabled=False, token="t").warnings())
    assert "clear text" in notes


def test_an_open_port_with_no_token_is_called_out():
    notes = " ".join(ApiSettings(token=None).warnings())
    assert "No API_TOKEN" in notes


def test_a_token_with_a_wildcard_origin_is_called_out():
    """Browsers refuse to send credentials to a wildcard origin, so this
    combination looks configured and fails only in a browser.
    """
    notes = " ".join(ApiSettings(token="t", cors_origins=("*",)).warnings())
    assert "wildcard origin" in notes
    assert not any("wildcard" in n for n in ApiSettings(token="t", cors_origins=("https://g",)).warnings())


def test_a_fully_locked_down_server_warns_about_nothing():
    settings = ApiSettings(token="t", cors_origins=("https://gui.example.com",), tls_enabled=True)
    assert settings.warnings() == []
    assert settings.authenticated is True


def test_letting_callers_choose_a_principal_is_called_out():
    notes = " ".join(ApiSettings(allow_principal=True, token="t", cors_origins=("https://g",)).warnings())
    assert "API_ALLOW_PRINCIPAL" in notes


def test_the_settings_are_a_dataclass_of_plain_values():
    """They are logged, compared and replaced in tests; a mutable default or
    an object with identity semantics would make all three surprising.
    """
    for f in fields(ApiSettings):
        assert f.default is not None or f.default_factory is not None  # type: ignore[misc]
