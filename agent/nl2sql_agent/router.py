"""The Model Router: which model answers each call.

Section 15.3 of `multi-agent_arch_specs/Multi-Agent_NL2SQL_arch5_2.md`. Every
agent that calls a model asks for one by task and rung -- the rung computed
by `complexity.py` -- and gets the cheapest model on the Ollama host that the
catalog shows is suited to that task at that rung, with a fallback. The
router calls no model, never routes the embedder, and never lowers a rung.

**The table** is built once, at startup, from `models/catalog.json`:

1. **Candidates.** For each task and rung, the chat models whose `suited`
   rung for the task is at or above it. Only suitability calibration
   *measured* counts unless `MODEL_ROUTE_ON_PRIOR=true`: the prior is a
   guess from a model's size and description, and by size alone a 2023
   mixture of experts outranks the reference model. A model the host no
   longer serves is dropped.
2. **Order.** Measured P50 at that rung, fastest first; a model with no
   timing after every model with one, smallest first.
3. **Residency.** Ollama loads a model on first use and swaps from disk
   when too many are asked for, so at most `MODEL_MAX_LOADED` distinct
   models (three) fill the table: `OLLAMA_MODEL`, which fills every rung
   nothing else can, and then whichever models win the most rungs.
4. **Pins.** `MODEL_ROUTE_<TASK>` overrides a task's rungs outright.

**A call** goes to the rung's model; if that model is not on the host,
cannot be reached, fails, or answers with nothing, the call is retried once
on the rung's fallback, then on `OLLAMA_MODEL`, and the trace records each
hop. A model that answers *badly* is not a routing failure: the gates catch
that, and a repair climbs the ladder.

**Off is v5.1.** `MODEL_ROUTING_ENABLED=false`, no catalog, a catalog with
nothing measured, or a catalog built for another host, sends every call to
`OLLAMA_MODEL`.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import statistics
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from . import tracing
from .complexity import RUNGS
from .config import Settings

log = logging.getLogger(__name__)

TASKS = ("supervisor", "generator", "reflection", "narrator", "repair")
#: The catalog layout this router reads (models/build_catalog.py SCHEMA_VERSION).
CATALOG_SCHEMA = 2
OLLAMA_PORT = 11434
CALIBRATION = "calibration"

ClientFactory = Callable[[str, int], Any]


class RoutingError(RuntimeError):
    """The routing configuration cannot be used, so the agent does not start."""


# --- names ------------------------------------------------------------------


def normalise_host(address: str) -> str:
    """The scanner's rules for naming a host (models/build_catalog.py), so a
    catalog and `OLLAMA_BASE_URL` compare equal however either was written:
    an address without a scheme is http, and without a port is 11434."""
    text = address.strip()
    bare = "://" not in text
    text = text.rstrip("/")
    if bare:
        try:
            text = f"[{ipaddress.IPv6Address(text).compressed}]"
        except ValueError:
            pass
        text = "http://" + text
    parts = urllib.parse.urlsplit(text)
    try:
        port = parts.port
    except ValueError:
        port = -1
    if parts.scheme not in ("http", "https") or not parts.hostname or port == -1:
        raise ValueError(f"{address!r} is not an Ollama host")
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    if port is None and bare:
        port = OLLAMA_PORT
    netloc = host if port is None else f"{host}:{port}"
    return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path.rstrip("/"), "", ""))


def full_name(name: str) -> str:
    """Ollama's name for a model: `qwen3.8-256k` is `qwen3.8-256k:latest`."""
    name = name.strip()
    return name if ":" in name.rsplit("/", 1)[-1] else name + ":latest"


# --- the catalog ----------------------------------------------------------------


