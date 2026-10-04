"""The adversarial reviews' figures, pinned to their generator and their documents.

`adversary_reviews/diagrams/` holds nineteen draw.io files over two review
cycles -- ten tagged `v6_x_review`, nine tagged `v6_1_review` -- each with an
SVG and a PNG that draw.io exported from it, and the `_enhanced` review
documents embed the SVGs. Three things can drift apart there, each silently:
the `.drawio` from the script that computes it (an edit made in draw.io, or a
change to the script never re-run), the exports from the `.drawio` (a
regeneration without a re-export), and the documents from the files (a figure
renamed, or one left over that nothing embeds). These tests hold each pair
together, hold each enhanced document to the original it adds figures to, and
hold the second cycle's comparison document to the ids both cycles use.

Self-contained like `test_arch_diagrams.py`: the generator is loaded by path
and needs nothing but the standard library.
"""

from __future__ import annotations

import difflib
import html
import importlib.util
import re
import runpy
import subprocess
import sys
import xml.dom.minidom
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REVIEWS = REPO_ROOT / "adversary_reviews"
DIAGRAMS = REVIEWS / "diagrams"
GENERATOR = DIAGRAMS / "generate.py"

#: The cycles, oldest first, and each one's figures, written down rather than
#: globbed: a directory that lost one would otherwise lose its tests with it.
NAMES = {
    "v6_x_review": [
        "deployment_topology", "repair_loop_state", "trace_field_loss", "row_data_flow", "spec_lineage",
        "trust_boundaries", "secrets_flow", "plan_dependencies", "severity_matrix", "duplication_matrix",
    ],
    "v6_1_review": [
        "deployment_topology", "signin_flow", "trust_boundaries", "key_sharing", "finding_status",
        "severity_matrix", "duplication_matrix", "plan_dependencies", "spec_lineage",
    ],
}
TAGS = tuple(NAMES)
STEMS = [f"{tag}_{name}" for tag in TAGS for name in NAMES[tag]]
DRAWIO = [DIAGRAMS / f"{stem}.drawio" for stem in STEMS]

#: The reviews that embed figures, and the originals they are copies of --
#: the same five documents in each cycle.
ORIGINALS = [
    "architecture_as_documented", "architecture_as_implemented", "implementation", "mitigation_plan", "summary",
]
PAIRS = [(tag, name) for tag in TAGS for name in ORIGINALS]
ENHANCED = [REVIEWS / f"{tag}_{name}_enhanced.md" for tag, name in PAIRS]

#: Documents with no enhanced twin: the second cycle's comparison with the
#: first, which embeds a figure of its own and links both cycles' files.
STANDALONE = [REVIEWS / "v6_1_review_changes_since_v6_x.md"]

#: Every review document, for the link check.
ALL_DOCS = [REVIEWS / f"{tag}_{name}.md" for tag, name in PAIRS] + ENHANCED + STANDALONE

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#: What a figure adds to a document: a caption, the image, the export links,
#: the note at the top, and the blank lines between them.
FIGURE_LINES = ("**Figure ", "![", "<sub>", "> **Enhanced edition.**")

#: A link to a figure file of either cycle.
FIGURE_LINK = re.compile(r"\(diagrams/(v6_(?:x|\d+)_review_\w+\.(?:svg|png|drawio))\)")

#: A finding id as the reviews write one.
FINDING_ID = re.compile(r"\b([DICMS]-\d{2})\b")


