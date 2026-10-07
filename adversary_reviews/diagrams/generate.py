#!/usr/bin/env python3
"""Generate the adversarial reviews' figures as draw.io files.

    python adversary_reviews/diagrams/generate.py            # writes beside this file
    python adversary_reviews/diagrams/generate.py some/dir   # or somewhere disposable

Nineteen diagrams over two review cycles -- ten tagged `v6_x_review` (the
first cycle, at 5.6.1) and nine tagged `v6_1_review` (the second, at 6.0.1)
-- one `build_*` function each, every one an uncompressed `.drawio` document
that draw.io opens and the `_enhanced` review documents embed through its SVG
export. A first-cycle figure is never edited for the second cycle: where the
code it draws did not change, the second cycle's documents embed the first
cycle's file and say so; where it did, the second cycle has a figure of its
own. The `.drawio` file is the source and the only thing this script writes.
The SVG and PNG beside each one are draw.io's own exports, made with the
desktop application's command line after a regeneration:

    DRAWIO="/Applications/draw.io.app/Contents/MacOS/draw.io"
    for f in adversary_reviews/diagrams/v6_*_review_*.drawio; do
      "$DRAWIO" -x -f svg -b 10 --theme light --embed-svg-fonts false -o "${f%.drawio}.svg" "$f"
      "$DRAWIO" -x -f png -s 2 -b 20 --theme light -o "${f%.drawio}.png" "$f"
    done

Computed rather than drawn, for the reason `arch_diagrams/generate.py` is: a
figure edited by hand in draw.io is a figure nobody will edit twice, and a
figure that only exists as a drawing cannot be checked. So
`tests/docs/test_review_diagrams.py` compares each committed `.drawio` with
what this file produces now -- an edit made in draw.io and never brought back
here fails, and so does a change here that was never re-run -- and checks that
the exports still carry every label the `.drawio` does, so a regeneration
without a re-export fails too.

The layout is explicit coordinates. These are one-off figures for a set of
documents, not a family of diagrams that must share a layout engine, and the
reader of a figure wants the two boxes that matter next to each other, which
no automatic layout promises. Each diagram's title names the findings it
illustrates, by the ids the three reviews use (D-, I-, C-, M-, S-).
"""
from __future__ import annotations

import html
import sys
from pathlib import Path
from xml.sax.saxutils import escape

#: draw.io's standard palette, by the name a reader of the reviews sees in a
#: legend: red is exposed without authentication, orange a default password,
#: green loopback only, grey not published, purple TLS material, yellow a
#: file on the host, blue a pipeline node.
PALETTE = {
    "red": ("#f8cecc", "#b85450"),
    "orange": ("#ffe6cc", "#d79b00"),
    "yellow": ("#fff2cc", "#d6b656"),
    "green": ("#d5e8d4", "#82b366"),
    "blue": ("#dae8fc", "#6c8ebf"),
    "purple": ("#e1d5e7", "#9673a6"),
    "grey": ("#f5f5f5", "#666666"),
    "white": ("#ffffff", "#333333"),
    "frame": ("#fafafa", "#999999"),
    "none": ("none", "none"),
}


def fmt(label: str, bold_first: bool, raw: bool) -> str:
    """A label as the HTML a `html=1` cell renders (not yet XML-escaped).

    Plain labels are escaped line by line and joined with `<br>`, the first
    line in bold when there is more than one -- a box's title over its body.
    A raw label is HTML already, for the few boxes that colour one line.
    """
    if raw:
        return label
    lines = label.split("\n")
    out = []
    for i, line in enumerate(lines):
        text = html.escape(line, quote=False)
        if i == 0 and bold_first and len(lines) > 1:
            text = f"<b>{text}</b>"
        out.append(text)
    return "<br>".join(out)


def small(text: str, colour: str = "#444444", size: int = 8) -> str:
    """A body line in a smaller, dimmer font than the box's title."""
    return f'<font style="font-size:{size}px;color:{colour}">{html.escape(text, quote=False)}</font>'


class Diagram:
    """One draw.io page, built cell by cell and rendered as mxGraph XML.

    Cells are emitted in call order, which is draw.io's z-order: frames
    first, boxes on them, edges last. Ids are sequential, so two builds of
    the same diagram are byte-identical, which is what lets a test compare
    the committed file with a fresh build.
    """

    def __init__(
        self,
        name: str,
        width: int,
        height: int,
        *,
        agent: str = "nl2sql v6_x_review diagram generator",
        stamp: str = "2026-10-03T00:00:00.000Z",
    ):
        self.name = name
        self.width = width
        self.height = height
        # The file's own account of who wrote it and when; the first cycle's
        # figures keep the first cycle's values so their bytes do not move.
        self.agent = agent
        self.stamp = stamp
        self.cells: list[str] = []
        self.i = 1

    def nid(self) -> str:
        self.i += 1
        return f"n{self.i}"

    def _cell(self, value: str, style: str, x, y, w, h) -> str:
        cid = self.nid()
        self.cells.append(
            f'<mxCell id="{cid}" value="{escape(value, {chr(34): "&quot;"})}" style="{style}" '
            f'vertex="1" parent="1"><mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'
        )
        return cid

    def box(self, x, y, w, h, label, color="white", font=11, align="center", valign="middle",
            bold_first=True, dashed=False, rounded=True, stroke_width=1, fontcolor=None,
            raw=False, extra="") -> str:
        """A rounded, filled box. `color` is a palette name or a (fill, stroke) pair."""
        fill, stroke = PALETTE[color] if isinstance(color, str) else color
        style = (
            f"rounded={1 if rounded else 0};whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};"
            f"align={align};verticalAlign={valign};fontSize={font};spacing=4;spacingLeft=6;spacingRight=6;"
            f"strokeWidth={stroke_width};"
        )
        if dashed:
            style += "dashed=1;"
        if fontcolor:
            style += f"fontColor={fontcolor};"
        style += extra
        return self._cell(fmt(label, bold_first, raw), style, x, y, w, h)

    def frame(self, x, y, w, h, label, color="frame", dashed=True) -> str:
        """A labelled container drawn behind the boxes it groups."""
        fill, stroke = PALETTE[color]
        style = (
            f"rounded=1;whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};align=left;"
            f"verticalAlign=top;spacingLeft=10;spacingTop=4;fontSize=12;fontStyle=1;"
            + ("dashed=1;" if dashed else "")
        )
        return self._cell(html.escape(label, quote=False), style, x, y, w, h)

    def text(self, x, y, w, h, label, font=11, align="left", bold=False, color=None) -> str:
        """Text with no box around it: titles, legends, notes."""
        style = (
            f"text;html=1;align={align};verticalAlign=top;whiteSpace=wrap;fontSize={font};"
            + ("fontStyle=1;" if bold else "")
            + (f"fontColor={color};" if color else "")
        )
        return self._cell(fmt(label, False, False), style, x, y, w, h)

    def edge(self, src, dst, label="", color="#333333", dashed=False, style="orth", points=(),
             exit=None, entry=None, width=1, start=None, font=10, fontcolor=None,
             label_pos=None) -> str:
        """An arrow from `src` to `dst`.

        `points` are waypoints the route passes through, `exit` and `entry`
        the fractions of the source's and target's sides it leaves and
        arrives at, and `label_pos` moves the label along the edge (-1 at the
        source, 1 at the target) and off it by a pixel offset -- the three
        things that keep a label off the box next to it.
        """
        es = {
            "orth": "edgeStyle=orthogonalEdgeStyle;",
            "straight": "edgeStyle=none;",
            "er": "edgeStyle=entityRelationEdgeStyle;",
        }[style]
        s = (
            f"{es}rounded=1;html=1;strokeColor={color};strokeWidth={width};endArrow=block;endFill=1;"
            f"fontSize={font};jettySize=auto;orthogonalLoop=1;"
        )
        if dashed:
            s += "dashed=1;"
        if start:
            s += f"startArrow={start};startFill=1;"
        if exit:
            s += f"exitX={exit[0]};exitY={exit[1]};exitDx=0;exitDy=0;"
        if entry:
            s += f"entryX={entry[0]};entryY={entry[1]};entryDx=0;entryDy=0;"
        if fontcolor:
            s += f"fontColor={fontcolor};"
        s += "labelBackgroundColor=#ffffff;"
        cid = self.nid()
        pts = ""
        if points:
            pts = "<Array as=\"points\">" + "".join(f'<mxPoint x="{px}" y="{py}"/>' for px, py in points) + "</Array>"
        value = escape(fmt(label, False, False), {'"': "&quot;"}) if label else ""
        geo_attrs = 'relative="1" as="geometry"'
        offset = ""
        if label_pos is not None:
            geo_attrs = f'x="{label_pos[0]}" relative="1" as="geometry"'
            offset = f'<mxPoint x="0" y="{label_pos[1]}" as="offset"/>'
        self.cells.append(
            f'<mxCell id="{cid}" value="{value}" style="{s}" edge="1" parent="1" source="{src}" target="{dst}">'
            f'<mxGeometry {geo_attrs}>{pts}{offset}</mxGeometry></mxCell>'
        )
        return cid

    def render(self) -> str:
        """The whole page as an uncompressed draw.io document."""
        body = "\n".join(self.cells)
        return (
            f'<mxfile host="Electron" modified="{self.stamp}" agent="{self.agent}" '
            'version="31.4.5" type="device">\n'
            f'  <diagram id="{self.name}" name="{self.name}">\n'
            f'    <mxGraphModel dx="0" dy="0" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" '
            f'fold="1" page="1" pageScale="1" pageWidth="{self.width}" pageHeight="{self.height}" math="0" shadow="0" '
            'background="#ffffff">\n'
            "      <root>\n        <mxCell id=\"0\"/>\n        <mxCell id=\"1\" parent=\"0\"/>\n"
            f"{body}\n"
            "      </root>\n    </mxGraphModel>\n  </diagram>\n</mxfile>\n"
        )


def title(d: Diagram, text: str, sub: str) -> None:
    """The heading every figure opens with, and the one-line reading of it."""
    d.text(40, 16, d.width - 80, 30, text, font=17, bold=True)
    d.text(40, 46, d.width - 80, 24, sub, font=11, color="#555555")


