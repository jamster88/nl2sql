#!/usr/bin/env python3
"""Build the model catalog: every model the Ollama host serves, what it is,
and which tasks -- at which complexity -- it is presumed suited to.

    python3 models/build_catalog.py                  # the host the agent uses
    python3 models/build_catalog.py 192.168.1.20     # any Ollama host, by address
    python3 models/build_catalog.py --no-library     # the host's own facts only
    python3 models/build_catalog.py gpu-box:11500 --out /tmp/catalog.json

This is section 15.1 of multi-agent_arch_specs/Multi-Agent_NL2SQL_arch5_2.md,
steps 1 to 4: inventory (`/api/tags`), facts (`/api/show`, per model), the
library page on ollama.com, and a prior -- a rule-based guess, with its
reasons, at the highest rung of each task a model is suited to. Step 5,
calibration, is `models/calibrate.py`: it needs the agent and its
databases, which this script deliberately does not. A rebuild keeps what
calibration measured for every model whose weights have not changed, and
`suited` is the measurement wherever there is one and the prior elsewhere.

The agent reads the catalog and never builds it. Building it needs the
public internet for the library pages, and a catalog is something to review
and commit, like code.

Standard library only, and Python 3.9 or later, so it runs with whatever
python3 the machine has -- macOS's own included -- without the agent's image
or its virtualenv.
"""

from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import os
import re
import ssl
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
DEFAULT_OUT = HERE / "catalog.json"
# The file `setup.sh` writes and compose reads. The host the stack is pointed
# at is set there, so a catalog built with no flags describes that host.
DOTENV = REPO_ROOT / ".env"

# The agent's own defaults, from agent/nl2sql_agent/config.py. Repeated here
# rather than imported so this runs without the agent's dependencies; a test
# holds the two equal.
DEFAULT_HOST = "http://192.168.10.82:11434"
DEFAULT_REFERENCE = "qwen3.8-256k"

# The port Ollama listens on unless told otherwise, for an address given
# without one.
OLLAMA_PORT = 11434

LIBRARY_URL = "https://ollama.com"
USER_AGENT = "nl2sql-model-catalog/1"
# Bumped when the catalog's shape changes, so the router can refuse a file it
# does not understand rather than misread it. 2: `suited_from` is per task,
# since calibration can measure some of a model's tasks and not others.
SCHEMA_VERSION = 2

RUNGS = ("light", "standard", "heavy")
TASKS = ("supervisor", "generator", "reflection", "narrator", "repair")
# The most tokens each task's prompt needs (section 15.3). A model whose
# context window is smaller is not a candidate for that task at all.
TASK_BUDGETS = {
    "supervisor": 8192,
    "generator": 16384,
    "reflection": 8192,
    "narrator": 8192,
    "repair": 16384,
}
# A classification with a four-field answer is not where parameters show, so
# these two are heavy from 7 B; the other three are placed by size bands.
CLASSIFYING = ("supervisor", "reflection")
# The tasks that answer in a JSON schema, which models trained for tool
# calling hold more reliably.
STRUCTURED = ("supervisor", "reflection", "narrator")

# Keyword tags recorded from a library description. Recorded, not all acted
# on: nearly every current model's description names coding among its
# strengths, so the prior promotes a model for code only when it is a code
# model -- see CODE_MODEL.
KEYWORDS = {
    "code": re.compile(r"\b(?:code|coding|coder|codebases?|programming|software[- ]engineering)\b", re.I),
    "reasoning": re.compile(r"\breason(?:ing|s)?\b|\bthinking\b", re.I),
    "vision": re.compile(r"\b(?:vision|visual|images?|multimodal)\b", re.I),
    "embedding": re.compile(r"\bembeddings?\b", re.I),
    "chat": re.compile(r"\b(?:chat|conversational|dialogue|assistant)\b", re.I),
    "instruct": re.compile(r"\binstruct", re.I),
}
# A description that says the model is *for* code: "a coding-focused language
# model", "a code model", "the best open model for coding agents", "explore
# codebases ... power software engineering agents". Not "well suited for
# reasoning, agentic workflows, coding, and multimodal understanding", which
# is a general model's list of strengths.
CODE_MODEL = re.compile(
    r"\b(?:code|coding)[- ](?:focused|specific|speciali[sz]ed|models?|llms?)\b|\bfor (?:code|coding)\b"
    r"|\bcodebases?\b|\bsoftware[- ]engineering\b",
    re.I,
)
# Library size badges: 7b, 1.5b, 567m, e4b, 8x22b.
SIZE_BADGE = re.compile(r"^(?:e?\d+(?:\.\d+)?|\d+x\d+(?:\.\d+)?)[kmbt]$")
# Where operating systems keep their certificate authorities, for a Python
# that has none of its own: python.org's macOS builds ship without, until
# their "Install Certificates" script has been run, and every https request
# then fails verification. Verification stays on; only the list of
# authorities is borrowed, and only when Python's own is missing.
SYSTEM_CA_BUNDLES = (
    "/etc/ssl/cert.pem",  # macOS, Alpine, the BSDs
    "/etc/ssl/certs/ca-certificates.crt",  # Debian, Ubuntu
    "/etc/pki/tls/certs/ca-bundle.crt",  # Fedora, RHEL
)


