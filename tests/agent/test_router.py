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
    ENSEMBLE_TASKS,
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
TASKS = ("supervisor", "generator", "reflection", "narrator", "repair", "paraphraser", "judge")


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
    ('{"schema": 4, "models": []}', "a catalog of schema 4; this agent reads schema 3"),
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


def test_a_rung_no_task_is_called_at_wins_no_model_a_place():
    """The Judge is called only at heavy: a model suited to all three of its
    rungs takes one that counts, and loses the place to one that takes two
    the Supervisor and the reflection are called at. Its other two rungs
    are still filled in the table, by the models chosen."""
    table = build_table(settings(model_max_loaded=2), catalog(
        entry(ANCHOR, p50=9.0),
        entry("judge-only:12b", p50=1.0, tasks=("judge",)),
        entry("light:7b", p50=2.0, suited="light", tasks=("supervisor", "reflection")),
    ))
    assert table.models() == [ANCHOR, "light:7b"]
    assert models_of(table, "judge") == [ANCHOR] * 3
    assert table.cells[("supervisor", "light")].model == "light:7b"


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


def test_a_catalog_of_another_host_is_ignored_even_when_it_measured_everything():
    """Another machine's models, measured on another machine, are never
    routed on -- and a checkout whose committed catalog is its maintainer's
    still starts anywhere else, as v5.1."""
    for elsewhere in (catalog(entry("fast:7b", p50=0.5), host="gpu-box"),
                      catalog(entry("fast:7b", source="prior"), host="ftp://not-ollama")):
        table = build_table(settings(model_route_on_prior=True), elsewhere)
        assert table.models() == [ANCHOR]
        assert "ignored, so every rung is OLLAMA_MODEL" in table.notes[0]


def twin(name, fingerprint, **kwargs):
    model = entry(name, **kwargs)
    model["facts"]["fingerprint"] = fingerprint
    return model


def test_one_name_per_behaviour_and_none_for_the_anchors_twins():
    """A build that only bakes in a bigger window is its parent once routed:
    loading both would hold the same weights twice."""
    ranked = candidates(catalog(
        twin(ANCHOR, "ref", p50=9.0), twin("qwen3.8:latest", "ref", p50=0.1),
        twin("fast-128k:latest", "fast", p50=1.0), twin("fast:7b", "fast", p50=1.0),
        twin("unproven:12b", None, p50=2.0), twin("unproven-256k:latest", None, p50=2.0),
    ), on_prior=False, host=None, notes=[], anchor=ANCHOR)
    # Unproven builds -- no fingerprint -- are each their own model.
    assert ranked[("generator", "light")] == ["fast-128k:latest", "unproven-256k:latest", "unproven:12b", ANCHOR]


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
            raise ConnectionError(self.fail)
        return SimpleNamespace(content=self.content)

    def with_structured_output(self, schema):
        client = self

        class Bound:
            def invoke(self, messages):
                client.calls += 1
                if client.fail:
                    raise ConnectionError(client.fail)
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


def test_a_structured_call_hops_on_an_error_too():
    clients = {"light:7b": Client("light:7b", fail="model 'light:7b' not found"),
               ANCHOR: Client(ANCHOR, structured="ok")}
    routed = router(clients).model("generator", "light")
    assert routed.with_structured_output(dict).invoke([]) == "ok"
    assert routed.hops == ["light:7b: model 'light:7b' not found"]
    assert routed.record()["model"] == ANCHOR


def test_the_last_model_in_the_chain_is_the_callers_to_handle():
    """Callers have always handled a failed or an empty call, and still do."""
    failing = {"light:7b": Client("light:7b", fail=" "), ANCHOR: Client(ANCHOR, fail="host unreachable")}
    routed = router(failing).model("generator", "light")
    with pytest.raises(ConnectionError, match="host unreachable"):
        routed.invoke([])
    assert routed.hops == ["light:7b: ConnectionError"]
    assert routed.record()["model"] == ANCHOR

    empty = router({"heavy:70b": Client("heavy:70b", content=""), ANCHOR: Client(ANCHOR, content="")})
    answer = empty.model("generator", "heavy").invoke([])
    assert answer.content == ""


def test_a_chain_names_each_model_once():
    clients = {ANCHOR: Client(ANCHOR, fail="down")}
    routed = router(clients).model("supervisor", "light")
    with pytest.raises(ConnectionError):
        routed.invoke([])
    assert clients[ANCHOR].calls == 1 and routed.hops == []


# ---------------------------------------------------------------------------
# arch7: the host gate, and the two tasks the router does not have yet
# ---------------------------------------------------------------------------


def test_every_routed_call_waits_for_a_slot_of_the_hosts_gate():
    """With one slot, a call another thread is making holds this one back
    until it is done -- whichever job or candidate either belongs to."""
    clients = {"light:7b": Client("light:7b")}
    routed = router(clients)
    answered = threading.Event()

    def call() -> None:
        routed.model("generator", "light").invoke([])
        answered.set()

    with routed.gate.slot():
        worker = threading.Thread(target=call)
        worker.start()
        assert not answered.wait(0.2), "the call went to the host while the only slot was held"
    assert answered.wait(5)
    worker.join()