# ---------------------------------------------------------------------------
# 1. Deployment topology (exposure and secrets), as compose builds it
# ---------------------------------------------------------------------------
def build_deployment_topology() -> str:
    """I-12, I-13, I-14, M-04, M-05, M-08, S-08: what listens where, as whom."""
    d = Diagram("deployment_topology", 1540, 820)
    title(d, "As-built compose topology at default settings: what listens where, with what, as whom",
          "docker-compose.yml at commit a625cf0 (5.6.1). Every application container runs as root (no USER in any application Dockerfile). "
          "Service-to-store edges are omitted for legibility.")
    lx = 40
    for colour, text in (("red", "every interface, no authentication by default"),
                         ("orange", "every interface, default password equal to the user name"),
                         ("green", "loopback only"),
                         ("grey", "not published")):
        d.box(lx, 74, 16, 14, "", color=colour, rounded=False)
        d.text(lx + 20, 70, 300, 20, text, font=10)
        lx += 330

    d.frame(40, 100, 1180, 680, "Published on every interface (0.0.0.0)")
    # row 1: services
    y1, h1, w1 = 160, 100, 200
    api = d.box(70, y1, w1, h1, "api :8443 (HTTPS)\nagent image, root\nAPI_TOKEN empty: no authentication\nCORS *; ?access_token on every route", "red", font=10)
    gui = d.box(300, y1, w1, h1, "gui :8080 (nginx)\nproxy holds API_TOKEN (empty)\nverifies the api certificate\nroot", "red", font=10)
    review = d.box(530, y1, w1, h1, "review :8444 (HTTPS)\nREVIEW_TOKEN empty: warning only\npresents the API's private key\nwrites ./context_questions as root", "red", font=10)
    reviewgui = d.box(760, y1, w1, h1, "reviewgui :8081 (nginx)\nholds REVIEW_TOKEN (empty)\nroot", "red", font=10)
    curategui = d.box(990, y1, w1, h1, "curategui :8083 (nginx)\nholds REVIEW_TOKEN (empty)\nroot", "red", font=10)
    # row 2: volumes and mounts
    y2, h2 = 325, 110
    apitls = d.box(70, y2, 300, h2, "volume apitls\nserver.key + server.crt, written by api (rw)\nmounted read-only into seven containers that need only the certificate: gui, review, reviewgui, curategui, console, consolegui, apitest", "purple", font=10)
    ctx = d.box(530, y2, 280, h2, "bind mount ./context_questions (rw)\nhost checkout <-> review container\ntranslated_questions.md, sql_snippets.md and their .bak files\nwritten by a root process", "yellow", font=10)
    d.box(850, y2, 170, h2, "bind mount\n./models/catalog.json\n-> agent, api (ro)", "grey", font=10)
    d.box(1040, y2, 160, h2, ".env and .env.bak\ntokens and passwords in clear text\n(setup.sh:376 copies the old file)", "grey", font=10)
    # row 3: stores
    y3, h3, w3 = 500, 120, 150
    stores = [
        ("retail db :5432\nnl2sql / nl2sql\nsuperuser postgres / nl2sql\nbaked into the published image", 2),
        ("chunkdb :5433\nragproc / ragproc", 1),
        ("vectordb :5434\nragproc / ragproc", 1),
        ("feedbackdb :5435\nfeedback / feedback", 1),
        ("correctionsdb :5436\ncorrections / corrections", 1),
        ("completionsdb :5437\ncompletions / completions", 1),
        ("snippetsdb :5438\nsnippets / snippets", 1),
    ]
    for i, (label, sw) in enumerate(stores):
        d.box(70 + 165 * i, y3, w3, h3, label, "orange", font=10, stroke_width=sw)
    d.text(70, 632, 1130, 40,
           "pg_hba: host all all all scram-sha-256 (init_db.sh:23): any address may attempt a login. "
           "The reader role, the validator and the planner gate protect the agent's path only; "
           "a direct connection as postgres bypasses all three.", font=10, color="#7a2e2a")
    d.text(70, 684, 1130, 60,
           "No service has deploy.resources limits, read_only, cap_drop or no-new-privileges; every service has restart: unless-stopped. "
           "Healthchecks use ssl._create_unverified_context() (compose 441, 605, 754).", font=10, color="#555555")

    d.frame(1250, 100, 250, 360, "Published on 127.0.0.1 only")
    console = d.box(1270, 160, 210, 90, "console :8445 (HTTPS)\nCONSOLE_TOKEN empty\npresents the API's private key\nroot", "green", font=10)
    consolegui = d.box(1270, 270, 210, 70, "consolegui :8082 (nginx)\nholds CONSOLE_TOKEN (empty), root", "green", font=10)
    mlflow = d.box(1270, 360, 210, 80, "mlflow :5001\nno login; shows every question's rows", "green", font=10)
    d.frame(1250, 480, 250, 300, "Not published")
    mlflowdb = d.box(1270, 515, 210, 70, "mlflowdb\nmlflow / mlflow", "grey", font=10)
    apitest = d.box(1270, 610, 210, 60, "apitest (smoke test)\nmounts apitls read-only", "grey", font=10)
    d.edge(mlflow, mlflowdb, "", color="#666666", style="orth")

    # proxy -> upstream, routed above the service row
    d.edge(gui, api, "proxy, TLS verified", style="orth", exit=(0.5, 0), entry=(0.5, 0), points=((400, 140), (170, 140)))
    d.edge(reviewgui, review, "proxy", style="orth", exit=(0.5, 0), entry=(0.5, 0), points=((860, 140), (630, 140)))
    d.edge(curategui, review, "proxy", style="orth", exit=(0.5, 0), entry=(0.75, 0), points=((1090, 130), (680, 130)))
    d.edge(consolegui, console, "proxy", style="orth", exit=(0.5, 0), entry=(0.5, 1))
    # apitls distribution
    P = "#9673a6"
    d.edge(apitls, api, "rw", color=P, width=2, exit=(0.33, 0), entry=(0.5, 1))
    d.edge(apitls, gui, "ro", color=P, dashed=True, exit=(0.9, 0), entry=(0.5, 1), points=((340, 300), (400, 300)))
    d.edge(apitls, review, "ro; presented as its own identity", color=P, dashed=True, exit=(1, 0.2), entry=(0.15, 1),
           points=((390, 347), (390, 275), (560, 275)), label_pos=(0.35, -9))
    d.edge(apitls, reviewgui, "ro", color=P, dashed=True, exit=(1, 0.5), entry=(0.5, 1),
           points=((470, 380), (470, 290), (860, 290)), label_pos=(0.5, -9))
    d.edge(apitls, curategui, "ro", color=P, dashed=True, exit=(1, 0.8), entry=(0.5, 1),
           points=((500, 413), (500, 305), (1090, 305)), label_pos=(0.5, -9))
    d.edge(apitls, console, "ro; presented as its own identity", color=P, dashed=True, exit=(0.5, 1), entry=(0, 0.5),
           points=((220, 450), (1240, 450), (1240, 205)), label_pos=(-0.45, -9))
    d.edge(apitls, consolegui, "ro", color=P, dashed=True, exit=(0.6, 1), entry=(0, 0.5),
           points=((250, 460), (1232, 460), (1232, 305)), label_pos=(0.1, -9))
    d.edge(apitls, apitest, "ro", color=P, dashed=True, exit=(0.7, 1), entry=(0, 0.5),
           points=((280, 470), (1224, 470), (1224, 640)), label_pos=(0.4, -9))
    # bind mount
    d.edge(ctx, review, "rw, root-owned writes", color="#b85450", dashed=True, start="block", exit=(0.6, 0), entry=(0.6, 1), fontcolor="#b85450")
    return d.render()


# ---------------------------------------------------------------------------
# 2. Repair loop and per-attempt state (I-02, D-11)
# ---------------------------------------------------------------------------
def build_repair_loop_state() -> str:
    """I-02, D-11: the repair edge resets none of the presentation state."""
    d = Diagram("repair_loop_state", 1560, 690)
    title(d, "The pipeline's repair loop and what it does not reset (graph.py, 5.6.1)",
          "Boxes are LangGraph nodes; the small text in a box is the state it reads or writes. Red marks the state that survives a repair and is read by the next attempt as if it were its own.")
    nodes = [
        ("supervise", ""),
        ("5 retrievers", "parallel superstep"),
        ("aggregate", ""),
        ("generate_sql", "re-entered by repair"),
        ("validate_static", "issues on a miss"),
        ("planner_gate", "issues on a miss"),
        ("execute_query", "writes result"),
        ("review", "completeness: writes completeness; issues on a miss"),
        ("visualise", "writes chart"),
        ("narrate", "reads audit (stale after a repair); writes claims; narration_retries +1 when told to rewrite"),
        ("audit", "writes audit; issues when semantic_issue"),
        ("finish", "reached when the audit passed or the one rewrite is spent"),
    ]
    y, h, w, step = 240, 120, 105, 120
    ids = []
    for i, (name, body) in enumerate(nodes):
        colour = "blue"
        if name in ("narrate", "audit"):
            colour = "yellow"
        if name == "generate_sql":
            colour = "green"
        text_colour = "#b85450" if name == "narrate" else "#444444"
        label = f"<b>{html.escape(name)}</b>" + (f"<br><br>{small(body, text_colour)}" if body else "")
        ids.append(d.box(40 + step * i, y, w, h, label, colour, font=10, raw=True, valign="top", extra="spacingTop=8;"))
    for a, b in zip(ids, ids[1:]):
        d.edge(a, b, "", style="straight")
    repair = d.box(640, 470, 320, 70, "repair\nattempts += 1, issues consumed, hint written.\nNOT reset: audit, claims, narration_retries, chart, completeness", "red", font=10)
    for k, (i, lab) in enumerate(((4, "issue"), (5, "issue"), (6, "issue"), (7, "issue"), (10, "semantic_issue"))):
        d.edge(ids[i], repair, lab, style="straight", exit=(0.5, 1), entry=(0.1 + 0.2 * k, 0), color="#b85450")
    d.edge(repair, ids[3], "re-enters generate_sql with the\nprevious attempt's audit still in state", style="orth",
           exit=(0, 0.5), entry=(0.5, 1), color="#b85450", width=2, fontcolor="#b85450",
           points=((452, 505),), label_pos=(0.5, 0))
    d.edge(ids[10], ids[9], "drop_reasons or missing_assumptions: back to narrate once (narration_retries < MAX_NARRATION_RETRIES = 1)", style="orth",
           exit=(0.5, 0), entry=(0.5, 0), points=((1292, 190), (1172, 190)), color="#d79b00", fontcolor="#7a5a00", label_pos=(0, -12))
    d.text(1340, 395, 180, 70, "narrator exception -> retrieval_errors['narrator'] (a Stage-1 reducer key, graph.py:1087)", font=9, color="#b85450", align="center")
    d.box(40, 585, 1480, 80,
          "Consequence (I-02). After a semantic_issue sends a run to repair, the new attempt reaches narrate with the old attempt's audit still in state. "
          "If that report had any drop_reasons or missing_assumptions, the first narration of the new result is treated as a rewrite (rung bumped, "
          "trace says 'rewritten', narration_retries = 1), and the new result's own audit can then send nothing back: dropped claims are dropped on the first pass. "
          "Spec 7.3 rule 4 says 'once'; the code implements once per run, not once per attempt.", "red", font=10, bold_first=False, align="left")
    return d.render()


# ---------------------------------------------------------------------------
# 3. Trace field loss (I-03, D-07)
# ---------------------------------------------------------------------------
def build_trace_field_loss() -> str:
    """I-03, D-07: the routing fields of the trace never reach a REST client."""
    d = Diagram("trace_field_loss", 1380, 520)
    title(d, "Where the per-model trace is lost between the pipeline and its clients (I-03)",
          "state.TraceEntry carries the routing fields; the REST model does not declare them, and pydantic drops undeclared keys silently.")
    left = d.box(40, 120, 300, 200,
                 "<b>state.TraceEntry</b> (state.py:283)<br><br>node · ms · model_calls · detail<br>"
                 "<font color=\"#b85450\"><b>model · rung · route · hops</b></font><br><br>"
                 "<font style=\"font-size:9px\">written by every node via _traced(); routing fields since 5.2</font>",
                 "blue", raw=True, font=11)
    mid = d.box(400, 150, 320, 140,
                "api/translate.py:88\nTraceEntry(**t) for t in state['trace']\n\npydantic default: extra='ignore'\nundeclared keys dropped without error", "yellow", font=10)
    right = d.box(780, 120, 260, 200,
                  "<b>api.models.TraceEntry</b> (models.py:179)<br><br>node · ms · model_calls · detail<br><br>"
                  "<font color=\"#b85450\">no model, rung, route, hops</font><br>"
                  "<font style=\"font-size:9px\">two other models in the same file are extra='forbid'</font>", "blue", raw=True, font=11)
    c1 = d.box(1100, 80, 240, 70, "gui/src/api/types.ts:82\nTraceEntry: the same four fields", "grey", font=10)
    c2 = d.box(1100, 170, 240, 70, "desktop Models.java:175\nTraceEntry: the same four fields", "grey", font=10)
    c3 = d.box(1100, 260, 240, 100, "agent/API.md:248\n\"Each answer's trace names the model that actually answered every call\"\nnot true over REST", "red", font=10)
    d.edge(left, mid, "", style="straight")
    d.edge(mid, right, "model, rung, route, hops lost", style="straight", color="#b85450", fontcolor="#b85450", label_pos=(0, -16))
    d.edge(right, c1, "", style="orth")
    d.edge(right, c2, "", style="orth")
    d.edge(right, c3, "", style="orth", color="#b85450", dashed=True)
    d.box(40, 390, 1000, 90,
          "Where the routing fields do survive: the CLI's --json output (state.to_json), benchmarks/runner.py (per-model attribution), the MLflow trace (tracing.py). "
          "So the architecture's cost claim is checkable from the command line and the benchmark, and from no client of the REST API; "
          "a verdict given through the API cannot be joined to the model that produced the SQL unless MLflow is on.", "green", font=10, bold_first=False, align="left")
    return d.render()