class HostError(RuntimeError):
    """The host could not be asked, or did not answer as Ollama does."""


# --- settings ---------------------------------------------------------------


def read_dotenv(path: Path) -> dict[str, str]:
    """The KEY=VALUE lines of a compose `.env` file; everything else ignored."""
    try:
        text = path.read_text()
    except OSError:
        return {}
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def setting(name: str, environ: dict, dotenv: dict, default: str) -> str:
    """Compose's precedence: the shell's environment, then `.env`, then the
    default. Empty counts as unset, as it does in the agent's config."""
    for source in (environ, dotenv):
        value = (source.get(name) or "").strip()
        if value:
            return value
    return default


def normalise_host(address: str) -> str:
    """Any way of naming an Ollama host, as the URL the agent would use.

    `192.168.1.20`, `gpu-box`, `gpu-box:11500`, `fe80::1`, `[fe80::1]:11434`
    and `http://gpu-box:11434/` all work. An address without a scheme gets
    http and, without a port, Ollama's own, 11434 -- port 80 is never where
    Ollama is. A URL with a scheme is taken as written, port and all: a
    reverse proxy on 443 is a real deployment. The agent's router applies the
    same rules to `OLLAMA_BASE_URL`, and a catalog is only ever compared with
    the host it was built for in this form.
    """
    text = address.strip()
    bare = "://" not in text
    text = text.rstrip("/")
    if bare:
        try:
            # A bare IPv6 address has colons that are not a port.
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
        raise ValueError(
            f"{address!r} is not an Ollama host: give an address such as 192.168.1.20, "
            "gpu-box:11434 or http://gpu-box:11434"
        )
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    if port is None and bare:
        port = OLLAMA_PORT
    netloc = host if port is None else f"{host}:{port}"
    return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path.rstrip("/"), "", ""))


def full_name(name: str) -> str:
    """Ollama's name for a model: `qwen3.8-256k` is `qwen3.8-256k:latest`."""
    return name if ":" in name.rsplit("/", 1)[-1] else name + ":latest"


# --- the host -----------------------------------------------------------------


def tls_context() -> ssl.SSLContext:
    """Python's default verifying context, with the system's authorities
    added when Python has none (SSL_CERT_FILE, when set, counts as its own)."""
    context = ssl.create_default_context()
    defaults = ssl.get_default_verify_paths()
    if defaults.cafile is None and defaults.capath is None:
        for bundle in SYSTEM_CA_BUNDLES:
            if os.path.isfile(bundle):
                context.load_verify_locations(cafile=bundle)
                break
    return context


def _fetch(url: str, timeout: float, payload: dict | None = None) -> bytes:
    headers = {"User-Agent": USER_AGENT}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    context = tls_context() if url.startswith("https:") else None
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        return response.read()


def _json_object(body: bytes) -> dict:
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError("not a JSON object")
    return value


