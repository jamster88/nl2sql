"""models/build_catalog.py: the model catalog of arch5.2, section 15.1, steps
1 to 4 -- inventory, facts, library, prior.

Most of these run the script against a fake Ollama host answering exactly
what the real one answered on 2026-09-27, and a fake ollama.com serving the
real descriptions and badges, so what is tested is what the script makes of
the shapes it will really meet. The rules of the prior are then tested one
at a time on models built to sit on their boundaries.
"""

from __future__ import annotations

import ast
import json
import os
import socket
import ssl
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from .conftest import FIXTURES, REPO_ROOT, SCRIPT, library_page

SPEC = REPO_ROOT / "multi-agent_arch_specs" / "Multi-Agent_NL2SQL_arch5_2.md"
COMMITTED = REPO_ROOT / "models" / "catalog.json"


def closed_port_url() -> str:
    """A loopback URL nothing listens on: connections are refused at once."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}"


def run(build_catalog, tmp_path: Path, *args: str, environ: dict | None = None):
    """main() with a clean environment; the exit code and what it wrote."""
    out = tmp_path / "catalog.json"
    code = build_catalog.main([*args, "--out", str(out)], environ=environ or {})
    return code, (json.loads(out.read_text()) if out.exists() else None)


def by_name(catalog: dict) -> dict[str, dict]:
    return {model["name"]: model for model in catalog["models"]}


def a_model(name: str, *, params: float | None = 27.3e9, capabilities=("completion", "tools"),
            context: int | None = 262144, quant: str = "Q4_K_M", family: str = "qwen35",
            parent: str = "", experts: tuple[int, int] | None = None, remote_host: str | None = None):
    """A `/api/tags` entry and an `/api/show` answer for a made-up model, in
    Ollama's shapes."""
    arch = family or "llama"
    info: dict = {"general.architecture": arch}
    if params is not None:
        info["general.parameter_count"] = int(params)
    if context is not None:
        info[f"{arch}.context_length"] = context
    if experts:
        info[f"{arch}.expert_count"], info[f"{arch}.expert_used_count"] = experts
    details = {"parent_model": parent, "format": "gguf", "family": family,
               "parameter_size": f"{params / 1e9:.1f}B" if params else "", "quantization_level": quant}
    entry = {"name": name, "model": name, "size": 1_000, "digest": "0" * 64,
             "modified_at": "2026-09-27T10:00:00-07:00", "details": details,
             "capabilities": list(capabilities or [])}
    show = {"details": details, "capabilities": list(capabilities) if capabilities else None,
            "model_info": info, "parameters": "temperature                    1", "license": "MIT License"}
    if remote_host:
        show["remote_host"] = remote_host
    return entry, show


def facts(build_catalog, name: str = "made-up:7b", **kwargs) -> dict:
    return build_catalog.facts_of(*a_model(name, **kwargs))


def small_host(serve, *models) -> object:
    server = serve()
    server.routes["/api/tags"] = (200, {"models": [entry for entry, _ in models]})
    server.routes["/api/version"] = (200, {"version": "0.34.4"})
    for entry, show in models:
        server.routes[f"/api/show {entry['name']}"] = (200, show)
    return server


# ---------------------------------------------------------------------------
# The host as it was
# ---------------------------------------------------------------------------


def test_every_model_the_host_serves_is_catalogued_from_its_own_answers(build_catalog, host, library, snapshot, tmp_path):
    code, catalog = run(build_catalog, tmp_path, "--host", host.url)

    assert code == 0
    listed = sorted(model["name"] for model in snapshot["tags"]["models"])
    assert [model["name"] for model in catalog["models"]] == listed
    for model in catalog["models"]:
        show = snapshot["show"][model["name"]]
        arch = show["model_info"]["general.architecture"]
        assert model["facts_from"] == "show"
        assert model["facts"]["parameter_count"] == show["model_info"]["general.parameter_count"]
        assert model["facts"]["context_length"] == show["model_info"][f"{arch}.context_length"]
        assert model["facts"]["capabilities"] == sorted(show["capabilities"])
        assert model["chat"] is True
    assert catalog["host"] == host.url
    assert catalog["ollama_version"] == "0.34.4"


def _spec_table() -> dict[str, tuple[str, str]]:
    """Section 15.1's "Today's host, through those rules": model -> (prior
    for the generator, prior for the Supervisor)."""
    lines = SPEC.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if "Prior: generator" in line)
    table = {}
    for line in lines[start + 2:]:
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        table[cells[0].strip("`")] = (cells[6].split()[0].rstrip(","), cells[7].split()[0])
    return table


def test_the_prior_is_the_table_the_spec_worked_by_hand(build_catalog, host, library, tmp_path):
    """arch5.2 applied its rules to the ten models the host served when it
    was written. The script applies the same rules to the same facts -- and
    the library descriptions the spec did not read -- and must agree."""
    _, catalog = run(build_catalog, tmp_path, "--host", host.url)
    models = by_name(catalog)

    table = _spec_table()
    assert len(table) == 10
    for name, (generator, supervisor) in table.items():
        prior = models[build_catalog.full_name(name)]["prior"]
        assert (prior["generator"], prior["supervisor"]) == (generator, supervisor), name


