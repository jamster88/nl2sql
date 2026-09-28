"""Fixtures for models/build_catalog.py: the script loaded as a module, and
fake HTTP servers standing in for the Ollama host and for ollama.com.

The fake host answers with what the real one answered on 2026-09-27
(`fixtures/ollama_host_2026-09-27.json`), so the tests exercise the script
against the shapes Ollama really returns -- the MLX builds' empty
`/api/tags` details included -- without needing the host.
"""

from __future__ import annotations

import importlib.util
import json
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "models" / "build_catalog.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
HOST_SNAPSHOT = FIXTURES / "ollama_host_2026-09-27.json"

# The library pages the fake ollama.com serves, one per family on the host,
# with the descriptions and badges the real site showed on 2026-09-27.
LIBRARY = {
    "deepseek-r1": ("DeepSeek-R1 is a family of open reasoning models with performance approaching "
                    "that of leading models, such as O3 and Gemini 2.5 Pro.",
                    ["tools", "thinking", "1.5b", "7b", "8b", "14b", "32b", "70b", "671b"]),
    "falcon3": ("A family of efficient AI models under 10B parameters performant in science, math, "
                "and coding through innovative training techniques.", ["1b", "3b", "7b", "10b"]),
    "gemma4": ("Gemma 4 models are designed to deliver frontier-level performance at each size. They "
               "are well-suited for reasoning, agentic workflows, coding, and multimodal understanding.",
               ["vision", "tools", "thinking", "audio", "cloud", "e2b", "e4b", "12b", "26b", "31b"]),
    "gpt-oss": ("OpenAI&#39;s open-weight models designed for powerful reasoning, agentic tasks, and "
                "versatile developer use cases.", ["tools", "thinking", "cloud", "20b", "120b"]),
    "granite3.1-moe": ("The IBM Granite 1B and 3B models are long-context mixture of experts (MoE) "
                       "Granite models from IBM designed for low latency usage.", ["tools", "1b", "3b"]),
    "llama3.3": ("New state of the art 70B model. Llama 3.3 70B offers similar performance compared "
                 "to the Llama 3.1 405B model.", ["tools", "70b"]),
    "mistral-small": ("Mistral Small 3 sets a new benchmark in the &#8220;small&#8221; Large Language "
                      "Models category below 70B.", ["tools", "22b", "24b"]),
    "mistral": ("The 7B model released by Mistral AI, updated to version 0.3.", ["tools", "7b"]),
    "mixtral": ("A set of Mixture of Experts (MoE) model with open weights by Mistral AI in 8x7b and "
                "8x22b parameter sizes.", ["tools", "8x7b", "8x22b"]),
    "qwen3-coder-next": ("Qwen3-Coder-Next is a coding-focused language model from Alibaba&#39;s Qwen "
                         "team, optimized for agentic coding workflows and local development.", ["tools"]),
    "qwen3.6": ("Qwen3.6 delivers substantial upgrades in agentic coding and thinking preservation "
                "than previous Qwen models.", ["vision", "tools", "thinking", "27b", "35b"]),
    "qwen3.8": ("Qwen3.8 delivers substantial gains across coding, professional work, research, and "
                "long-horizon agentic tasks.", ["vision", "tools", "thinking", "27b"]),
}


def library_page(description: str | None, badges: list[str]) -> str:
    """A page in the real site's markup, reduced to what is read: the
    description meta tag, and each badge a `rounded-md` span beside the
    page's other spans. `fixtures/ollama_library_qwen3.8.html` is the
    verbatim original."""
    meta = f'<meta name="description" content="{description}"/>' if description is not None else ""
    spans = "".join(
        f'<span class="inline-flex items-center rounded-md bg-indigo-50 px-2 text-xs">{badge}</span>'
        for badge in badges
    )
    return (f"<html><head>{meta}</head><body><div class=\"flex flex-wrap gap-2\">{spans}"
            '<span class="hidden sm:inline">Documentation</span></div></body></html>')


@pytest.fixture(scope="session")
def build_catalog():
    """The script as a module, loaded from its path the way it is run."""
    spec = importlib.util.spec_from_file_location("build_catalog", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def no_dotenv(build_catalog, tmp_path, monkeypatch):
    """Never the checkout's own `.env`: it names a real host."""
    monkeypatch.setattr(build_catalog, "DOTENV", tmp_path / "no.env")


@pytest.fixture(scope="session")
def snapshot() -> dict:
    return json.loads(HOST_SNAPSHOT.read_text())


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._answer(self.path)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self._answer(f"{self.path} {body.get('model')}")

    def _answer(self, key: str) -> None:
        self.server.requests.append((key, dict(self.headers)))
        status, payload = self.server.routes.get(key, (404, {"error": "not found"}))
        data = payload if isinstance(payload, bytes) else (
            payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
        )
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class FakeServer(ThreadingHTTPServer):
    """Answers `routes[key]` -- `(status, JSON-able | str | bytes)` -- where
    the key is a GET's path, or a POST's path and its `model`:
    `"/api/show qwen3.8:latest"`. Everything else is a 404. `requests` is
    every key asked for, in order, with its headers."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.routes: dict[str, tuple[int, object]] = {}
        self.requests: list[tuple[str, dict]] = []
        self.url = f"http://127.0.0.1:{self.server_address[1]}"

    def asked(self) -> list[str]:
        return [key for key, _ in self.requests]

    def handle_error(self, request, client_address):
        """A client that refuses the certificate is a test passing, not an
        error worth a traceback."""


@pytest.fixture
def serve():
    """Starts fake servers; `tls=(certificate, key)` serves https."""
    started: list[FakeServer] = []

    def start(tls: tuple[Path, Path] | None = None) -> FakeServer:
        server = FakeServer()
        if tls:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(*tls)
            server.socket = context.wrap_socket(server.socket, server_side=True)
            server.url = server.url.replace("http://", "https://")
        # A short poll, because shutdown() waits for the next one: at the
        # default half second, this module's fixtures alone cost fifteen.
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
        started.append(server)
        return server

    yield start
    for server in started:
        server.shutdown()
        server.server_close()


@pytest.fixture
def host(serve, snapshot) -> FakeServer:
    """The chat host as it was on 2026-09-27."""
    server = serve()
    server.routes["/api/tags"] = (200, snapshot["tags"])
    server.routes["/api/version"] = (200, snapshot["version"])
    for name, show in snapshot["show"].items():
        server.routes[f"/api/show {name}"] = (200, show)
    return server


@pytest.fixture
def library(serve, build_catalog, monkeypatch) -> FakeServer:
    """ollama.com, with a page for every family on the host and none for the
    local `qwen3.8-256k` build -- as on the real site."""
    server = serve()
    for family, (description, badges) in LIBRARY.items():
        server.routes[f"/library/{family}"] = (200, library_page(description, badges))
    monkeypatch.setattr(build_catalog, "LIBRARY_URL", server.url)
    return server
