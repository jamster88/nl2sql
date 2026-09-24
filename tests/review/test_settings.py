"""Settings and the server entry point.

The rule this file exists to enforce is the same one the agent API's
settings follow: Compose forwards a variable the host has not set as an
empty string, so empty has to read as absent or every forwarded knob
overrides its own default with nothing.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from nl2sql_review import server
from nl2sql_review.settings import SERVICE_HOSTNAME, SETTING_FIELDS, ReviewSettings
from nl2sql_review.store import WRITER_ROLE

ENVIRONMENT = {
    "REVIEW_HOST": "127.0.0.1",
    "REVIEW_PORT": "9444",
    "REVIEW_ROOT_PATH": "/review",
    "REVIEW_TLS_ENABLED": "false",
    "REVIEW_TLS_CERT_FILE": "/tls/c.crt",
    "REVIEW_TLS_KEY_FILE": "/tls/c.key",
    "REVIEW_TOKEN": "s3cret",
    "REVIEW_CORS_ORIGINS": "https://a.example, https://b.example",
    "FEEDBACK_DB_URL": "postgresql://u:p@host/db",
    "FEEDBACK_WRITER_PASSWORD": "wpw",
    "REVIEW_MANAGE_SCHEMA": "false",
    "REVIEW_DOCUMENT": "/doc.md",
    "REVIEW_RAG_DIR": "/rag",
    "REVIEW_RELOAD_CONTEXT": "false",
    "REVIEW_RELOAD_VECTORS": "false",
    "REVIEW_RELOAD_TIMEOUT_SECONDS": "30",
    "CHUNK_DB_URL": "postgresql://c/db",
    "VECTOR_DB_URL": "postgresql://v/db",
    "OLLAMA_URL": "http://ollama:11434",
    "EMBED_MODEL": "nomic",
    "REVIEW_DOCS_ENABLED": "false",
    "REVIEW_LOG_LEVEL": "debug",
}


def test_every_setting_can_be_set_from_the_environment(monkeypatch):
    """A field with no variable is a knob nobody can turn in a container."""
    for name, value in ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    settings = ReviewSettings.from_env()
    defaults = ReviewSettings()

    unchanged = [
        name
        for name in SETTING_FIELDS
        if getattr(settings, name) == getattr(defaults, name)
    ]
    assert unchanged == [], f"no environment variable reaches: {unchanged}"


def test_an_empty_variable_reads_as_absent(monkeypatch):
    """Compose forwards an unset host variable as empty.

    Without this, every forwarded knob would override its own default with
    nothing -- which for `FEEDBACK_DB_URL` means a service that cannot find
    its own database because someone did not set an unrelated variable.
    """
    for name in ENVIRONMENT:
        monkeypatch.setenv(name, "")
    assert ReviewSettings.from_env() == replace(ReviewSettings(), source="environment")


def test_a_list_variable_drops_the_blanks(monkeypatch):
    monkeypatch.setenv("REVIEW_CORS_ORIGINS", "https://a.example, ,https://b.example,")
    assert ReviewSettings.from_env().cors_origins == ("https://a.example", "https://b.example")


@pytest.mark.parametrize("value,expected", [("1", True), ("TRUE", True), ("on", True), ("no", False), ("0", False)])
def test_booleans_read_the_usual_spellings(monkeypatch, value, expected):
    monkeypatch.setenv("REVIEW_MANAGE_SCHEMA", value)
    assert ReviewSettings.from_env().manage_schema is expected


# ---------------------------------------------------------------------------
# Derived values
# ---------------------------------------------------------------------------


def test_the_public_url_follows_the_scheme_and_the_binding():
    assert ReviewSettings(tls_enabled=False).public_url() == "http://localhost:8444"
    assert ReviewSettings(host="1.2.3.4").public_url() == "https://1.2.3.4:8444"
    assert ReviewSettings(root_path="/r").public_url("x") == "https://x:8444/r"


def test_a_certificate_needs_both_halves(tmp_path):
    """A certificate without its key starts, looks right, and fails at the
    first handshake."""
    crt, key = tmp_path / "s.crt", tmp_path / "s.key"
    settings = ReviewSettings(tls_cert_file=str(crt), tls_key_file=str(key))

    assert settings.certificate_present is False
    crt.write_text("cert")
    assert settings.certificate_present is False
    key.write_text("key")
    assert settings.certificate_present is True


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------


def test_an_open_service_is_the_loudest_warning():
    notes = ReviewSettings(tls_enabled=False).warnings()
    assert any("REVIEW_TOKEN" in note and "golden questions" in note for note in notes)


def test_a_claimed_certificate_that_is_not_there_is_reported():
    notes = ReviewSettings(token="t", tls_cert_file="/nope/s.crt").warnings()
    assert any("is not readable" in note for note in notes)


def test_clear_text_is_reported():
    assert any("clear text" in n for n in ReviewSettings(token="t", tls_enabled=False).warnings())


def test_a_wildcard_origin_with_a_token_is_reported():
    notes = ReviewSettings(token="t", tls_enabled=False, cors_origins=("*",)).warnings()
    assert any("wildcard origin" in note for note in notes)


def test_switching_off_the_context_reload_is_reported():
    notes = ReviewSettings(reload_context=False, reload_vectors=False).warnings()
    assert any("will not retrieve it" in note for note in notes)


def test_embedding_without_loading_is_reported_as_the_mistake_it_is():
    """The embedder reads the rows the loader writes.

    On its own it would find nothing new and report success, which is the
    worst of the three possible outcomes.
    """
    notes = ReviewSettings(reload_context=False, reload_vectors=True).warnings()
    assert any("find nothing new to embed" in note for note in notes)


def test_a_well_configured_service_warns_about_nothing(tmp_path):
    crt, key = tmp_path / "s.crt", tmp_path / "s.key"
    crt.write_text("c")
    key.write_text("k")
    settings = ReviewSettings(
        token="t", cors_origins=("https://review.example",),
        tls_cert_file=str(crt), tls_key_file=str(key),
    )
    assert settings.warnings() == []


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


def test_the_flags_override_the_environment(monkeypatch):
    monkeypatch.setenv("REVIEW_PORT", "1111")
    settings = server.settings_from_args(server.parse_args(["--port", "2222"]))
    assert settings.port == 2222


def test_a_flag_nobody_passed_does_not_override_anything(monkeypatch):
    """`default=None` on every boolean is what makes this true.

    Without it a store_true flag nobody passed would silently turn an
    environment variable off.
    """
    monkeypatch.setenv("REVIEW_TLS_ENABLED", "false")
    monkeypatch.setenv("REVIEW_RELOAD_VECTORS", "false")
    settings = server.settings_from_args(server.parse_args([]))
    assert settings.tls_enabled is False
    assert settings.reload_vectors is False


@pytest.mark.parametrize(
    "flags,field,expected",
    [
        (["--no-tls"], "tls_enabled", False),
        (["--tls"], "tls_enabled", True),
        (["--no-manage-schema"], "manage_schema", False),
        (["--manage-schema"], "manage_schema", True),
        (["--no-reload-context"], "reload_context", False),
        (["--reload-context"], "reload_context", True),
        (["--no-reload-vectors"], "reload_vectors", False),
        (["--reload-vectors"], "reload_vectors", True),
        (["--document", "/x.md"], "document", "/x.md"),
        (["--rag-dir", "/r"], "rag_dir", "/r"),
        (["--token", "t"], "token", "t"),
        (["--host", "h"], "host", "h"),
        (["--root-path", "/p"], "root_path", "/p"),
        (["--log-level", "debug"], "log_level", "debug"),
        (["--feedback-db-url", "postgresql://x/y"], "feedback_db_url", "postgresql://x/y"),
    ],
)
def test_every_flag_reaches_its_setting(flags, field, expected):
    assert getattr(server.settings_from_args(server.parse_args(flags)), field) == expected


def test_build_returns_an_app_without_binding_a_socket():
    app, settings = server.build(["--no-tls", "--port", "9999"])
    assert settings.port == 9999
    assert any(getattr(r, "path", None) == "/v1/meta" for r in app.routes)


def test_the_banner_says_what_is_on_and_what_is_open():
    text = server.banner(ReviewSettings(tls_enabled=False))
    assert "auth           NONE" in text
    assert WRITER_ROLE in text
    assert "INSERT only" in text
    assert any(line.startswith("  ! ") for line in text.splitlines())


def test_the_banner_never_prints_the_database_password():
    text = server.banner(ReviewSettings(feedback_db_url="postgresql://u:hunter2@h/db"))
    assert "hunter2" not in text
    assert "u:***@h/db" in text


@pytest.mark.parametrize(
    "url,expected",
    [("postgresql://h/db", "postgresql://h/db"), ("postgresql://:p@h/db", "postgresql://h/db")],
)
def test_the_banner_redacts_every_url_shape(url, expected):
    assert expected in server.banner(ReviewSettings(feedback_db_url=url))


def test_the_banner_names_the_certificate_it_will_present(tmp_path):
    text = server.banner(ReviewSettings(tls_cert_file="/tls/server.crt"))
    assert "/tls/server.crt (written by the agent API)" in text


def test_print_settings_prints_and_stops(capsys):
    assert server.main(["--print-settings", "--no-tls"]) == 0
    assert "nl2sql review service" in capsys.readouterr().out


def test_tls_without_a_certificate_refuses_to_start(capsys):
    """The failure that would otherwise look like a working HTTPS URL."""
    code = server.main(["--tls", "--no-manage-schema"])
    assert code == 2
    error = capsys.readouterr().err
    assert "is not readable" in error
    assert SERVICE_HOSTNAME in error


def test_preparing_reports_a_database_that_is_not_up_yet(monkeypatch):
    """A staging database still starting is the normal case on compose up.

    A service that refused to start because of it would need a restart to
    recover from a condition that fixes itself.
    """
    class Refuses:
        def __init__(self, url):
            pass

        def setup(self, password):
            raise OSError("connection refused")

    monkeypatch.setattr(server, "Repository", Refuses)
    notes = server.prepare(ReviewSettings())
    assert notes == ["schema: NOT ready -- OSError: connection refused"]


def test_preparing_says_so_when_it_is_not_managing_the_schema():
    notes = server.prepare(ReviewSettings(manage_schema=False))
    assert "not managed" in notes[0]


def test_preparing_resets_the_writer_role(monkeypatch):
    calls: list[str] = []

    class Records:
        def __init__(self, url):
            calls.append(url)

        def setup(self, password):
            calls.append(password)

    monkeypatch.setattr(server, "Repository", Records)
    notes = server.prepare(ReviewSettings(feedback_db_url="postgresql://x/y", writer_password="pw"))
    assert calls == ["postgresql://x/y", "pw"]
    assert WRITER_ROLE in notes[0]


def test_main_starts_the_server(monkeypatch, capsys):
    started: dict = {}

    class FakeUvicorn:
        @staticmethod
        def run(app, **kwargs):
            started.update(kwargs)

    monkeypatch.setitem(__import__("sys").modules, "uvicorn", FakeUvicorn)
    monkeypatch.setattr(server, "prepare", lambda settings: ["schema: ready"])

    assert server.main(["--no-tls", "--port", "9001"]) == 0
    assert started["port"] == 9001
    assert started["ssl_certfile"] is None
    assert "schema: ready" in capsys.readouterr().out


def test_main_hands_uvicorn_the_certificate_when_tls_is_on(monkeypatch, tmp_path):
    crt, key = tmp_path / "s.crt", tmp_path / "s.key"
    crt.write_text("c")
    key.write_text("k")
    started: dict = {}

    class FakeUvicorn:
        @staticmethod
        def run(app, **kwargs):
            started.update(kwargs)

    monkeypatch.setitem(__import__("sys").modules, "uvicorn", FakeUvicorn)
    monkeypatch.setattr(server, "prepare", lambda settings: [])
    monkeypatch.setenv("REVIEW_TLS_CERT_FILE", str(crt))
    monkeypatch.setenv("REVIEW_TLS_KEY_FILE", str(key))

    assert server.main([]) == 0
    assert started["ssl_certfile"] == str(crt)


def test_the_module_can_be_run_as_a_module(monkeypatch, capsys):
    """`python -m nl2sql_review` is the container's entrypoint, so the entry
    point itself is worth executing rather than only importing.
    """
    import runpy
    import sys

    monkeypatch.setattr(sys, "argv", ["nl2sql_review", "--print-settings", "--no-tls"])
    with pytest.raises(SystemExit) as raised:
        runpy.run_module("nl2sql_review", run_name="__main__")

    assert raised.value.code == 0
    assert "nl2sql review service" in capsys.readouterr().out