# ---------------------------------------------------------------------------
# 4. Data flow of rows, and where the sensitive-column policy sits (I-01, I-05, D-04)
# ---------------------------------------------------------------------------
def build_row_data_flow() -> str:
    """I-01, I-05, D-04: rows reach the model host before the only control."""
    d = Diagram("row_data_flow", 1560, 740)
    title(d, "Where result rows and sample rows go, and where the documented leakage control sits (I-01, I-05, D-04)",
          "Two egress paths to the model host happen before anything the audit stage could redact; the sensitive-column parameters exist and are never passed.")
    db = d.box(40, 300, 200, 120, "retail Postgres\nread as nl2sql_reader\nREAD ONLY transaction, SET LOCAL statement_timeout, row cap", "orange", font=10)
    sample = d.box(320, 120, 260, 90, "Database._sample_rows\nSELECT * FROM <table> LIMIT n\n(database.py:254-256)", "yellow", font=10)
    schema = d.box(640, 120, 240, 90, "schema prompt block\nDDL comments + sample rows per table", "grey", font=10)
    host = d.box(960, 160, 260, 110, "model host (OLLAMA_BASE_URL)\nmay be another machine on the network\nreceives sample rows and result rows", "red", font=10)
    execute = d.box(320, 330, 260, 90, "execute_query\nresult rows (<= max_rows, truncated flag)", "blue", font=10)
    narr = d.box(640, 250, 240, 90, "narrator prompt\nquestion + result rows + chart spec", "grey", font=10)
    jobs = d.box(640, 380, 240, 90, "API job store (process memory)\nkept 1 h after finishing", "grey", font=10)
    rest = d.box(960, 380, 260, 90, "REST answer\nresult, sql, plan_cost, trace, retrieval_errors", "grey", font=10)
    clients = d.box(1280, 380, 240, 90, "web GUI · desktop client · CLI\nany HTTP client", "grey", font=10)
    fb = d.box(640, 500, 240, 90, "feedback snapshot\nfeedbackdb (pending -> reviewed)", "grey", font=10)
    rev = d.box(960, 500, 260, 90, "review service · review GUI\nvalidation runs more SQL as the reader", "grey", font=10)
    ml = d.box(640, 620, 240, 80, "MLflow trace\nmlflowdb; UI on :5001 (loopback)", "grey", font=10)
    policy = d.box(1280, 540, 240, 160,
                   "present.audit(sensitive_columns=...)\npresent.render_answer(sensitive_columns=...)\nthe only documented leakage control\n"
                   "never called with the tag set (graph.py:1107)\ntagged_sensitive_columns() has no caller\nredactions is always []", "red", font=10, dashed=True, stroke_width=2)
    d.edge(db, sample, "egress path 1 (per table)", style="orth", exit=(0.5, 0), entry=(0, 0.5), points=((140, 165),), color="#b85450", fontcolor="#b85450")
    d.edge(sample, schema, "", style="straight")
    d.edge(schema, host, "", style="orth", color="#b85450")
    d.edge(db, execute, "validated SQL", style="straight", label_pos=(0, 16))
    d.edge(execute, narr, "egress path 2", style="orth", exit=(0.5, 0), entry=(0, 0.5), points=((450, 295),), color="#b85450", fontcolor="#b85450")
    d.edge(narr, host, "", style="orth", color="#b85450")
    d.edge(execute, jobs, "", style="orth")
    d.edge(jobs, rest, "", style="straight")
    d.edge(rest, clients, "", style="straight")
    d.edge(execute, fb, "on a verdict", style="orth", exit=(0.5, 1), entry=(0, 0.5), points=((450, 545),))
    d.edge(fb, rev, "", style="straight")
    d.edge(execute, ml, "when tracing is on", style="orth", exit=(0.25, 1), entry=(0, 0.5), points=((385, 660),))
    d.edge(rest, policy, "would apply here, last of all", style="orth", dashed=True, color="#b85450", fontcolor="#b85450", exit=(0.5, 1), entry=(0.5, 0), points=((1090, 520), (1400, 520)))
    return d.render()


# ---------------------------------------------------------------------------
# 5. Spec lineage against releases (D-01)
# ---------------------------------------------------------------------------
def build_spec_lineage() -> str:
    """D-01: the specs stop at 5.2; five releases since have none."""
    d = Diagram("spec_lineage", 1600, 570)
    title(d, "Architecture specs against releases: the design authority stopped at 5.2 (D-01)",
          "By the repository's rule the highest-suffixed spec is the authority and is never edited in place. Dates are the changelog's.")
    d.text(40, 110, 70, 100, "Specs", font=12, bold=True)
    d.text(40, 345, 70, 100, "Releases", font=12, bold=True)
    ys, hs = 110, 110
    d.box(120, ys, 150, hs, "arch1 - arch3\ndesign iterations\n2026-09-20", "grey", font=10)
    a4 = d.box(300, ys, 130, hs, "arch4\nmulti-agent, four stages\n2026-09-20", "blue", font=10)
    a5 = d.box(520, ys, 130, hs, "arch5\nanswer contract, completeness\n2026-09-26", "blue", font=10)
    a51 = d.box(680, ys, 130, hs, "arch5_1\nhuman review, fix stores\n2026-09-26", "blue", font=10)
    a52 = d.box(840, ys, 200, hs, "arch5_2 - current authority\ndated 2026-09-27\nstatus line: routing 'Designed, not built'\ncatalog 'script has to be written'", "yellow", font=10, stroke_width=2)
    a6 = d.box(1340, ys, 220, hs, "arch6 - does not exist\nconsole, undo, tracing, snippets, curation, routing as built, decisions closed", "grey", font=10, dashed=True)
    yr, hr = 345, 110
    v4 = d.box(300, yr, 90, hr, "v4\n09-20", "blue", font=10)
    d.box(400, yr, 110, hr, "v4_1 … v4_5\n09-21 … 09-25\nAPI, GUI, feedback, desktop", "grey", font=10)
    v5 = d.box(520, yr, 90, hr, "v5\n09-26", "blue", font=10)
    v51 = d.box(620, yr, 100, hr, "v5_1, v5_1_2\n09-26", "blue", font=10)
    v52 = d.box(840, yr, 90, hr, "v5_2\nrouting\n09-28", "yellow", font=10)
    gap = d.frame(940, 305, 540, 165, "no architecture spec", color="red")
    d.box(955, yr, 95, hr, "v5_3\nSQL console\n09-28", "red", font=10)
    d.box(1060, yr, 95, hr, "v5_4\nreopen, delete, undo\n09-30", "red", font=10)
    d.box(1165, yr, 95, hr, "v5_5, v5_5_1\nMLflow tracing\n10-01", "red", font=10)
    d.box(1270, yr, 100, hr, "v5_6\nsnippets, curation GUI\n10-03", "red", font=10)
    d.box(1380, yr, 85, hr, "v5_6_1\n10-03", "red", font=10)
    d.edge(a4, v4, "describes", style="straight")
    d.edge(a5, v5, "describes", style="straight")
    d.edge(a51, v51, "describes", style="straight")
    d.edge(a52, v52, "describes it as unbuilt", style="straight", dashed=True, color="#d79b00", fontcolor="#7a5a00")
    d.edge(gap, a6, "should be written as", style="orth", dashed=True, color="#999999", exit=(0.9, 0), entry=(0.5, 1))
    d.text(40, 495, 1500, 60,
           "Design decisions made since 5.2 live in agent/README.md (six measured routing departures) and in the changelogs; arch_diagrams/arch_v5_6.svg exists with no prose. "
           "Open decisions in arch5_2 are decided in code and closed nowhere.", font=10, color="#555555")
    return d.render()


# ---------------------------------------------------------------------------
# 6. Trust boundaries and identity (D-05, I-06, S-01..S-06, S-14)
# ---------------------------------------------------------------------------
def build_trust_boundaries() -> str:
    """D-05, I-06, S-01, S-03, S-06, S-14: every hop, and the identity none carries."""
    d = Diagram("trust_boundaries", 1600, 800)
    title(d, "Trust boundaries as built, and where identity would have to be (D-05, I-06, S-01, S-03, S-06, S-14)",
          "Dashed verticals are hops between processes or machines. Every hop that authenticates does so with one static token per service, empty by default.")
    cols = [("Actors", 40, 200), ("Clients", 300, 220), ("Proxies (nginx)", 580, 240), ("Services", 880, 260), ("Stores", 1200, 180), ("Model host", 1420, 150)]
    for name, x, w in cols:
        d.text(x, 76, w, 24, name, font=12, bold=True, align="center")
    for bx in (270, 560, 860, 1180, 1400):
        d.box(bx, 100, 1, 580, "", color=("none", "#999999"), dashed=True, rounded=False)
    user = d.box(40, 120, 200, 70, "end user\n(browser)", "blue", font=10)
    reviewer = d.box(40, 220, 200, 70, "reviewer / curator\n(browser)", "blue", font=10)
    desk = d.box(40, 320, 200, 70, "desktop user", "blue", font=10)
    d.box(40, 420, 200, 80, "operator (shell)\n.env holds every token and password; .env.bak holds a copy", "yellow", font=10)
    attacker = d.box(40, 560, 200, 100, "anyone on the network\nreaches the 0.0.0.0 ports directly", "red", font=10)
    page = d.box(300, 120, 220, 70, "web GUI page\nsame origin, never sees a token", "grey", font=10)
    rpage = d.box(300, 220, 220, 70, "review / curation pages\nsame origin", "grey", font=10)
    cpage = d.box(300, 320, 220, 70, "console page (loopback)", "grey", font=10)
    jar = d.box(300, 420, 220, 80, "desktop jar\nbearer token; CA file, pinned fingerprint or --insecure", "grey", font=10)
    curl = d.box(300, 560, 220, 70, "any HTTP or Postgres client", "grey", font=10)
    pgui = d.box(580, 120, 240, 80, "nginx (gui)\nadds Authorization: Bearer API_TOKEN\nAPI_TOKEN empty by default", "grey", font=10)
    prev = d.box(580, 220, 240, 80, "nginx (reviewgui, curategui)\nadds REVIEW_TOKEN (empty by default)", "grey", font=10)
    pcon = d.box(580, 320, 240, 80, "nginx (consolegui)\nadds CONSOLE_TOKEN (empty by default)", "grey", font=10)
    api = d.box(880, 100, 260, 130, "API :8443\nauthenticate(): presented != token (api/app.py:310)\n?access_token= accepted on every route\nno principal; GET /v1/questions lists every job", "red", font=10)
    rsvc = d.box(880, 250, 260, 130, "Review :8444\ntoken optional, warning only (settings.py:282)\nX-Reviewer header is the attribution\nwrites the checkout, runs the loaders", "red", font=10)
    csvc = d.box(880, 400, 260, 100, "Console :8445 (loopback)\nvalidator + planner gate + reader role\ntoken optional", "green", font=10)
    retail = d.box(1200, 100, 180, 120, "retail\nnl2sql_reader (SELECT only)\nSET LOCAL ROLE <principal>: no caller\nsuperuser postgres / nl2sql", "orange", font=10)
    fdb = d.box(1200, 240, 180, 80, "feedbackdb\nnl2sql_feedback_writer + RLS fence", "orange", font=10)
    fix = d.box(1200, 340, 180, 100, "corrections · completions · snippets\nowner role from review; snippets_reader for the agent", "orange", font=10)
    rag = d.box(1200, 460, 180, 80, "chunkdb · vectordb\nragproc owner role", "orange", font=10)
    host = d.box(1420, 180, 150, 120, "Ollama host\nremote machine\nsees schema samples and result rows", "red", font=10)
    d.edge(user, page, "", style="straight")
    d.edge(page, pgui, "", style="straight")
    d.edge(pgui, api, "HTTPS, verified", style="straight")
    d.edge(reviewer, rpage, "", style="straight")
    d.edge(rpage, prev, "", style="straight")
    d.edge(prev, rsvc, "HTTPS, verified", style="straight")
    d.edge(cpage, pcon, "", style="straight")
    d.edge(pcon, csvc, "", style="straight")
    d.edge(desk, jar, "", style="straight")
    d.edge(jar, api, "Bearer token, direct", style="orth", exit=(1, 0.5), entry=(0, 0.9), points=((850, 460), (850, 217)))
    d.edge(api, retail, "", style="straight")
    d.edge(api, fdb, "", style="straight")
    d.edge(api, fix, "", style="straight")
    d.edge(api, rag, "", style="straight")
    d.edge(api, host, "prompts carrying rows", style="orth", color="#b85450", fontcolor="#b85450", exit=(0.5, 0), entry=(0.13, 0),
           points=((1010, 64), (1440, 64)), label_pos=(0.1, -10))
    d.edge(rsvc, fdb, "", style="straight")
    d.edge(rsvc, fix, "", style="straight")
    d.edge(rsvc, retail, "validates as the reader", style="straight", font=9, label_pos=(0.55, -10))
    d.edge(rsvc, rag, "", style="straight")
    d.edge(csvc, retail, "as the reader", style="orth", exit=(1, 0.5), entry=(0.3, 1), points=((1170, 450), (1170, 232), (1254, 232)), font=9, label_pos=(-0.2, 12))
    d.edge(attacker, api, "no token needed by default", style="orth", color="#b85450", fontcolor="#b85450", width=2, exit=(1, 0.3), entry=(0, 0.5), points=((860, 590), (860, 165)))
    d.edge(attacker, rsvc, "no token needed by default", style="orth", color="#b85450", fontcolor="#b85450", width=2, exit=(1, 0.5), entry=(0, 0.5), points=((870, 610), (870, 315)))
    d.edge(attacker, retail, "5432-5438 open; default and superuser passwords", style="orth", color="#b85450", fontcolor="#b85450", width=2, exit=(1, 0.8), entry=(0.5, 1), points=((1290, 640),))
    d.edge(curl, api, "", style="straight", color="#999999")
    d.box(40, 700, 1530, 70,
          "Identity: none. One static token per service, compared with != in three separately written closures; no user, tenant or session; attribution is a self-asserted header; "
          "the executor's SET LOCAL ROLE path (database.py:299-312) has no caller. Adding identity later means touching three applications and four proxies with no shared dependency to change.",
          "red", font=10, bold_first=False, align="left")
    return d.render()


