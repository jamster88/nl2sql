"""Tracing against a real MLflow server: docker/mlflow/Dockerfile, built and run here.

Everything else about tracing is tested on a fake (tests/fake_mlflow.py);
this is where the fake is held to the real thing. The pipeline runs on the
same fakes as test_graph.py -- no Ollama, no Postgres -- and its trace goes to
a throwaway server started from the image compose builds, on a private
network where it answers to `nl2sql-mlflow`, the name setup.sh
writes into .env, behind the rebinding guard compose gives it. Read back
with MLflow's own client, the trace has to be what the fake said it would.

Then the two clients: the benchmark's runs, through `mlflow-skinny` as
tests/requirements.txt installs it, and the agent image's own -- which
carries the tracing client alone, so a call that client lacks fails there
and nowhere else.

Opt-in (--run-docker): it pulls MLflow's image (about 370 MB) the first time.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.docker

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
COMPOSE = (REPO_ROOT / "docker-compose.yml").read_text()
#: Built from docker/mlflow/Dockerfile, as compose builds it unpinned.
IMAGE = "nl2sql-mlflow:pytest"
ALLOWED_HOSTS = re.search(r"--allowed-hosts=\$\{MLFLOW_ALLOWED_HOSTS:-([^}]+)\}", COMPOSE).group(1)
AGENT_IMAGE = "nl2sql-agent:pytest"


def _docker(*args: str, timeout: int = 120, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout, check=check)


@pytest.fixture(scope="module")
def server(docker_daemon_available: bool):
    """A server on its own network, answering to the name compose gives it."""
    if not docker_daemon_available:
        pytest.skip("no working docker daemon")
    built = subprocess.run(
        ["docker", "build", "-f", "docker/mlflow/Dockerfile", "-t", IMAGE, "."],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=900,
    )
    if built.returncode != 0:
        pytest.skip(f"could not build the MLflow image:\n{built.stderr[-2000:]}")
    suffix = uuid.uuid4().hex[:8]
    network, name = f"nl2sql-mlflow-test-{suffix}", f"nl2sql-mlflow-test-{suffix}"
    _docker("network", "create", network)
    try:
        _docker(
            "run", "-d", "--rm", "--name", name, "--network", network, "--network-alias", "nl2sql-mlflow",
            "-p", "127.0.0.1::5000", IMAGE,
            "mlflow", "server", "--host=0.0.0.0", "--port=5000",
            "--backend-store-uri=sqlite:////tmp/mlflow.db",
            "--serve-artifacts", "--artifacts-destination=/tmp/artifacts",
            f"--allowed-hosts={ALLOWED_HOSTS}", "--workers=1",
            timeout=600,
        )
        port = _docker("port", name, "5000/tcp").stdout.strip().rsplit(":", 1)[1]
        url = f"http://127.0.0.1:{port}"
        for _ in range(90):
            try:
                urllib.request.urlopen(f"{url}/health", timeout=2).read()
                break
            except Exception:
                time.sleep(1)
        else:
            pytest.fail(f"MLflow never answered:\n{_docker('logs', name, check=False).stderr[-2000:]}")
        yield {"url": url, "network": network, "name": name}
    finally:
        _docker("rm", "-f", name, check=False)
        _docker("network", "rm", network, check=False)


@pytest.fixture(scope="module")
def mlflow_client(server):
    import mlflow

    mlflow.set_tracking_uri(server["url"])
    return mlflow


def _agent(server, experiment: str, llm=None):
    from nl2sql_agent.config import Settings
    from nl2sql_agent.tracing import Tracer

    from tests.agent.conftest import FakeDatabase
    from tests.agent.test_graph import TABLES, make_agent, scripted

    tracer = Tracer(Settings(mlflow_tracking_uri=server["url"], mlflow_experiment_name=experiment))
    return make_agent(FakeDatabase(tables=TABLES), llm or scripted(["SELECT count(*) AS n FROM dim_store"]),
                      tracer=tracer)


def _tree(trace) -> list[tuple[int, str, str]]:
    """(depth, name, type) for every span, in the order they started."""
    by_id = {span.span_id: span for span in trace.data.spans}

    def depth(span) -> int:
        return 0 if not span.parent_id else 1 + depth(by_id[span.parent_id])

    return [(depth(s), s.name, s.span_type) for s in sorted(trace.data.spans, key=lambda s: s.start_time_ns)]


# ---------------------------------------------------------------------------
# The pipeline's trace
# ---------------------------------------------------------------------------


def test_a_question_is_one_trace_shaped_like_the_architecture(server, mlflow_client):
    from nl2sql_agent import __version__, tracing

    agent = _agent(server, "pytest-shape")
    with tracing.tagged({"nl2sql.job_id": "shape"}):
        state = agent.run("How many stores are there?", principal="store_manager")

    trace = mlflow_client.get_trace(state["trace_id"], flush=True)
    assert trace.info.state == "OK"
    tree = _tree(trace)
    assert tree[0] == (0, "nl2sql", "AGENT")
    agents = [(name, kind) for depth, name, kind in tree if depth == 1]
    assert ("Supervisor", "AGENT") in agents and ("Answer", "TASK") in agents
    assert {name for name, kind in agents if kind == "RETRIEVER"} == {
        "Schema Retriever", "Literal Matcher", "Knowledge Retriever", "Example Retriever", "Snippet Retriever",
    }
    calls = [name for depth, name, kind in tree if depth == 2 and kind == "CHAT_MODEL"]
    assert len(calls) == sum(e.model_calls for e in state["trace"])

    assert trace.info.tags["nl2sql.version"] == __version__
    assert trace.info.tags["nl2sql.job_id"] == "shape"
    assert trace.info.tags["nl2sql.outcome"] == "answered"
    assert trace.info.trace_metadata["mlflow.trace.user"] == "store_manager"
    generator = next(s for s in trace.data.spans if s.name == "SQL Generator")
    assert generator.outputs["sql"] == "SELECT count(*) AS n FROM dim_store"
    model_call = next(s for s in trace.data.spans if s.parent_id == generator.span_id)
    assert model_call.attributes["nl2sql.task"] == "generator"
    assert model_call.outputs["choices"][0]["message"]["content"] == "SELECT count(*) AS n FROM dim_store"


def test_a_verdict_lands_on_the_trace_is_replaced_and_withdrawn(server, mlflow_client):
    from nl2sql_agent import tracing

    agent = _agent(server, "pytest-verdicts")
    with tracing.tagged({"nl2sql.job_id": "verdicts"}):
        trace_id = agent.run("How many stores are there?")["trace_id"]

    assert agent.tracer.record_verdict(trace_id, "no", comment="joined on the wrong key")
    assert agent.tracer.record_verdict(trace_id, "yes")
    assessments = mlflow_client.get_trace(trace_id).info.assessments
    assert sorted((a.name, a.feedback.value, a.valid) for a in assessments) == [
        ("verdict", "no", False), ("verdict", "yes", True),
    ]
    assert agent.tracer.find_job_trace("verdicts") == trace_id
    assert agent.tracer.withdraw_verdict(trace_id)
    assert not mlflow_client.get_trace(trace_id).info.assessments


def test_the_benchmark_files_a_configuration_as_a_run_holding_its_traces(server, mlflow_client):
    from benchmarks import tracking
    from benchmarks.questions import by_id
    from benchmarks.runner import CORRECT, BenchmarkReport, QuestionResult, StageTiming
    from nl2sql_agent import tracing

    agent = _agent(server, "pytest-benchmark")
    question = by_id("B01")
    run = tracking.open_run(agent, "multi-shot", [question])
    with tracing.tagged(tracking.question_tags(question, "multi-shot")):
        state = agent.run(question.question)
    result = QuestionResult(
        question_id="B01", category=question.category, question=question.question, outcome=CORRECT,
        wall_seconds=1.0, timing=StageTiming(stages=[("generate_sql", 1.0)]), trace_id=state["trace_id"],
    )
    run.score(result)
    report = BenchmarkReport(label="multi-shot", results=[result])
    run.finish(report, {"configurations": [{"label": "multi-shot"}]}, complete=True)

    recorded = mlflow_client.MlflowClient().get_run(run.run_id)
    assert recorded.info.status == "FINISHED"
    assert recorded.data.params["questions"] == "B01"
    assert recorded.data.metrics["accuracy"] == 1.0
    assert [f.path for f in mlflow_client.MlflowClient().list_artifacts(run.run_id)] == ["benchmark.json"]
    [trace] = mlflow_client.search_traces(run_id=run.run_id, return_type="list", flush=True)
    assert trace.info.trace_id == state["trace_id"]
    [score] = trace.info.assessments
    assert (score.name, score.feedback.value) == (tracking.SCORE, True)


# ---------------------------------------------------------------------------
# The agent image's client, and the server's guard
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def agent_image(server) -> str:
    result = subprocess.run(
        ["docker", "build", "-f", "agent/Dockerfile", "-t", AGENT_IMAGE, "."],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0:
        pytest.skip(f"could not build the agent image:\n{result.stderr[-2000:]}")
    return AGENT_IMAGE


IN_THE_IMAGE = """
import json
from nl2sql_agent import tracing
from nl2sql_agent.config import Settings
from nl2sql_agent.state import TraceEntry
from nl2sql_agent.tracing import Tracer