@pytest.fixture(scope="module")
def generator():
    spec = importlib.util.spec_from_file_location("review_diagrams_generate", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# The .drawio files against the generator
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", DRAWIO, ids=STEMS)
def test_the_committed_drawio_is_what_the_generator_produces(generator, path: Path):
    """Catches both halves of the drift: a file edited in draw.io and never
    brought back into the script, and a change to the script never re-run.
    It also holds the first cycle's ten files still: a change to the shared
    `Diagram` class that moved their bytes would show here first.
    """
    builder = dict(generator.DIAGRAMS)[path.name]
    assert path.read_text() == getattr(generator, builder)(), (
        f"{path.name} is stale or hand-edited -- run `python adversary_reviews/diagrams/generate.py` "
        "and re-export the SVG and PNG (the commands are in the script's docstring)"
    )


def test_the_generator_is_deterministic(generator):
    # Otherwise the check above would fail spuriously and every regeneration
    # would produce a noisy diff.
    for _, builder in generator.DIAGRAMS:
        assert getattr(generator, builder)() == getattr(generator, builder)()


def test_the_list_here_is_the_generator_s_and_the_directory_s(generator):
    """Three lists of the same nineteen names: this file's, the generator's
    and the directory's. One that gained or lost a figure without the others
    is a figure nobody checks, or a test for a figure that is not there.
    """
    assert [name for name, _ in generator.DIAGRAMS] == [p.name for p in DRAWIO]
    assert sorted(p.name for p in DIAGRAMS.glob("*.drawio")) == sorted(p.name for p in DRAWIO)


def test_every_builder_in_the_module_is_one_the_generator_writes(generator):
    """The other direction: a `build_*` nobody wired into `DIAGRAMS` is a
    figure that exists in code and never on disk.
    """
    builders = {name for name in vars(generator) if name.startswith("build_")}
    assert builders == {builder for _, builder in generator.DIAGRAMS}


def test_each_cycle_s_figures_say_which_cycle_wrote_them(generator):
    """The file's own header names the cycle: the first cycle's files keep
    the first cycle's agent string and date so their bytes never move, and
    the second cycle's say so themselves.
    """
    for name, builder in generator.DIAGRAMS:
        tag = name[: name.index("_review_") + len("_review")]
        head = getattr(generator, builder)().splitlines()[0]
        assert f'agent="nl2sql {tag} diagram generator"' in head, name


@pytest.mark.parametrize("path", DRAWIO, ids=STEMS)
def test_diagrams_are_well_formed_drawio(path: Path):
    """What draw.io needs to open the file: one page, a graph model, unique
    cell ids, every vertex with a geometry, every edge joining two vertices
    that exist.
    """
    root = ET.parse(path).getroot()
    assert root.tag == "mxfile"
    pages = root.findall("diagram")
    assert len(pages) == 1
    cells = pages[0].findall("./mxGraphModel/root/mxCell")
    ids = [cell.get("id") for cell in cells]
    assert len(ids) == len(set(ids)), "duplicate cell ids"
    vertices = {cell.get("id") for cell in cells if cell.get("vertex") == "1"}
    edges = [cell for cell in cells if cell.get("edge") == "1"]
    # Some figures are tables -- the severity, status and duplication
    # matrices -- and have no arrows at all; every figure has boxes.
    assert vertices, "a figure with no boxes"
    for cell in cells:
        if cell.get("vertex") == "1":
            geometry = cell.find("mxGeometry")
            assert geometry is not None and geometry.get("width") and geometry.get("height"), cell.get("id")
    for edge in edges:
        assert edge.get("source") in vertices, f"edge {edge.get('id')} leaves a vertex that does not exist"
        assert edge.get("target") in vertices, f"edge {edge.get('id')} arrives at a vertex that does not exist"


# ---------------------------------------------------------------------------
# The exports against the .drawio files
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", DRAWIO, ids=STEMS)
def test_every_drawio_has_both_exports(path: Path):
    """The documents embed the SVG and link the PNG; a figure regenerated and
    not re-exported, or exported in one format and not the other, would be a
    broken image in the review.
    """
    svg, png = path.with_suffix(".svg"), path.with_suffix(".png")
    assert svg.is_file() and png.is_file(), f"{path.name} is missing an export"
    root = xml.dom.minidom.parse(str(svg)).documentElement
    assert root.tagName == "svg"
    assert root.getAttribute("viewBox"), "a missing viewBox stops the SVG scaling"
    assert png.read_bytes()[:8] == PNG_SIGNATURE, f"{png.name} is not a PNG"


def _labels(path: Path) -> list[str]:
    """The first line of every label in a .drawio file, as plain text."""
    found = []
    for cell in ET.parse(path).getroot().iter("mxCell"):
        first = re.split(r"<br\s*/?>", cell.get("value") or "", maxsplit=1)[0]
        text = html.unescape(re.sub(r"<[^>]+>", "", first)).strip()
        if text:
            found.append(text)
    return found


@pytest.mark.parametrize("path", DRAWIO, ids=STEMS)
def test_the_svg_export_carries_every_label_of_the_drawio(path: Path):
    """A regeneration without a re-export leaves an SVG that still opens and
    still looks right, and says something the .drawio no longer does. draw.io
    writes each label into the SVG as HTML, so every label the source has must
    be in the export.
    """
    labels = _labels(path)
    assert labels, f"{path.name} has no labelled cells"
    rendered = html.unescape(path.with_suffix(".svg").read_text())
    missing = [label for label in labels if label not in rendered]
    assert not missing, f"{path.with_suffix('.svg').name} is stale: it lacks {missing[:5]}"


# ---------------------------------------------------------------------------
# The generator's entry points
# ---------------------------------------------------------------------------


def test_the_generator_writes_every_diagram_it_claims_to(generator, tmp_path, capsys):
    """`python adversary_reviews/diagrams/generate.py` is the documented way to
    regenerate these, and it is the only part of the module the tests above
    never run -- they call the builders directly.
    """
    generator.main(tmp_path)

    written = sorted(p.name for p in tmp_path.glob("*.drawio"))
    assert written == sorted(p.name for p in DRAWIO)
    for path in DRAWIO:
        assert (tmp_path / path.name).read_text() == path.read_text()
    output = capsys.readouterr().out
    for path in DRAWIO:
        assert f"{path.name}:" in output and "KB" in output


def test_running_the_generator_as_a_script_writes_where_it_is_told(tmp_path, capsys):
    """The line that invokes `main` is the one calling it directly never
    exercises, and the optional destination is what keeps this test out of
    the working tree.
    """
    saved = sys.argv
    sys.argv = ["generate.py", str(tmp_path)]
    try:
        runpy.run_path(str(GENERATOR), run_name="__main__")
    finally:
        sys.argv = saved

    assert sorted(p.name for p in tmp_path.glob("*.drawio")) == sorted(p.name for p in DRAWIO)
    assert "KB" in capsys.readouterr().out
    for path in DRAWIO:
        assert (tmp_path / path.name).read_text() == path.read_text()


# ---------------------------------------------------------------------------
# The documents against the figures
# ---------------------------------------------------------------------------


def _figure_links(doc: Path) -> list[str]:
    return FIGURE_LINK.findall(doc.read_text())


@pytest.mark.parametrize("doc", ENHANCED + STANDALONE, ids=lambda p: p.stem)
def test_every_figure_a_review_embeds_exists_in_all_three_formats(doc: Path):
    """Each figure block embeds the SVG and links the PNG and the .drawio; a
    name that resolves in one format and not another is a broken link in the
    rendered page.
    """
    links = _figure_links(doc)
    assert links, f"{doc.name} embeds no figure"
    for link in links:
        assert (DIAGRAMS / link).is_file(), f"{doc.name} links {link}, which is not there"
    stems = {Path(link).stem for link in links}
    for stem in stems:
        for suffix in (".svg", ".png", ".drawio"):
            assert f"{stem}{suffix}" in links, f"{doc.name} links {stem} without its {suffix}"


def test_every_diagram_is_embedded_by_some_review():
    """A figure nothing embeds is a figure nobody reviews when it changes."""
    embedded = {Path(link).stem for doc in ENHANCED + STANDALONE for link in _figure_links(doc)}
    assert embedded == {path.stem for path in DRAWIO}


def test_a_second_cycle_document_that_embeds_a_first_cycle_figure_says_so():
    """The second cycle re-embeds a first-cycle figure only where the code it
    draws did not change, and its caption has to say that: a reader of the
    v6_1 review must not take a 5.6.1 figure for a 6.0.1 one.
    """
    for doc in [p for p in ENHANCED if p.name.startswith("v6_1_")]:
        lines = doc.read_text().splitlines()
        for i, line in enumerate(lines):
            if line.startswith("![") and "v6_x_review_" in line:
                caption = next(c for c in reversed(lines[:i]) if c.startswith("**Figure "))
                assert "first-cycle figure" in caption, f"{doc.name}: {line} is a v6_x figure whose caption does not say so"


@pytest.mark.parametrize("tag,name", PAIRS, ids=[f"{tag}_{name}" for tag, name in PAIRS])
def test_each_enhanced_review_is_its_original_plus_figures(tag: str, name: str):
    """The enhanced editions promise the original's text unchanged. Every
    line of the original must appear in the enhanced file, in order, and
    every line the enhanced file adds must belong to a figure block or the
    note that announces them -- so a fix made to one edition and not the
    other fails here rather than leaving two reviews that disagree.
    """
    original = (REVIEWS / f"{tag}_{name}.md").read_text().splitlines()
    enhanced = (REVIEWS / f"{tag}_{name}_enhanced.md").read_text().splitlines()
    matcher = difflib.SequenceMatcher(a=original, b=enhanced, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        assert op in ("equal", "insert"), f"{tag}_{name}_enhanced changes the original at lines {i1 + 1}-{i2}: {original[i1:i2][:2]}"
        for line in enhanced[j1:j2] if op == "insert" else ():
            assert line == "" or line.startswith(FIGURE_LINES), f"{tag}_{name}_enhanced adds a line that is not a figure: {line[:80]}"

    figures = [line for line in enhanced if line.startswith("**Figure ")]
    note = next(line for line in enhanced if line.startswith("> **Enhanced edition.**"))
    assert int(re.search(r"with (\d+) figures? added", note).group(1)) == len(figures)
    assert [int(re.match(r"\*\*Figure (\d+)\.", line).group(1)) for line in figures] == list(range(1, len(figures) + 1))


@pytest.mark.parametrize("doc", ALL_DOCS, ids=lambda p: p.stem)
def test_review_links_point_at_files_that_are_actually_committed(doc: Path):
    """A link can resolve on the author's machine and 404 in a fresh clone:
    the target exists locally but is untracked.
    """
    text = doc.read_text()
    missing = []
    for link in re.findall(r"\]\(([^)]+)\)", text):
        if link.startswith(("http://", "https://", "#")):
            continue
        target = (REVIEWS / link.split("#")[0]).resolve()
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(target)], cwd=REPO_ROOT, capture_output=True, text=True,
        )
        if tracked.returncode != 0:
            missing.append(link)
    assert not missing, f"{doc.name} links to files not committed to the repo: {missing}"


