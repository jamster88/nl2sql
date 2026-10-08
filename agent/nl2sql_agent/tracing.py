"""MLflow tracing: one trace per question, a span per agent and per model call.

`MLFLOW_TRACKING_URI` turns it on. Each run of the pipeline is then one trace
in the experiment `MLFLOW_EXPERIMENT_NAME`, shaped like the architecture:

    nl2sql                      AGENT       the question in, the answer out
      Supervisor                AGENT       what the agent read of the state,
        <model>                 CHAT_MODEL  what it wrote back, and every
      Schema Retriever          RETRIEVER   model call it made: the messages,
      ...                                   the answer, its tokens, and the
      SQL Generator             AGENT       task, rung and route the Model
        <model>                 CHAT_MODEL  Router chose it by
      Static Validator          GUARDRAIL
      ...

A routed call that falls back is two model spans, the first marked failed or
empty, so the trace shows the hop the state's `hops` describes. The trace is
tagged with how the run ended, the agent's version and -- under the REST API
-- the job, so a job id finds its trace.

**Best effort, like retrieval.** A server that does not answer costs the
trace and nothing else: the run goes ahead untraced, the reason is logged
once, and the next run asks again after `RETRY_SECONDS`. Nothing here is
imported from MLflow until a server has answered, so with tracing off the
agent does not load it at all.
"""

from __future__ import annotations

import logging
import os
import ssl
import threading
import time
import urllib.request
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator, Mapping

from nl2sql_common.errors import NETWORK_ERRORS, Nl2SqlError, Refused, _family, _optional

from .config import Settings
from .state import TraceEntry, to_jsonable

log = logging.getLogger(__name__)

#: What talking to MLflow fails with: the network, the server's refusal as
#: MLflow raises it, and a reply it could not use. Tracing is best effort --
#: a question is answered whether or not its trace is kept -- but a bug in
#: this module is still a bug, not a server that is down (V6-23).
TRACING_ERRORS = _family(NETWORK_ERRORS, [ValueError, Nl2SqlError], _optional("mlflow.exceptions.MlflowException"))

#: How long the health check waits for the tracking server. A server that is
#: up answers in milliseconds; this bounds the one that is routed and silent.
PROBE_TIMEOUT = 2.0
#: After a failed check, how long runs go untraced before one asks again --
#: so a server started after the agent is found without a restart, and one
#: that is down is not asked on every question.
RETRY_SECONDS = 30.0

#: MLflow's HTTP client retries a failed request seven times with a growing
#: backoff, and a finished trace is sent from a queue the process waits on
#: before it exits. Measured: one trace finished while the server was down
#: held the CLI's exit for four minutes. These bound that to seconds. They
#: are defaults, set only where the environment says nothing.
HTTP_DEFAULTS = {
    "MLFLOW_HTTP_REQUEST_MAX_RETRIES": "1",
    "MLFLOW_HTTP_REQUEST_BACKOFF_FACTOR": "1",
    "MLFLOW_HTTP_REQUEST_TIMEOUT": "10",
}

#: The span that holds a run, and the name the trace list shows for it.
ROOT_SPAN = "nl2sql"

#: Span attributes MLflow's interface reads, by their MLflow names
#: (`mlflow.tracing.constant.SpanAttributeKey`): the model and provider shown
#: beside a call, its token usage, which the trace sums, and the message
#: format that makes the interface draw the call as a conversation.
MODEL = "mlflow.llm.model"
PROVIDER = "mlflow.llm.provider"
TOKEN_USAGE = "mlflow.chat.tokenUsage"
MESSAGE_FORMAT = "mlflow.message.format"

#: A person's verdict on an answer, recorded on the answer's trace. The name
#: is the assessment's, so a trace judged twice shows one verdict and its
#: history rather than two.
VERDICT = "verdict"
#: Who gave it, as far as this process knows: a caller of the feedback route.
#: The API holds a shared token, not an identity, so it does not name a person.
VERDICT_SOURCE = "nl2sql-api feedback"

