"""router.py: the Model Router (arch5.2 section 15.3).

The table is built from catalogs made here, shaped as models/build_catalog.py
writes them, so each rule -- suited candidates, speed order, the residency
limit, pins, the host check -- is tested on the case that shows it. The call
side is tested with fake clients that fail on cue.
"""

from __future__ import annotations

import importlib.util
import json
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from nl2sql_agent.config import Settings
from nl2sql_agent.router import (
    CATALOG_SCHEMA,
    RoutingError,
    Router,
    build_table,
    candidates,
    full_name,
    listed_models,
    load_catalog,
    normalise_host,
    parse_pin,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOST = "http://192.168.10.82:11434"
ANCHOR = "qwen3.8-256k:latest"
RUNGS = ("light", "standard", "heavy")
TASKS = ("supervisor", "generator", "reflection", "narrator", "repair")


def settings(**overrides) -> Settings:
    return replace(Settings(), ollama_base_url=HOST, ollama_model="qwen3.8-256k", **overrides)


def entry(name, *, suited="heavy", source="calibration", p50=None, params=27e9, context=262144, chat=True,
          tasks=TASKS):
    """A catalog entry: `suited` for `tasks`, measured or presumed."""
    measured = {}
    if p50 is not None:
        measured = {task: {rung: {"correct": 6, "of": 6, "p50_s": p50} for rung in RUNGS} for task in tasks}
    return {
        "name": name,
        "chat": chat,
        "facts": {"parameter_count": params, "context_length": context},
        "measured": measured,
        "suited": {task: (suited if task in tasks else None) for task in TASKS},
        "suited_from": {task: source for task in TASKS},
    }


def catalog(*models, host=HOST) -> dict:
    return {"schema": CATALOG_SCHEMA, "host": host, "built": "2026-09-28T00:00:00Z",
            "calibrated": "2026-09-28T01:00:00Z", "reference": ANCHOR, "models": list(models)}


def models_of(table, task) -> list[str]:
    return [table.cells[(task, rung)].model for rung in RUNGS]


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

HOSTS = [
    ("192.168.1.20", "http://192.168.1.20:11434"),
    ("gpu-box:11500", "http://gpu-box:11500"),
    ("fe80::1", "http://[fe80::1]:11434"),
    ("[fe80::1]:11500", "http://[fe80::1]:11500"),
    ("http://192.168.10.82:11434/", "http://192.168.10.82:11434"),
    ("https://ollama.example.com", "https://ollama.example.com"),
]


@pytest.mark.parametrize("given,url", HOSTS)
def test_hosts_are_named_as_the_scanner_names_them(given, url):
    """A catalog and OLLAMA_BASE_URL are compared in this form, so the two
    copies of the rule -- here and in models/build_catalog.py, which runs
    without the agent's dependencies -- must agree."""
    spec = importlib.util.spec_from_file_location("scanner", REPO_ROOT / "models" / "build_catalog.py")
    scanner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scanner)
    assert normalise_host(given) == scanner.normalise_host(given) == url


@pytest.mark.parametrize("given", ["", "ftp://x", "gpu-box:port"])
def test_what_is_not_a_host_is_refused(given):
    with pytest.raises(ValueError):
        normalise_host(given)


def test_names_are_completed_as_ollama_completes_them():
    assert (full_name(" qwen3.8-256k "), full_name("a/b:7b")) == ("qwen3.8-256k:latest", "a/b:7b")


# ---------------------------------------------------------------------------
# The catalog
# ---------------------------------------------------------------------------


def test_a_catalog_is_read(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog()))
    assert load_catalog(str(path))["host"] == HOST


@pytest.mark.parametrize("content,message", [
    (None, "cannot be read"),
    ("not json", "is not a model catalog"),
    ("[1, 2]", "a catalog of schema None"),
    ('{"schema": 1, "models": []}', "a catalog of schema 1"),
])
def test_a_catalog_that_cannot_be_used_stops_the_agent(tmp_path, content, message):
    """Routing everything to one model the operator did not choose, silently,
    is worse than not starting."""
    path = tmp_path / "catalog.json"
    if content is not None:
        path.write_text(content)
    with pytest.raises(RoutingError, match=message):
        load_catalog(str(path))


