"""models/build_catalog.py against the real services: the chat host and
ollama.com.

test_build_catalog.py's fake host answers with what the real one said on
2026-09-27, and its fake library with what the site showed. These ask the
real ones, so an Ollama upgrade that moves a field, or a redesign of the
library pages, fails here rather than quietly producing a thinner catalog.
Each skips, with the reason, when its service cannot be reached.
"""

from __future__ import annotations

import os
import urllib.request

import pytest

pytestmark = pytest.mark.docker


def _unreachable(url: str) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            response.read(1)
    except Exception as exc:  # noqa: BLE001 -- any failure is a reason to skip
        return str(exc)
    return None


def test_every_model_on_the_live_host_is_catalogued_from_its_own_answers(build_catalog):
    """TEST_OLLAMA_BASE_URL, not the agent's OLLAMA_BASE_URL, for the reason
    TEST_EMBED_BASE_URL exists: pointing the tests elsewhere must not also
    repoint an agent running beside them."""
    host = build_catalog.normalise_host(os.environ.get("TEST_OLLAMA_BASE_URL", build_catalog.DEFAULT_HOST))
    why = _unreachable(f"{host}/api/version")
    if why:
        pytest.skip(f"no Ollama at {host}: {why}")

    listed = {model["name"] for model in build_catalog.fetch_inventory(host, 30)}
    offline = build_catalog.Library(build_catalog.LIBRARY_URL, 5.0, enabled=False)
    catalog = build_catalog.build_catalog(host, "qwen3.8-256k:latest", library=offline, timeout=30)

    assert {model["name"] for model in catalog["models"]} == listed
    assert catalog["ollama_version"]
    for model in catalog["models"]:
        assert model["facts_from"] == "show", model.get("show_error")
        if model["chat"]:
            facts = model["facts"]
            assert facts["parameter_count"] and facts["context_length"] and facts["capabilities"], model["name"]


def test_the_live_library_page_is_still_read(build_catalog):
    library = build_catalog.Library(build_catalog.LIBRARY_URL, 15.0)
    page = library.lookup(["library/qwen3.8"])
    if library.down:
        pytest.skip(f"ollama.com: {library.down}")

    assert page.get("page"), page
    assert page["description"]
    assert "tools" in page["badges"] and page["sizes"]