#: The ways a run ends, as the trace's `nl2sql.outcome` tag says them.
ANSWERED = "answered"
GAVE_UP = "gave_up"
REFUSED = "refused"

_ROLES = {"system": "system", "human": "user", "ai": "assistant", "tool": "tool"}

#: The tracer whose trace is open in this context, if any. A context
#: variable for the reason progress is one (`graph._progress`): the REST
#: server runs several questions at once, and LangGraph copies the context
#: into the threads it fans stage 1 across, so a retriever's span finds its
#: own run's trace rather than another's.
_active: ContextVar["Tracer | None"] = ContextVar("nl2sql_tracer", default=None)
#: Tags for the next trace opened in this context, from whoever started the
#: run: the API's job id, the benchmark's question id.
_tags: ContextVar[tuple[tuple[str, str], ...]] = ContextVar("nl2sql_trace_tags", default=())


def probe(uri: str, timeout: float = PROBE_TIMEOUT) -> None:
    """Raise unless the tracking server answers its health check.

    Only an HTTP server can be asked; any other URI (`databricks`, say) is
    left to MLflow's own client, whose first call says whether it works.
    """
    if not uri.startswith(("http://", "https://")):
        return
    with urllib.request.urlopen(uri.rstrip("/") + "/health", timeout=timeout, context=tls_context()) as response:
        response.read(1)


def tls_context() -> ssl.SSLContext | None:
    """The trust MLflow's own client uses, so the probe agrees with it.

    The proxy in front of MLflow presents the stack's development certificate
    unless a real one is mounted. MLflow's client is told about it with
    MLFLOW_TRACKING_SERVER_CERT_PATH (or, for an experiment, told not to
    check with MLFLOW_TRACKING_INSECURE_TLS); a probe that ignored them would
    call a server MLflow can reach unreachable, and trace nothing.
    """
    if (os.getenv("MLFLOW_TRACKING_INSECURE_TLS") or "").strip().lower() == "true":
        return ssl._create_unverified_context()
    cafile = (os.getenv("MLFLOW_TRACKING_SERVER_CERT_PATH") or "").strip()
    return ssl.create_default_context(cafile=cafile) if cafile else None


#: MLflow announces, at INFO, that it is sending the queue on the way out --
#: "This may take a while..." -- after every question the CLI answers, where
#: it reads like a hang. A send that fails still says so, at WARNING.
QUIET_LOGGERS = ("mlflow.tracing.export.async_export_queue",)


def _load_mlflow() -> Any:
    for name, value in HTTP_DEFAULTS.items():
        os.environ.setdefault(name, value)
    import mlflow

    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    return mlflow