# ---------------------------------------------------------------------------
# 7. Secrets flow (M-06, S-10)
# ---------------------------------------------------------------------------
def build_secrets_flow() -> str:
    """M-06, S-10: every place a secret is readable, and the one place it is masked."""
    d = Diagram("secrets_flow", 1320, 560)
    title(d, "How secrets move through the stack (M-06, S-10)",
          "Orange: a place a secret is readable in clear text at runtime. Red: a copy nobody needs. Green: the one place masking exists.")
    setup = d.box(40, 90, 230, 90, "setup.sh\ngenerates no secrets: tokens default empty, passwords default to user names", "grey", font=10)
    env = d.box(330, 90, 240, 90, ".env\nAPI_TOKEN, REVIEW_TOKEN, CONSOLE_TOKEN\n*_PASSWORD, *_DB_URL with inline passwords", "yellow", font=10)
    bak = d.box(330, 220, 240, 70, ".env.bak\nfull copy on every setup.sh run (setup.sh:376)", "red", font=10)
    urls = d.box(620, 220, 240, 90, "API_FEEDBACK_DB_URL=\npostgresql://user:password@host/db\n(a password inside a URL inside the environment)", "orange", font=10)
    compose = d.box(620, 90, 240, 90, "docker compose\nenvironment: blocks (no secrets:)", "grey", font=10)
    cenv = d.box(920, 90, 240, 90, "container environment\nreadable via docker inspect and /proc/<pid>/environ", "orange", font=10)
    proxies = d.box(920, 220, 240, 80, "nginx proxies\ntoken -> Authorization header on every upstream request", "grey", font=10)
    sub = d.box(920, 330, 240, 90, "review service -> subprocess environment\nloaders and the snippet validator receive reader credentials", "orange", font=10)
    red = d.box(620, 340, 240, 80, "_redacted() for readiness and logs\nmasks URL passwords; defined twice", "green", font=10)
    d.edge(setup, env, "writes", style="straight")
    d.edge(env, bak, "copies", style="straight", color="#b85450")
    d.edge(env, compose, "", style="straight")
    d.edge(env, urls, "contains", style="orth", exit=(1, 0.8), entry=(0, 0.3), points=((600, 162), (600, 247)))
    d.edge(compose, cenv, "", style="straight")
    d.edge(cenv, proxies, "", style="orth")
    d.edge(cenv, sub, "", style="orth", exit=(0.2, 1), entry=(0.2, 0))
    d.edge(cenv, red, "only when printed", style="orth", dashed=True, color="#82b366", exit=(0, 0.8), entry=(1, 0.5), points=((890, 162), (890, 380)), label_pos=(0.6, 0))
    d.box(40, 450, 1240, 80,
          "Missing: generated secrets at first setup; 0600 on .env; secrets excluded from .env.bak; file-backed compose secrets: read from /run/secrets; a rotation procedure; "
          "scrubbing of ?access_token= from access logs; credentials passed as arguments rather than inherited environment.",
          "red", font=10, bold_first=False, align="left", dashed=True)
    return d.render()


# ---------------------------------------------------------------------------
# 8. Plan dependencies (the combined plan)
# ---------------------------------------------------------------------------
#: The combined plan's items by phase, as v6_x_review_mitigation_plan.md lists them.
PLAN = {
    0: [("01", "Decide identity model"), ("02", "Decide sensitive-column policy"), ("03", "Decide topology target"),
        ("04", "Decide review write model"), ("05", "Benchmark wording (decided)")],
    1: [("06", "Stores bind to loopback by default"), ("07", "Dataset image: no baked superuser password (v1_2)"),
        ("08", "Generated secrets on first setup"), ("09", "Tokens required by default"), ("10", "Constant-time token compare"),
        ("11", "Query token on events route only; log scrub"), ("12", "CORS default none"), ("13", "Bound job queue, rate limit"),
        ("14", "Time-box EXPLAIN")],
    2: [("15", "Per-attempt state reset on repair"), ("16", "node_errors channel"), ("17", "Resolve sensitive-column policy"),
        ("18", "Routing fields over REST"), ("19", "extra=forbid on wire models")],
    3: [("20", "Shared Python package"), ("21", "Principal type, token mapping"), ("22", "Single engine accessor, SET LOCAL"),
        ("23", "Error taxonomy"), ("24", "Pin and hash Python dependencies"), ("25", "Shared front-end package")],
    4: [("26", "Routers instead of closures"), ("27", "Review writes via library with a lock"), ("28", "Non-root review owns the mount"),
        ("29", "Sequences and upserts"), ("30", "Identity applied end to end"), ("31", "Non-root USER in every image"),
        ("32", "Operator-only detail"), ("33", "Cache reload signal")],
    5: [("34", "Compose hardening block"), ("35", "Base images by digest"), ("36", "Separate TLS identities"),
        ("37", "One proxy image, verifying healthcheck"), ("38", "Compose secrets:"), ("39", "Role-level limits"),
        ("40", "Store consolidation"), ("41", "Orchestration in Python"), ("42", "Provenance in publishing")],
    6: [("43", "arch6 spec"), ("44", "Verified security blueprint"), ("45", "Threat model and tiers"),
        ("46", "Fix overstated controls in docs"), ("47", "One home per fact"), ("48", "Comment policy")],
    7: [("49", "Security test tier"), ("50", "Conformance and drift tests"), ("51", "Reviewed coverage exclusions")],
}
PHASE_NAMES = {0: "Phase 0: decisions", 1: "Phase 1: safe defaults and secrets", 2: "Phase 2: pipeline correctness",
               3: "Phase 3: shared foundations", 4: "Phase 4: service refactors", 5: "Phase 5: hardening and topology",
               6: "Phase 6: documentation", 7: "Phase 7: tests"}
#: The suggested release of each phase, as a palette colour: decisions,
#: v6.0.0, v6.1, v6.2, documentation alongside v6.1, tests with what they guard.
RELEASE_COLOUR = {0: "yellow", 1: "green", 2: "green", 3: "blue", 4: "blue", 5: "orange", 6: "purple", 7: "grey"}
#: "needed before" pairs from the plan's Depends-on column; `Pn` is a whole phase.
DEPS = [("08", "09"), ("02", "17"), ("18", "19"), ("01", "21"), ("20", "21"), ("20", "22"), ("20", "23"), ("20", "26"),
        ("04", "27"), ("20", "27"), ("04", "28"), ("31", "28"), ("21", "30"), ("26", "30"), ("16", "32"), ("30", "33"),
        ("31", "34"), ("24", "35"), ("25", "37"), ("36", "37"), ("08", "38"), ("03", "40"), ("06", "40"), ("08", "40"),
        ("27", "41"), ("P0", "43"), ("P2", "43"), ("43", "44"), ("39", "44"), ("P1", "45"), ("09", "46"), ("11", "46"),
        ("18", "46"), ("43", "47"), ("P1", "49"), ("31", "49"), ("35", "49"), ("39", "49"), ("43", "50")]


def build_plan_dependencies() -> str:
    """The combined plan's 51 items in their phases, with what each needs first.

    An arrow is drawn only within a phase or into the next one; a dependency
    on an earlier phase is written inside the box instead, because forty
    arrows across eight columns is a picture nobody can read.
    """
    d = Diagram("plan_dependencies", 1900, 1000)
    title(d, "Combined plan: phases and dependencies (V6-01 … V6-51)",
          "An arrow means 'needed before' and is drawn only within a phase or into the next one; a dependency on an earlier phase is written inside the box. "
          "Colour is the suggested release: yellow decisions; green v6.0.0; blue v6.1; orange v6.2; purple documentation alongside v6.1; grey tests landing with what they guard.")
    column = {num: p for p, items in PLAN.items() for num, _ in items}
    for p in PLAN:
        column[f"P{p}"] = p
    far: dict[str, list[str]] = {}
    for a, b in DEPS:
        if column[b] - column[a] >= 2:
            far.setdefault(b, []).append(a)
    ids: dict[str, str] = {}
    colw, gapx, x0, y0, h, gapy = 200, 30, 40, 140, 62, 10
    for p, items in PLAN.items():
        x = x0 + (colw + gapx) * p
        ids[f"P{p}"] = d.box(x, 92, colw, 36, PHASE_NAMES[p], "frame", font=10, bold_first=False, dashed=False)
        for k, (num, label) in enumerate(items):
            y = y0 + (h + gapy) * k
            colour = RELEASE_COLOUR[p] if not (p == 6 and num == "46") else "green"
            value = f"<b>V6-{num}</b><br>{html.escape(label)}"
            if num in far:
                names = ", ".join(("Phase " + a[1:]) if a.startswith("P") else f"V6-{a}" for a in far[num])
                value += "<br>" + small("after " + names, "#555555")
            ids[num] = d.box(x, y, colw, h, value, colour, font=10, raw=True)
    for a, b in DEPS:
        span = column[b] - column[a]
        if span >= 2:
            continue
        src, dst = ids[a], ids[b]
        if span == 1:
            d.edge(src, dst, "", style="er", color="#555555")
        else:
            x = x0 + (colw + gapx) * column[a]
            ka = [n for n, _ in PLAN[column[a]]].index(a)
            kb = [n for n, _ in PLAN[column[b]]].index(b)
            ya = y0 + (h + gapy) * ka + h / 2
            yb = y0 + (h + gapy) * kb + h / 2
            d.edge(src, dst, "", style="orth", color="#555555", exit=(0, 0.5), entry=(0, 0.5), points=((x - 14, ya), (x - 14, yb)))
    d.text(40, 800, 1800, 60,
           "Range dependencies: arch6 (V6-43) needs every Phase 0 decision and every Phase 2 fix; the threat model (V6-45) and the security test tier (V6-49) need Phase 1's safe defaults. "
           "Items with neither an arrow nor an 'after' line can start at any time within their phase.", font=10, color="#555555")
    return d.render()


# ---------------------------------------------------------------------------
# 9. Severity matrix (the summary)
# ---------------------------------------------------------------------------
def build_severity_matrix() -> str:
    """The summary's count of findings by lens and severity, as stacked bars."""
    d = Diagram("severity_matrix", 940, 440)
    title(d, "Findings by area and severity (66 findings, five lenses)",
          "D architecture as documented · I architecture as implemented · C code hygiene · M containers · S security controls")
    rows = [("D  architecture as documented (15)", (0, 5, 7, 3)), ("I  architecture as implemented (17)", (0, 5, 10, 2)),
            ("C  code hygiene (10)", (0, 0, 6, 4)), ("M  containers and microservices (9)", (1, 3, 4, 1)),
            ("S  security controls (15)", (1, 2, 8, 4)), ("All (66)", (2, 15, 35, 14))]
    colours = [("#b85450", "#ffffff"), ("#ea6b66", "#ffffff"), ("#ffe6cc", "#000000"), ("#d5e8d4", "#000000")]
    scale = 9
    for i, (label, counts) in enumerate(rows):
        y = 90 + 46 * i
        d.text(40, y + 6, 240, 30, label, font=11, bold=(label.startswith("All")))
        x = 290
        for (n, (fill, fc)) in zip(counts, colours):
            if n == 0:
                continue
            w = n * scale
            d.box(x, y, w, 32, str(n), color=(fill, "#ffffff"), font=11, bold_first=False, rounded=False, fontcolor=fc)
            x += w
    lx = 290
    for (fill, fc), name in zip(colours, ("Critical", "High", "Medium", "Low")):
        d.box(lx, 380, 16, 14, "", color=(fill, "#999999"), rounded=False)
        d.text(lx + 20, 376, 120, 20, name, font=10)
        lx += 130
    return d.render()


