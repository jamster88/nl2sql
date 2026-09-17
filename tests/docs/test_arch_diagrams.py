"""arch_diagrams/ pinned to the code it describes.

A diagram is documentation that nothing executes, so it rots silently: rename
a node in graph.py and the picture keeps confidently showing the old one. The
generator records the nodes it drew in an SVG <metadata> element, and these
tests check that list against graph.py, and check the committed SVGs against
what the generator currently produces.

Self-contained on purpose -- no conftest, no pytest.ini, no third-party
imports, and graph.py is parsed as text rather than imported, so this runs on
a checkout with none of the agent's dependencies installed.
"""

from __future__ import annotations

import importlib.util
import re
import xml.dom.minidom
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DIAGRAMS = REPO_ROOT / "arch_diagrams"
GRAPH_PY = REPO_ROOT / "agent" / "nl2sql_agent" / "graph.py"
RETRIEVAL_PY = REPO_ROOT / "agent" / "nl2sql_agent" / "retrieval.py"

V1, V2 = DIAGRAMS / "arch_v1.svg", DIAGRAMS / "arch_v2.svg"

# The one node v2 adds. v1 and v2 live on different branches, so only one of
# them is ever checked out; this is what lets the other diagram still be
# verified against the tree that is here.
RETRIEVAL_NODE = "retrieve_knowledge"


def graph_nodes() -> set[str]:
    """Node names registered in graph.py, read as text (no import needed)."""
    nodes = set(re.findall(r'add_node\(\s*"(\w+)"', GRAPH_PY.read_text()))
    assert nodes, "no add_node() calls found in graph.py -- the pattern needs updating"
    return nodes


def diagram_nodes(svg: Path) -> set[str]:
    match = re.search(r'<metadata id="graph-nodes">([^<]*)</metadata>', svg.read_text())
    assert match, f"{svg.name} carries no graph-nodes metadata -- regenerate it"
    return set(match.group(1).split())


def this_tree_is_v2() -> bool:
    return RETRIEVAL_PY.exists()


# ---------------------------------------------------------------------------
# The diagrams against the graph
# ---------------------------------------------------------------------------


def test_the_diagram_for_this_tree_matches_its_graph_exactly():
    """Whichever version is checked out, its diagram draws that graph's nodes
    -- no node missing from the picture, and none invented for it.
    """
    svg = V2 if this_tree_is_v2() else V1
    assert diagram_nodes(svg) == graph_nodes(), (
        f"{svg.name} and graph.py disagree. Update the rows in "
        f"arch_diagrams/generate.py and re-run it."
    )


def test_the_other_diagram_differs_only_by_the_retrieval_node():
    """v1 and v2 are the same pipeline apart from retrieval, and the pair of
    diagrams has to keep saying so even though only one version is checked out.
    """
    assert diagram_nodes(V2) == diagram_nodes(V1) | {RETRIEVAL_NODE}


def test_the_retrieval_node_is_present_exactly_when_the_module_is():
    node_drawn = RETRIEVAL_NODE in diagram_nodes(V2 if this_tree_is_v2() else V1)
    assert node_drawn is this_tree_is_v2(), (
        "the retrieval node and retrieval.py must appear together: this tree "
        f"{'has' if this_tree_is_v2() else 'does not have'} retrieval.py"
    )


@pytest.mark.parametrize("svg", [V1, V2], ids=["v1", "v2"])
def test_every_node_in_the_metadata_is_actually_drawn(svg: Path):
    """Guards the metadata itself: it is generated from the rows, so a node
    listed there but absent from the rendered text would mean the two drifted.
    """
    text = svg.read_text()
    for node in diagram_nodes(svg):
        assert f">{node}</text>" in text, f"{node} is listed in {svg.name} but never rendered"


# ---------------------------------------------------------------------------
# The diagrams against the generator
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def generator():
    spec = importlib.util.spec_from_file_location("arch_generate", DIAGRAMS / "generate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name,builder", [("arch_v1.svg", "build_v1"), ("arch_v2.svg", "build_v2")],
                         ids=["v1", "v2"])
def test_the_committed_svg_is_what_the_generator_produces(generator, name: str, builder: str):
    """Catches both halves of the drift: an SVG edited by hand, and a change to
    generate.py that was never re-run.
    """
    assert (DIAGRAMS / name).read_text() == getattr(generator, builder)(), (
        f"{name} is stale or hand-edited -- run `python arch_diagrams/generate.py`"
    )


def test_the_generator_is_deterministic(generator):
    # Otherwise the check above would fail spuriously and every regeneration
    # would produce a noisy diff.
    assert generator.build_v1() == generator.build_v1()
    assert generator.build_v2() == generator.build_v2()


@pytest.mark.parametrize("svg", [V1, V2], ids=["v1", "v2"])
def test_diagrams_are_well_formed_svg(svg: Path):
    root = xml.dom.minidom.parse(str(svg)).documentElement
    assert root.tagName == "svg"
    assert root.getAttribute("viewBox"), "a missing viewBox stops the SVG scaling"