class Tracer:
    """The connection to MLflow: made on first use, then shared by every run.

    `client` and `probe` are injectable so the whole of this can be tested
    against a fake, with no server and no MLflow installed; `client` is the
    `mlflow` module itself when nothing is given.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        client: Any = None,
        probe: Callable[[str], None] = probe,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.uri = settings.mlflow_tracking_uri
        self.experiment = settings.mlflow_experiment_name
        self._client = client
        self._probe = probe
        self._clock = clock
        self._lock = threading.Lock()
        self._connected = False
        self._retry_at = 0.0
        self.status = "off (MLFLOW_TRACKING_URI is not set)" if not self.uri else "not connected yet"

    @property
    def enabled(self) -> bool:
        return bool(self.uri)

    def mlflow(self) -> Any:
        if self._client is None:
            self._client = _load_mlflow()
        return self._client

    def ready(self) -> bool:
        """Whether runs are traced: connected now, or connected by this call.

        The experiment is set here, once, because MLflow keeps it per
        process; a run never sets it, so two runs at once cannot race on it.
        """
        if not self.uri:
            return False
        with self._lock:
            if self._connected:
                return True
            now = self._clock()
            if now < self._retry_at:
                return False
            try:
                self._probe(self.uri)
                mlflow = self.mlflow()
                mlflow.set_tracking_uri(self.uri)
                mlflow.set_experiment(self.experiment)
            except TRACING_ERRORS as exc:
                self._retry_at = now + RETRY_SECONDS
                self.status = f"MLflow at {self.uri} did not answer ({_reason(exc)}); runs are not traced"
                log.warning("%s; asking again in %gs", self.status, RETRY_SECONDS)
                return False
            self._connected = True
            self.status = f"tracing to {self.uri}, experiment {self.experiment}"
            log.info(self.status)
            return True

    @contextmanager
    def run(self, question: str, *, principal: str | None = None) -> Iterator["RunTrace | None"]:
        """The trace of one run, or None when it is not traced."""
        if not self.ready():
            yield None
            return
        from . import __version__

        mlflow = self.mlflow()
        with mlflow.start_span(name=ROOT_SPAN, span_type="AGENT") as span:
            span.set_inputs({"question": question})
            mlflow.update_current_trace(
                tags={"nl2sql.version": __version__, **dict(_tags.get())},
                # The end user the database was asked on behalf of, when the
                # caller named one: MLflow's own field for it, so the trace
                # list can be filtered by it.
                user=principal,
            )
            token = _active.set(self)
            try:
                yield RunTrace(mlflow, span)
            finally:
                _active.reset(token)

    # --- verdicts ---------------------------------------------------------

    def record_verdict(self, trace_id: str | None, verdict: str, *, comment: str | None = None) -> bool:
        """A person's verdict on the answer, as feedback on its trace.

        Judged again, the earlier verdict is overridden rather than joined
        by a second: MLflow keeps it as the new one's history, which is what
        the staging database's replace-until-reviewed amounts to. False when
        there is no trace to put it on or the server would not take it --
        the staged verdict stands either way.
        """
        if not trace_id or not self.ready():
            return False
        mlflow = self.mlflow()
        try:
            from mlflow.entities import AssessmentSource

            source = AssessmentSource(source_type="HUMAN", source_id=VERDICT_SOURCE)
            current = [a for a in self._verdicts(mlflow, trace_id) if a.valid is not False]
            if current:
                mlflow.override_feedback(
                    trace_id=trace_id,
                    assessment_id=current[-1].assessment_id,
                    value=verdict,
                    rationale=comment,
                    source=source,
                )
            else:
                mlflow.log_feedback(
                    trace_id=trace_id, name=VERDICT, value=verdict, rationale=comment, source=source
                )
        except TRACING_ERRORS as exc:
            log.warning("could not record the verdict on trace %s: %s", trace_id, _reason(exc))
            return False
        return True

    def withdraw_verdict(self, trace_id: str | None) -> bool:
        """Take a withdrawn verdict off the trace, history and all."""
        if not trace_id or not self.ready():
            return False
        mlflow = self.mlflow()
        try:
            for assessment in self._verdicts(mlflow, trace_id):
                mlflow.delete_assessment(trace_id=trace_id, assessment_id=assessment.assessment_id)
        except TRACING_ERRORS as exc:
            log.warning("could not withdraw the verdict on trace %s: %s", trace_id, _reason(exc))
            return False
        return True

    def find_job_trace(self, job_id: str) -> str | None:
        """The trace of an API job, by the id it was tagged with; None when
        there is none or the server cannot be asked."""
        # A job id is hex; anything else came from a URL and is not quoted
        # into a filter.
        if not job_id.isalnum() or not self.ready():
            return None
        try:
            found = self.mlflow().search_traces(
                filter_string=f"tags.`nl2sql.job_id` = '{job_id}'",
                max_results=1,
                return_type="list",
                include_spans=False,
            )
        except TRACING_ERRORS as exc:
            log.warning("could not look up the trace of job %s: %s", job_id, _reason(exc))
            return None
        return found[0].info.trace_id if found else None

    @staticmethod
    def _verdicts(mlflow: Any, trace_id: str) -> list[Any]:
        """Every verdict on the trace, overridden ones included.

        `flush` first: a verdict can arrive before the trace has left this
        process's send queue, and until it has there is nothing to judge.
        """
        trace = mlflow.get_trace(trace_id, flush=True)
        if trace is None:
            raise Refused(f"no trace {trace_id} on the server")
        return [a for a in trace.info.assessments or [] if a.name == VERDICT]


class RunTrace:
    """The open trace of one run, as `Tracer.run` hands it out."""

    def __init__(self, mlflow: Any, span: Any) -> None:
        self._mlflow = mlflow
        self._span = span

    @property
    def trace_id(self) -> str:
        return self._span.trace_id

    def finish(self, state: Mapping[str, Any]) -> None:
        """The answer as the trace's output, and how the run went as its tags."""
        self._span.set_outputs(
            {"answer": state.get("answer", ""), "sql": state.get("sql", ""), "error": state.get("error")}
        )
        result = state.get("result")
        self._mlflow.update_current_trace(
            tags={
                "nl2sql.outcome": outcome(state),
                # The Supervisor's screening, named apart from a person's
                # verdict on the answer, which is the assessment below.
                "nl2sql.screening": str(state.get("verdict", "")),
                "nl2sql.intent": str(state.get("intent", "")),
                "nl2sql.attempts": str(state.get("attempts", 0)),
                "nl2sql.model_calls": str(sum(e.model_calls for e in state.get("trace", []))),
                "nl2sql.rows": str(len(result.rows)) if result is not None else "",
            }
        )


    def finish_ensemble(self, state: Mapping[str, Any]) -> None:
        """What `finish` cannot read off the ensemble's outer state (arch7).

        The agreement, `k/n level`, and how many wordings ran, as two tags
        more; and the attempts and model calls read from the runs rather
        than the outer nodes alone -- the attempts are the delivered run's,
        the calls every run's and the outer nodes' together.
        """
        from .ensemble_state import run_state, whole_trace

        agreement = state.get("agreement")
        candidates = state.get("candidates") or []
        self._mlflow.update_current_trace(
            tags={
                "nl2sql.agreement": f"{getattr(agreement, 'agreed', 0)}/{len(candidates)} "
                f"{getattr(agreement, 'level', 'none')}",
                "nl2sql.candidates": str(len(candidates)),
                "nl2sql.attempts": str(run_state(state).get("attempts", 0)),
                "nl2sql.model_calls": str(sum(entry.model_calls for entry in whole_trace(state))),
            }
        )