def test_a_general_model_that_lists_coding_among_its_strengths_is_not_a_code_model(build_catalog, host, library, tmp_path):
    """Every current family's description names coding. Read as a code
    model, qwen3.8 -- the reference -- would be presumed a heavy generator,
    and so would a 12 B gemma4; the spec's own table says standard for both."""
    _, catalog = run(build_catalog, tmp_path, "--host", host.url)
    models = by_name(catalog)

    for name in ("qwen3.8:latest", "gemma4:12b-mlx", "qwen3.6:latest", "falcon3:10b"):
        assert "code" in models[name]["library"]["tags"], name
        assert not any("code model" in reason for reason in models[name]["prior"]["reasons"]), name
    assert "a code model by name (qwen3-coder-next): generator and repair one rung up" in (
        models["qwen3-coder-next:latest"]["prior"]["reasons"]
    )


def test_the_mlx_builds_are_described_by_show_where_tags_says_nothing(build_catalog, host, library, snapshot, tmp_path):
    tags = next(m for m in snapshot["tags"]["models"] if m["name"] == "gemma4:31b-mlx")
    assert not tags["details"]["parameter_size"] and not tags["details"]["family"]

    _, catalog = run(build_catalog, tmp_path, "--host", host.url)
    gemma = by_name(catalog)["gemma4:31b-mlx"]["facts"]

    assert gemma["parameter_count"] == 31742607472
    assert (gemma["family"], gemma["quantization"], gemma["context_length"]) == ("gemma4", "nvfp4", 262144)


def test_a_local_build_is_described_by_its_parents_library_page(build_catalog, host, library, tmp_path):
    """qwen3.8-256k is qwen3.8 with a bigger window baked in, and has no page
    of its own -- ollama.com answers 404, and the parent's page is used."""
    _, catalog = run(build_catalog, tmp_path, "--host", host.url)

    page = by_name(catalog)["qwen3.8-256k:latest"]["library"]
    assert page["page"] == f"{library.url}/library/qwen3.8"
    assert page["badges"] == ["vision", "tools", "thinking"]
    assert page["sizes"] == ["27b"]
    asked = library.asked()
    assert asked.index("/library/qwen3.8-256k") < asked.index("/library/qwen3.8")


def test_each_library_page_is_fetched_once(build_catalog, host, library, tmp_path):
    """Four deepseek-r1 sizes and three gemma4 builds share a page each."""
    run(build_catalog, tmp_path, "--host", host.url)
    assert sorted(library.asked()) == sorted(set(library.asked()))


def test_an_uncalibrated_catalog_says_so(build_catalog, host, library, tmp_path):
    _, catalog = run(build_catalog, tmp_path, "--host", host.url)

    assert catalog["schema"] == build_catalog.SCHEMA_VERSION
    assert catalog["calibrated"] is None
    assert (catalog["rungs"], catalog["tasks"]) == (["light", "standard", "heavy"],
                                                    ["supervisor", "generator", "reflection", "narrator", "repair"])
    assert catalog["task_budgets"] == build_catalog.TASK_BUDGETS
    for model in catalog["models"]:
        assert model["measured"] == {}
        assert model["suited_from"] == {task: "prior" for task in catalog["tasks"]}
        assert model["suited"] == {task: model["prior"][task] for task in catalog["tasks"]}


def test_the_reference_model_is_named_and_found(build_catalog, host, library, tmp_path, capsys):
    _, catalog = run(build_catalog, tmp_path, "--host", host.url, "--reference", "qwen3.8-256k")

    assert catalog["reference"] == "qwen3.8-256k:latest"
    assert catalog["reference_on_host"] is True
    assert "reference qwen3.8-256k:latest: on the host" in capsys.readouterr().out


def test_the_summary_shows_every_model_and_its_prior(build_catalog, host, library, tmp_path, capsys):
    run(build_catalog, tmp_path, "--host", host.url)
    out = capsys.readouterr().out

    assert "Ollama 0.34.4 at" in out and "18 models, 18 of them chat candidates" in out
    header = next(line for line in out.splitlines() if line.startswith("model"))
    assert header.split() == ["model", "params", "quant", "context", "supervisor", "generator",
                              "reflection", "narrator", "repair"]
    assert "qwen3-coder-next:latest  79.7B   Q4_K_M  256k     heavy" in out
    assert f"wrote {tmp_path / 'catalog.json'}" in out


def test_the_requests_say_who_is_asking(build_catalog, host, library, tmp_path):
    run(build_catalog, tmp_path, "--host", host.url)

    for server in (host, library):
        assert {headers["User-Agent"] for _, headers in server.requests} == {build_catalog.USER_AGENT}
    shows = [headers for key, headers in host.requests if key.startswith("/api/show")]
    assert shows and all(headers["Content-Type"] == "application/json" for headers in shows)