def fetch_inventory(host: str, timeout: float) -> list[dict]:
    """Step 1. Without a model list there is nothing to catalogue, so every
    way of not getting one stops the run."""
    url = f"{host}/api/tags"
    try:
        tags = _json_object(_fetch(url, timeout))
    except urllib.error.HTTPError as exc:
        raise HostError(f"{url} answered {exc.code}: is {host} an Ollama host?") from exc
    except (OSError, http.client.HTTPException) as exc:
        reason = getattr(exc, "reason", exc)
        raise HostError(
            f"cannot reach Ollama at {host} ({reason}). Check that the host is running "
            "and reachable, and note that port 11434 serves http, not https."
        ) from exc
    except ValueError as exc:
        raise HostError(f"{url} did not answer with Ollama's model list: {exc}") from exc
    models = tags.get("models")
    if not isinstance(models, list):
        raise HostError(f"{url} did not answer with Ollama's model list")
    models = [m for m in models if isinstance(m, dict) and m.get("name")]
    if not models:
        raise HostError(f"{host} serves no models; pull one on the host with `ollama pull <name>`")
    return models


def fetch_version(host: str, timeout: float) -> str | None:
    try:
        return str(_json_object(_fetch(f"{host}/api/version", timeout))["version"])
    except (OSError, http.client.HTTPException, ValueError, KeyError):
        return None


def fetch_show(host: str, name: str, timeout: float) -> tuple[dict | None, str | None]:
    """Step 2, for one model: its facts, or why there are none. A model whose
    `/api/show` fails is still catalogued, from what `/api/tags` said."""
    try:
        return _json_object(_fetch(f"{host}/api/show", timeout, {"model": name})), None
    except (OSError, http.client.HTTPException, ValueError) as exc:
        return None, f"/api/show failed: {exc}"


def parse_size(label: str | None) -> int | None:
    """`27.3B` -> 27_300_000_000: the fallback when /api/show gave no exact count."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([KMBT])\s*", label or "", re.I)
    if match is None:
        return None
    scale = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[match.group(2).upper()]
    return round(float(match.group(1)) * scale)


def parse_parameters(text: str | None) -> dict:
    """The numeric defaults baked into a model, from `/api/show`'s
    `parameters` block -- a `num_ctx`, a temperature. Stop tokens and other
    strings are left out."""
    defaults = {}
    for line in (text or "").splitlines():
        key, _, value = line.strip().partition(" ")
        try:
            number = float(value.strip())
        except ValueError:
            continue
        defaults[key] = int(number) if number.is_integer() else number
    return defaults


def first_line(text: str | None) -> str | None:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()[:80]
    return None


def facts_of(entry: dict, show: dict | None) -> dict:
    """One model's facts: `/api/show` where it answered, `/api/tags` where it
    did not. The MLX builds are why both are read -- `/api/tags` leaves
    their parameter size, family and context empty."""
    show = show or {}
    tag_details = entry.get("details") or {}
    show_details = show.get("details") or {}
    details = {key: show_details.get(key) or tag_details.get(key) for key in {*tag_details, *show_details}}
    info = show.get("model_info") or {}
    arch = info.get("general.architecture")

    def of_arch(suffix: str):
        return info.get(f"{arch}.{suffix}")

    capabilities = show.get("capabilities") or entry.get("capabilities")
    return {
        "family": details.get("family") or None,
        "parent_model": details.get("parent_model") or None,
        "parameter_size": details.get("parameter_size") or None,
        "parameter_count": info.get("general.parameter_count") or parse_size(details.get("parameter_size")),
        "expert_count": of_arch("expert_count"),
        "expert_used_count": of_arch("expert_used_count"),
        "quantization": details.get("quantization_level") or None,
        "format": details.get("format") or None,
        "context_length": of_arch("context_length") or details.get("context_length"),
        "capabilities": sorted(capabilities) if capabilities else None,
        "defaults": parse_parameters(show.get("parameters")),
        "license": first_line(show.get("license")),
        # A cloud model: listed by this host, run by another. Routing a
        # question to it sends the question and the schema off the network.
        "remote_host": show.get("remote_host") or entry.get("remote_host"),
        "size_bytes": entry.get("size"),
        "modified_at": (entry.get("modified_at") or "")[:10] or None,
    }


# --- the library ----------------------------------------------------------------


def library_path(name: str) -> str | None:
    """Where a model's page is on ollama.com: `library/qwen3.8` for
    `qwen3.8:latest`, `someone/model` for a namespaced one. None for a model
    pulled from another registry (`hf.co/...`), which has no page there."""
    segments = name.split("/")
    base = segments[-1].split(":")[0]
    if len(segments) == 1:
        return f"library/{base}"
    if len(segments) == 2 and not any(mark in segments[0] for mark in ".:"):
        return f"{segments[0]}/{base}"
    return None


def library_candidates(name: str, parents: dict[str, str | None]) -> list[str]:
    """The model's own page, then its parents'. A local build such as
    `qwen3.8-256k` -- `qwen3.8` with a bigger window baked in -- has no page of
    its own, and its parent's describes it."""
    candidates: list[str] = []
    seen = set()
    current: str | None = name
    while current and current not in seen:
        seen.add(current)
        path = library_path(current)
        if path and path not in candidates:
            candidates.append(path)
        current = parents.get(full_name(current))
    return candidates