def outcome(state: Mapping[str, Any]) -> str:
    """Answered, gave up, or refused: the three ways the graph reaches END."""
    if state.get("verdict", "proceed") != "proceed":
        return REFUSED
    if state.get("error"):
        return GAVE_UP
    return ANSWERED


@contextmanager
def tagged(tags: Mapping[str, str]) -> Iterator[None]:
    """Tag the trace of any run started inside this block."""
    token = _tags.set(_tags.get() + tuple((str(k), str(v)) for k, v in tags.items()))
    try:
        yield
    finally:
        _tags.reset(token)


# --- spans --------------------------------------------------------------------


@contextmanager
def agent_span(name: str, span_type: str, inputs: Callable[[], Mapping[str, Any]]) -> Iterator[Any]:
    """One agent's span, inside the run's trace; None when nothing is traced.

    `inputs` is called only when there is a trace to put them in, so an
    untraced run does not convert state it will not record.
    """
    tracer = _active.get()
    if tracer is None:
        yield None
        return
    with tracer.mlflow().start_span(name=name, span_type=span_type) as span:
        span.set_inputs(to_jsonable(dict(inputs())))
        yield span


def finish_agent_span(span: Any, entry: TraceEntry, update: Mapping[str, Any]) -> None:
    """What the agent wrote back to the state, and the trace entry's record of it."""
    if span is None:
        return
    span.set_outputs(to_jsonable({k: v for k, v in update.items() if k != "trace"}))
    span.set_attributes(
        {
            "nl2sql.node": entry.node,
            "nl2sql.detail": entry.detail,
            "nl2sql.model_calls": entry.model_calls,
            "nl2sql.model": entry.model,
            "nl2sql.rung": entry.rung,
            "nl2sql.route": entry.route,
            "nl2sql.hops": list(entry.hops),
        }
    )


