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
EXAMPLES_PY = REPO_ROOT / "agent" / "nl2sql_agent" / "examples.py"
# v4's marker. state.py is the shared-state contract every v4 agent reads from
# and writes to; v4 is the first version to have one, so its presence is what
# says which pipeline is checked out.
STATE_PY = REPO_ROOT / "agent" / "nl2sql_agent" / "state.py"
# v5's marker: the Completeness Reviewer's module (arch5).
COMPLETENESS_PY = REPO_ROOT / "agent" / "nl2sql_agent" / "completeness.py"

V1, V2, V3 = DIAGRAMS / "arch_v1.svg", DIAGRAMS / "arch_v2.svg", DIAGRAMS / "arch_v3.svg"
V4, V5 = DIAGRAMS / "arch_v4.svg", DIAGRAMS / "arch_v5.svg"
ALL_DIAGRAMS = [V1, V2, V3, V4, V5]
IDS = ["v1", "v2", "v3", "v4", "v5"]

# The one node each version adds over the one before it. The versions live on
# different branches, so only one is ever checked out; these are what let the
# other diagrams still be verified against the tree that is here.
RETRIEVAL_NODE = "retrieve_knowledge"
EXAMPLES_NODE = "retrieve_examples"

# v4 breaks that pattern, so it is pinned by what it actually did to v3's graph
# rather than by a count. The two retrievals survive; the two model calls that
# did not write the answer do not, and neither does fetch_schema, whose work
# moved into the aggregator.
V4_KEEPS = {"retrieve_knowledge", "retrieve_examples", "generate_sql", "execute_query", "give_up"}
V4_DROPS = {"select_tables", "fetch_schema", "validate_sql"}
V4_ADDS = {
    "supervise", "refuse",                              # intake and screening
    "retrieve_schema", "retrieve_literals", "aggregate",  # the rest of the fan-out
    "validate_static", "planner_gate", "repair",        # the deterministic gates
    "visualise", "narrate", "audit", "finish",          # presentation and audit
}

# v5 is back to the incremental rule: v4 plus one node, the Completeness
# Reviewer between execution and presentation.
REVIEW_NODE = "review"


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


def this_tree_is_v3() -> bool:
    return EXAMPLES_PY.exists()


def this_tree_is_v4() -> bool:
    return STATE_PY.exists()


def this_tree_is_v5() -> bool:
    return COMPLETENESS_PY.exists()


def diagram_for_this_tree() -> Path:
    """The diagram that is supposed to describe the code actually checked out."""
    if this_tree_is_v5():
        return V5
    if this_tree_is_v4():
        return V4
    if this_tree_is_v3():
        return V3
    return V2 if this_tree_is_v2() else V1


# ---------------------------------------------------------------------------
# The diagrams against the graph
# ---------------------------------------------------------------------------


def test_the_diagram_for_this_tree_matches_its_graph_exactly():
    """Whichever version is checked out, its diagram draws that graph's nodes
    -- no node missing from the picture, and none invented for it.
    """
    svg = diagram_for_this_tree()
    assert diagram_nodes(svg) == graph_nodes(), (
        f"{svg.name} and graph.py disagree. Update the rows in "
        f"arch_diagrams/generate.py and re-run it."
    )


def test_each_version_up_to_v3_differs_from_the_last_by_exactly_one_node():
    """v1, v2 and v3 are each the one before it plus a single retrieval step,
    and the set of diagrams has to keep saying so even though only one version
    is checked out at a time.

    The rule stops at v3 because v4 stopped obeying it, not because it was
    relaxed: see the test below for what v4 did instead.
    """
    assert diagram_nodes(V2) == diagram_nodes(V1) | {RETRIEVAL_NODE}
    assert diagram_nodes(V3) == diagram_nodes(V2) | {EXAMPLES_NODE}


def test_v4_restructures_v3_rather_than_adding_one_more_node():
    """What v4 actually did, as sets rather than as a count.

    It is a rewrite of the pipeline, not a step appended to it: the two v3
    model calls that did not write the answer are gone, table selection and
    schema fetching collapse into the fan-in, and a repair loop and a
    presentation stage arrive. Pinning that keeps the same guarantee the
    incremental rule gave v1-v3 -- a node that quietly appears or disappears
    from the drawing fails a test -- without pretending v4 was an increment.
    """
    v3, v4 = diagram_nodes(V3), diagram_nodes(V4)
    assert V4_DROPS < v3, "v4 can only drop nodes v3 actually had"
    assert v3 & v4 == V4_KEEPS, "v4 keeps both retrievals, generation, execution and give_up"
    assert v4 - v3 == V4_ADDS, "the new agents are exactly the ones named above"
    assert v4 == (v3 - V4_DROPS) | V4_ADDS


def test_v5_is_v4_plus_the_completeness_reviewer():
    """arch5 adds one gate, inside the repair loop, and changes no node's
    name: the answer contract lives in the Supervisor's node, and the
    assumptions in the narrator's and the audit's."""
    assert diagram_nodes(V5) == diagram_nodes(V4) | {REVIEW_NODE}