# ---------------------------------------------------------------------------
# The rules of the prior, one at a time
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "billions,classifying,drafting",
    [(3.3, "light", "light"), (6.9, "light", "light"), (7.0, "heavy", "light"), (9.9, "heavy", "light"),
     (10.0, "heavy", "standard"), (40.0, "heavy", "standard"), (40.1, "heavy", "heavy")],
)
def test_size_bands(build_catalog, billions, classifying, drafting):
    prior = build_catalog.prior_for("m", facts(build_catalog, params=billions * 1e9), {})
    assert (prior["supervisor"], prior["reflection"]) == (classifying, classifying)
    assert (prior["generator"], prior["narrator"], prior["repair"]) == (drafting, drafting, drafting)


def test_an_embedding_model_is_never_a_chat_candidate(build_catalog):
    for fact in (facts(build_catalog, params=0.57e9, capabilities=("embedding",), family="bert"),
                 # /api/show failed and /api/tags named no capabilities: the family says it.
                 facts(build_catalog, params=0.57e9, capabilities=None, family="nomic-bert")):
        prior = build_catalog.prior_for("bge-m3:latest", fact, {})
        assert {prior[task] for task in build_catalog.TASKS} == {None}
        assert prior["reasons"] == ["an embedding model: never routed a chat task"]
        assert build_catalog.not_chat(fact)


def test_a_model_run_by_another_host_is_not_a_candidate(build_catalog):
    """A cloud model is listed by the host and run elsewhere: routing to it
    would send the question and the schema off the network."""
    prior = build_catalog.prior_for("gpt-oss:120b-cloud", facts(
        build_catalog, remote_host="https://ollama.com:443"), {})

    assert {prior[task] for task in build_catalog.TASKS} == {None}
    assert "served by https://ollama.com:443" in prior["reasons"][0]


def test_an_unknown_size_is_light_until_measured_and_promoted_for_nothing(build_catalog):
    """A promotion adjusts the rung the size gave; with no size there is
    nothing to adjust, and a thinking code model of unknown size might be
    1.5 B."""
    fact = facts(build_catalog, "some-coder:latest", params=None, capabilities=("completion", "tools", "thinking"))
    prior = build_catalog.prior_for("some-coder:latest", fact, {"tags": ["reasoning"]})

    assert {prior[task] for task in build_catalog.TASKS} == {"light"}
    assert prior["reasons"] == ["parameter count unknown: light for every task until measured"]


@pytest.mark.parametrize("name,description", [
    ("qwen2.5-coder:32b", None),
    ("codestral:22b", None),
    ("devstral:24b", "Devstral: the best open source model for coding agents"),
    # devstral-small-2's page on 2026-09-27: neither "code" nor "coding" in it.
    ("devstral-small-2:24b", "24B model that excels at using tools to explore codebases, editing multiple "
                             "files and power software engineering agents."),
    ("granite-x:20b", "A code model designed for code generation tasks."),
])
def test_a_code_model_drafts_and_repairs_a_rung_up(build_catalog, name, description):
    prior = build_catalog.prior_for(name, facts(build_catalog, name, params=22e9, capabilities=("completion", "tools")),
                                    {"description": description})
    assert (prior["generator"], prior["repair"], prior["narrator"]) == ("heavy", "heavy", "standard")


def test_a_reasoning_model_repairs_a_rung_up(build_catalog):
    plain = build_catalog.prior_for("m", facts(build_catalog, params=22e9), {})
    thinking = build_catalog.prior_for("m", facts(build_catalog, params=22e9,
                                                  capabilities=("completion", "tools", "thinking")), {})
    described = build_catalog.prior_for("m", facts(build_catalog, params=22e9), {"tags": ["reasoning"]})

    assert plain["repair"] == "standard"
    assert thinking["repair"] == described["repair"] == "heavy"
    assert thinking["generator"] == described["generator"] == "standard"


@pytest.mark.parametrize("quant,drops", [("Q3_K_S", True), ("IQ2_XXS", True), ("Q4_K_M", False),
                                         ("F16", False), ("", False)])
def test_quantised_below_four_bits_is_a_rung_down(build_catalog, quant, drops):
    prior = build_catalog.prior_for("m", facts(build_catalog, params=22e9, quant=quant), {})
    assert (prior["generator"], prior["supervisor"]) == (("light", "standard") if drops else ("standard", "heavy"))


def test_without_tool_support_the_structured_tasks_are_a_rung_down(build_catalog):
    none = build_catalog.prior_for("m", facts(build_catalog, params=22e9, capabilities=("completion",)), {})
    unknown = build_catalog.prior_for("m", facts(build_catalog, params=22e9, capabilities=None), {})

    for prior in (none, unknown):
        assert [prior[t] for t in ("supervisor", "reflection", "narrator")] == ["standard", "standard", "light"]
        assert (prior["generator"], prior["repair"]) == ("standard", "standard")
    assert none["reasons"][-1].startswith("no tool support")
    assert unknown["reasons"][-1].startswith("capabilities unknown")


def test_a_window_smaller_than_a_tasks_prompt_rules_the_model_out_for_it(build_catalog):
    medium = build_catalog.prior_for("m", facts(build_catalog, context=12000), {})
    tiny = build_catalog.prior_for("m", facts(build_catalog, context=4096), {})
    unknown = build_catalog.prior_for("m", facts(build_catalog, context=None), {})

    assert (medium["generator"], medium["repair"]) == (None, None)
    assert (medium["supervisor"], medium["narrator"]) == ("heavy", "standard")
    assert {tiny[task] for task in build_catalog.TASKS} == {None}
    assert None not in {unknown[task] for task in build_catalog.TASKS}
    assert unknown["reasons"][-1] == "context length unknown: no task excluded for it"