def load_catalog(path: str) -> dict[str, Any]:
    """The catalog MODEL_CATALOG names. A setting that points at nothing, or
    at a file this router cannot read, stops the agent rather than silently
    routing everything to one model the operator did not choose."""
    try:
        catalog = json.loads(Path(path).read_text())
    except OSError as exc:
        raise RoutingError(
            f"MODEL_CATALOG names {path}, which cannot be read ({exc}). Build one with "
            "`python3 models/build_catalog.py`, or unset MODEL_CATALOG."
        ) from exc
    except ValueError as exc:
        raise RoutingError(f"{path} is not a model catalog: {exc}") from exc
    if not isinstance(catalog, dict) or catalog.get("schema") != CATALOG_SCHEMA:
        found = catalog.get("schema") if isinstance(catalog, dict) else None
        raise RoutingError(
            f"{path} is a catalog of schema {found}; this agent reads schema {CATALOG_SCHEMA}. "
            "Re-run `python3 models/build_catalog.py` to rebuild it."
        )
    return catalog


def listed_models(base_url: str, timeout: float) -> set[str] | None:
    """What the host serves right now, or None when it cannot be asked."""
    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/api/tags", timeout=timeout) as response:
            return {m["name"] for m in json.loads(response.read()).get("models", []) if m.get("name")}
    except Exception:
        return None


def parse_pin(text: str, setting: str) -> dict[str, str]:
    """`MODEL_ROUTE_<TASK>`: a model for every rung, or `light=a,heavy=b`."""
    text = text.strip()
    if not text:
        return {}
    if "=" not in text:
        return {rung: full_name(text) for rung in RUNGS}
    pins = {}
    for part in text.split(","):
        rung, _, model = part.partition("=")
        rung, model = rung.strip().lower(), model.strip()
        if rung not in RUNGS or not model:
            raise RoutingError(
                f"{setting}={text!r}: expected a model, or rung=model pairs such as "
                "light=gemma4:12b-mlx,heavy=qwen3.8-256k"
            )
        pins[rung] = full_name(model)
    return pins


# --- the table ----------------------------------------------------------------------


@dataclass
class Cell:
    model: str
    fallback: str | None
    why: str


@dataclass
class RoutingTable:
    """Task x rung -> model, built once at startup."""

    enabled: bool
    anchor: str
    cells: dict[tuple[str, str], Cell]
    windows: dict[str, int]
    max_loaded: int
    keep_alive: str | None = None
    catalog: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)

    def models(self) -> list[str]:
        """The distinct models the table routes to, the anchor first."""
        seen = [self.anchor]
        for cell in self.cells.values():
            if cell.model not in seen:
                seen.append(cell.model)
        return seen

    def describe(self) -> dict[str, Any]:
        """The table as `/v1/meta` reports it, so what a run was routed with
        is never a matter of memory."""
        return {
            "enabled": self.enabled,
            "anchor": self.anchor,
            "catalog": self.catalog,
            "models": self.models(),
            "max_loaded": self.max_loaded,
            "keep_alive": self.keep_alive,
            "windows": dict(self.windows),
            "table": {
                task: {
                    rung: {"model": cell.model, "fallback": cell.fallback, "why": cell.why}
                    for (cell_task, rung), cell in self.cells.items()
                    if cell_task == task
                }
                for task in TASKS
            },
            "notes": list(self.notes),
        }

    def lines(self) -> list[str]:
        """The table for a log or a terminal: one line per task."""
        state = "on" if self.enabled else "off"
        out = [f"model routing {state}: {len(self.models())} model(s), anchor {self.anchor}"]
        for task in TASKS:
            rungs = ", ".join(f"{rung} {self.cells[(task, rung)].model}" for rung in RUNGS)
            out.append(f"  {task:<10} {rungs}")
        out += [f"  note: {note}" for note in self.notes]
        return out


def _p50(model: dict[str, Any], task: str, rung: str) -> float | None:
    """Measured P50 at the rung; a rung implied rather than probed borrows the
    task's median."""
    measured = model.get("measured", {}).get(task, {})
    at_rung = measured.get(rung, {}).get("p50_s")
    if at_rung is not None:
        return float(at_rung)
    values = [float(v["p50_s"]) for v in measured.values() if v.get("p50_s") is not None]
    return statistics.median(values) if values else None