# ---------------------------------------------------------------------------
# 10. Duplication matrix (C-01, I-09)
# ---------------------------------------------------------------------------
def build_duplication_matrix() -> str:
    """C-01, I-09: the same helper defined in several packages, counted."""
    cols = ["agent core", "agent/api", "agent/console", "review", "rag", "gui", "review/gui", "curate", "console GUI", "desktop"]
    rows = [
        ("env helpers (_env, _env_bool, _env_int, …)", {"agent core": 1, "agent/api": 1, "review": 1, "rag": 1}),
        ("Embedder protocol", {"agent core": 3, "review": 1, "rag": 1}),
        ("build_embedder()", {"agent core": 2, "rag": 1}),
        ("pgvector literal (vector_literal)", {"agent core": 2, "review": 1, "rag": 3}),
        ("FALLBACK_CODES error map", {"agent/api": 1, "review": 1}),
        ("Health / Readiness / ApiError models", {"agent/api": 1, "review": 1}),
        ("_redacted()", {"review": 2}),
        ("json_safe()", {"agent/console": 1, "review": 1}),
        ("token check (presented != token)", {"agent/api": 1, "agent/console": 1, "review": 1}),
        ("plainText / Markup.plain (three-entity unescape)", {"gui": 1, "review/gui": 1, "desktop": 1}),
        ("HTTP client + ApiError / ApiException", {"gui": 1, "review/gui": 1, "curate": 1, "console GUI": 1, "desktop": 1}),
        ("nginx image + 10-*.envsh + vite proxy block", {"gui": 1, "review/gui": 1, "curate": 1, "console GUI": 1}),
    ]
    d = Diagram("duplication_matrix", 1500, 660)
    title(d, "Copy-and-extend: the same helper written in several packages (C-01, I-09)",
          "Cell = number of independent definitions in that package at 5.6.1 (non-test code, confirmed by search). 41 copies of 12 things; no shared package exists.")
    x0, y0, rowh, colw = 40, 120, 36, 95
    for j, c in enumerate(cols):
        d.text(x0 + 340 + colw * j, 84, colw, 34, c, font=10, bold=True, align="center")
    d.text(x0 + 340 + colw * len(cols), 84, 90, 34, "copies", font=10, bold=True, align="center")
    shade = {1: ("#fff2cc", "#000000"), 2: ("#ffe6cc", "#000000"), 3: ("#f8cecc", "#000000")}
    for i, (name, cells) in enumerate(rows):
        y = y0 + rowh * i
        d.text(x0, y + 8, 330, rowh, name, font=10)
        total = 0
        for j, c in enumerate(cols):
            n = cells.get(c, 0)
            total += n
            if n:
                fill, fc = shade[min(n, 3)]
                d.box(x0 + 340 + colw * j, y, colw - 2, rowh - 4, str(n), color=(fill, "#cccccc"), font=11, bold_first=False, rounded=False, fontcolor=fc)
            else:
                d.box(x0 + 340 + colw * j, y, colw - 2, rowh - 4, "", color=("#ffffff", "#eeeeee"), rounded=False)
        d.box(x0 + 340 + colw * len(cols), y, 88, rowh - 4, str(total), color=("#f5f5f5", "#cccccc"), font=11, bold_first=False, rounded=False)
    d.text(40, 560, 1420, 60,
           "The review service also uses the RAG package two ways: as a library for parsing (rag/ on sys.path) and as a command line for loading (subprocess.run([sys.executable, script], cwd=rag_dir), promote.py:437). "
           "Every fix to the token check, the error envelope or the embedder must be repeated in each copy, and the tests give no signal when one is missed.", font=10, color="#555555")
    return d.render()


# ===========================================================================
# The second cycle: v6_1_review, at commit 9b0b340 (6.0.1)
# ===========================================================================

V6_1 = dict(agent="nl2sql v6_1_review diagram generator", stamp="2026-10-04T00:00:00.000Z")


# ---------------------------------------------------------------------------
# 11. Deployment topology at 6.0.1 (I-12, I-13, M-04, M-08, M-10, S-08, S-16)
# ---------------------------------------------------------------------------
def build_v6_1_deployment_topology() -> str:
    """I-12, I-13, M-01, M-04, M-08, M-10, S-08, S-16: what listens where at 6.0.1."""
    d = Diagram("v6_1_deployment_topology", 1560, 900, **V6_1)
    title(d, "As-built compose topology at default settings, 6.0.1: what listens where, with what, as whom",
          "docker-compose.yml at commit 9b0b340 (6.0.1): 23 services, 14 volumes. Fourteen of fifteen images run as root; "
          "the directory's drops to the ldap user in its entry point. Service-to-store edges are omitted for legibility.")
    lx = 40
    for colour, text in (("red", "every interface; a session is required"),
                         ("orange", "every interface; default password equal to the user name"),
                         ("green", "loopback only"),
                         ("grey", "not published"),
                         ("purple", "TLS material")):
        d.box(lx, 74, 16, 14, "", color=colour, rounded=False)
        d.text(lx + 20, 70, 290, 20, text, font=10)
        lx += 300

    d.frame(40, 100, 1200, 760, "Published on every interface (0.0.0.0)")
    y1, h1, w1 = 160, 104, 180
    api = d.box(60, y1, w1, h1, "api :8443 (HTTPS)\nagent image, root\nsession (nl2sql_users)\npresents the shared key\nCORS * by default", "red", font=10)
    gui = d.box(255, y1, w1, h1, "gui :8080 (nginx, HTTPS)\nholds no token with sign-in on\nproxies /auth/ and /v1/\nroot", "red", font=10)
    review = d.box(450, y1, w1, h1, "review :8444 (HTTPS)\nsession (reviewers / curators)\npresents the shared key\nwrites ./context_questions as root", "red", font=10)
    reviewgui = d.box(645, y1, w1, h1, "reviewgui :8081 (nginx)\ncurategui :8083 (nginx)\nno token with sign-in on\nroot", "red", font=10)
    auth = d.box(840, y1, w1, h1, "auth :8446 (HTTPS)\nsign-in; /directory/v1 (admins)\nsigning key in its own volume\npresents the shared key; root\nrolesync: CREATEROLE", "red", font=10, stroke_width=2)
    d.box(1035, y1, 190, h1, "apitest (smoke test)\nmounts apitls read-only\ncurl + jq, alpine:3.21", "grey", font=10)

    y2, h2 = 320, 112
    apitls = d.box(60, y2, 330, h2, "volume apitls\nserver.key (0600, root) + server.crt, written by api\npresented as their own identity by api, review, console, auth\nmounted into 11 containers; 7 need only the certificate", "purple", font=10, stroke_width=2)
    authkeys = d.box(410, y2, 220, h2, "volume authkeys\nsession.pub, written by auth\nread-only in api, console, review\nre-read when it changes", "purple", font=10)
    d.box(650, y2, 190, h2, "volume authdata\nsession.key (0600)\nmounted by auth alone", "purple", font=10)
    ldaptls = d.box(860, y2, 200, h2, "volume ldaptls\nldap.crt + ldap.key, written by ldap\nread-only in postgres and auth", "purple", font=10)
    ctx = d.box(1080, y2, 145, h2, "bind mount\n./context_questions (rw)\nroot-owned writes", "yellow", font=10)

    y3, h3, w3 = 490, 130, 158
    stores = [
        ("retail db :5432 -- no TLS\nnl2sql / nl2sql\nsuperuser postgres / nl2sql\nrolesync: generated\npg_hba: sign-in block, then\nhost all all all scram", 2),
        ("chunkdb :5433\nragproc / ragproc", 1),
        ("vectordb :5434\nragproc / ragproc", 1),
        ("feedbackdb :5435\nfeedback / feedback", 1),
        ("correctionsdb :5436\ncorrections / corrections", 1),
        ("completionsdb :5437\ncompletions / completions", 1),
        ("snippetsdb :5438\nsnippets / snippets", 1),
    ]
    boxes = []
    for i, (label, sw) in enumerate(stores):
        boxes.append(d.box(60 + 168 * i, y3, w3, h3, label, "orange", font=10, stroke_width=sw))
    retail = boxes[0]
    d.text(60, 636, 1160, 44,
           "pg_hba.conf after launch.sh: 'host all reader,rolesync all scram-sha-256', then 'host nl2sql_retail +nl2sql_ldap all ldap ...' (clear-text "
           "password to the server, which has no certificate), then the image's own 'host all all all scram-sha-256' -- the catch-all that admits the superuser "
           "from any address is still the last rule (ldap_hba.sh:76-77, init_db.sh:23, 34).", font=10, color="#7a2e2a")
    d.text(60, 690, 1160, 56,
           "No service has deploy.resources, read_only, cap_drop or no-new-privileges; restart: unless-stopped everywhere. Health checks skip certificate "
           "verification in twelve places (four compose services, two Python Dockerfiles, six nginx Dockerfiles). AUTH_ENABLED defaults true in compose "
           "(eight services) and false in every settings module (S-17).", font=10, color="#555555")

    d.frame(1270, 100, 270, 380, "Published on 127.0.0.1 only")
    console = d.box(1290, 150, 230, 80, "console :8445 (HTTPS)\nsession; presents the shared key; root", "green", font=10)
    d.box(1290, 245, 230, 60, "consolegui :8082 (nginx)\nroot", "green", font=10)
    dirgui = d.box(1290, 320, 230, 64, "directorygui :8084 (nginx)\nits API is /directory/v1 on auth :8446\n-- every interface (M-10)", "green", font=10)
    mlproxy = d.box(1290, 400, 230, 64, "mlflowproxy :5001 (HTTPS)\nauth_request -> /auth/verify\nBasic or session", "green", font=10)

    d.frame(1270, 500, 270, 360, "Not published")
    ldap = d.box(1290, 540, 230, 110, "ldap :389 / :636 (OpenLDAP)\nruns as ldap (become); own key\nStartTLS required for a password\nArgon2; lockout after 5\nreplica: remoteauth to the primary", "grey", font=10, stroke_width=2)
    mlflow = d.box(1290, 665, 230, 60, "mlflow :5000\nno login; every question's rows", "grey", font=10)
    mlflowdb = d.box(1290, 740, 230, 50, "mlflowdb -- mlflow / mlflow", "grey", font=10)
    d.edge(mlproxy, mlflow, "", color="#666666", style="orth", exit=(0.25, 1), entry=(0.25, 0))
    d.edge(mlflow, mlflowdb, "", color="#666666", style="orth")

    # proxies -> upstreams
    d.edge(gui, api, "proxy, TLS verified", style="orth", exit=(0.5, 0), entry=(0.5, 0), points=((345, 140), (150, 140)))
    d.edge(reviewgui, review, "proxy", style="orth", exit=(0.5, 0), entry=(0.5, 0), points=((735, 140), (540, 140)))
    d.edge(gui, auth, "/auth/ (sign-in, cookie)", style="orth", exit=(0.8, 0), entry=(0.5, 0), points=((399, 130), (930, 130)), label_pos=(0.1, -9))
    d.edge(dirgui, auth, "/directory/, /auth/", style="orth", exit=(0, 0.5), entry=(0.5, 1), points=((1250, 352), (1250, 290), (930, 290)), label_pos=(0.3, -9))
    # sign-in path
    d.edge(auth, retail, "signs in as the person: clear-text password, sslmode=prefer", color="#b85450", width=2, exit=(0.3, 1), entry=(0.5, 0),
           points=((894, 470), (139, 470)), fontcolor="#b85450", label_pos=(0.3, -9))
    d.edge(retail, ldap, "binds to the directory: StartTLS, verified (LDAPTLS_CACERT)", color="#82b366", width=2, exit=(0.5, 1), entry=(0.5, 1),
           points=((139, 880), (1405, 880)), fontcolor="#4f7a45", label_pos=(0.2, -9))
    d.edge(ldaptls, retail, "ro", color="#9673a6", dashed=True, exit=(0.2, 1), entry=(0.9, 0), points=((900, 460), (202, 460)), label_pos=(0.1, -9))
    # key distribution: a few representative edges
    P = "#9673a6"
    d.edge(apitls, api, "rw", color=P, width=2, exit=(0.2, 0), entry=(0.5, 1))
    d.edge(apitls, review, "ro; presented", color=P, dashed=True, exit=(0.7, 0), entry=(0.3, 1), points=((291, 300), (504, 300)), label_pos=(0.4, -9))
    d.edge(apitls, auth, "ro; presented", color=P, dashed=True, exit=(1, 0.3), entry=(0.2, 1), points=((400, 354), (400, 290), (876, 290)), label_pos=(0.5, -9))
    d.edge(apitls, console, "ro; presented", color=P, dashed=True, exit=(0.5, 1), entry=(0, 0.5), points=((225, 450), (1255, 450), (1255, 190)), label_pos=(-0.4, -9))
    d.edge(authkeys, api, "ro", color=P, dashed=True, exit=(0.3, 0), entry=(0.8, 1), points=((476, 280), (204, 280)), label_pos=(0.5, -9))
    d.edge(ctx, review, "rw, root-owned writes", color="#b85450", dashed=True, start="block", exit=(0.2, 0), entry=(0.85, 1),
           points=((1109, 292), (603, 292)), fontcolor="#b85450", label_pos=(0.3, -9))
    return d.render()