def test_rungs_stop_at_the_ends_of_the_ladder(build_catalog):
    """Adjustments are summed and clamped: a heavy code model stays heavy, and
    a small, crudely quantised one without tools stays light."""
    big = build_catalog.prior_for("big-coder:70b", facts(build_catalog, params=70e9), {})
    small = build_catalog.prior_for("m", facts(build_catalog, params=3e9, quant="Q2_K", capabilities=("completion",)), {})

    assert {big[task] for task in build_catalog.TASKS} == {"heavy"}
    assert {small[task] for task in build_catalog.TASKS} == {"light"}


def test_a_mixture_of_experts_is_placed_by_its_total_size(build_catalog):
    prior = build_catalog.prior_for("m", facts(build_catalog, params=46.7e9, experts=(8, 2)), {})
    assert prior["generator"] == "heavy"
    assert "a mixture of 8 experts, 2 active per token" in prior["reasons"][1]


# ---------------------------------------------------------------------------
# Reading what the host and the library say
# ---------------------------------------------------------------------------


def test_the_real_library_markup_is_read(build_catalog):
    page = build_catalog.parse_library_page((FIXTURES / "ollama_library_qwen3.8.html").read_text())
    assert page == {
        "description": "Qwen3.8 delivers substantial gains across coding, professional work, research, "
                       "and long-horizon agentic tasks.",
        "badges": ["vision", "tools", "thinking"],
        "sizes": ["27b"],
        "tags": ["code"],
    }


def test_a_page_with_no_description_and_an_empty_badge_is_read_as_such(build_catalog):
    page = build_catalog.parse_library_page(library_page(None, ["tools", " ", "8x22b", "e4b", "1.5b"]))
    assert page == {"description": None, "badges": ["tools"], "sizes": ["8x22b", "e4b", "1.5b"], "tags": []}


def test_description_keywords_are_tagged(build_catalog):
    page = build_catalog.parse_library_page(library_page(
        "An instruct chat assistant with vision, for reasoning over images and code; not embeddings.", []))
    assert page["tags"] == ["code", "reasoning", "vision", "embedding", "chat", "instruct"]
    assert build_catalog.parse_library_page(library_page("Explores codebases.", []))["tags"] == ["code"]


@pytest.mark.parametrize("label,count", [("27.3B", 27_300_000_000), ("566.70M", 566_700_000),
                                         ("1.2T", 1_200_000_000_000), ("12k", 12_000), ("", None),
                                         (None, None), ("big", None)])
def test_parameter_size_labels(build_catalog, label, count):
    assert build_catalog.parse_size(label) == count


def test_only_numeric_defaults_are_kept(build_catalog):
    text = 'num_ctx                        262144\ntop_p     0.95\n\nstop   "[INST]"\ntemperature 1'
    assert build_catalog.parse_parameters(text) == {"num_ctx": 262144, "top_p": 0.95, "temperature": 1}
    assert build_catalog.parse_parameters(None) == {}


@pytest.mark.parametrize("label,bits", [("Q4_K_M", 4), ("IQ3_XXS", 3), ("Q2_K", 2), ("Q8_0", 8), ("MXFP4", 4),
                                        ("nvfp4", 4), ("F16", 16), ("BF16", 16), ("F32", 32), ("", None),
                                        (None, None), ("weird", None)])
def test_quantisation_labels(build_catalog, label, bits):
    assert build_catalog.quant_bits(label) == bits


def test_the_license_is_its_first_line(build_catalog):
    assert build_catalog.first_line("\n\n   MIT License  \nCopyright ...") == "MIT License"
    assert build_catalog.first_line(None) is None


@pytest.mark.parametrize("name,path", [
    ("qwen3.8:latest", "library/qwen3.8"),
    ("mistral", "library/mistral"),
    ("someone/tuned:7b", "someone/tuned"),
    ("hf.co/org/repo:Q4_K_M", None),
    ("registry.example.com/model", None),
    ("localhost:5000/model", None),
])
def test_where_a_model_is_on_ollama_com(build_catalog, name, path):
    assert build_catalog.library_path(name) == path


def test_a_models_ancestry_is_walked_for_pages_once_each(build_catalog):
    parents = {"a-256k:latest": "a:latest", "a:latest": "a:27b-q4", "b:latest": "c:latest", "c:latest": "b:latest"}

    assert build_catalog.library_candidates("a-256k:latest", parents) == ["library/a-256k", "library/a"]
    assert build_catalog.library_candidates("b:latest", parents) == ["library/b", "library/c"]
    assert build_catalog.library_candidates("hf.co/org/repo:q4", parents) == []


@pytest.mark.parametrize("given,expected", [("qwen3.8-256k", "qwen3.8-256k:latest"),
                                            ("qwen3.8:27b", "qwen3.8:27b"),
                                            ("someone/model", "someone/model:latest")])
