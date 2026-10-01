"""The slice of the `mlflow` module this repository calls, kept in memory.

`nl2sql_agent.tracing.Tracer` takes the module it talks to as `client`, and
the benchmark reaches MLflow through the tracer, so handing either one of
these is what lets the agent's tracing, the API's verdicts and the
benchmark's runs be tested on every run -- no server, no network, and the
span tree there to be read back as a tree.

Two flavours, as there are two packages: `FakeMlflow` is the tracing client
the agent image carries, and `FakeMlflowWithRuns` adds the runs API that
only `mlflow-skinny` has, which is how the benchmark tells them apart.
What the real thing does with all of it is tests/docker/test_mlflow_live.py's
to check.
"""

from __future__ import annotations

import itertools
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Iterator


@dataclass
class FakeSpan:
    name: str
    span_type: str
    trace_id: str
    parent: "FakeSpan | None" = None
    inputs: Any = None
    outputs: Any = None
    attributes: dict[str, Any] = field(default_factory=dict)
    children: list["FakeSpan"] = field(default_factory=list)
    error: BaseException | None = None

    def set_inputs(self, value: Any) -> None:
        self.inputs = value

    def set_outputs(self, value: Any) -> None:
        self.outputs = value

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def set_attributes(self, values: dict[str, Any]) -> None:
        self.attributes.update(values)

    def child(self, name: str) -> "FakeSpan":
        """The one child of that name; fails the test when there is not one."""
        [found] = [c for c in self.children if c.name == name]
        return found


@dataclass
class FakeAssessment:
    assessment_id: str
    name: str
    value: Any
    rationale: str | None
    source: Any
    valid: bool = True
    overrides: str | None = None


@dataclass
class FakeTrace:
    root: FakeSpan
    tags: dict[str, str] = field(default_factory=dict)
    user: str | None = None
    run_id: str | None = None
    assessments: list[FakeAssessment] = field(default_factory=list)

    @property
    def info(self) -> SimpleNamespace:
        return SimpleNamespace(trace_id=self.root.trace_id, assessments=list(self.assessments))


class FakeMlflow:
    """The tracing client: spans, traces, and the assessments on them."""

    def __init__(self) -> None:
        self.tracking_uri: str | None = None
        self.experiment: str | None = None
        self.traces: dict[str, FakeTrace] = {}
        self.flushes = 0
        self._ids = itertools.count(1)
        self._span: ContextVar[FakeSpan | None] = ContextVar(f"fake_span_{id(self)}", default=None)
        self._run_id: str | None = None

    # --- connection ---------------------------------------------------------

    def set_tracking_uri(self, uri: str) -> None:
        self.tracking_uri = uri

    def set_experiment(self, name: str) -> SimpleNamespace:
        self.experiment = name
        return SimpleNamespace(name=name, experiment_id="1")

    # --- spans --------------------------------------------------------------

    @contextmanager
    def start_span(self, name: str = "span", span_type: str = "UNKNOWN") -> Iterator[FakeSpan]:
        parent = self._span.get()
        trace_id = parent.trace_id if parent else f"tr-{next(self._ids)}"
        span = FakeSpan(name=name, span_type=span_type, trace_id=trace_id, parent=parent)
        if parent is None:
            self.traces[trace_id] = FakeTrace(root=span, run_id=self._run_id)
        else:
            parent.children.append(span)
        token = self._span.set(span)
        try:
            yield span
        except BaseException as exc:
            span.error = exc
            raise
        finally:
            self._span.reset(token)

    def update_current_trace(self, tags: dict[str, str] | None = None, user: str | None = None) -> None:
        span = self._span.get()
        assert span is not None, "update_current_trace outside a trace"
        trace = self.traces[span.trace_id]
        trace.tags.update(tags or {})
        if user is not None:
            trace.user = user

    def flush_trace_async_logging(self) -> None:
        self.flushes += 1

    def get_trace(self, trace_id: str, flush: bool = False) -> FakeTrace | None:
        self.flushes += flush
        return self.traces.get(trace_id)

    def search_traces(self, *, filter_string: str, max_results: int, return_type: str, include_spans: bool):
        tag, _, value = filter_string.partition(" = ")
        key = tag.removeprefix("tags.").strip("`")
        found = [t for t in self.traces.values() if t.tags.get(key) == value.strip("'")]
        return found[:max_results]

    # --- assessments --------------------------------------------------------

    def _assess(self, trace_id: str, name: str, value: Any, rationale: str | None, source: Any) -> FakeAssessment:
        if trace_id not in self.traces:
            raise LookupError(f"no trace {trace_id}")
        assessment = FakeAssessment(f"a-{next(self._ids)}", name, value, rationale, source)
        self.traces[trace_id].assessments.append(assessment)
        return assessment

    def log_feedback(self, *, trace_id: str, name: str, value: Any, rationale: str | None = None, source: Any = None):
        return self._assess(trace_id, name, value, rationale, source)

    def override_feedback(
        self, *, trace_id: str, assessment_id: str, value: Any, rationale: str | None = None, source: Any = None
    ):
        trace = self.traces[trace_id]
        [old] = [a for a in trace.assessments if a.assessment_id == assessment_id]
        old.valid = False
        new = self._assess(trace_id, old.name, value, rationale, source)
        new.overrides = assessment_id
        return new

    def delete_assessment(self, *, trace_id: str, assessment_id: str) -> None:
        trace = self.traces[trace_id]
        trace.assessments = [a for a in trace.assessments if a.assessment_id != assessment_id]

    # --- reading back -------------------------------------------------------

    def only_trace(self) -> FakeTrace:
        [trace] = self.traces.values()
        return trace


class FakeMlflowWithRuns(FakeMlflow):
    """The same, with the runs API `mlflow-skinny` adds and the benchmark uses."""

    def __init__(self) -> None:
        super().__init__()
        self.runs: dict[str, dict[str, Any]] = {}

    def start_run(self, run_name: str, tags: dict[str, str]) -> SimpleNamespace:
        run_id = f"run-{next(self._ids)}"
        self.runs[run_id] = {
            "name": run_name, "tags": dict(tags), "params": {}, "metrics": {},
            "artifacts": {}, "status": "RUNNING",
        }
        # As MLflow does: a trace opened while a run is active is filed under it.
        self._run_id = run_id
        return SimpleNamespace(info=SimpleNamespace(run_id=run_id))

    def _active(self) -> dict[str, Any]:
        assert self._run_id is not None, "no active run"
        return self.runs[self._run_id]

    def log_params(self, params: dict[str, Any]) -> None:
        self._active()["params"].update(params)

    def log_metrics(self, metrics: dict[str, float]) -> None:
        self._active()["metrics"].update(metrics)

    def log_dict(self, document: dict[str, Any], path: str) -> None:
        self._active()["artifacts"][path] = document

    def end_run(self, status: str = "FINISHED") -> None:
        self._active()["status"] = status
        self._run_id = None