def finish_run_span(span: Any, state: Mapping[str, Any]) -> None:
    """A whole run's span -- one of the ensemble's candidates (arch7) -- given
    the run's answer as its outputs and how it ended, as the root span is."""
    if span is None:
        return
    span.set_outputs({"answer": state.get("answer", ""), "sql": state.get("sql", ""), "error": state.get("error")})
    span.set_attributes({"nl2sql.outcome": outcome(state), "nl2sql.attempts": int(state.get("attempts", 0))})


@contextmanager
def model_span(
    model: str,
    messages: Any,
    *,
    task: str,
    rung: str = "",
    route: str = "",
    schema: type | None = None,
) -> Iterator[Any]:
    """One call to one model; None when nothing is traced.

    An exception raised inside is recorded on the span and passed on: the
    caller decides whether a failed call is a fallback or a failure.
    """
    tracer = _active.get()
    if tracer is None:
        yield None
        return
    with tracer.mlflow().start_span(name=model, span_type="CHAT_MODEL") as span:
        span.set_inputs({"messages": chat_messages(messages)})
        attributes: dict[str, Any] = {
            MODEL: model,
            PROVIDER: "ollama",
            MESSAGE_FORMAT: "langchain",
            "nl2sql.task": task,
            "nl2sql.rung": rung,
            "nl2sql.route": route,
        }
        if schema is not None:
            attributes["nl2sql.structured_output"] = schema.__name__
        span.set_attributes(attributes)
        yield span


def finish_model_span(span: Any, answer: Any) -> None:
    """The model's answer, and the tokens it cost when the client reports them.

    A plain answer is recorded as a chat completion, which is what makes the
    interface draw the call as a conversation; a structured one -- the
    Supervisor's screening, the narrator's claims -- as the object it parsed.
    """
    if span is None:
        return
    if hasattr(answer, "content"):
        span.set_outputs(
            {"choices": [{"message": {"role": "assistant", "content": str(answer.content)}}]}
        )
    elif hasattr(answer, "model_dump"):
        span.set_outputs(to_jsonable(answer.model_dump()))
    else:
        span.set_outputs(to_jsonable(answer))
    usage = getattr(answer, "usage_metadata", None)
    if usage:
        span.set_attribute(
            TOKEN_USAGE,
            {key: int(usage.get(key) or 0) for key in ("input_tokens", "output_tokens", "total_tokens")},
        )
    if not str(getattr(answer, "content", answer) or "").strip():
        # Empty is a fallback in the router, not an error the model raised;
        # this is what tells the two hops apart in the trace.
        span.set_attribute("nl2sql.empty_answer", True)


def chat_messages(messages: Any) -> list[dict[str, Any]]:
    """What a model was sent, as role and content: the shape MLflow draws as a chat.

    Every form LangChain accepts and an agent here uses: message objects
    (a prompt template's), `(role, text)` pairs (the Repair Agent's), and a
    bare string.
    """
    if not isinstance(messages, list):
        messages = [messages]
    out = []
    for message in messages:
        if isinstance(message, tuple):
            kind, content = message
        elif hasattr(message, "type"):
            kind, content = message.type, message.content
        else:
            kind, content = "human", str(message)
        out.append({"role": _ROLES.get(kind, kind), "content": to_jsonable(content)})
    return out


def _reason(exc: Exception) -> str:
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return text[:200]