# ---------------------------------------------------------------------------
# 12. How a sign-in travels (S-16, S-17, S-18, S-19, S-20)
# ---------------------------------------------------------------------------
def build_v6_1_signin_flow() -> str:
    """S-16, S-18, S-19, S-20: every hop a password or a session crosses, and which are in clear."""
    d = Diagram("v6_1_signin_flow", 1560, 760, **V6_1)
    title(d, "How a sign-in travels at 6.0.1, and where the password is in clear text",
          "auth/nl2sql_auth/login.py, auth/nl2sql_identity/guard.py, docker/ldap_hba.sh, gui/nginx.conf.template. "
          "Red: a clear-text password. Green: TLS, verified. Purple: a signed session. Grey: a static token.")
    lx = 40
    for colour, text in (("#b85450", "clear-text password"), ("#82b366", "TLS, certificate verified"),
                         ("#9673a6", "signed session (Ed25519 JWT)"), ("#666666", "static service token")):
        d.box(lx, 74, 16, 14, "", color=(colour, colour), rounded=False)
        d.text(lx + 20, 70, 260, 20, text, font=10)
        lx += 290

    # columns
    browser = d.box(40, 130, 170, 90, "Browser\nany page :8080 / :8081 / :8083\n(HTTPS, self-signed)", "white", font=10)
    desktop = d.box(40, 250, 170, 80, "Desktop client\n--auth-url https://host:8446\ntoken kept in memory", "white", font=10)
    psql = d.box(40, 370, 170, 80, "psql / a BI tool\non another machine\n(documented: rolesync.py:14)", "white", font=10)
    mlclient = d.box(40, 490, 170, 80, "MLflow client\nMLFLOW_TRACKING_USERNAME\n/ _PASSWORD (Basic)", "white", font=10)

    nginx = d.box(300, 130, 190, 90, "nginx (the page's)\nproxies /auth/ and /v1/\nX-Forwarded-Host, -Proto=$scheme\nholds no token", "blue", font=10)
    mlproxy = d.box(300, 490, 190, 80, "mlflowproxy :5001\nauth_request\n-> /auth/verify (method in a header)", "blue", font=10)

    authsvc = d.box(590, 130, 230, 440,
                    "auth service :8446\n\nPOST /auth/login -> cookie\nPOST /auth/token -> token\nGET /auth/verify (Basic cached 5 min)\n\n"
                    "throttle: 5 per name, 50 per address\n(address = last X-Forwarded-For hop,\nclient-controlled when direct: S-20)\n\n"
                    "signs with session.key (Ed25519)\n8 hours; jti minted, never checked\nsign-out deletes the cookie only (S-18)",
                    "purple", font=10, valign="top", stroke_width=2)

    pg = d.box(920, 130, 230, 300,
               "retail Postgres :5432\nno certificate, ssl off\n\npg_hba (ldap_hba.sh:76-77):\nhost all reader,rolesync all scram\n"
               "host nl2sql_retail +nl2sql_ldap all ldap\n  ldapserver=nl2sql-ldap ldaptls=1\nhost all all all scram  (image's own)\n\n"
               "the ldap method is AuthenticationCleartextPassword:\nthe client sends the password as typed",
               "orange", font=10, valign="top", stroke_width=2)

    ldap = d.box(1250, 130, 270, 180,
                 "OpenLDAP (nl2sql-ldap) :389\nStartTLS required for a simple bind\n(security simple_bind=64)\nArgon2 hash; lockout 5 / 900 s\n"
                 "replica: bind passed to the primary\n(no StartTLS by default: S-23)", "grey", font=10, valign="top")

    guard = d.box(920, 470, 230, 150,
                  "API / console / review\nGuard.identify -> Identity\nverifies with session.pub (authkeys)\nroles re-read via pg_has_role <= 60 s\n"
                  "SET LOCAL ROLE <person>\n-- same SELECT as the reader; no RLS", "blue", font=10, valign="top")
    token = d.box(1250, 470, 270, 150,
                  "Static service token (if set)\nAPI_TOKEN / REVIEW_TOKEN / CONSOLE_TOKEN\nIdentity(kind=service, roles=every role\nof the service, name='service token')\n"
                  "author falls back to X-Reviewer (S-19)", ("#f5f5f5", "#666666"), font=10, valign="top")

    R, G, P, K = "#b85450", "#82b366", "#9673a6", "#666666"
    # browser sign-in
    d.edge(browser, nginx, "1. POST /auth/login (TLS)", color=G, width=2, exit=(1, 0.3), entry=(0, 0.3), label_pos=(0, -9))
    d.edge(nginx, authsvc, "2. proxied, TLS verified", color=G, width=2, exit=(1, 0.3), entry=(0, 0.12), label_pos=(0, -9))
    d.edge(authsvc, pg, "3. connect as the person\nsslmode=prefer -> plain TCP", color=R, width=3, exit=(1, 0.2), entry=(0, 0.25), fontcolor=R, label_pos=(0, -16))
    d.edge(pg, ldap, "4. simple bind over StartTLS\nLDAPTLS_CACERT verifies", color=G, width=2, exit=(1, 0.3), entry=(0, 0.4), fontcolor="#4f7a45", label_pos=(0, -16))
    d.edge(authsvc, nginx, "5. Set-Cookie nl2sql_session\nHttpOnly SameSite=Strict Secure", color=P, width=2, dashed=True, exit=(0, 0.3), entry=(1, 0.7), fontcolor=P, label_pos=(0, 16))
    d.edge(nginx, guard, "6. /v1/... with the cookie\n(cross-site write check)", color=P, width=2, exit=(0.5, 1), entry=(0, 0.3), points=((395, 300), (860, 300), (860, 515)), fontcolor=P, label_pos=(0.3, -16))
    # desktop
    d.edge(desktop, authsvc, "POST /auth/token (TLS; X-Forwarded-For is the client's)", color=G, width=2, exit=(1, 0.5), entry=(0, 0.4), label_pos=(-0.1, 10))
    d.edge(desktop, guard, "Authorization: Bearer <session>", color=P, width=2, exit=(1, 0.9), entry=(0, 0.6), points=((230, 330), (230, 560), (880, 560)), fontcolor=P, label_pos=(0.35, -9))
    # direct psql
    d.edge(psql, pg, "connect as the person over the LAN: clear-text password to a server with no TLS (S-16)", color=R, width=3,
           exit=(1, 0.2), entry=(0.5, 0), points=((560, 386), (560, 110), (1035, 110)), fontcolor=R, label_pos=(0.2, -10))
    # mlflow
    d.edge(mlclient, mlproxy, "Basic (TLS)", color=G, width=2, exit=(1, 0.5), entry=(0, 0.5), label_pos=(0, -9))
    d.edge(mlproxy, authsvc, "auth_request: Basic -> sign_in (then 3, 4)", color=G, width=2, exit=(1, 0.5), entry=(0, 0.88), label_pos=(0, 10))
    # service token
    d.edge(token, guard, "Bearer / X-API-Key\nhmac.compare_digest", color=K, width=2, dashed=True, exit=(0, 0.5), entry=(1, 0.5), fontcolor=K, label_pos=(0, -16))
    d.text(40, 640, 1480, 70,
           "What is right: the browser's hop, the proxy's hop and the directory's hop are all TLS with the certificate verified; the session cannot be forged "
           "(fixed header, Ed25519, issuer and audience checked); a removed person is signed out within a minute. What is not: the one hop that carries the "
           "password as typed, auth service to Postgres, is plain TCP because the server has no certificate and sslmode=prefer accepts that silently -- and the "
           "same 'host ... ldap' rule answers a connection from anywhere on the network.", font=10, color="#555555")
    return d.render()


# ---------------------------------------------------------------------------
# 13. Trust boundaries at 6.0.1 (D-16, I-13, I-18, S-08, S-18, S-19)
# ---------------------------------------------------------------------------
def build_v6_1_trust_boundaries() -> str:
    """D-16, I-13, I-18, S-08, S-16, S-18, S-19: what each zone holds and what each credential is worth."""
    d = Diagram("v6_1_trust_boundaries", 1560, 620, **V6_1)
    title(d, "Trust boundaries as built at 6.0.1: what each zone holds, and what each credential is worth",
          "A session now crosses every hop and the person reaches SET LOCAL ROLE. The boundaries that did not move are drawn in red.")
    zones = [
        (40, 110, 230, 440, "Outside the stack",
         ["Browser: cookie nl2sql_session\n8 h, HttpOnly, SameSite=Strict\nno revocation (S-18)",
          "Desktop / scripts: bearer session\nor a static token (S-19)",
          "psql / BI tool: directory password\nin clear text to :5432 (S-16)",
          "Anyone on the LAN: :5432-:5438\ndefault passwords; superuser\npostgres / nl2sql (S-08)"]),
        (300, 110, 260, 440, "Pages (nginx, root)",
         ["Six images, one pattern\nproxy_ssl_verify on\nhold no token with sign-in on",
          "Mount apitls read-only:\nthe API's private key, needed\nonly for the certificate (I-13)",
          "SameSite=Strict cookie is sent\nto every port of this host:\ncross-port writes refused by\nSec-Fetch-Site / Origin check"]),
        (590, 110, 290, 440, "Services (Python, root)",
         ["API, console, review:\nverify the session with session.pub\nre-read roles <= 60 s\nSET LOCAL ROLE person",
          "Static token = Identity(service)\nevery role of the service, no name\nauthor = X-Reviewer header (S-19)",
          "Present the API's key as their own\nTLS identity; run as root to read it\n(auth/Dockerfile:18-19)",
          "Review: writes the checkout as root\nruns loaders as subprocesses"]),
        (910, 110, 290, 440, "Auth service (Python, root)",
         ["session.key: the one private key\nthat mints every session",
          "nl2sql_rolesync: CREATEROLE,\nADMIN on five roles and no more",
          "LDAP_SERVICE_PASSWORD: reads and,\nstandalone, writes every person",
          "Throttle in memory; address =\nlast X-Forwarded-For hop (S-20)",
          "/directory/v1 on every interface\nbehind an admin session (M-10)"]),
        (1230, 110, 300, 440, "Data (Postgres, OpenLDAP; model host)",
         ["retail Postgres: no TLS\nsuperuser postgres / nl2sql reachable\nfrom the LAN; catch-all hba rule last",
          "persons: LOGIN, SELECT on every table\n(no RLS), 60 s timeout, 5 connections;\ncurrent_user is the person, session_user\nthe reader (I-18)",
          "OpenLDAP: passwords, Argon2, lockout;\nStartTLS required; runs as ldap;\nown key; not published",
          "Model host (remote by default):\nsample rows and result rows (I-05)"]),
    ]
    for x, y, w, h, label, items in zones:
        d.frame(x, y, w, h, label)
        yy = y + 36
        for text in items:
            colour = "red" if "(S-" in text or "(I-13)" in text or "(M-10)" in text or "(I-18)" in text or "(I-05)" in text else "white"
            box_h = 18 + 15 * text.count("\n") + 20
            d.box(x + 12, yy, w - 24, box_h, text, color=colour, font=9, bold_first=False, valign="middle")
            yy += box_h + 10
    d.text(40, 562, 1480, 40,
           "Red: a boundary the first cycle drew that did not move (S-08), or a new one 6.0 introduced (S-16, S-18, S-19, S-20, M-10, I-18). "
           "White: what 6.0 built. The identity model is implied by every box here and written down in none of multi-agent_arch_specs/ (D-16).",
           font=10, color="#555555")
    return d.render()