class _PageParser(HTMLParser):
    """The two things a library page says that the host does not: a
    one-paragraph description, and badges -- capabilities and sizes, as
    `<span class="... rounded-md ...">tools</span>`."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.description: str | None = None
        self.badges: list[str] = []
        self._badge: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "meta" and attributes.get("name") == "description":
            self.description = (attributes.get("content") or "").strip() or None
        elif tag == "span" and "rounded-md" in (attributes.get("class") or "").split():
            self._badge = []

    def handle_data(self, data):
        if self._badge is not None:
            self._badge.append(data)

    def handle_endtag(self, tag):
        if tag == "span" and self._badge is not None:
            text = "".join(self._badge).strip().lower()
            self._badge = None
            if text:
                self.badges.append(text)


def parse_library_page(page: str) -> dict:
    parser = _PageParser()
    parser.feed(page)
    description = parser.description
    return {
        "description": description,
        "badges": [b for b in parser.badges if not SIZE_BADGE.match(b)],
        "sizes": [b for b in parser.badges if SIZE_BADGE.match(b)],
        "tags": [tag for tag, pattern in KEYWORDS.items() if description and pattern.search(description)],
    }


class Library:
    """ollama.com's model pages, each fetched at most once, and none at all
    once the site has failed: one timeout is a fact about the network, and
    waiting it out once per model would only repeat it."""

    def __init__(self, base_url: str, timeout: float, enabled: bool = True) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.pages: dict[str, dict | None] = {}
        self.down: str | None = None if enabled else "skipped (--no-library)"

    def lookup(self, candidates: list[str]) -> dict:
        if not candidates:
            return {"page": None, "unavailable": "not an ollama.com model"}
        for path in candidates:
            page = self._page(path)
            if self.down:
                return {"page": None, "unavailable": self.down}
            if page:
                return page
        return {"page": None, "unavailable": "no page for " + " or ".join(candidates)}

    def _page(self, path: str) -> dict | None:
        if self.down or path in self.pages:
            return self.pages.get(path)
        url = f"{self.base_url}/{path}"
        try:
            body = _fetch(url, self.timeout).decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                self.down = f"{self.base_url} answered {exc.code}"
                return None
            self.pages[path] = None
        except (OSError, http.client.HTTPException) as exc:
            self.down = f"{self.base_url} unreachable: {getattr(exc, 'reason', exc)}"
            return None
        else:
            self.pages[path] = {"page": url, **parse_library_page(body)}
        return self.pages[path]


# --- the prior ------------------------------------------------------------------


def quant_bits(quantization: str | None) -> int | None:
    """Bits per weight: Q4_K_M and IQ4 are 4, Q2_K is 2, MXFP4 and nvfp4 are
    4, F16 and BF16 are 16. None when the label says nothing recognisable."""
    match = (
        re.match(r"i?q(\d)", quantization or "", re.I)
        or re.search(r"fp(\d+)", quantization or "", re.I)
        or re.fullmatch(r"b?f(\d+)", quantization or "", re.I)
    )
    return int(match.group(1)) if match else None


def not_chat(facts: dict) -> str | None:
    """Why a model is never routed a chat task, or None when it may be."""
    if "embedding" in (facts["capabilities"] or []) or "bert" in (facts["family"] or ""):
        return "an embedding model: never routed a chat task"
    if facts["remote_host"]:
        return (
            f"served by {facts['remote_host']}, not by this host: a question routed "
            "to it would send the question and the schema off the network"
        )
    return None


def code_model(name: str, library: dict) -> str | None:
    base = name.split("/")[-1].split(":")[0]
    if "code" in base.lower():
        return f"a code model by name ({base})"
    if library.get("description") and CODE_MODEL.search(library["description"]):
        return "a code model by its library description"
    return None


def _size_rung(task: str, parameters: int) -> int:
    if task in CLASSIFYING:
        return 2 if parameters >= 7e9 else 0
    if parameters < 10e9:
        return 0
    return 1 if parameters <= 40e9 else 2


def _size_reason(parameters: int) -> str:
    classifying = "heavy (7 B and over)" if parameters >= 7e9 else "light (under 7 B)"
    drafting = RUNGS[_size_rung("generator", parameters)]
    band = {"light": "under 10 B", "standard": "10 to 40 B", "heavy": "over 40 B"}[drafting]
    return (
        f"{parameters / 1e9:.1f} B parameters: supervisor and reflection {classifying}; "
        f"generator, narrator and repair {drafting} ({band})"
    )


def _placed(name: str, facts: dict, library: dict, parameters: int) -> tuple[dict, list[str]]:
    """Rungs by size, then a rung up or down for what the model is. Each
    adjustment is summed, and the sum clamped afterwards, so the order they
    are listed in cannot change the result."""
    rung = {task: _size_rung(task, parameters) for task in TASKS}
    reasons = [_size_reason(parameters)]
    if facts["expert_count"]:
        reasons.append(
            f"a mixture of {facts['expert_count']} experts, {facts['expert_used_count']} active "
            "per token: placed by total parameters, so it will measure faster than its size"
        )
    code = code_model(name, library)
    if code:
        rung["generator"] += 1
        rung["repair"] += 1
        reasons.append(f"{code}: generator and repair one rung up")
    capabilities = facts["capabilities"]
    if "thinking" in (capabilities or []) or "reasoning" in library.get("tags", []):
        rung["repair"] += 1
        reasons.append("a reasoning model: repair one rung up, and routed with thinking off everywhere")
    bits = quant_bits(facts["quantization"])
    if bits is not None and bits < 4:
        rung = {task: value - 1 for task, value in rung.items()}
        reasons.append(f"quantised to {facts['quantization']}, below 4 bits: every task one rung down")
    if "tools" not in (capabilities or []):
        for task in STRUCTURED:
            rung[task] -= 1
        known = "capabilities unknown" if capabilities is None else "no tool support"
        reasons.append(f"{known}: supervisor, reflection and narrator one rung down")
    return rung, reasons


def prior_for(name: str, facts: dict, library: dict) -> dict:
    """Step 4: for every task, the highest rung the model is presumed suited
    to -- None when it is no candidate for the task -- and the reasons.

    Section 15.1's rules: a guess with its reasons attached, which the
    calibration overrides wherever the two disagree. A model of unknown size
    is light everywhere and promoted for nothing, since a promotion is a
    guess about a model the size was meant to anchor."""
    excluded = not_chat(facts)
    if excluded:
        return {**{task: None for task in TASKS}, "reasons": [excluded]}

    parameters = facts["parameter_count"]
    if parameters is None:
        rung = {task: 0 for task in TASKS}
        reasons = ["parameter count unknown: light for every task until measured"]
    else:
        rung, reasons = _placed(name, facts, library, parameters)

    prior: dict = {task: RUNGS[max(0, min(len(RUNGS) - 1, value))] for task, value in rung.items()}
    context = facts["context_length"]
    if context is None:
        reasons.append("context length unknown: no task excluded for it")
    else:
        short = [task for task in TASKS if context < TASK_BUDGETS[task]]
        for task in short:
            prior[task] = None
        if short:
            reasons.append(f"a {context}-token context is below the budget of {', '.join(short)}: not a candidate")
    prior["reasons"] = reasons
    return prior


# --- what calibration measured -----------------------------------------------------


def measured_rung(model_task: dict, reference_task: dict) -> tuple[bool, str | None]:
    """The highest rung calibration showed the model suited to for one task.

    A rung is suited when the model scored within one question of the
    reference model on the same probes (section 15.5). Rungs are read from
    the light end and the first failure stops the climb, since a model
    suited to heavy is suited to everything below it and one that fails the
    light probe is no candidate at all. Rungs with no probes on both sides
    are passed over. Returns `(False, None)` when nothing is comparable --
    the task was not calibrated, and the prior stands.
    """
    comparable = [
        rung
        for rung in RUNGS
        if rung in model_task and rung in reference_task
        and model_task[rung].get("of") == reference_task[rung].get("of")
    ]
    if not comparable:
        return False, None
    best = None
    for rung in comparable:
        if model_task[rung]["correct"] < reference_task[rung]["correct"] - 1:
            break
        best = rung
    return True, best


def suitability(model: dict, reference: dict | None) -> tuple[dict, dict]:
    """`suited` and `suited_from` for one model: calibration's answer for a
    task it measured, the prior's for the rest. An exclusion in the prior --
    an embedding model, a cloud model, a window too small for the task --
    is a fact, and no measurement overrides it."""
    reference_measured = (reference or {}).get("measured", {})
    suited, source = {}, {}
    for task in TASKS:
        measured, rung = measured_rung(
            model["measured"].get(task, {}), reference_measured.get(task, {})
        )
        if measured and model["prior"][task] is not None:
            suited[task], source[task] = rung, "calibration"
        else:
            suited[task], source[task] = model["prior"][task], "prior"
    return suited, source


def read_catalog(path: Path) -> dict | None:
    """A catalog already on disk, or None when there is none worth reading."""
    try:
        catalog = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return catalog if isinstance(catalog, dict) and isinstance(catalog.get("models"), list) else None


def carried_measurements(previous: dict | None, host: str) -> dict[tuple[str, str], dict]:
    """(name, digest) -> what calibration measured, from the catalog being
    replaced. Only for the same host: a measurement is of weights on a
    machine, and the digest is what says the weights are the same."""
    if not previous or previous.get("host") != host:
        return {}
    return {
        (model.get("name"), model.get("digest")): model["measured"]
        for model in previous["models"]
        if isinstance(model, dict) and model.get("measured")
    }


# --- the catalog ------------------------------------------------------------------


def build_catalog(
    host: str,
    reference: str,
    *,
    library: Library,
    timeout: float,
    now: datetime | None = None,
    previous: dict | None = None,
) -> dict:
    entries = sorted(fetch_inventory(host, timeout), key=lambda entry: entry["name"])
    version = fetch_version(host, timeout)
    shown = {entry["name"]: fetch_show(host, entry["name"], timeout) for entry in entries}
    facts = {entry["name"]: facts_of(entry, shown[entry["name"]][0]) for entry in entries}
    parents = {name: fact["parent_model"] for name, fact in facts.items()}
    carried = carried_measurements(previous, host)

    models = []
    for entry in entries:
        name = entry["name"]
        page = library.lookup(library_candidates(name, parents))
        prior = prior_for(name, facts[name], page)
        model = {
            "name": name,
            "digest": entry.get("digest"),
            "chat": not_chat(facts[name]) is None,
            "facts": facts[name],
            "facts_from": "tags" if shown[name][1] else "show",
            "library": page,
            "prior": prior,
            # Step 5, calibration, fills `measured`; `suited` is filled below,
            # once the reference model's measurements are known too.
            "measured": carried.get((name, entry.get("digest")), {}),
        }
        if shown[name][1]:
            model["show_error"] = shown[name][1]
        models.append(model)

    by_name = {model["name"]: model for model in models}
    for model in models:
        model["suited"], model["suited_from"] = suitability(model, by_name.get(reference))
    kept = any(model["measured"] for model in models)

    built = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "schema": SCHEMA_VERSION,
        "host": host,
        "ollama_version": version,
        "built": built.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reference": reference,
        "reference_on_host": reference in facts,
        # When calibration last ran, kept while any of its measurements are.
        "calibrated": previous.get("calibrated") if kept else None,
        "rungs": list(RUNGS),
        "tasks": list(TASKS),
        "task_budgets": dict(TASK_BUDGETS),
        "models": models,
    }


def write_catalog(catalog: dict, out: Path) -> None:
    """Written beside its destination and renamed into place, so a run that
    fails leaves the previous catalog whole."""
    out.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=out.parent, prefix=".catalog-", suffix=".json")
    with os.fdopen(handle, "w") as fh:
        json.dump(catalog, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    # mkstemp makes the file private; the agent's container reads it through
    # a bind mount as another user.
    os.chmod(temporary, 0o644)
    os.replace(temporary, out)


def _context(tokens: int | None) -> str:
    if tokens is None:
        return "?"
    return f"{tokens // 1024}k" if tokens % 1024 == 0 else str(tokens)


def render_summary(catalog: dict) -> str:
    chat = sum(1 for model in catalog["models"] if model["chat"])
    lines = [
        f"Ollama {catalog['ollama_version'] or '(version unknown)'} at {catalog['host']}: "
        f"{len(catalog['models'])} models, {chat} of them chat candidates",
        f"reference {catalog['reference']}: "
        + ("on the host" if catalog["reference_on_host"] else "NOT on the host"),
        "",
        "The highest rung of each task each model is suited to: measured where",
        "calibration has run ('*'), presumed from the prior elsewhere ('-': no candidate):",
        "",
    ]
    header = ["model", "params", "quant", "context", *catalog["tasks"]]
    rows = [header]
    for model in catalog["models"]:
        facts = model["facts"]
        parameters = facts["parameter_count"]
        rows.append([
            model["name"],
            f"{parameters / 1e9:.1f}B" if parameters else "?",
            facts["quantization"] or "?",
            _context(facts["context_length"]),
            *[(model["suited"][task] or "-") + ("*" if model["suited_from"][task] == "calibration" else "")
              for task in catalog["tasks"]],
        ])
    widths = [max(len(row[i]) for row in rows) for i in range(len(header))]
    lines += ["  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip() for row in rows]
    return "\n".join(lines)


def notes(catalog: dict) -> list[str]:
    """What the operator should know that the table does not say."""
    found = []
    if not catalog["reference_on_host"]:
        found.append(
            f"warning: the reference model {catalog['reference']} is not on {catalog['host']}; "
            "an agent left on its default model would fail against this host"
        )
    for model in catalog["models"]:
        if "show_error" in model:
            found.append(f"warning: {model['name']}: {model['show_error']}; catalogued from /api/tags alone")
    unavailable = {model["library"].get("unavailable") for model in catalog["models"]}
    for reason in sorted(r for r in unavailable if r and ("unreachable" in r or "answered" in r)):
        found.append(f"note: no library pages ({reason}); descriptions and badges left out")
    return found


def _shown(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None, environ: dict | None = None) -> int:
    environ = dict(os.environ) if environ is None else environ
    dotenv = read_dotenv(DOTENV)
    parser = argparse.ArgumentParser(
        description="Catalogue the models an Ollama host serves, with the tasks and "
        "complexity each is presumed suited to (arch5.2, section 15.1).",
    )
    default_host = setting("OLLAMA_BASE_URL", environ, dotenv, DEFAULT_HOST)
    parser.add_argument(
        "address",
        nargs="?",
        help="the Ollama host: an IP address, a name, host:port or a URL; without a "
        "port, Ollama's 11434 (default: OLLAMA_BASE_URL from the environment or .env, "
        f"else the agent's default; now {default_host})",
    )
    parser.add_argument("--host", help="the same, as a flag")
    parser.add_argument(
        "--reference",
        default=setting("OLLAMA_MODEL", environ, dotenv, DEFAULT_REFERENCE),
        help="the model the agent runs every call on today, which calibration "
        "measures the others against (default: OLLAMA_MODEL, else %(default)s)",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="where to write it (default: models/catalog.json)")
    parser.add_argument(
        "--no-library", action="store_true", help="skip ollama.com: catalogue from the host's own answers only"
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="seconds per request (default: %(default)s)")
    args = parser.parse_args(argv)
    if args.address and args.host and args.address != args.host:
        parser.error("give the host once: as an address or with --host, not both")
    try:
        host = normalise_host(args.address or args.host or default_host)
    except ValueError as exc:
        parser.error(str(exc))

    library = Library(LIBRARY_URL, args.timeout, enabled=not args.no_library)
    try:
        catalog = build_catalog(
            host,
            full_name(args.reference),
            library=library,
            timeout=args.timeout,
            previous=read_catalog(args.out),
        )
    except HostError as exc:
        print(f"build_catalog: {exc}", file=sys.stderr)
        return 1
    write_catalog(catalog, args.out)
    print(render_summary(catalog))
    print(f"\nwrote {_shown(args.out)}")
    for line in notes(catalog):
        print(line, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