def test_names_are_completed_as_ollama_completes_them(build_catalog, given, expected):
    assert build_catalog.full_name(given) == expected


# ---------------------------------------------------------------------------
# When the host or the library does not answer
# ---------------------------------------------------------------------------


def test_an_unreachable_host_stops_the_run_and_writes_nothing(build_catalog, tmp_path, capsys):
    code, catalog = run(build_catalog, tmp_path, "--host", closed_port_url())

    assert (code, catalog) == (1, None)
    err = capsys.readouterr().err
    assert "cannot reach Ollama at http://127.0.0.1:" in err
    assert "port 11434 serves http, not https" in err


@pytest.mark.parametrize("status,payload,message", [
    (500, {"error": "boom"}, "answered 500"),
    (200, b"<html>not json</html>", "did not answer with Ollama's model list"),
    (200, [1, 2, 3], "did not answer with Ollama's model list: not a JSON object"),
    (200, {"tags": []}, "did not answer with Ollama's model list"),
    (200, {"models": []}, "serves no models"),
    (200, {"models": [{"name": ""}, "junk", {"size": 1}]}, "serves no models"),
])
def test_a_host_that_answers_without_a_model_list_stops_the_run(build_catalog, serve, tmp_path, capsys, status, payload, message):
    server = serve()
    server.routes["/api/tags"] = (status, payload)

    code, catalog = run(build_catalog, tmp_path, "--host", server.url)

    assert (code, catalog) == (1, None)
    assert message in capsys.readouterr().err


def test_a_model_whose_show_fails_is_placed_from_tags_alone(build_catalog, host, library, tmp_path, capsys):
    """mistral:7b's `/api/tags` entry carries its size, capabilities and
    context, so it lands where `/api/show` would have put it. An MLX build's
    carries none of them, so it is light and says why."""
    del host.routes["/api/show mistral:7b"]
    del host.routes["/api/show gemma4:31b-mlx"]

    code, catalog = run(build_catalog, tmp_path, "--host", host.url)

    assert code == 0
    mistral, gemma = by_name(catalog)["mistral:7b"], by_name(catalog)["gemma4:31b-mlx"]
    assert mistral["facts_from"] == "tags"
    assert mistral["show_error"] == "/api/show failed: HTTP Error 404: Not Found"
    assert (mistral["prior"]["generator"], mistral["prior"]["supervisor"]) == ("light", "heavy")
    assert {gemma["prior"][task] for task in build_catalog.TASKS} == {"light"}
    assert "context length unknown: no task excluded for it" in gemma["prior"]["reasons"]
    err = capsys.readouterr().err
    assert "warning: mistral:7b: /api/show failed" in err and "catalogued from /api/tags alone" in err


def test_an_unknown_version_is_said_so(build_catalog, host, library, tmp_path, capsys):
    del host.routes["/api/version"]
    _, catalog = run(build_catalog, tmp_path, "--host", host.url)

    assert catalog["ollama_version"] is None
    assert capsys.readouterr().out.startswith("Ollama (version unknown) at")