def test_a_call_that_hops_gives_its_slot_back_before_the_next_model_is_asked():
    clients = {"light:7b": Client("light:7b", fail="model 'light:7b' not found"),
               ANCHOR: Client(ANCHOR, content="SELECT 2")}
    routed = router(clients)
    assert routed.model("generator", "light").invoke([]).content == "SELECT 2"
    assert routed.gate.waiting == 0
    with routed.gate.slot():
        pass  # free: neither attempt kept it


# ---------------------------------------------------------------------------
# The ensemble's two tasks (arch7 section 22.10), and catalogs that predate them
# ---------------------------------------------------------------------------


def _schema_2(*models) -> dict:
    """A catalog as models/build_catalog.py wrote one before 7.0: five tasks."""
    five = TASKS[:5]
    for model in models:
        for part in ("suited", "suited_from"):
            model[part] = {task: value for task, value in model[part].items() if task in five}
        model["prior"] = {task: "heavy" for task in five}
    return {**catalog(*models), "schema": 2, "tasks": list(five),
            "task_budgets": {task: 8192 for task in five}}


def test_a_schema_2_catalog_is_read_with_the_two_tasks_unmeasured(tmp_path):
    """R4: every calibrated host's catalog predates the two tasks. It is read,
    not refused, and nothing is routed for them -- they go to OLLAMA_MODEL,
    routing on measured suitability only, as everything else is."""
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(_schema_2(entry("middle:14b", p50=1.0, tasks=TASKS[:5]))))
    read = load_catalog(str(path))

    assert read["schema"] == 2 and read["tasks"] == [*TASKS[:5], *ENSEMBLE_TASKS]
    assert read["task_budgets"]["paraphraser"] == 8192 and read["task_budgets"]["judge"] == 16384
    [model] = read["models"]
    assert {task: model["suited"][task] for task in ENSEMBLE_TASKS} == {"paraphraser": None, "judge": None}
    assert model["suited_from"]["judge"] == "unmeasured" and model["prior"]["paraphraser"] is None
    table = build_table(settings(), read, catalog_path=str(path), host={ANCHOR, "middle:14b"})
    assert models_of(table, "generator") == ["middle:14b"] * 3
    assert models_of(table, "paraphraser") == models_of(table, "judge") == [ANCHOR] * 3
    assert any("schema 2: paraphraser and judge are unmeasured" in note for note in table.notes)


def test_the_committed_catalog_is_calibrated_for_the_two_tasks():
    """arch7's Phase 4 rebuilt the checkout's own catalog at schema 3 and
    calibrated the Paraphraser and the Judge on the host, so it is read as it
    is, and each of the two tasks has a model measured suited to it."""
    read = load_catalog(str(REPO_ROOT / "models" / "catalog.json"))
    assert read["schema"] == CATALOG_SCHEMA and read["tasks"][-2:] == list(ENSEMBLE_TASKS)
    [reference] = [model for model in read["models"] if model["name"] == read["reference"]]
    assert all(task in reference["measured"] for task in ENSEMBLE_TASKS)
    for task in ENSEMBLE_TASKS:
        assert any(
            model["suited"][task] and model["suited_from"][task] == "calibration" for model in read["models"]
        ), task


def test_a_schema_3_catalog_is_read_as_it_is(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog(entry("middle:14b", p50=1.0))))
    read = load_catalog(str(path))
    assert read["schema"] == CATALOG_SCHEMA == 3
    table = build_table(settings(), read, host={ANCHOR, "middle:14b"})
    assert models_of(table, "judge") == ["middle:14b"] * 3, "a measured task is routed like any other"
    assert not any("unmeasured" in note for note in table.notes)


def test_the_routing_table_shows_all_seven_tasks():
    table = build_table(settings(), _schema_2(entry("middle:14b", p50=1.0, tasks=TASKS[:5])),
                        host={ANCHOR, "middle:14b"})
    assert {task for task, _ in table.cells} == set(TASKS)
    lines = table.lines()
    assert any(line.strip().startswith("paraphraser") for line in lines)
    assert any(line.strip().startswith("judge") for line in lines)
    assert set(table.describe()["table"]) == set(TASKS)


@pytest.mark.parametrize("variable,task", [("model_route_paraphraser", "paraphraser"), ("model_route_judge", "judge")])
def test_the_ensembles_tasks_take_a_pin_as_the_five_do(variable, task):
    with pytest.raises(RoutingError, match=variable.upper()):
        build_table(settings(**{variable: "nonsense=x"}), catalog(entry("middle:14b", p50=1.0)))
    table = build_table(settings(**{variable: "light=middle:14b"}), catalog(entry("middle:14b", p50=1.0)),
                        host={ANCHOR, "middle:14b"})
    assert table.cells[(task, "light")].why == f"pinned by {variable.upper()}"
