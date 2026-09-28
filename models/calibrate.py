#!/usr/bin/env python3
"""Calibrate the model catalog: measure which tasks, at which rungs, each
model on the Ollama host is suited to.

    python models/calibrate.py                                     # every chat model, every task
    python models/calibrate.py --models gemma4:12b-mlx mistral:7b --tasks supervisor narrator
    python models/calibrate.py --questions B01 B07 B15 --host-memory 128G

Step 5 of section 15.1 of multi-agent_arch_specs/Multi-Agent_NL2SQL_arch5_2.md,
measured the way section 15.5 describes. Each model is run through a probe
per task; for every rung it records correct out of tried and the P50, and
per model the cold load time and resident size. A model is *suited* to a
rung when it scores within one question of the reference model on the same
probes -- the Supervisor within none (`build_catalog.TOLERANCE`) -- and
`suited` in the catalog becomes that measurement wherever there is one.

| Task | Probe | Correct when |
|---|---|---|
| generator | the benchmark questions, the pipeline run with MODEL_ROUTE_GENERATOR pinned to the model | execution accuracy, as the benchmark scores it |
| supervisor | the benchmark questions (all proceed) and probes/triage.json | the verdict is the expected one |
| reflection | each benchmark question's accepted result, replayed | the model agrees with the reference model's own reflection |
| narrator | each benchmark question's accepted result, narrated again | the audit passes every claim and every assumption is stated |
| repair | probes/repair.json: errors the classifier cannot place | the diagnosis names the fault |

The reflection is scored against the reference rather than a key: whether
a result is "fleshed out" is a judgement, and matching the model the
pipeline was tuned on is what suited means there.

It needs the agent and the stack, so it runs where the benchmark runs -- the
repository's virtualenv, against the stack's published ports -- and against
the host the catalog was built for. The generator probe is the one that
takes real time -- the pipeline, per question, with the generator pinned --
so a run is built to be left alone:

* Models with one behaviour fingerprint (`build_catalog.fingerprint`) are
  measured once and the measurement copied to the rest, `measured_as` naming
  the one measured: a local build that bakes in a bigger window is its parent,
  once the router sets the window.
* A generator stops being probed once no remaining question could make it
  suited, rung by rung from light.
* A question or a probe that fails costs that question or that task, never
  the run; a model that cannot be measured at all is reported and skipped.
* The catalog is rewritten after every model, and `--resume` skips every
  model already measured on every task asked for.

Narration is off in the pipeline runs: it never changes the SQL a generator
is scored on, and the narrator has its own probe.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
for _path in (HERE, REPO_ROOT, REPO_ROOT / "agent"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import build_catalog  # noqa: E402 -- models/build_catalog.py, beside this file
from benchmarks.questions import QUESTIONS, BenchmarkQuestion  # noqa: E402
from benchmarks.run_benchmark import build_settings, parse_args as benchmark_args, reference_rows  # noqa: E402
from benchmarks.runner import result_matches  # noqa: E402
from nl2sql_agent import complexity, present, supervisor  # noqa: E402
from nl2sql_agent import repair as repair_agent  # noqa: E402
from nl2sql_agent.completeness import entity_tables, read_query, reflect  # noqa: E402
from nl2sql_agent.config import Settings  # noqa: E402
from nl2sql_agent.contract import load_resources  # noqa: E402
from nl2sql_agent.router import window_for  # noqa: E402
from nl2sql_agent.state import PLANNER, Attempt, Issue  # noqa: E402

TASKS = build_catalog.TASKS
PROBES = HERE / "probes"


@dataclass
class Sample:
    """One probe's outcome for one model, and the rungs it counts toward.
    `seconds` is None for a probe never asked -- one an early stop made
    pointless -- which still counts toward `of`, as not right."""

    rungs: tuple[str, ...]
    correct: bool
    seconds: float | None


def tally(samples: Sequence[Sample]) -> dict[str, dict[str, Any]]:
    """Per rung: correct out of the probes there are, the P50 of the timed
    calls, and how many were asked when an early stop left some unasked."""
    out: dict[str, dict[str, Any]] = {}
    for rung in build_catalog.RUNGS:
        mine = [s for s in samples if rung in s.rungs]
        if mine:
            timed = [s.seconds for s in mine if s.seconds is not None]
            out[rung] = {
                "correct": sum(s.correct for s in mine),
                "of": len(mine),
                "p50_s": round(statistics.median(timed), 3) if timed else None,
            }
            if len(timed) < len(mine):
                out[rung]["tried"] = len(timed)
    return out


@dataclass
class Run:
    """One benchmark question as the reference model answered it."""

    question: BenchmarkQuestion
    state: dict[str, Any]
    correct: bool
    rung: str
    seconds: float


@dataclass
class Probes:
    questions: list[BenchmarkQuestion]
    triage: list[dict[str, str]] = field(default_factory=list)
    repair: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def load(cls, questions: list[BenchmarkQuestion], directory: Path = PROBES) -> "Probes":
        return cls(
            questions=questions,
            triage=json.loads((directory / "triage.json").read_text())["cases"],
            repair=json.loads((directory / "repair.json").read_text())["cases"],
        )


class HostControl:
    """The Ollama host's own API, for what calibration measures about a model
    rather than about its answers: how long it takes to load, and how much
    memory it holds once loaded (section 15.4)."""

    def __init__(self, base_url: str, timeout: float = 900.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _call(self, path: str, payload: dict | None = None) -> dict:
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read() or b"{}")

    def load_seconds(self, model: str, num_ctx: int) -> float | None:
        """Unload the model, then time loading it with the window it will be
        routed with: a cold start, as the first question after a swap sees."""
        try:
            self._call("/api/generate", {"model": model, "keep_alive": 0})
            started = time.perf_counter()
            self._call("/api/generate", {"model": model, "keep_alive": "30m", "options": {"num_ctx": num_ctx}})
        except Exception:
            return None
        return round(time.perf_counter() - started, 2)

    def resident_bytes(self, model: str) -> int | None:
        try:
            loaded = self._call("/api/ps").get("models", [])
        except Exception:
            return None
        for entry in loaded:
            if entry.get("name") == model or entry.get("model") == model:
                return int(entry.get("size_vram") or entry.get("size") or 0) or None
        return None


def announce(line: str) -> None:
    """A progress line, flushed: a calibration runs for minutes to hours, and
    whoever is watching it -- or tailing its log -- should see each probe land."""
    print(line, flush=True)


class Calibrator:
    """Measures models against the reference and writes what it measured."""

    def __init__(
        self,
        catalog: dict[str, Any],
        settings: Settings,
        *,
        database: Any,
        probes: Probes,
        agent_factory: Callable[[Settings], Any],
        client_factory: Callable[[str, int], Any],
        host: HostControl | None = None,
        label_map: Any = None,
        say: Callable[[str], None] = announce,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.catalog = catalog
        self.settings = settings
        self.db = database
        self.probes = probes
        self.agent_factory = agent_factory
        self.client_factory = client_factory
        self.host = host
        self.label_map = label_map
        self.say = say
        self.clock = clock
        self.reference = catalog["reference"]
        self.models = {model["name"]: model for model in catalog["models"]}
        self._runs: list[Run] | None = None
        self._expected: dict[str, set[str]] | None = None

    # --- the reference ---------------------------------------------------------

    def runs(self) -> list[Run]:
        """The benchmark as the reference answers it, once: its generator
        score, and the accepted results the narrator and reflection replay."""
        if self._runs is None:
            self.say(f"  reference pass: {len(self.probes.questions)} question(s) on {self.reference}")
            self._runs = self._pipeline(self.settings, self.reference)
        return self._runs

    def _pipeline(self, settings: Settings, model: str) -> list[Run]:
        agent = self.agent_factory(settings)
        return [self._ask(agent, question, model) for question in self.probes.questions]

    def _ask(self, agent: Any, question: BenchmarkQuestion, model: str) -> Run:
        """One question through the pipeline, scored as the benchmark scores
        it -- right only when every draft was `model`'s own. A run that fails
        outright is a wrong answer, not the end of the calibration."""
        try:
            state = agent.run(question.question)
        except Exception as exc:  # noqa: BLE001 -- any failure is this question's
            self.say(f"    {question.id} failed: {str(exc).splitlines()[0][:120] if str(exc) else type(exc).__name__}")
            return Run(question, {}, False, complexity.LIGHT, 0.0)
        calls = [e for e in state.get("trace", []) if e.node == "generate_sql"]
        own = [e for e in calls if e.model == model]
        result = state.get("result")
        correct = (
            bool(own) and len(own) == len(calls) and result is not None and not state.get("error")
            and result_matches(reference_rows(self.db, question), result.rows, ordered=question.ordered)
        )
        seconds = sum(e.ms for e in calls) / 1000 / max(1, len(calls))
        rung = state.get("complexity").rung if state.get("complexity") else complexity.LIGHT
        self.say(f"    {question.id} {'ok' if correct else 'wrong'} at {rung}, {seconds:.1f}s")
        return Run(question, state, correct, rung, seconds)

    def _accepted(self) -> list[Run]:
        return [run for run in self.runs() if run.state.get("result") is not None and run.state["result"].rows]

    # --- the probes --------------------------------------------------------------

    def generator(self, model: str, client: Any) -> list[Sample]:
        """The benchmark with the generator pinned to `model`, rung by rung
        from light, stopping once the model cannot be suited: a rung it has
        failed is a rung it cannot be routed at, and nothing above it counts
        (`build_catalog.measured_rung` stops the climb at the first failure).
        Each question is counted at the rung the reference's run scored it,
        so the two are compared on the same probes."""
        reference = self.runs()
        if model == self.reference:
            return [Sample((run.rung,), run.correct, run.seconds) for run in reference]
        agent = self.agent_factory(replace(self.settings, model_route_generator=model))
        allowed = build_catalog.TOLERANCE["generator"]
        samples: list[Sample] = []
        for rung in build_catalog.RUNGS:
            probes = [run for run in reference if run.rung == rung]
            bar = sum(run.correct for run in probes) - allowed
            right = 0
            for asked, probe in enumerate(probes, start=1):
                run = self._ask(agent, probe.question, model)
                samples.append(Sample((rung,), run.correct, run.seconds))
                right += run.correct
                if right + len(probes) - asked < bar:
                    samples += [Sample((rung,), False, None)] * (len(probes) - asked)
                    self.say(f"    stopped: {model} cannot be suited to the {rung} rung")
                    return samples
        return samples

    def supervisor(self, model: str, client: Any) -> list[Sample]:
        cases = [{"question": q.question, "verdict": "proceed"} for q in self.probes.questions]
        tables = self.db.table_names()
        samples = []
        for case in cases + self.probes.triage:
            started = self.clock()
            screened = supervisor.screen(client, case["question"], clarify_enabled=False, tables=tables)
            seconds = self.clock() - started
            # A failed call degrades to "proceed", which must not score as a
            # right answer to a question that should proceed.
            correct = screened["verdict"] == case["verdict"] and "supervisor" not in screened.get(
                "retrieval_errors", {}
            )
            samples.append(Sample((complexity.supervisor_rung(case["question"])[0],), correct, seconds))
        return samples

    def narrator(self, model: str, client: Any) -> list[Sample]:
        samples = []
        for run in self._accepted():
            state, result = run.state, run.state["result"]
            shots = state.get("example_shots") or []
            assumptions = state.get("assumptions") or []
            started = self.clock()
            try:
                claims = present.narrate(
                    client,
                    run.question.question,
                    result,
                    chart=state.get("chart"),
                    reasoning_target=shots[0].reasoning_target if shots else "",
                    max_rows=self.settings.max_rows,
                    assumptions=assumptions,
                )
            except Exception:
                claims = []
            seconds = self.clock() - started
            report = present.audit(claims, result, question=run.question.question, assumptions=assumptions)
            correct = bool(claims) and report.passed and not report.missing_assumptions
            samples.append(Sample((complexity.narrator_rung(result)[0],), correct, seconds))
        return samples

    def _reflect(self, client: Any, run: Run) -> tuple[set[str] | None, float]:
        """The columns a model's reflection asks for; None when it failed."""
        state, result = run.state, run.state["result"]
        query = read_query(state.get("sql", ""), self.label_map)
        started = self.clock()
        found, note, _ = reflect(
            client,
            question=run.question.question,
            intent=state.get("intent", ""),
            contract=state.get("answer_contract"),
            result=result,
            query=query,
            schema=state.get("schema", ""),
            tables=entity_tables(result, query, self.label_map),
        )
        seconds = self.clock() - started
        if note.startswith("reflection unavailable"):
            return None, seconds
        return {gap.column for gap in found}, seconds

    def _reflectable(self) -> list[Run]:
        return [
            run
            for run in self._accepted()
            if entity_tables(run.state["result"], read_query(run.state.get("sql", ""), self.label_map), self.label_map)
        ]

    def reflection(self, model: str, client: Any) -> list[Sample]:
        if self._expected is None:
            reference = self.client_factory(self.reference, self.settings.num_ctx)
            self._expected = {run.question.id: self._reflect(reference, run)[0] or set() for run in self._reflectable()}
        samples = []
        for run in self._reflectable():
            found, seconds = self._reflect(client, run)
            expected = self._expected[run.question.id]
            agrees = found is not None and (bool(found & expected) if expected else not found)
            rung = complexity.reflection_rung(run.state.get("generation_rung", run.rung))[0]
            samples.append(Sample((rung,), agrees, seconds))
        return samples

    def repair(self, model: str, client: Any) -> list[Sample]:
        samples = []
        for case in self.probes.repair:
            issues = [Issue(source=PLANNER, message=case["error"])]
            schema = self.db.schema_and_samples(case["tables"], self.settings.sample_rows)
            started = self.clock()
            hinted, calls = repair_agent.repair_hint(
                issues,
                llm=client,
                schema=schema,
                allowed_tables=case["tables"],
                history=[Attempt(sql=case["sql"], issues=issues)],
            )
            seconds = self.clock() - started
            text = " ".join(issue.hint or "" for issue in hinted).lower()
            named = calls == 1 and any(word.lower() in text for word in case["expect"])
            samples.append(Sample((complexity.STANDARD, complexity.HEAVY), named, seconds))
        return samples

    # --- one model ---------------------------------------------------------------

    def measure(self, name: str, tasks: Sequence[str]) -> dict[str, Any]:
        """What calibration measured for one model, in the catalog's shape."""
        model = self.models[name]
        window = window_for(self.settings, name, self.reference, self.catalog)
        measured: dict[str, Any] = {}
        if self.host is not None:
            measured["load_s"] = self.host.load_seconds(name, window)
            measured["resident_bytes"] = self.host.resident_bytes(name)
        client = self.client_factory(name, window)
        for task in tasks:
            if model["prior"][task] is None:
                self.say(f"  {task}: not a candidate ({model['prior']['reasons'][-1]})")
                continue
            try:
                samples = getattr(self, task)(name, client)
            except Exception as exc:  # noqa: BLE001 -- one task's failure is that task's
                self.say(f"  {task}: could not be measured ({exc})")
                continue
            measured[task] = tally(samples)
            scores = ", ".join(f"{r} {v['correct']}/{v['of']} ({v['p50_s']}s)" for r, v in measured[task].items())
            self.say(f"  {task}: {scores or 'no probes'}")
        return measured

    def twins(self, name: str) -> list[str]:
        """The other chat models with `name`'s behaviour fingerprint."""
        mark = self.models[name]["facts"].get("fingerprint")
        return sorted(
            other for other, model in self.models.items()
            if mark and other != name and model["chat"] and model["facts"].get("fingerprint") == mark
        )

    def record(self, name: str, measured: dict[str, Any], now: datetime | None = None,
               measured_as: str | None = None) -> None:
        """Merge one model's measurements into the catalog and recompute what
        every model is suited to -- the reference's own scores are the bar.
        `measured_as` names the twin the numbers were taken on."""
        model = self.models[name]
        model["measured"] = {**model.get("measured", {}), **measured}
        if measured_as:
            model["measured"]["measured_as"] = measured_as
        reference = self.models.get(self.reference)
        for entry in self.catalog["models"]:
            entry["suited"], entry["suited_from"] = build_catalog.suitability(entry, reference)
        stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        self.catalog["calibrated"] = stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def residency_note(table: Any, catalog: dict[str, Any], host_memory: int | None) -> str | None:
    """Section 15.4 rule 2: warn when the models the table routes to cannot
    all be resident at once on a host of `host_memory` bytes."""
    sizes = {m["name"]: (m.get("measured") or {}).get("resident_bytes") for m in catalog["models"]}
    known = [sizes.get(name) for name in table.models()]
    if host_memory is None or None in known:
        return None
    total = sum(known)
    if total <= host_memory:
        return None
    return (
        f"warning: the routed models need {total / 1e9:.1f} GB resident together, more than "
        f"--host-memory {host_memory / 1e9:.1f} GB: the host will swap them"
    )