def test_an_unreachable_library_is_given_up_on_after_one_try(build_catalog, host, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(build_catalog, "LIBRARY_URL", closed_port_url())
    _, catalog = run(build_catalog, tmp_path, "--host", host.url)

    reasons = {model["library"]["unavailable"] for model in catalog["models"]}
    assert len(reasons) == 1 and "unreachable" in reasons.pop()
    assert "note: no library pages" in capsys.readouterr().err


def test_a_library_that_answers_with_an_error_is_given_up_on_too(build_catalog, host, serve, tmp_path, monkeypatch):
    site = serve()
    site.routes = {f"/library/{name}": (503, "busy") for name in ("deepseek-r1", "falcon3", "gemma4")}
    monkeypatch.setattr(build_catalog, "LIBRARY_URL", site.url)

    _, catalog = run(build_catalog, tmp_path, "--host", host.url)

    assert site.asked() == ["/library/deepseek-r1"]
    assert {model["library"]["unavailable"] for model in catalog["models"]} == {f"{site.url} answered 503"}


def test_without_the_library_nothing_leaves_for_ollama_com(build_catalog, host, library, tmp_path, capsys):
    _, catalog = run(build_catalog, tmp_path, "--host", host.url, "--no-library")

    assert library.requests == []
    assert {model["library"]["unavailable"] for model in catalog["models"]} == {"skipped (--no-library)"}
    assert "note:" not in capsys.readouterr().err


@pytest.fixture
def https_library(serve, tmp_path):
    """ollama.com over real TLS, with a certificate no authority signed: the
    certificate itself is the only thing that can vouch for it."""
    from nl2sql_agent.api.tls import generate_self_signed

    certificate, key = tmp_path / "authority.pem", tmp_path / "key.pem"
    generate_self_signed(certificate, key, hostnames=["127.0.0.1"])
    site = serve(tls=(certificate, key))
    site.routes["/library/qwen3.8"] = (200, library_page("Qwen3.8 delivers.", ["tools"]))
    return site, certificate


def _python_trusts(monkeypatch, build_catalog, own: str | None, system: list[Path]) -> None:
    """Python's own default authorities (a file, or none), and the system
    bundles there are to borrow."""
    monkeypatch.setattr(build_catalog.ssl, "get_default_verify_paths",
                        lambda: ssl.DefaultVerifyPaths(own, None, "SSL_CERT_FILE", "", "SSL_CERT_DIR", ""))
    monkeypatch.setattr(build_catalog, "SYSTEM_CA_BUNDLES", tuple(str(path) for path in system))


def test_a_python_with_no_authorities_of_its_own_borrows_the_systems(build_catalog, https_library, tmp_path, monkeypatch):
    """python.org's macOS builds ship with none, so without this every
    library page would fail verification on the machines most likely to run
    this script."""
    site, certificate = https_library
    _python_trusts(monkeypatch, build_catalog, None, [tmp_path / "absent.pem", certificate])

    library = build_catalog.Library(site.url, 5.0)
    assert library.lookup(["library/qwen3.8"])["description"] == "Qwen3.8 delivers."


def test_with_no_authorities_anywhere_verification_still_fails(build_catalog, https_library, tmp_path, monkeypatch):
    """Borrowing is the fallback. Turning verification off is not."""
    site, _ = https_library
    _python_trusts(monkeypatch, build_catalog, None, [tmp_path / "absent.pem"])

    library = build_catalog.Library(site.url, 5.0)
    library.lookup(["library/qwen3.8"])
    assert "CERTIFICATE_VERIFY_FAILED" in library.down


def test_a_python_with_authorities_of_its_own_is_left_to_them(build_catalog, https_library, monkeypatch):
    site, certificate = https_library
    _python_trusts(monkeypatch, build_catalog, "/its/own/cert.pem", [certificate])

    library = build_catalog.Library(site.url, 5.0)
    library.lookup(["library/qwen3.8"])
    assert "CERTIFICATE_VERIFY_FAILED" in library.down


def test_a_model_the_library_does_not_know_says_where_it_looked(build_catalog, serve, library, tmp_path):
    server = small_host(serve, a_model("private-tune:latest", parent="also-private:latest"),
                        a_model("hf.co/org/repo:Q4_K_M"))
    _, catalog = run(build_catalog, tmp_path, "--host", server.url)
    models = by_name(catalog)

    assert models["private-tune:latest"]["library"] == {
        "page": None, "unavailable": "no page for library/private-tune or library/also-private"}
    assert models["hf.co/org/repo:Q4_K_M"]["library"] == {"page": None, "unavailable": "not an ollama.com model"}


def test_a_missing_reference_model_is_warned_about(build_catalog, host, library, tmp_path, capsys):
    _, catalog = run(build_catalog, tmp_path, "--host", host.url, "--reference", "llama9:400b")

    assert catalog["reference_on_host"] is False
    captured = capsys.readouterr()
    assert "reference llama9:400b: NOT on the host" in captured.out
    assert "warning: the reference model llama9:400b is not on" in captured.err


# ---------------------------------------------------------------------------
# Where it points and what it writes
# ---------------------------------------------------------------------------


def test_the_host_is_the_environments_then_dotenvs_then_the_agents(build_catalog, host, library, tmp_path, monkeypatch):
    """Compose's own precedence, so a catalog built with no flags describes
    the host the stack is pointed at."""
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"# written by setup.sh\nexport-less line\nOLLAMA_BASE_URL='{host.url}'\nOLLAMA_MODEL=\n")
    monkeypatch.setattr(build_catalog, "DOTENV", dotenv)

    _, from_dotenv = run(build_catalog, tmp_path, environ={"OLLAMA_BASE_URL": "  "})
    _, from_environment = run(build_catalog, tmp_path, environ={"OLLAMA_BASE_URL": host.url.replace("http://", "")})

    assert from_dotenv["host"] == from_environment["host"] == host.url
    assert from_dotenv["reference"] == "qwen3.8-256k:latest"


@pytest.mark.parametrize("given,url", [
    ("192.168.1.20", "http://192.168.1.20:11434"),
    ("  192.168.1.20/ ", "http://192.168.1.20:11434"),
    ("gpu-box", "http://gpu-box:11434"),
    ("GPU-Box:11500", "http://gpu-box:11500"),
    ("fe80::1", "http://[fe80::1]:11434"),
    ("[fe80::1]:11500", "http://[fe80::1]:11500"),
    ("http://192.168.1.20:11434/", "http://192.168.1.20:11434"),
    ("https://ollama.example.com", "https://ollama.example.com"),
    ("http://proxy.example.com/ollama/", "http://proxy.example.com/ollama"),
])
def test_any_way_of_naming_a_host_becomes_the_url_the_agent_would_use(build_catalog, given, url):
    """An address alone is enough: without a port it is Ollama's own, since
    port 80 is never where Ollama is. A URL with a scheme is taken as
    written -- a proxy on 443 is a real deployment."""
    assert build_catalog.normalise_host(given) == url


@pytest.mark.parametrize("given", ["", "   ", "ftp://gpu-box", "gpu-box:port", "http://"])
def test_what_is_not_a_host_is_refused(build_catalog, given):
    with pytest.raises(ValueError, match="is not an Ollama host"):
        build_catalog.normalise_host(given)


