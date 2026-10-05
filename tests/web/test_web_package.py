"""`web/`, what every page shares (V6-25), and how each page takes it in.

The package's own behaviour is tested in TypeScript (`web/test/`), in every
page's suite. These are the things only the repository as a whole can say:
that every page takes it in the same way -- its configuration, its image --
and that the desktop client unescapes the agent's text by the same rule.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
WEB = ROOT / "web"
PAGES = {"gui": "../web", "review/gui": "../../web", "curate": "../web", "console": "../web", "auth/gui": "../../web"}


def _replacements(source: str, pattern: str) -> list[tuple[str, str]]:
    return re.findall(pattern, source)


def test_the_desktop_unescapes_by_the_same_rule_as_every_page():
    """Three entities, `&` last -- in Java as in TypeScript."""
    web = (WEB / "src" / "text.ts").read_text()
    body = re.search(r"export function plainText\(markup: string\): string \{\n\s*return (.*?);\n\}", web, re.S).group(1)
    typescript = _replacements(body, r'\.replace\(/(&\w+;)/g, "([^"]+)"\)')
    java = (ROOT / "desktop/src/main/java/org/nl2sql/desktop/ui/Markup.java").read_text()
    method = re.search(r"static String plain\(String markup\) \{\n\s*return (.*?);\n", java).group(1)
    desktop = _replacements(method, r'\.replace\("(&\w+;)", "([^"]+)"\)')
    assert typescript == desktop == [("&lt;", "<"), ("&gt;", ">"), ("&amp;", "&")]


@pytest.mark.parametrize("page,relative", sorted(PAGES.items()))
def test_every_page_takes_the_package_in_the_same_way(page: str, relative: str):
    vite = (ROOT / page / "vite.config.ts").read_text()
    vitest = (ROOT / page / "vitest.config.ts").read_text()
    tsconfig = (ROOT / page / "tsconfig.json").read_text()
    assert f'import {{ sharedPackage }} from "{relative}/src/config.ts";' in vite
    assert f'import {{ devProxies }} from "{relative}/src/vite.ts";' in vite
    assert "resolve: shared.resolve" in vite and "fs: shared.server.fs" in vite
    assert f'import {{ sharedPackage }} from "{relative}/src/config.ts";' in vitest
    assert "...shared.tests" in vitest and "...shared.sources" in vitest and "allowExternal: true" in vitest
    assert f'"@nl2sql/web/*": ["{relative}/src/*"]' in tsconfig


@pytest.mark.parametrize("page", sorted(PAGES))
def test_every_pages_image_is_built_with_the_package(page: str):
    dockerfile = (ROOT / page / "Dockerfile").read_text()
    assert "WORKDIR /build" in dockerfile, "../web and ../../web are both /web from /build"
    assert "COPY web/package.json /web/package.json" in dockerfile and "COPY web/src/ /web/src/" in dockerfile
    assert dockerfile.index("COPY web/src/") < dockerfile.index("RUN npm run build")


def test_the_package_needs_nothing_of_its_own():
    """Source, compiled by each page; React and the testing library are the page's."""
    package = json.loads((WEB / "package.json").read_text())
    assert package["type"] == "module" and package["private"] is True
    assert not package.get("dependencies") and not package.get("devDependencies")
    assert not (WEB / "node_modules").exists()