tracer = Tracer(Settings.from_env())
assert tracer.ready(), tracer.status
with tracing.tagged({"nl2sql.job_id": "fromtheimage"}):
    with tracer.run("How many stores?") as trace:
        with tracing.agent_span("SQL Generator", "AGENT", lambda: {"question": "How many stores?"}) as span:
            with tracing.model_span("m", [("human", "How many stores?")], task="generator") as call:
                tracing.finish_model_span(call, type("A", (), {"content": "SELECT 1", "usage_metadata": None})())
            tracing.finish_agent_span(span, TraceEntry(node="generate_sql"), {"sql": "SELECT 1"})
        trace.finish({"answer": "1", "sql": "SELECT 1", "verdict": "proceed", "result": None})
assert tracer.record_verdict(trace.trace_id, "incomplete")
assert tracer.record_verdict(trace.trace_id, "yes")
assert tracer.find_job_trace("fromtheimage") == trace.trace_id
assert tracer.withdraw_verdict(trace.trace_id)
assert tracer.record_verdict(trace.trace_id, "yes")
import importlib.util
print(json.dumps({"trace_id": trace.trace_id, "skinny": importlib.util.find_spec("mlflow.tracking") is not None}))
"""


def test_the_agent_images_own_client_does_everything_the_agent_asks_of_it(server, agent_image, mlflow_client):
    """By the name setup.sh writes, through the rebinding guard compose sets."""
    result = _docker(
        "run", "--rm", "--network", server["network"], "--entrypoint", "python",
        "-e", "MLFLOW_TRACKING_URI=http://nl2sql-mlflow:5000", "-e", "MLFLOW_EXPERIMENT_NAME=pytest-image",
        agent_image, "-c", IN_THE_IMAGE,
        timeout=180, check=False,
    )
    assert result.returncode == 0, result.stderr[-3000:]
    said = json.loads(result.stdout.strip().splitlines()[-1])
    trace = mlflow_client.get_trace(said["trace_id"])
    assert [a.feedback.value for a in trace.info.assessments if a.valid] == ["yes"]
    assert {s.name for s in trace.data.spans} == {"nl2sql", "SQL Generator", "m"}


def test_the_rebinding_guard_turns_away_a_name_it_was_not_given(server):
    request = urllib.request.Request(
        f"{server['url']}/api/2.0/mlflow/experiments/search?max_results=1", headers={"Host": "attacker.example"}
    )
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(request, timeout=5)
    assert refused.value.code == 403


def test_the_guard_compose_sets_keeps_everything_mlflows_own_default_lets_in(server):
    """Naming any host replaces MLflow's default list rather than adding to
    it, so compose restates it -- and a new MLflow that widens its default
    would otherwise narrow ours without a word."""
    said = _docker(
        "run", "--rm", "--entrypoint", "python", IMAGE, "-c",
        "from mlflow.server.security_utils import get_default_allowed_hosts as d; print(','.join(d()))",
    ).stdout.strip()
    assert set(said.split(",")) <= set(ALLOWED_HOSTS.split(","))