def test_a_host_can_be_given_as_a_bare_address(build_catalog, host, library, tmp_path):
    address = host.url.removeprefix("http://")
    code, catalog = run(build_catalog, tmp_path, address)
    assert (code, catalog["host"]) == (0, host.url)


def test_a_host_given_twice_differently_is_refused(build_catalog, host, tmp_path, capsys):
    with pytest.raises(SystemExit) as exit_info:
        run(build_catalog, tmp_path, host.url, "--host", "10.0.0.1")
    assert exit_info.value.code == 2
    assert "give the host once" in capsys.readouterr().err

    code, _ = run(build_catalog, tmp_path, host.url, "--host", host.url, "--no-library")
    assert code == 0


def test_an_address_that_is_not_a_host_is_a_usage_error(build_catalog, tmp_path, capsys):
    with pytest.raises(SystemExit) as exit_info:
        run(build_catalog, tmp_path, "gpu-box:port")
    assert exit_info.value.code == 2
    assert "is not an Ollama host" in capsys.readouterr().err


def test_the_help_names_the_host_it_would_ask(build_catalog, capsys):
    with pytest.raises(SystemExit) as exit_info:
        build_catalog.main(["--help"], environ={})
    assert exit_info.value.code == 0
    assert build_catalog.DEFAULT_HOST in capsys.readouterr().out.replace("\n", "").replace(" ", "")


def test_the_catalog_is_written_whole_and_readable_by_the_agents_container(build_catalog, host, library, tmp_path):
    out = tmp_path / "nested" / "dir" / "catalog.json"
    assert build_catalog.main(["--host", host.url, "--out", str(out)], environ={}) == 0

    assert oct(out.stat().st_mode & 0o777) == "0o644"
    assert [p.name for p in out.parent.iterdir()] == ["catalog.json"]
    assert out.read_text().endswith("}\n")


def test_a_catalog_written_under_the_working_directory_is_named_relative_to_it(build_catalog, host, library, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    build_catalog.main(["--host", host.url, "--out", "catalog.json"], environ={})
    assert "\nwrote catalog.json\n" in capsys.readouterr().out


def test_the_build_time_is_utc(build_catalog, host):
    library = build_catalog.Library("http://unused", 1.0, enabled=False)
    eastern = datetime(2026, 9, 27, 20, 11, 4, tzinfo=timezone.utc).astimezone()
    catalog = build_catalog.build_catalog(host.url, "qwen3.8-256k:latest", library=library, timeout=5, now=eastern)
    assert catalog["built"] == "2026-09-27T20:11:04Z"


def test_the_summary_shows_a_window_that_is_not_whole_kilotokens(build_catalog, serve, library, tmp_path, capsys):
    server = small_host(serve, a_model("odd:7b", params=None, context=12000), a_model("no-window:7b", context=None))
    run(build_catalog, tmp_path, "--host", server.url)

    rows = {line.split()[0]: line.split() for line in capsys.readouterr().out.splitlines() if ":7b" in line}
    assert rows["odd:7b"][1:4] == ["?", "Q4_K_M", "12000"]
    assert rows["no-window:7b"][3] == "?"


def test_run_as_documented_it_writes_the_catalog(host, tmp_path):
    """`python3 models/build_catalog.py` is how it is run, in its own process."""
    out = tmp_path / "catalog.json"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--host", host.url, "--no-library", "--out", str(out)],
        capture_output=True, text=True, timeout=120, env={**os.environ, "OLLAMA_MODEL": ""},
    )
    assert result.returncode == 0, result.stderr
    assert len(json.loads(out.read_text())["models"]) == 18


# ---------------------------------------------------------------------------
# What calibration measured
# ---------------------------------------------------------------------------


def _scores(**rungs: tuple[int, int]) -> dict:
    return {rung: {"correct": correct, "of": of, "p50_s": 1.0} for rung, (correct, of) in rungs.items()}


@pytest.mark.parametrize("model,reference,expected", [
    (_scores(light=(6, 6), standard=(5, 6), heavy=(3, 3)), _scores(light=(6, 6), standard=(6, 6), heavy=(3, 3)),
     (True, "heavy")),
    # Within one question of the reference is suited; two behind is not.
    (_scores(light=(5, 6), standard=(4, 6), heavy=(3, 3)), _scores(light=(6, 6), standard=(6, 6), heavy=(3, 3)),
     (True, "light")),
    # Failing the light probe rules the model out, whatever it did above.
    (_scores(light=(3, 6), standard=(6, 6)), _scores(light=(6, 6), standard=(6, 6)), (True, None)),
    # A rung nobody probed is passed over, not failed.
    (_scores(light=(6, 6), heavy=(3, 3)), _scores(light=(6, 6), heavy=(3, 3)), (True, "heavy")),
    # Different probe counts are not the same probes.
    (_scores(light=(4, 5)), _scores(light=(6, 6)), (False, None)),
    ({}, _scores(light=(6, 6)), (False, None)),
])
def test_a_rung_is_suited_within_one_question_of_the_reference(build_catalog, model, reference, expected):
    assert build_catalog.measured_rung(model, reference) == expected