def candidates(
    catalog: dict[str, Any], *, on_prior: bool, host: set[str] | None, notes: list[str], anchor: str = ""
) -> dict[tuple[str, str], list[str]]:
    """For every task and rung, the models suited to it, best first.

    One name per behaviour fingerprint: a local build that only bakes in a
    bigger window behaves as its parent once routed, and loading both would
    hold the same weights in memory twice. The anchor's twins are left out
    altogether -- every rung can fall back to the anchor itself."""
    order: dict[tuple[str, str], list[tuple[tuple, str]]] = {
        (task, rung): [] for task in TASKS for rung in RUNGS
    }
    gone = []
    seen = {
        (model.get("facts") or {}).get("fingerprint")
        for model in catalog.get("models", [])
        if model.get("name") == anchor
    } - {None}
    for model in sorted(catalog.get("models", []), key=lambda m: m.get("name") or ""):
        name = model.get("name")
        if not model.get("chat"):
            continue
        if host is not None and name not in host:
            gone.append(name)
            continue
        twin = (model.get("facts") or {}).get("fingerprint")
        if name != anchor and twin in seen:
            continue
        if twin:
            seen.add(twin)
        size = (model.get("facts") or {}).get("parameter_count") or float("inf")
        for task in TASKS:
            suited = (model.get("suited") or {}).get(task)
            source = (model.get("suited_from") or {}).get(task)
            if suited not in RUNGS or (source != CALIBRATION and not on_prior):
                continue
            for rung in RUNGS[: RUNGS.index(suited) + 1]:
                p50 = _p50(model, task, rung)
                key = (0, p50, name) if p50 is not None else (1, size, name)
                order[(task, rung)].append((key, name))
    if gone:
        notes.append(f"not on the host, so not routed to: {', '.join(sorted(gone))}")
    return {cell: [name for _, name in sorted(ranked)] for cell, ranked in order.items()}


def choose_models(ranked: dict[tuple[str, str], list[str]], anchor: str, limit: int) -> list[str]:
    """At most `limit` models, the anchor first, then greedily whichever model
    would take the most rungs from the models already chosen."""
    chosen = [anchor]
    while len(chosen) < max(1, limit):
        gains: dict[str, int] = {}
        places: dict[str, int] = {}
        for order in ranked.values():
            for place, name in enumerate(order):
                if name in chosen:
                    break
                gains[name] = gains.get(name, 0) + 1
                places[name] = places.get(name, 0) + place
        if not gains:
            break
        # Most rungs taken; on a tie, the model the rungs rank faster.
        chosen.append(min(gains, key=lambda name: (-gains[name], places[name], name)))
    return chosen