# ---------------------------------------------------------------------------
# 14. One key, four identities, eleven containers (I-13, M-01, M-05, M-11, S-09)
# ---------------------------------------------------------------------------
def build_v6_1_key_sharing() -> str:
    """I-13, M-01, M-05, M-11, S-09: the apitls volume and why nothing but the directory drops root."""
    d = Diagram("v6_1_key_sharing", 1300, 640, **V6_1)
    title(d, "One private key, four TLS identities, eleven containers -- and why nothing drops root (I-13, M-11)",
          "docker-compose.yml:445 (rw) and ten read-only mounts; auth/Dockerfile:18-19; ldap/nl2sql_ldap/service.py:53-86 (become).")
    vol = d.box(500, 250, 300, 120, "volume apitls\nserver.key -- written 0600 by root, in the api container\nserver.crt -- the one certificate; API_TLS_HOSTNAMES names\nlocalhost, nl2sql-api, nl2sql-review, nl2sql-console, nl2sql-auth", "purple", font=10, stroke_width=2)
    api = d.box(40, 110, 200, 70, "api :8443\nwrites the key (rw); root", "red", font=10)
    presenters = [
        ("review :8444\npresents it; root", 300, 110),
        ("console :8445\npresents it; root", 560, 110),
        ("auth :8446\npresents it; root", 820, 110),
    ]
    pbox = [d.box(x, y, 200, 70, label, "red", font=10) for label, x, y in presenters]
    d.text(1040, 110, 230, 80,
           "\"Root, as the review service and the console are, for the same reason: it presents the agent API's certificate, whose key that API writes 0600.\" -- auth/Dockerfile:18-19",
           font=9, color="#7a2e2a")
    mounters = ["gui :8080", "reviewgui :8081", "curategui :8083", "consolegui :8082", "directorygui :8084", "mlflowproxy :5001", "apitest"]
    mboxes = []
    for i, label in enumerate(mounters):
        mboxes.append(d.box(40 + 178 * i, 450, 165, 54, f"{label}\nnginx, root; needs server.crt only", "yellow", font=9))
    P = "#9673a6"
    d.edge(api, vol, "rw", color=P, width=2, exit=(0.5, 1), entry=(0.1, 0), points=((140, 230), (530, 230)))
    for b in pbox:
        d.edge(b, vol, "ro; presented", color=P, width=2, dashed=True, exit=(0.5, 1), entry=(0.5, 0), label_pos=(-0.75, -9))
    for b in mboxes:
        d.edge(vol, b, "ro", color=P, dashed=True, exit=(0.5, 1), entry=(0.5, 0), label_pos=(0.8, -9))
    d.box(40, 540, 600, 70,
          "The dependency the first plan missed (M-11)\nV6-31 (non-root in every image) cannot land while four services must read a 0600 key written by another container's root. "
          "V6-36 (each service its own key, certificates only into proxies) comes first.", "white", font=10)
    d.box(680, 540, 580, 70,
          "The template: ldap/\nOwn key in ldaptls (tls.py), created 0600 by the user that reads it; the entry point takes its volumes as root and drops to the ldap user (become); "
          "the health check and docker exec drop the same way. The one image that does not run as root.", "green", font=10)
    return d.render()


# ---------------------------------------------------------------------------
# 15. The first cycle's findings by status (the changes document, the summary)
# ---------------------------------------------------------------------------
def build_v6_1_finding_status() -> str:
    """The 66 first-cycle findings by lens and status at 6.0.1, with the 19 new ones."""
    d = Diagram("v6_1_finding_status", 1100, 520, **V6_1)
    title(d, "The first cycle's 66 findings by status at 6.0.1, and the 19 new ones",
          "Resolved · Improved · Mitigated (code unchanged; sign-in by default narrows who can reach it) · Unchanged · Worse · New")
    rows = [("D  architecture as documented", (1, 1, 0, 8, 5, 2)), ("I  architecture as implemented", (1, 0, 1, 9, 6, 3)),
            ("C  code hygiene", (1, 0, 0, 6, 3, 3)), ("M  containers and microservices", (0, 1, 0, 6, 2, 3)),
            ("S  security controls", (5, 3, 2, 3, 2, 8)), ("All", (8, 5, 3, 32, 18, 19))]
    colours = [("#d5e8d4", "#000000"), ("#b9e0b4", "#000000"), ("#fff2cc", "#000000"),
               ("#f5f5f5", "#000000"), ("#f8cecc", "#000000"), ("#dae8fc", "#000000")]
    scale = 8
    for i, (label, counts) in enumerate(rows):
        y = 94 + 50 * i
        d.text(40, y + 6, 250, 30, label, font=11, bold=(label == "All"))
        x = 300
        for n, (fill, fc) in zip(counts, colours):
            if n == 0:
                continue
            w = n * scale
            d.box(x, y, w, 34, str(n), color=(fill, "#999999"), font=11, bold_first=False, rounded=False, fontcolor=fc)
            x += w
    lx = 300
    for (fill, _), name in zip(colours, ("Resolved", "Improved", "Mitigated", "Unchanged", "Worse", "New")):
        d.box(lx, 420, 16, 14, "", color=(fill, "#999999"), rounded=False)
        d.text(lx + 20, 416, 120, 20, name, font=10)
        lx += 125
    d.text(40, 456, 1020, 40,
           "Of the 17 Critical or High findings: 3 resolved (D-05, I-06, S-01), 1 mitigated (S-07), 10 unchanged -- both Criticals among them -- and 3 worse (D-01, D-03, I-12).",
           font=10, color="#555555")
    return d.render()


# ---------------------------------------------------------------------------
# 16. Severity matrix at 6.0.1
# ---------------------------------------------------------------------------
def build_v6_1_severity_matrix() -> str:
    """The second cycle's count of findings by lens and severity."""
    d = Diagram("v6_1_severity_matrix", 1040, 440, **V6_1)
    title(d, "Findings by area and severity (77 findings, five lenses; the first cycle had 66)",
          "D architecture as documented · I architecture as implemented · C code hygiene · M containers · S security controls. Resolved findings are not counted.")
    rows = [("D  architecture as documented (16)", (0, 6, 7, 3)), ("I  architecture as implemented (19)", (0, 5, 11, 3)),
            ("C  code hygiene (12)", (0, 0, 8, 4)), ("M  containers and microservices (12)", (1, 3, 6, 2)),
            ("S  security controls (18)", (1, 1, 8, 8)), ("All (77)", (2, 15, 40, 20))]
    colours = [("#b85450", "#ffffff"), ("#ea6b66", "#ffffff"), ("#ffe6cc", "#000000"), ("#d5e8d4", "#000000")]
    scale = 9
    for i, (label, counts) in enumerate(rows):
        y = 90 + 46 * i
        d.text(40, y + 6, 240, 30, label, font=11, bold=(label.startswith("All")))
        x = 290
        for n, (fill, fc) in zip(counts, colours):
            if n == 0:
                continue
            w = n * scale
            d.box(x, y, w, 32, str(n), color=(fill, "#ffffff"), font=11, bold_first=False, rounded=False, fontcolor=fc)
            x += w
    lx = 290
    for (fill, _), name in zip(colours, ("Critical", "High", "Medium", "Low")):
        d.box(lx, 380, 16, 14, "", color=(fill, "#999999"), rounded=False)
        d.text(lx + 20, 376, 120, 20, name, font=10)
        lx += 130
    d.text(810, 376, 220, 40, "First cycle: 2 / 15 / 35 / 14 = 66", font=10, color="#555555")
    return d.render()


# ---------------------------------------------------------------------------
# 17. Duplication matrix at 6.0.1 (C-01, I-09, I-14)
# ---------------------------------------------------------------------------
def build_v6_1_duplication_matrix() -> str:
    """C-01, I-09, I-14: the same helper defined in several packages, counted again."""
    cols = ["agent core", "agent/api", "agent/console", "review", "rag", "auth svc", "identity", "ldap",
            "gui", "review/gui", "curate", "console GUI", "directory GUI", "mlflow proxy", "desktop"]
    rows = [
        ("env helpers (_env, _env_bool, _env_int, …)", {"agent core": 1, "agent/api": 1, "review": 1, "rag": 1, "auth svc": 1, "identity": 1, "ldap": 1}),
        ("Embedder protocol", {"agent core": 3, "review": 1, "rag": 1}),
        ("build_embedder()", {"agent core": 2, "rag": 1}),
        ("pgvector literal (vector_literal)", {"agent core": 2, "review": 1, "rag": 3}),
        ("FALLBACK_CODES error map", {"agent/api": 1, "review": 1, "auth svc": 1}),
        ("Health / Readiness / ApiError models", {"agent/api": 1, "review": 1, "auth svc": 1}),
        ("_redacted()", {"review": 2, "auth svc": 1}),
        ("json_safe()", {"agent/console": 1, "review": 1}),
        ("token check (was ×3; now one Guard)", {"identity": 1}),
        ("plainText / Markup.plain", {"gui": 1, "review/gui": 1, "desktop": 1}),
        ("HTTP client + ApiError / ApiException", {"gui": 1, "review/gui": 1, "curate": 1, "console GUI": 1, "directory GUI": 1, "desktop": 1}),
        ("nginx image + 10-*.envsh (upstream_tls)", {"gui": 1, "review/gui": 1, "curate": 1, "console GUI": 1, "directory GUI": 1, "mlflow proxy": 1}),
        ("session.ts + SignInGate.tsx (476 lines, identical)", {"gui": 1, "review/gui": 1, "curate": 1, "console GUI": 1, "directory GUI": 1}),
    ]
    d = Diagram("v6_1_duplication_matrix", 1560, 700, **V6_1)
    title(d, "Copy-and-extend at 6.0.1: the same helper written in several packages (C-01, I-09, I-14)",
          "Cell = number of independent definitions in that package (non-test code, confirmed by search). 53 copies of 13 things, up from 41 of 12; "
          "the one shared package, nl2sql_identity, replaced the three token checks.")
    x0, y0, rowh, colw = 40, 124, 34, 74
    for j, c in enumerate(cols):
        d.text(x0 + 330 + colw * j, 84, colw, 38, c, font=9, bold=True, align="center")
    d.text(x0 + 330 + colw * len(cols), 84, 70, 38, "copies", font=9, bold=True, align="center")
    shade = {1: ("#fff2cc", "#000000"), 2: ("#ffe6cc", "#000000"), 3: ("#f8cecc", "#000000")}
    for i, (name, cells) in enumerate(rows):
        y = y0 + rowh * i
        d.text(x0, y + 7, 320, rowh, name, font=9)
        total = 0
        for j, c in enumerate(cols):
            n = cells.get(c, 0)
            total += n
            if n:
                fill, fc = shade[min(n, 3)]
                colour = ("#d5e8d4", "#82b366") if name.startswith("token check") else (fill, "#cccccc")
                d.box(x0 + 330 + colw * j, y, colw - 2, rowh - 4, str(n), color=colour, font=10, bold_first=False, rounded=False, fontcolor=fc)
            else:
                d.box(x0 + 330 + colw * j, y, colw - 2, rowh - 4, "", color=("#ffffff", "#eeeeee"), rounded=False)
        d.box(x0 + 330 + colw * len(cols), y, 68, rowh - 4, str(total), color=("#f5f5f5", "#cccccc"), font=10, bold_first=False, rounded=False)
    d.text(40, 586, 1480, 70,
           "Green: the one helper that became shared. The first cycle asked for a shared Python package (V6-20) and a shared front-end package (V6-25) before the next "
           "feature; the next feature added three Python packages with their own env helpers and five web interfaces with identical sign-in code. "
           "A defect in the sign-in gate must be fixed in five places and the vitest suites give no signal when one is missed.", font=10, color="#555555")
    return d.render()