def parse_memory(text: str) -> int:
    """`128G`, `96GB`, `64000M` -> bytes, in the decimal units Ollama reports
    model sizes in."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([KMGT])B?\s*", text, re.I)
    if match is None:
        raise argparse.ArgumentTypeError(f"{text!r}: give a size such as 128G or 96000M")
    return round(float(match.group(1)) * {"K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12}[match.group(2).upper()])


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure which tasks, at which rungs, each catalogued model is suited to "
        "(arch5.2, section 15.5), and write it into the catalog."
    )
    parser.add_argument("--catalog", type=Path, default=build_catalog.DEFAULT_OUT, help="default: models/catalog.json")
    parser.add_argument("--models", nargs="+", metavar="MODEL", help="default: every chat model in the catalog")
    parser.add_argument("--tasks", nargs="+", choices=TASKS, default=list(TASKS), help="default: all five")
    parser.add_argument("--questions", nargs="+", metavar="ID", help="benchmark question ids (default: all)")
    parser.add_argument("--host-memory", type=parse_memory, help="the host's memory, e.g. 128G: warns when "
                        "the routed models cannot all be resident")
    parser.add_argument("--no-load", action="store_true", help="skip timing each model's cold load")
    parser.add_argument("--resume", action="store_true",
                        help="skip every model already measured on every task asked for")
    return parser.parse_args(argv)


def settings_for(catalog: dict[str, Any]) -> Settings:
    """The benchmark's settings -- the stack's published ports -- pointed at
    the catalog's host, with the reference as OLLAMA_MODEL and no catalog, so
    every call the probes do not pin goes to the reference."""
    settings = build_settings(benchmark_args([]), "multi-shot")
    return replace(
        settings,
        ollama_base_url=catalog["host"],
        ollama_model=catalog["reference"],
        model_routing_enabled=True,
        model_catalog="",
        narrate_enabled=False,
    )


def main(argv: list[str] | None = None, *, calibrator_factory: Callable[..., Calibrator] | None = None) -> int:
    args = parse_args(argv)
    catalog = build_catalog.read_catalog(args.catalog)
    if catalog is None or catalog.get("schema") != build_catalog.SCHEMA_VERSION:
        print(f"calibrate: {args.catalog} is not a current catalog; build it with "
              "`python3 models/build_catalog.py` first", file=sys.stderr)
        return 1
    chat = [m["name"] for m in catalog["models"] if m["chat"]]
    if catalog["reference"] not in chat:
        print(f"calibrate: the reference model {catalog['reference']} is not on {catalog['host']}: "
              "nothing to measure the others against", file=sys.stderr)
        return 1
    wanted = [build_catalog.full_name(m) for m in (args.models or chat)]
    unknown = sorted(set(wanted) - set(chat))
    if unknown:
        print(f"calibrate: not chat models in the catalog: {', '.join(unknown)}", file=sys.stderr)
        return 1
    questions = [q for q in QUESTIONS if not args.questions or q.id in {i.upper() for i in args.questions}]
    if not questions:
        print("calibrate: no benchmark questions selected", file=sys.stderr)
        return 1

    calibrator = (calibrator_factory or default_calibrator)(catalog, questions, measure_load=not args.no_load)
    order: list[str] = []
    for name in [calibrator.reference] + wanted:
        if name not in order and not set(calibrator.twins(name)) & set(order):
            order.append(name)
    for number, name in enumerate(order, start=1):
        twins = calibrator.twins(name)
        also = f" (and its twins {', '.join(twins)})" if twins else ""
        done = calibrator.models[name]["measured"]
        if args.resume and all(task in done for task in args.tasks):
            calibrator.say(f"[{number}/{len(order)}] {name}{also}: measured already")
            continue
        calibrator.say(f"[{number}/{len(order)}] {name}{also}")
        try:
            measured = calibrator.measure(name, args.tasks)
        except Exception as exc:  # noqa: BLE001 -- one model's failure is that model's
            calibrator.say(f"  could not be measured: {exc}")
            continue
        calibrator.record(name, measured)
        for twin in twins:
            calibrator.record(twin, measured, measured_as=name)
        build_catalog.write_catalog(catalog, args.catalog)

    print()
    print(build_catalog.render_summary(catalog))
    from nl2sql_agent.router import build_table, listed_models

    settings = replace(calibrator.settings, model_catalog=str(args.catalog))
    table = build_table(settings, catalog, catalog_path=str(args.catalog),
                        host=listed_models(settings.ollama_base_url, settings.ollama_connect_timeout))
    print()
    print("\n".join(table.lines()))
    note = residency_note(table, catalog, args.host_memory)
    if note:
        print(note, file=sys.stderr)
    return 0


def default_calibrator(catalog: dict[str, Any], questions: list[BenchmarkQuestion], *, measure_load: bool) -> Calibrator:
    """The real thing: the stack's databases, the agent, the host."""
    from nl2sql_agent.database import Database
    from nl2sql_agent.graph import Nl2SqlAgent
    from nl2sql_agent.llm import build_routed_client

    settings = settings_for(catalog)
    database = Database(
        settings.database_url, db_schema=settings.db_schema,
        statement_timeout_ms=settings.statement_timeout_ms, max_rows=settings.max_rows,
    )
    return Calibrator(
        catalog,
        settings,
        database=database,
        probes=Probes.load(questions),
        agent_factory=lambda s: Nl2SqlAgent(s),
        client_factory=lambda model, num_ctx: build_routed_client(settings, model, num_ctx),
        host=HostControl(settings.ollama_base_url) if measure_load else None,
        label_map=load_resources(database).label_map,
    )


if __name__ == "__main__":
    sys.exit(main())