def build_table(
    settings: Settings,
    catalog: dict[str, Any] | None = None,
    *,
    catalog_path: str = "",
    host: set[str] | None = None,
) -> RoutingTable:
    anchor = full_name(settings.ollama_model)
    notes: list[str] = []
    keep_alive = settings.ollama_keep_alive or None
    if not settings.model_routing_enabled:
        cells = {(t, r): Cell(anchor, None, "routing off") for t in TASKS for r in RUNGS}
        return RoutingTable(False, anchor, cells, {anchor: settings.num_ctx}, 1, None, None, notes)

    described = None
    ranked: dict[tuple[str, str], list[str]] = {(t, r): [] for t in TASKS for r in RUNGS}
    if catalog is not None:
        described = {
            "path": catalog_path,
            "host": catalog.get("host"),
            "built": catalog.get("built"),
            "calibrated": catalog.get("calibrated"),
        }
        measured = any(
            source == CALIBRATION
            for model in catalog.get("models", [])
            for source in (model.get("suited_from") or {}).values()
        )
        would_route = measured or settings.model_route_on_prior
        if _host_of(catalog) != _host_of({"host": settings.ollama_base_url}):
            # Another machine's models, measured on another machine: never
            # routed on. Ignored rather than refused, so a checkout whose
            # committed catalog describes its maintainer's host still starts
            # anywhere else -- as v5.1, until its own catalog is built.
            notes.append(
                f"the catalog describes {catalog.get('host')}, and the agent is pointed at "
                f"{settings.ollama_base_url}: ignored, so every rung is OLLAMA_MODEL. Build one for "
                "this host with `python3 models/build_catalog.py` and `models/calibrate.py`"
            )
        elif not would_route:
            notes.append(
                "the catalog is uncalibrated: every rung is OLLAMA_MODEL until models/calibrate.py "
                "has measured a candidate (or MODEL_ROUTE_ON_PRIOR=true routes on the prior)"
            )
        else:
            ranked = candidates(catalog, on_prior=settings.model_route_on_prior, host=host, notes=notes,
                                anchor=anchor)
    else:
        notes.append("no catalog (MODEL_CATALOG): every rung is OLLAMA_MODEL")

    chosen = choose_models(ranked, anchor, settings.model_max_loaded)
    cells = {}
    for (task, rung), order in ranked.items():
        usable = [name for name in order if name in chosen]
        if usable:
            model = usable[0]
            others = usable[1:] + ([anchor] if anchor != model and anchor not in usable else [])
            why = "catalog: fastest suited"
        else:
            model, others, why = anchor, [], "no suited candidate: OLLAMA_MODEL"
        cells[(task, rung)] = Cell(model, others[0] if others else None, why)

    for task in TASKS:
        setting = f"MODEL_ROUTE_{task.upper()}"
        for rung, model in parse_pin(getattr(settings, f"model_route_{task}"), setting).items():
            if host is not None and model not in host:
                notes.append(f"{setting} pins {model}, which the host does not serve: pin ignored")
                continue
            cells[(task, rung)] = Cell(model, anchor if model != anchor else None, f"pinned by {setting}")

    table_models = {cell.model for cell in cells.values()} | {anchor}
    if len(table_models) > settings.model_max_loaded:
        notes.append(
            f"{len(table_models)} distinct models, more than MODEL_MAX_LOADED={settings.model_max_loaded}: "
            "the host may swap them in and out"
        )
    windows = {name: window_for(settings, name, anchor, catalog) for name in sorted(table_models)}
    return RoutingTable(True, anchor, cells, windows, settings.model_max_loaded, keep_alive, described, notes)


def _host_of(catalog: dict[str, Any]) -> str:
    try:
        return normalise_host(str(catalog.get("host") or ""))
    except ValueError:
        return ""


def window_for(settings: Settings, name: str, anchor: str, catalog: dict[str, Any] | None) -> int:
    """`num_ctx` per model, fixed for the process. Ollama reloads a model
    whenever a request asks for a different window, so a window per task
    would reload the model between every pair of calls. The anchor keeps
    OLLAMA_NUM_CTX, exactly as without routing; every other model gets the
    smaller of its own context length and MODEL_NUM_CTX."""
    if name == anchor:
        return settings.num_ctx
    context = None
    for model in (catalog or {}).get("models", []):
        if model.get("name") == name:
            context = (model.get("facts") or {}).get("context_length")
    return min(v for v in (settings.num_ctx, settings.model_num_ctx, context) if v)


# --- the call ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelChoice:
    """What `route(task, rung)` answers (section 15.3)."""

    task: str
    rung: str
    model: str
    num_ctx: int
    think: bool
    temperature: float
    fallback: str | None
    reason: str