# ---------------------------------------------------------------------------
# 18. The second plan's phases and the re-ordering (V6-36 before V6-31)
# ---------------------------------------------------------------------------
def build_v6_1_plan_dependencies() -> str:
    """The v6.1 plan: phases, what is done, and the dependency the first plan missed."""
    d = Diagram("v6_1_plan_dependencies", 1560, 760, **V6_1)
    title(d, "The v6.1 plan: phases, status of the first plan's items, and the dependency it missed",
          "Green: done in 6.0 (V6-01, V6-10, V6-21, V6-30; V6-09 superseded). Orange: partial. Black: open. Blue: new (V6-52 to V6-71). "
          "The red edge is V6-36 -> V6-31, which moves separate TLS identities to Phase 1.")
    # Each phase is one box whose lines are coloured by status, so the box is
    # HTML built here rather than a plain label: `raw=True`.
    STATUS_COLOUR = {"done": "#2e7d32", "partial": "#b26a00", "open": "#000000", "new": "#1f5fbf"}

    def phase(x, y, w, h, heading, items, font=10):
        lines = [f"<b>{html.escape(heading, quote=False)}</b>"]
        for text, status in items:
            lines.append(f'<font color="{STATUS_COLOUR[status]}">{html.escape(text, quote=False)}</font>')
        return d.box(x, y, w, h, "<br>".join(lines), "white", font=font, valign="top", raw=True)

    p0 = phase(40, 120, 220, 170, "Phase 0 -- decisions", [
        ("V6-01 identity", "done"), ("V6-02 sensitive policy", "open"), ("V6-03 topology", "open"), ("V6-04 review writes", "open"),
        ("V6-05 benchmark wording", "open"), ("V6-70 direct DB access", "new"), ("V6-71 revocation model", "new")])
    p1 = phase(300, 120, 260, 300, "Phase 1 -- safe defaults, transport, secrets", [
        ("V6-06 stores on loopback", "open"), ("V6-07 retail v1_2, no baked superuser", "open"), ("V6-52 TLS for the retail database", "new"),
        ("V6-53 hostssl + verify-full", "new"), ("V6-08 generated secrets", "partial"), ("V6-54 secure default in the code", "new"),
        ("V6-36 own TLS identities (moved here)", "open"), ("V6-10 constant-time", "done"), ("V6-11 log scrub", "partial"), ("V6-12 CORS none", "partial"),
        ("V6-13 queue bound   V6-14 EXPLAIN timeout", "open"), ("V6-55..58 argv, X-Forwarded-Proto,", "new"), ("  replica TLS, directory API", "new"),
        ("V6-59 red test   V6-60 default-deny test", "new")], font=9)
    p2 = phase(600, 120, 220, 140, "Phase 2 -- pipeline", [
        ("V6-15 per-attempt reset", "open"), ("V6-16 node_errors", "open"), ("V6-17 sensitive policy", "open"),
        ("V6-18 trace fields over REST", "open"), ("V6-19 extra=forbid", "open")])
    p3 = phase(600, 290, 220, 150, "Phase 3 -- foundations", [
        ("V6-20 shared package", "partial"), ("V6-66 installed as a package", "new"), ("V6-21 Principal", "done"), ("V6-22 engine accessor", "open"),
        ("V6-23 error taxonomy", "open"), ("V6-24 pins + hashes", "open"), ("V6-25 web package + sign-in gate", "open")])
    p4 = phase(860, 120, 240, 320, "Phase 4 -- service refactors", [
        ("V6-26 routers, default-deny", "open"), ("V6-27 review writes via library", "open"), ("V6-28 non-root review", "open"), ("V6-29 sequences", "open"),
        ("V6-30 identity applied", "done"), ("V6-31 non-root images (1 of 15)", "partial"), ("  <- waits for V6-36", "partial"),
        ("V6-32 operator-only detail", "open"), ("V6-33 cache reload (admins exist)", "open"), ("V6-61 session revocation", "new"),
        ("V6-62 named service tokens", "new"), ("V6-63 trusted proxies", "new"), ("V6-64 application_name", "new")], font=9)
    p5 = phase(1140, 120, 200, 200, "Phase 5 -- hardening, topology", [
        ("V6-34 compose hardening", "open"), ("V6-35 digests, one Alpine", "open"), ("V6-65 no start-up dependency", "new"),
        ("  on the API's certificate", "new"), ("V6-37 one proxy image", "open"), ("V6-38 compose secrets", "partial"),
        ("V6-39 role limits", "partial"), ("V6-40 consolidation", "open"), ("V6-41 ops in Python", "open"), ("V6-42 provenance", "open")], font=9)
    p6 = phase(1140, 350, 200, 170, "Phase 6 -- documentation", [
        ("V6-43 arch6 (+ sign-in)", "open"), ("V6-68 sign-in design", "new"), ("V6-44 verified blueprint", "open"), ("V6-45 threat model, tiers", "open"),
        ("V6-46 overstated controls", "partial"), ("V6-69 cost of direct access", "new"), ("V6-47 one home per fact", "open"),
        ("V6-48 comment policy", "open")], font=9)
    p7 = phase(1370, 120, 170, 170, "Phase 7 -- tests", [
        ("V6-49 security tier", "open"), ("V6-67 acceptance tier", "new"), ("  -- land first", "new"), ("V6-50 drift tests", "open"),
        ("V6-51 coverage exclusions", "open")])
    # status swatches
    for status, text, x in (("done", "done in 6.0", 40), ("partial", "partial", 170), ("open", "open", 290), ("new", "new this cycle", 400)):
        d.box(x, 560, 16, 14, "", color=(STATUS_COLOUR[status], STATUS_COLOUR[status]), rounded=False)
        d.text(x + 20, 556, 120, 20, text, font=10)
    # highlighted edges
    d.edge(p0, p1, "decisions", style="orth", exit=(1, 0.3), entry=(0, 0.2))
    d.edge(p1, p2, "", style="orth", exit=(1, 0.2), entry=(0, 0.5))
    d.edge(p2, p3, "", style="orth", exit=(0.5, 1), entry=(0.5, 0))
    d.edge(p3, p4, "", style="orth", exit=(1, 0.5), entry=(0, 0.5))
    d.edge(p4, p5, "", style="orth", exit=(1, 0.3), entry=(0, 0.5))
    d.edge(p5, p6, "", style="orth", exit=(0.5, 1), entry=(0.5, 0))
    d.edge(p6, p7, "", style="orth", exit=(1, 0.3), entry=(0.5, 1), points=((1455, 435),))
    d.edge(p1, p4, "V6-36 (own TLS identities) -> V6-31 (non-root): the dependency the first plan missed", color="#b85450", width=3,
           exit=(0.5, 1), entry=(0.4, 1), points=((430, 480), (956, 480)), fontcolor="#b85450", label_pos=(0, 12))
    d.text(40, 600, 1480, 90,
           "The first plan's release shape put Phase 1 and Phase 2 in 6.0.0 and identity in 6.1. What shipped as 6.0.0 was identity (V6-01, V6-21, V6-30), done larger than "
           "recommended, with Phases 1 and 2 untouched: the Critical (V6-07) and ten Highs shipped unchanged under the major version. 6.1.0 should be Phase 1 and Phase 2, "
           "which now also carry TLS for the retail database (V6-52, V6-53) and the secure default in the code (V6-54); the acceptance tier (V6-67) should be in place before it is published.",
           font=10, color="#555555")
    return d.render()


# ---------------------------------------------------------------------------
# 19. Spec lineage against releases, second cycle (D-01, D-02, D-03, D-16)
# ---------------------------------------------------------------------------
def build_v6_1_spec_lineage() -> str:
    """D-01, D-16: the specs' dates against the releases', with the two review cycles marked."""
    d = Diagram("v6_1_spec_lineage", 1500, 540, **V6_1)
    title(d, "Architecture specs against releases, second cycle (D-01, D-16)",
          "multi-agent_arch_specs/ has no file newer than 2026-09-28. Seven releases since, two of them after the first review, one of them a major.")
    d.text(40, 96, 200, 24, "Specs (design authority)", font=11, bold=True)
    specs = [("arch4", "09-25", 60), ("arch5", "09-25", 200), ("arch5.1", "09-26", 340), ("arch5.2\n\"Designed, not built\"\n\"connects as the owner\"", "09-27", 480)]
    for label, date, x in specs:
        d.box(x, 124, 130, 70, f"{label}\n{date}", "blue", font=10)
    d.box(640, 124, 820, 70, "no spec\nthe design record for 5.3-5.6 is READMEs; for 6.0 it is auth/README.md, ldap/README.md, README.md Sign-in and compose comments", ("#f8cecc", "#b85450"), font=10, dashed=True)
    d.text(40, 230, 200, 24, "Releases (what shipped)", font=11, bold=True)
    releases = [("5.2 routing", "09-28", 480), ("5.3 console", "09-29", 610), ("5.4 reopen/undo", "10-01", 740), ("5.5 MLflow", "10-01", 870),
                ("5.6 snippets\n5.6.1", "10-03", 1000), ("6.0 sign-in\n(major)", "10-03/04", 1130), ("6.0.1\ncorrection", "10-04", 1260)]
    boxes = {}
    for label, date, x in releases:
        colour = "red" if label.startswith("6.0") else "white"
        boxes[label] = d.box(x, 258, 115, 70, f"{label}\n{date}", colour, font=10)
    d.text(40, 370, 200, 24, "Review cycles", font=11, bold=True)
    d.box(1000, 400, 115, 50, "v6_x_review\n10-03, at 5.6.1", "purple", font=10)
    d.box(1260, 400, 115, 50, "v6_1_review\n10-04, at 6.0.1", "purple", font=10)
    d.edge(boxes["5.6 snippets\n5.6.1"], boxes["6.0 sign-in\n(major)"], "the first review sat between these two", color="#9673a6", dashed=True,
           exit=(0.5, 1), entry=(0.5, 1), points=((1057, 350), (1187, 350)), fontcolor="#9673a6", label_pos=(0, 12))
    d.text(40, 470, 1420, 50,
           "What 6.0 changed without a design document: who may call what (four groups), where a password is checked (Postgres through pg_hba), what a session is "
           "(an Ed25519 JWT, 8 h), who holds which key (one API certificate for four services; the auth service's signing key), what runs as whom (SET LOCAL ROLE), "
           "and a directory that may be a replica of another organisation's. arch5.2 says the agent connects as the owner.", font=10, color="#555555")
    return d.render()


#: What `main` writes, in the order the reviews introduce them: the first
#: cycle's ten, then the second cycle's nine.
DIAGRAMS = (
    ("v6_x_review_deployment_topology.drawio", "build_deployment_topology"),
    ("v6_x_review_repair_loop_state.drawio", "build_repair_loop_state"),
    ("v6_x_review_trace_field_loss.drawio", "build_trace_field_loss"),
    ("v6_x_review_row_data_flow.drawio", "build_row_data_flow"),
    ("v6_x_review_spec_lineage.drawio", "build_spec_lineage"),
    ("v6_x_review_trust_boundaries.drawio", "build_trust_boundaries"),
    ("v6_x_review_secrets_flow.drawio", "build_secrets_flow"),
    ("v6_x_review_plan_dependencies.drawio", "build_plan_dependencies"),
    ("v6_x_review_severity_matrix.drawio", "build_severity_matrix"),
    ("v6_x_review_duplication_matrix.drawio", "build_duplication_matrix"),
    ("v6_1_review_deployment_topology.drawio", "build_v6_1_deployment_topology"),
    ("v6_1_review_signin_flow.drawio", "build_v6_1_signin_flow"),
    ("v6_1_review_trust_boundaries.drawio", "build_v6_1_trust_boundaries"),
    ("v6_1_review_key_sharing.drawio", "build_v6_1_key_sharing"),
    ("v6_1_review_finding_status.drawio", "build_v6_1_finding_status"),
    ("v6_1_review_severity_matrix.drawio", "build_v6_1_severity_matrix"),
    ("v6_1_review_duplication_matrix.drawio", "build_v6_1_duplication_matrix"),
    ("v6_1_review_plan_dependencies.drawio", "build_v6_1_plan_dependencies"),
    ("v6_1_review_spec_lineage.drawio", "build_v6_1_spec_lineage"),
)


def main(output_dir: Path | None = None) -> None:
    """Write every diagram beside this file, or into `output_dir`.

    The argument exists so a test can write somewhere disposable: without
    it, exercising this function means writing into the working tree, and a
    test that edits the files another test checks is a test that can hide a
    failure.
    """
    here = output_dir or Path(__file__).resolve().parent
    here.mkdir(parents=True, exist_ok=True)
    for name, builder in DIAGRAMS:
        xml = globals()[builder]()
        (here / name).write_text(xml)
        print(f"{name}: {len(xml) / 1024:.1f} KB")


if __name__ == "__main__":
    # An optional destination, so the diagrams can be written somewhere
    # other than the working tree -- which is what lets the tests run this
    # entry point without editing the files another test checks.
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