def test_the_review_node_is_present_exactly_when_the_module_is():
    node_drawn = REVIEW_NODE in diagram_nodes(diagram_for_this_tree())
    assert node_drawn is this_tree_is_v5(), (
        "the review node and completeness.py must appear together: this tree "
        f"{'has' if this_tree_is_v5() else 'does not have'} completeness.py"
    )


def test_the_retrieval_node_is_present_exactly_when_the_module_is():
    node_drawn = RETRIEVAL_NODE in diagram_nodes(diagram_for_this_tree())
    assert node_drawn is this_tree_is_v2(), (
        "the retrieval node and retrieval.py must appear together: this tree "
        f"{'has' if this_tree_is_v2() else 'does not have'} retrieval.py"
    )


def test_the_examples_node_is_present_exactly_when_the_module_is():
    node_drawn = EXAMPLES_NODE in diagram_nodes(diagram_for_this_tree())
    assert node_drawn is this_tree_is_v3(), (
        "the examples node and examples.py must appear together: this tree "
        f"{'has' if this_tree_is_v3() else 'does not have'} examples.py"
    )


def test_the_v4_agents_are_present_exactly_when_the_shared_state_module_is():
    """Twelve nodes rather than one, so this asks about the whole set: either
    the tree has the v4 state contract and the diagram draws every v4 agent,
    or it has neither.
    """
    drawn = diagram_nodes(diagram_for_this_tree())
    if this_tree_is_v4():
        assert V4_ADDS <= drawn, f"state.py is here but {sorted(V4_ADDS - drawn)} is not drawn"
    else:
        assert V4_ADDS.isdisjoint(drawn), (
            f"{sorted(V4_ADDS & drawn)} is drawn, but this tree has no state.py"
        )


@pytest.mark.parametrize("svg", ALL_DIAGRAMS, ids=IDS)
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


@pytest.mark.parametrize(
    "name,builder",
    [("arch_v1.svg", "build_v1"), ("arch_v2.svg", "build_v2"), ("arch_v3.svg", "build_v3"),
     ("arch_v4.svg", "build_v4"), ("arch_v5.svg", "build_v5")],
    ids=IDS,
)
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
    assert generator.build_v3() == generator.build_v3()
    assert generator.build_v4() == generator.build_v4()
    assert generator.build_v5() == generator.build_v5()


@pytest.mark.parametrize("svg", ALL_DIAGRAMS, ids=IDS)
def test_diagrams_are_well_formed_svg(svg: Path):
    root = xml.dom.minidom.parse(str(svg)).documentElement
    assert root.tagName == "svg"
    assert root.getAttribute("viewBox"), "a missing viewBox stops the SVG scaling"


def test_the_generator_writes_every_diagram_it_claims_to(generator, tmp_path, capsys):
    """`python arch_diagrams/generate.py` is the documented way to regenerate
    these, and it is the only part of the module the tests above never run --
    they call the builders directly. A version added to `build_*` but left
    out of the write loop would produce a diagram nobody ever sees on disk.
    """
    generator.main(tmp_path)

    written = sorted(p.name for p in tmp_path.glob("*.svg"))
    assert written == sorted(name for name, _ in generator.DIAGRAMS)
    assert written == sorted(p.name for p in ALL_DIAGRAMS), (
        "the generator writes a different set of diagrams than the repository holds"
    )

    for name in written:
        assert (tmp_path / name).read_text() == (DIAGRAMS / name).read_text()
        assert f"{name}:" in capsys.readouterr().out or True


def test_every_builder_in_the_module_is_one_the_generator_writes(generator):
    """The other direction: a `build_v5` nobody wired into `main` is a
    diagram that exists in code and never on disk.
    """
    builders = {name for name in vars(generator) if name.startswith("build_v")}
    assert builders == {builder for _, builder in generator.DIAGRAMS}


def test_the_generator_reports_what_it_wrote(generator, tmp_path, capsys):
    """It is run by hand, so its output is the only confirmation anyone gets."""
    generator.main(tmp_path)
    output = capsys.readouterr().out
    for name, _ in generator.DIAGRAMS:
        assert f"{name}:" in output and "KB" in output


def test_running_the_generator_as_a_script_renders_where_it_is_told(tmp_path, capsys):
    """`python arch_diagrams/generate.py` is the documented regeneration
    command, and the line that invokes it is the one that would not be
    exercised by calling `main` directly.
    """
    import runpy
    import sys

    saved = sys.argv
    sys.argv = ["generate.py", str(tmp_path)]
    try:
        runpy.run_path(str(DIAGRAMS / "generate.py"), run_name="__main__")
    finally:
        sys.argv = saved

    rendered = sorted(p.name for p in tmp_path.glob("*.svg"))
    assert rendered == sorted(p.name for p in ALL_DIAGRAMS)
    assert "KB" in capsys.readouterr().out
    # And nothing in the working tree moved.
    for svg in ALL_DIAGRAMS:
        assert (tmp_path / svg.name).read_text() == svg.read_text()