class _Tags(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"models": [{"name": "a:latest"}, {"name": "b:7b"}, {"size": 1}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_the_hosts_models_are_listed_once_at_startup():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Tags)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    try:
        assert listed_models(f"http://127.0.0.1:{server.server_address[1]}/", 5) == {"a:latest", "b:7b"}
    finally:
        server.shutdown()
        server.server_close()
    assert listed_models("http://127.0.0.1:9", 1) is None


@pytest.mark.parametrize("text,pins", [
    ("", {}),
    ("gemma4:12b-mlx", {rung: "gemma4:12b-mlx" for rung in RUNGS}),
    ("light=mistral:7b, heavy=qwen3-coder-next", {"light": "mistral:7b", "heavy": "qwen3-coder-next:latest"}),
])
def test_pins(text, pins):
    assert parse_pin(text, "MODEL_ROUTE_GENERATOR") == pins


@pytest.mark.parametrize("text", ["medium=a", "light=", "light=a,b"])
def test_a_pin_that_does_not_parse_stops_the_agent(text):
    with pytest.raises(RoutingError, match="MODEL_ROUTE_GENERATOR"):
        parse_pin(text, "MODEL_ROUTE_GENERATOR")


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------


def test_off_is_v51_every_call_to_ollama_model():
    table = build_table(settings(model_routing_enabled=False), catalog(entry("fast:7b", p50=0.1)))
    assert not table.enabled
    assert {cell.model for cell in table.cells.values()} == {ANCHOR}
    assert table.windows == {ANCHOR: Settings().num_ctx}
    assert table.lines()[0] == f"model routing off: 1 model(s), anchor {ANCHOR}"


def test_without_a_catalog_every_rung_is_the_anchor():
    table = build_table(settings())
    assert table.enabled and table.models() == [ANCHOR]
    assert "no catalog" in table.notes[0]
    assert table.cells[("generator", "heavy")].why == "no suited candidate: OLLAMA_MODEL"


def test_an_uncalibrated_catalog_routes_nothing_unless_the_prior_is_trusted():
    """By size alone a 2023 mixture of experts outranks the reference."""
    presumed = catalog(entry(ANCHOR, source="prior"), entry("mixtral:8x7b", source="prior", params=46.7e9),
                       entry("small:3b", source="prior", params=3e9))
    table = build_table(settings(), presumed)
    assert table.models() == [ANCHOR]
    assert "uncalibrated" in table.notes[0]

    trusting = build_table(settings(model_route_on_prior=True), presumed)
    assert models_of(trusting, "generator") == ["small:3b"] * 3


def test_the_fastest_suited_model_takes_each_rung_with_a_fallback():
    table = build_table(settings(), catalog(
        entry(ANCHOR, p50=9.0),
        entry("fast-light:7b", suited="light", p50=1.0),
        entry("fast-standard:14b", suited="standard", p50=3.0),
        entry("embedder:latest", chat=False, p50=0.01),
    ))
    assert models_of(table, "generator") == ["fast-light:7b", "fast-standard:14b", ANCHOR]
    assert table.cells[("generator", "light")].fallback == "fast-standard:14b"
    assert table.cells[("generator", "standard")].fallback == ANCHOR
    assert table.cells[("generator", "heavy")].fallback is None
    assert table.cells[("generator", "light")].why == "catalog: fastest suited"


def test_no_more_models_than_the_host_keeps_loaded():
    """The anchor, then whichever model takes the most rungs."""
    table = build_table(settings(model_max_loaded=2), catalog(
        entry(ANCHOR, p50=9.0),
        entry("narrow:7b", p50=1.0, tasks=("narrator",)),
        entry("broad:14b", p50=2.0),
    ))
    assert table.models() == [ANCHOR, "broad:14b"]
    assert models_of(table, "narrator") == ["broad:14b"] * 3
    assert build_table(settings(model_max_loaded=1), catalog(entry("broad:14b", p50=2.0))).models() == [ANCHOR]


def test_an_unmeasured_candidate_follows_every_timed_one_smallest_first():
    ranked = candidates(catalog(
        entry("timed:30b", p50=4.0, params=30e9),
        entry("big:70b", source="prior", params=70e9),
        entry("small:8b", source="prior", params=8e9),
    ), on_prior=True, host=None, notes=[])
    assert ranked[("supervisor", "light")] == ["timed:30b", "small:8b", "big:70b"]


def test_models_that_would_take_as_many_rungs_are_chosen_fastest_first():
    """Every candidate here would take every rung from the anchor; the tie
    goes to the one the rungs rank first, and nothing ranked behind it is
    loaded for nothing."""
    table = build_table(settings(model_route_on_prior=True), catalog(
        entry("big:70b", source="prior", params=70e9),
        entry("small:8b", source="prior", params=8e9),
    ))
    assert table.models() == [ANCHOR, "small:8b"]
    assert table.cells[("supervisor", "light")].fallback == ANCHOR


def test_a_rung_implied_rather_than_probed_borrows_the_tasks_median():
    implied = entry("implied:14b")
    implied["measured"] = {"narrator": {"light": {"correct": 6, "of": 6, "p50_s": 5.0},
                                        "heavy": {"correct": 3, "of": 3, "p50_s": 1.0}}}
    probed = entry("probed:14b")
    probed["measured"] = {"narrator": {"standard": {"correct": 6, "of": 6, "p50_s": 2.5}}}
    table = build_table(settings(), catalog(implied, probed))
    assert table.cells[("narrator", "standard")].model == "probed:14b"  # 2.5 against a median of 3.0
    assert table.cells[("narrator", "light")].model == "probed:14b"      # 2.5 against 5.0


def test_a_model_the_host_no_longer_serves_is_not_routed_to():
    table = build_table(settings(), catalog(entry("gone:7b", p50=0.5), entry("here:7b", p50=1.0)),
                        host={ANCHOR, "here:7b"})
    assert models_of(table, "repair") == ["here:7b"] * 3
    assert "not on the host, so not routed to: gone:7b" in table.notes


def test_pins_override_the_catalog():
    table = build_table(
        settings(model_route_generator="light=mistral:7b,heavy=qwen3-coder-next", model_route_narrator="gone",
                 model_max_loaded=2),
        catalog(entry("fast:7b", p50=0.5)),
        host={ANCHOR, "mistral:7b", "qwen3-coder-next:latest", "fast:7b"},
    )
    assert models_of(table, "generator") == ["mistral:7b", "fast:7b", "qwen3-coder-next:latest"]
    assert table.cells[("generator", "light")].why == "pinned by MODEL_ROUTE_GENERATOR"
    assert table.cells[("generator", "light")].fallback == ANCHOR
    assert models_of(table, "narrator") == ["fast:7b"] * 3
    assert "MODEL_ROUTE_NARRATOR pins gone:latest, which the host does not serve: pin ignored" in table.notes
    assert any("4 distinct models, more than MODEL_MAX_LOADED=2" in note for note in table.notes)


def test_pinning_the_anchor_leaves_no_fallback():
    table = build_table(settings(model_route_supervisor="qwen3.8-256k"))
    assert table.cells[("supervisor", "light")].fallback is None


def test_a_catalog_of_another_host_is_refused_when_it_would_route():
    elsewhere = catalog(entry("fast:7b", p50=0.5), host="gpu-box")
    with pytest.raises(RoutingError, match="another machine's models"):
        build_table(settings(), elsewhere)
    presumed = catalog(entry("fast:7b", source="prior"), host="gpu-box")
    with pytest.raises(RoutingError):
        build_table(settings(model_route_on_prior=True), presumed)


def test_a_catalog_of_another_host_that_measured_nothing_is_ignored_with_a_note():
    table = build_table(settings(), catalog(entry("fast:7b", source="prior"), host="ftp://not-ollama"))
    assert table.models() == [ANCHOR]
    assert "it measured nothing, so it is ignored" in table.notes[0]


def test_the_same_host_written_differently_is_the_same_host():
    table = build_table(settings(), catalog(entry("fast:7b", p50=0.5), host="192.168.10.82"))
    assert "fast:7b" in table.models()


def test_every_routed_model_but_the_anchor_gets_a_smaller_window():
    """Fixed per model, since Ollama reloads a model asked for another window."""
    table = build_table(settings(model_num_ctx=32768), catalog(
        entry("short:7b", p50=0.5, context=8192, tasks=("supervisor",)),
        entry("long:14b", p50=0.6, context=131072, tasks=("narrator",)),
    ))
    assert table.windows == {ANCHOR: 262144, "long:14b": 32768, "short:7b": 8192}


def test_the_table_describes_itself():
    table = build_table(settings(), catalog(entry("fast:7b", p50=0.5)), catalog_path="/app/models/catalog.json")
    described = table.describe()
    assert described["enabled"] and described["anchor"] == ANCHOR
    assert described["catalog"] == {"path": "/app/models/catalog.json", "host": HOST,
                                    "built": "2026-09-28T00:00:00Z", "calibrated": "2026-09-28T01:00:00Z"}
    assert described["table"]["generator"]["light"] == {"model": "fast:7b", "fallback": ANCHOR,
                                                        "why": "catalog: fastest suited"}
    assert described["keep_alive"] == "30m"
    lines = table.lines()
    assert lines[0] == f"model routing on: 2 model(s), anchor {ANCHOR}"
    assert lines[1].split() == ["supervisor", "light", "fast:7b,", "standard", "fast:7b,", "heavy", "fast:7b"]


# ---------------------------------------------------------------------------
# The call
# ---------------------------------------------------------------------------


class Client:
    """A fake chat client that answers, fails, or answers with nothing."""

    def __init__(self, name, *, content="SELECT 1", fail=None, structured="parsed"):
        self.name, self.content, self.fail, self.structured = name, content, fail, structured
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        if self.fail:
            raise RuntimeError(self.fail)
        return SimpleNamespace(content=self.content)

    def with_structured_output(self, schema):
        client = self

        class Bound:
            def invoke(self, messages):
                client.calls += 1
                if client.fail:
                    raise RuntimeError(client.fail)
                return client.structured

        return Bound()


def router(clients: dict, **overrides) -> Router:
    table = build_table(
        settings(model_route_generator="light=light:7b,heavy=heavy:70b", **overrides),
        catalog(entry("middle:14b", p50=1.0, suited="standard", tasks=("generator",))),
    )
    built = []

    def factory(name, num_ctx):
        built.append((name, num_ctx))
        return clients[name]

    routed = Router(settings(**overrides), table, factory)
    routed.built = built
    return routed


def test_a_choice_says_what_and_why():
    choice = router({}).route("generator", "light", "attempt 1")
    assert (choice.model, choice.rung, choice.fallback, choice.num_ctx) == ("light:7b", "light", ANCHOR, 32768)
    assert (choice.think, choice.temperature) == (False, 0.0)
    assert choice.reason == "generator: attempt 1; light -> light:7b (pinned by MODEL_ROUTE_GENERATOR)"
    assert router({}).route("supervisor", "light").reason.startswith("supervisor: light -> ")


def test_the_routed_model_answers_and_each_client_is_built_once():
    clients = {"light:7b": Client("light:7b")}
    routed = router(clients)
    first = routed.model("generator", "light")
    assert first.invoke([]).content == "SELECT 1"
    routed.model("generator", "light").invoke([])
    assert routed.built == [("light:7b", 32768)]
    assert first.record() == {"model": "light:7b", "rung": "light", "hops": [],
                              "route": "generator: light -> light:7b (pinned by MODEL_ROUTE_GENERATOR)"}


def test_a_model_that_fails_hops_to_the_fallback_then_the_anchor():
    clients = {"light:7b": Client("light:7b", fail="model 'light:7b' not found"),
               ANCHOR: Client(ANCHOR, content="SELECT 2")}
    routed = router(clients).model("generator", "light")
    assert routed.invoke([]).content == "SELECT 2"
    assert routed.record()["model"] == ANCHOR
    assert routed.hops == ["light:7b: model 'light:7b' not found"]


def test_an_empty_answer_is_a_hop_too():
    clients = {"middle:14b": Client("middle:14b", content="  "), ANCHOR: Client(ANCHOR, content="SELECT 3")}
    routed = router(clients).model("generator", "standard")
    assert routed.invoke([]).content == "SELECT 3"
    assert routed.hops == ["middle:14b: an empty answer"]


def test_a_structured_call_hops_on_nothing_parsed():
    clients = {"light:7b": Client("light:7b", structured=None), ANCHOR: Client(ANCHOR, structured="ok")}
    routed = router(clients).model("generator", "light")
    assert routed.with_structured_output(dict).invoke([]) == "ok"
    assert routed.calls == 1 and routed.hops == ["light:7b: an empty answer"]


def test_the_last_model_in_the_chain_is_the_callers_to_handle():
    """Callers have always handled a failed or an empty call, and still do."""
    failing = {"light:7b": Client("light:7b", fail=" "), ANCHOR: Client(ANCHOR, fail="host unreachable")}
    routed = router(failing).model("generator", "light")
    with pytest.raises(RuntimeError, match="host unreachable"):
        routed.invoke([])
    assert routed.hops == ["light:7b: RuntimeError"]
    assert routed.record()["model"] == ANCHOR

    empty = router({"heavy:70b": Client("heavy:70b", content=""), ANCHOR: Client(ANCHOR, content="")})
    answer = empty.model("generator", "heavy").invoke([])
    assert answer.content == ""


def test_a_chain_names_each_model_once():
    clients = {ANCHOR: Client(ANCHOR, fail="down")}
    routed = router(clients).model("supervisor", "light")
    with pytest.raises(RuntimeError):
        routed.invoke([])
    assert clients[ANCHOR].calls == 1 and routed.hops == []