# ---------------------------------------------------------------------------
# The second cycle against the first: ids are the thread between them
# ---------------------------------------------------------------------------


def _ids_in(paths: list[Path]) -> set[str]:
    return {found for path in paths for found in FINDING_ID.findall(path.read_text())}


def test_the_comparison_names_every_first_cycle_finding_and_nothing_else_as_old():
    """Section 3 of the comparison is one row per first-cycle finding. Every
    row's id must be a finding the first cycle's three reviews made, and
    every finding they made must have a row -- or the comparison has lost or
    invented a finding.
    """
    text = STANDALONE[0].read_text()
    section = text.split("## 3. ", 1)[1].split("## 4. ", 1)[0]
    rows = re.findall(r"^\| ([DICMS]-\d{2}) \|", section, flags=re.M)
    first = _ids_in([REVIEWS / f"v6_x_review_{name}.md" for name in ORIGINALS[:3]])
    assert rows, "the comparison has no status rows"
    assert len(rows) == len(set(rows)), "a finding has two rows"
    assert set(rows) == first, f"rows and first-cycle findings differ: {sorted(set(rows) ^ first)}"


def test_every_finding_the_second_cycle_carries_or_adds_is_in_its_own_reviews():
    """A row that says a finding persists, and every new finding section 4
    lists, must appear in the second cycle's three reviews under the same id;
    a resolved finding is listed there too, as resolved. Ids are the thread a
    third cycle will follow, so the comparison may not use one the reviews
    do not.
    """
    text = STANDALONE[0].read_text()
    compared = set(re.findall(r"^\| ([DICMS]-\d{2}) \|", text, flags=re.M))
    second = _ids_in([REVIEWS / f"v6_1_review_{name}.md" for name in ORIGINALS[:3]])
    assert compared <= second, f"the comparison names findings the second cycle's reviews do not: {sorted(compared - second)}"