class Router:
    """Turns a task and a rung into a model, and hands out routed models."""

    def __init__(self, settings: Settings, table: RoutingTable, factory: ClientFactory) -> None:
        self.settings = settings
        self.table = table
        self._factory = factory
        self._clients: dict[str, Any] = {}
        self._lock = threading.Lock()

    def route(self, task: str, rung: str, why: str = "") -> ModelChoice:
        cell = self.table.cells[(task, rung)]
        return ModelChoice(
            task=task,
            rung=rung,
            model=cell.model,
            num_ctx=self.table.windows[cell.model],
            think=self.settings.reasoning,
            temperature=self.settings.temperature,
            fallback=cell.fallback,
            reason=f"{task}: {why + '; ' if why else ''}{rung} -> {cell.model} ({cell.why})",
        )

    def client(self, name: str) -> Any:
        """One client per model, built on first use: a fallback nothing falls
        back to is never constructed."""
        with self._lock:
            if name not in self._clients:
                window = self.table.windows.get(name) or self.settings.num_ctx
                self._clients[name] = self._factory(name, window)
            return self._clients[name]

    def model(self, task: str, rung: str, why: str = "") -> "RoutedModel":
        choice = self.route(task, rung, why)
        chain: list[str] = []
        for name in (choice.model, choice.fallback, self.table.anchor):
            if name and name not in chain:
                chain.append(name)
        return RoutedModel(choice, chain, self.client)


class RoutedModel:
    """What an agent calls instead of a chat model: the same `invoke` and
    `with_structured_output`, answered by the routed model or, when that one
    cannot answer at all, by the next in its chain."""

    def __init__(self, choice: ModelChoice, chain: Sequence[str], client: Callable[[str], Any]) -> None:
        self.choice = choice
        self._chain = list(chain)
        self._client = client
        self.calls = 0
        self.answered_by: str | None = None
        self.hops: list[str] = []

    def invoke(self, messages: Any) -> Any:
        return self._answer(lambda client: client.invoke(messages), _empty_message, messages)

    def with_structured_output(self, schema: type) -> "_Structured":
        return _Structured(self, schema)

    def _answer(
        self,
        call: Callable[[Any], Any],
        empty: Callable[[Any], bool],
        messages: Any,
        schema: type | None = None,
    ) -> Any:
        """The first usable answer down the chain. The last model's answer is
        returned whatever it is, and its failure raised: the caller has
        always handled a failed or empty call, and still does."""
        self.calls += 1
        *earlier, last = self._chain
        for name in earlier:
            self.answered_by = name
            try:
                answer = self._traced_call(name, call, messages, schema)
            except Exception as exc:
                self.hops.append(f"{name}: {_reason(exc)}")
                continue
            if not empty(answer):
                return answer
            self.hops.append(f"{name}: an empty answer")
        self.answered_by = last
        return self._traced_call(last, call, messages, schema)

    def _traced_call(self, name: str, call: Callable[[Any], Any], messages: Any, schema: type | None) -> Any:
        """One model's attempt, as its own span in the run's trace: a hop
        down the chain is then two spans, the first failed or empty."""
        with tracing.model_span(
            name,
            messages,
            task=self.choice.task,
            rung=self.choice.rung,
            route=self.choice.reason,
            schema=schema,
        ) as span:
            answer = call(self._client(name))
            tracing.finish_model_span(span, answer)
            return answer

    def record(self) -> dict[str, Any]:
        """For the trace: which model answered, at what rung, and why."""
        return {
            "model": self.answered_by or self.choice.model,
            "rung": self.choice.rung,
            "route": self.choice.reason,
            "hops": list(self.hops),
        }


class _Structured:
    def __init__(self, routed: RoutedModel, schema: type) -> None:
        self._routed = routed
        self._schema = schema

    def invoke(self, messages: Any) -> Any:
        return self._routed._answer(
            lambda client: client.with_structured_output(self._schema).invoke(messages),
            lambda answer: answer is None,
            messages,
            self._schema,
        )


def _empty_message(answer: Any) -> bool:
    return not str(getattr(answer, "content", answer) or "").strip()


def _reason(exc: Exception) -> str:
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return text[:160]