def test_calibration_overrides_the_prior_task_by_task_but_never_an_exclusion(build_catalog):
    reference = {"measured": {"generator": _scores(light=(6, 6), standard=(6, 6)),
                              "narrator": _scores(light=(9, 10))}}
    model = {
        "prior": {"supervisor": "heavy", "generator": "heavy", "reflection": "heavy",
                  "narrator": None, "repair": "heavy"},
        "measured": {"generator": _scores(light=(6, 6), standard=(3, 6)), "narrator": _scores(light=(10, 10))},
    }
    suited, source = build_catalog.suitability(model, reference)

    assert suited["generator"] == "light" and source["generator"] == "calibration"
    assert suited["narrator"] is None and source["narrator"] == "prior"
    assert suited["supervisor"] == "heavy" and source["supervisor"] == "prior"
    assert build_catalog.suitability(model, None)[1]["generator"] == "prior"


def test_a_rebuild_keeps_what_calibration_measured_for_unchanged_weights(build_catalog, host, library, tmp_path, snapshot, capsys):
    """Pulling one new model must not throw away an hour of calibration on
    the others. The digest is what says the weights are the ones measured."""
    _, first = run(build_catalog, tmp_path, "--host", host.url)
    models = by_name(first)
    measured = {"generator": _scores(light=(6, 6), standard=(6, 6))}
    for name in ("qwen3.8-256k:latest", "gemma4:12b-mlx", "mistral:7b"):
        models[name]["measured"] = measured
    models["mistral:7b"]["digest"] = "a different pull"
    first["calibrated"] = "2026-09-28T01:00:00Z"
    (tmp_path / "catalog.json").write_text(json.dumps(first))

    _, second = run(build_catalog, tmp_path, "--host", host.url)
    rebuilt = by_name(second)

    assert second["calibrated"] == "2026-09-28T01:00:00Z"
    assert rebuilt["gemma4:12b-mlx"]["measured"] == measured
    assert rebuilt["gemma4:12b-mlx"]["suited"]["generator"] == "standard"
    assert rebuilt["gemma4:12b-mlx"]["suited_from"]["generator"] == "calibration"
    assert rebuilt["mistral:7b"]["measured"] == {}
    assert "standard*" in capsys.readouterr().out


def test_measurements_of_another_host_are_not_kept(build_catalog, host, library, tmp_path):
    _, first = run(build_catalog, tmp_path, "--host", host.url)
    for model in first["models"]:
        model["measured"] = {"generator": _scores(light=(6, 6))}
    first["host"] = "http://elsewhere:11434"
    (tmp_path / "catalog.json").write_text(json.dumps(first))

    _, second = run(build_catalog, tmp_path, "--host", host.url)
    assert {json.dumps(m["measured"]) for m in second["models"]} == {"{}"}
    assert second["calibrated"] is None


@pytest.mark.parametrize("content", ["not json", "[1, 2]", '{"models": "none"}'])
def test_an_unreadable_previous_catalog_is_simply_replaced(build_catalog, host, library, tmp_path, content):
    (tmp_path / "catalog.json").write_text(content)
    code, catalog = run(build_catalog, tmp_path, "--host", host.url)
    assert code == 0 and len(catalog["models"]) == 18


# ---------------------------------------------------------------------------
# The script against the rest of the repository
# ---------------------------------------------------------------------------


def test_its_defaults_are_the_agents(build_catalog):
    """Repeated so it runs without the agent's dependencies; held equal here."""
    from nl2sql_agent import config

    assert build_catalog.DEFAULT_HOST == config.DEFAULT_OLLAMA_BASE_URL
    assert build_catalog.DEFAULT_REFERENCE == config.DEFAULT_OLLAMA_MODEL


def test_it_needs_only_the_standard_library_and_python_3_9():
    """So it runs with the python3 a Mac already has, outside any image or
    virtualenv."""
    tree = ast.parse(SCRIPT.read_text(), feature_version=(3, 9))
    imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert imported - {"__future__"} <= set(sys.stdlib_module_names)


def test_the_committed_catalog_is_what_the_rules_make_of_its_facts(build_catalog):
    """Like the diagrams: a catalog edited by hand, or rules changed and never
    re-run, fails here. Re-run `python3 models/build_catalog.py`."""
    catalog = json.loads(COMMITTED.read_text())

    assert catalog["schema"] == build_catalog.SCHEMA_VERSION
    assert catalog["tasks"] == list(build_catalog.TASKS) and catalog["rungs"] == list(build_catalog.RUNGS)
    assert catalog["task_budgets"] == build_catalog.TASK_BUDGETS
    assert [m["name"] for m in catalog["models"]] == sorted(m["name"] for m in catalog["models"])
    models = by_name(catalog)
    for model in catalog["models"]:
        assert model["prior"] == build_catalog.prior_for(model["name"], model["facts"], model["library"]), model["name"]
        assert model["chat"] == (build_catalog.not_chat(model["facts"]) is None)
        suited, source = build_catalog.suitability(model, models.get(catalog["reference"]))
        assert (model["suited"], model["suited_from"]) == (suited, source), model["name"]
