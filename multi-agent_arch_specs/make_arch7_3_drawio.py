#!/usr/bin/env python3
"""Emit Multi-Agent_NL2SQL_arch7_3.drawio next to this script.

    python multi-agent_arch_specs/make_arch7_3_drawio.py

arch7.3 is arch7.2 with the owner's decisions on the grain check: it ships
in 7.0.0, on by default, its grains measured from the data, and its rule
catches tables of different grains joined on no date as well as on the
date. arch7.2 is arch7.1 with its Phase 3 as built -- the second wave when the
vote is unsettled, fusion's claims about rows not yet spoken of, a dissent
that reads each query's grain -- and with the grain check, a phase of its
own (Phase 6), marked in the candidate runs' gates. arch7.1 is arch7 with
the Judge before the vote. A Stage 0 that screens the
original, rewords it and checks each rewording's fidelity; the arch6
pipeline run once per wording -- one after another by default, more at once
when the deployer says the host serves more -- drawn here as the box it is
in that design (arch6's own diagram is the drawing of what is inside each
box); and a Stage 6 that decides which results are admissible and which
agree, has the Judge read every distinct answer, lets only the accepted
vote, and delivers. The routing band notes the two tasks arch7 adds.
Everything is arch7.3's section 22, drawn by the same code and in the same
styles as the arch7.2 generator this one was copied from, so the two open
side by side in draw.io and the change shows in the grain check's note.

Render it with draw.io, or without a desktop app, from a folder holding only
this diagram -- the exporter renders every .drawio in the folder it is given,
and a published spec's drawing is a fixed point:

    mkdir -p /tmp/arch7_3 && cp multi-agent_arch_specs/Multi-Agent_NL2SQL_arch7_3.drawio /tmp/arch7_3/
    docker run --rm -v /tmp/arch7_3:/data rlespinasse/drawio-export -f png -s 2
    cp /tmp/arch7_3/export/Multi-Agent_NL2SQL_arch7_3-Multi-Agent-NL2SQL-v7.3.png \
       multi-agent_arch_specs/Multi-Agent_NL2SQL_arch7_3.png

(the exporter appends the page name to the file it writes).

Coordinates are computed here rather than typed, so a box can be resized in
one place.
"""
from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

OUT = str(Path(__file__).resolve().parent / "Multi-Agent_NL2SQL_arch7_3.drawio")

# --- styles (arch6's) -------------------------------------------------------------
BOX = ("rounded=0;whiteSpace=wrap;html=1;align=left;verticalAlign=top;"
       "spacingLeft=5;spacingTop=0;spacingRight=5;spacingBottom=0;")
LLM = BOX + "fillColor=#fff2cc;strokeColor=#d6b656;"
DET = BOX + "fillColor=#dae8fc;strokeColor=#6c8ebf;"
JOIN = BOX + "fillColor=#e1d5e7;strokeColor=#9673a6;"
STORE = BOX + "fillColor=#f5f5f5;strokeColor=#666666;fontColor=#333333;"
PIPE = BOX + "fillColor=#fff2cc;strokeColor=#d6b656;dashed=0;"
PIPE_LATER = BOX + "fillColor=#ffffff;strokeColor=#d6b656;dashed=1;fontColor=#666666;"
DECISION = "strokeWidth=2;html=1;shape=mxgraph.flowchart.decision;whiteSpace=wrap;fillColor=#ffffff;"
TERM = ("strokeWidth=2;html=1;shape=mxgraph.flowchart.terminator;whiteSpace=wrap;"
        "fontStyle=1;fontSize=13;")
TERM_BAD = TERM + "fillColor=#f8cecc;strokeColor=#b85450;"
TERM_OK = TERM + "fillColor=#d5e8d4;strokeColor=#82b366;"
LANE = ("swimlane;html=1;startSize=30;align=left;spacingLeft=10;fontStyle=1;fontSize=13;"
        "strokeColor=#666666;fillColor=#ffffff;swimlaneFillColor=#ffffff;")
EDGE = "edgeStyle=orthogonalEdgeStyle;curved=1;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;"
EDGE_BACK = EDGE + "dashed=1;strokeColor=#b85450;fontColor=#b85450;fontStyle=1;"
EDGE_FAN = EDGE + "strokeColor=#9673a6;fontColor=#333333;"
TEXT = ("text;html=1;align=left;verticalAlign=middle;resizable=0;points=[];autosize=1;"
        "strokeColor=none;fillColor=none;fontSize=11;fontColor=#666666;")

cells: list[str] = []
_n = 0


def nid(prefix="c") -> str:
    global _n
    _n += 1
    return f"{prefix}{_n}"


def attr(s: str) -> str:
    return escape(s, {'"': "&quot;"})


def title(name: str, sub: str = "") -> str:
    h = f'<b><font style="font-size: 14px;"><u>{name}</u></font></b>'
    if sub:
        h += f' <font style="font-size: 11px;" color="#666666">{sub}</font>'
    return h


def bullets(*lines: str) -> str:
    return "".join(f"<br>- {escape(x)}" for x in lines)


def vertex(parent, x, y, w, h, value, style) -> str:
    i = nid()
    cells.append(
        f'<mxCell id="{i}" value="{attr(value)}" style="{style}" vertex="1" parent="{parent}">'
        f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'
    )
    return i


def edge(src, dst, value="", style=EDGE, points=(), exit=None, entry=None, label_at=None) -> str:
    i = nid("e")
    st = style
    if exit:
        st += f"exitX={exit[0]};exitY={exit[1]};exitDx=0;exitDy=0;"
    if entry:
        st += f"entryX={entry[0]};entryY={entry[1]};entryDx=0;entryDy=0;"
    pts = ""
    if points:
        pts = '<Array as="points">' + "".join(f'<mxPoint x="{x}" y="{y}"/>' for x, y in points) + "</Array>"
    cells.append(
        f'<mxCell id="{i}" value="{attr(value)}" style="{st}" edge="1" parent="1" source="{src}" target="{dst}">'
        f'<mxGeometry relative="1"{"" if label_at is None else f" x=\"{label_at}\""} as="geometry">{pts}</mxGeometry></mxCell>'
    )
    return i


# --- canvas plan -------------------------------------------------------------------
LX, LW = 40, 1160          # swimlane x and width
CX = LX + LW // 2          # centre line (620)
RAIL_WAVE = LX + LW + 40   # x of the second-wave loop-back rail

# --- title -------------------------------------------------------------------------
vertex("1", LX, 14, LW, 44,
       '<font style="font-size: 18px;"><b>Multi-Agent NL2SQL, v7.3</b></font>'
       '<font color="#666666"> &#8212; the question asked several ways: arch6 run once per wording, one after another '
       'by default (the host serves one call at a time; a deployer may allow more); the results checked and grouped, every distinct '
       'answer read by the Judge before the vote, the accepted voted on, one chosen or fused; arch7.1 with its Phase 3 as built '
       'and the grain check (Phase 6: in 7.0.0, on by default), 2026-10-09; supersedes arch7.2, arch7.1, arch7 and arch6.3 where they disagree</font>',
       "text;html=1;align=left;verticalAlign=middle;whiteSpace=wrap;strokeColor=none;fillColor=none;")

# --- legend ------------------------------------------------------------------------
leg_y = 62
vertex("1", LX, leg_y, 60, 22, "LEGEND", TEXT + "fontStyle=1;letterSpacing=1;")
legend = [("calls the chat model (LLM)", LLM), ("deterministic code", DET), ("join / fan-out plan", JOIN),
          ("the arch6 pipeline, one run", PIPE), ("stops the run", TERM_BAD + "fontStyle=0;fontSize=11;")]
lx = LX + 70
for text, st in legend:
    w = 190 if "arch6" in text else 170
    vertex("1", lx, leg_y, w, 22, f'<font style="font-size: 11px;">{escape(text)}</font>',
           st.replace("verticalAlign=top", "verticalAlign=middle").replace("align=left", "align=center"))
    lx += w + 14

# --- model routing band (v5.2; two tasks more in v7) -------------------------------------
rt_y = leg_y + 44
rt_h = 206
RT = vertex("1", LX, rt_y, LW, rt_h,
            "MODEL ROUTING (v5.2)  (not a stage: asked by every LLM agent, per call; arch7 adds two tasks and bumps the catalog to schema 3)",
            LANE)
rw, rgap = 360, 30
rx0 = (LW - (3 * rw + 2 * rgap)) // 2
CAT = vertex(RT, rx0, 44, rw, 146,
             title("Model catalog", "models/catalog.json, schema 3") + bullets(
                 "seven tasks: supervisor, generator, reflection,",
                 "narrator, repair + paraphraser, judge (v7)",
                 "two probes more: fidelity of rewordings against",
                 "the reference's reading; verdicts that match the reference",
                 "a schema-2 catalog loads with the two unmeasured"),
             STORE)
RTR = vertex(RT, rx0 + rw + rgap, 44, rw, 146,
             title("Model Router", "no LLM") + bullets(
                 "route(task, rung) -> model, num_ctx, think, fallback, reason",
                 "paraphraser: light; standard when the pre-screen flags",
                 "judge: heavy, always -- once a wave, decides which answers count",
                 "unmeasured task -> OLLAMA_MODEL (routes on measured only)",
                 "every call to the host takes a slot: OLLAMA_PARALLEL_CALLS (1 = serial, default)"),
             DET)
LAD = vertex(RT, rx0 + 2 * (rw + rgap), 44, rw, 146,
             title("The ladder", "light -> standard -> heavy") + bullets(
                 "inside each candidate run, as arch6: every repair climbs",
                 "each candidate's generator is scored by its own aggregator",
                 "so two wordings may run at two rungs (their diversity)",
                 "MODEL_ROUTE_PARAPHRASER, MODEL_ROUTE_JUDGE pin a rung",
                 "a routed model that fails -> its fallback -> OLLAMA_MODEL"),
             DET)
edge(CAT, RTR, exit=(1, 0.5), entry=(0, 0.5))
edge(RTR, LAD, exit=(1, 0.5), entry=(0, 0.5))

# --- input -------------------------------------------------------------------------
in_y = rt_y + rt_h + 40
Q = vertex("1", CX - 150, in_y, 300, 40, "Natural-language question", TERM)

# --- stage 0 -----------------------------------------------------------------------
s0_y = in_y + 70
s0_h = 560
S0 = vertex("1", LX, s0_y, LW, s0_h,
            "STAGE 0 &#8212; ASKING IT SEVERAL WAYS  (arch7.3, section 22.3: the anchor, the rewordings, the fidelity gate, the wave)",
            LANE)
SCR = vertex(S0, CX - LX - 200, 50, 400, 110,
             title("screen", "the Supervisor on the original, LLM") + bullets(
                 "the anchor: verdict, intent, the answer contract",
                 "every rewording is held to this contract",
                 "nothing is reworded before this call: an injection rephrased",
                 "is still an attempt, further from the words the screen saw",
                 "model: light; standard when the pre-screen flags (v5.2)"),
             LLM)
STOP = vertex(S0, LW - 20 - 300, 68, 300, 70,
              "Refuse (out of domain, injected)<br>or ask to clarify (ambiguous)<br>&#8212; nothing is reworded",
              TERM_BAD + "fontSize=11;")
edge(SCR, STOP, "verdict &#8800; proceed", exit=(1, 0.5), entry=(0, 0.5))

PAR = vertex(S0, CX - LX - 250, 190, 500, 110,
             title("paraphrase", "the Paraphraser, LLM, one structured call") + bullets(
                 "the question and what may not change, in plain words; no rows or column names",
                 "keeps every number, year, name, quoted value, the entities, the measure,",
                 "the period, the direction and the row count; varies words and sentence shape",
                 "writes up to ENSEMBLE_MAX_PARAPHRASES (10), most different first, each with 'what changed'",
                 "asked once more for replacements if too few pass the gate; never a third time"),
             LLM)
edge(SCR, PAR, "proceed", exit=(0.5, 1), entry=(0.5, 0))

fid_y, fid_h, fid_w, fid_gap = 330, 118, 208, 21
checks = [
    ("F1 numbers", DET, ["the multiset of numerals equals",
                         "the original's; number words",
                         "one to twenty count as numerals",
                         "'top ten' -> 'top five' fails"]),
    ("F2 literals", DET, ["every quoted string and every",
                          "capitalised multi-word phrase",
                          "appears verbatim (case-insensitive)",
                          "'Dairy & Eggs' -> 'dairy' fails"]),
    ("F3 polarity", DET, ["the direction classes present are",
                          "equal: superlative high / low,",
                          "change up / down, negation",
                          "'lowest' -> 'highest' fails"]),
    ("F4 the same contract", LLM, ["the Supervisor reads the rewording;",
                                   "its contract, in the original's shape,",
                                   "must equal the anchor's: entities,",
                                   "measure, period; proceed"]),
    ("F5 distinct", DET, ["differs from the original and from",
                          "each one before it that passed;",
                          "token Jaccard <= 0.8 to each",
                          "two that differ by a comma: one kept"]),
]
fid_ids = []
for i, (name, style, lines) in enumerate(checks):
    x = 16 + i * (fid_w + fid_gap)
    fid_ids.append(vertex(S0, x, fid_y, fid_w, fid_h, title(name) + bullets(*lines), style))
for f in fid_ids:
    edge(PAR, f, exit=(0.5, 1), entry=(0.5, 0), style=EDGE_FAN)
vertex(S0, 16, fid_y - 22, 600, 18,
       "screen_paraphrase &#8212; one rewording after another by default (OLLAMA_PARALLEL_CALLS=1); a rewording that fails any check is discarded with the check's name and never run",
       TEXT)

WAVE = vertex(S0, CX - LX - 270, 470, 540, 78,
              title("plan_wave", "join, no LLM") + bullets(
                  "wave 1: the original + the first ENSEMBLE_PARAPHRASES (3) faithful rewordings -> 4 runs",
                  "wave 2: the rest, up to 10 in all -- only when the vote is unsettled, while waves < ENSEMBLE_WAVES (2)",
                  "and, when a deadline is set (ENSEMBLE_DEADLINE_SECONDS; none by default), before it; a wave in flight is always awaited"),
              JOIN)
for f in fid_ids:
    edge(f, WAVE, exit=(0.5, 1), entry=(0.5, 0), style=EDGE_FAN)

# --- the candidate runs ---------------------------------------------------------------
cr_y = s0_y + s0_h + 40
cr_h = 400
CR = vertex("1", LX, cr_y, LW, cr_h,
            "THE CANDIDATE RUNS &#8212; answer: the arch6 pipeline once per wording, one after another by default  (section 22.4; "
            "OLLAMA_PARALLEL_CALLS, 1 unless the deployer's host serves more)",
            LANE)
pw, pgap = 208, 21
run_lines = [
    "Stage 1 on this wording:",
    "five retrievers, its own exemplars,",
    "snippets and literals; the",
    "Aggregator scores its rung",
    "Stage 2: SQL Generator",
    "Stage 3: AST, grain* -> EXPLAIN -> run",
    "-> Completeness Reviewer;",
    "Repair Agent, budget 7, the ladder",
    "Stage 4: chart -> Narrator -> Audit",
    "supervise: accepts the screening",
    "done for it (no model call)",
]
runs = [
    ("Candidate 0", "the original", PIPE),
    ("Candidate 1", "rewording 1", PIPE),
    ("Candidate 2", "rewording 2", PIPE),
    ("Candidate 3", "rewording 3", PIPE),
    ("Candidates 4..10", "a second wave, on disagreement", PIPE_LATER),
]
run_ids = []
for i, (name, sub, style) in enumerate(runs):
    x = 16 + i * (pw + pgap)
    lines = run_lines if style is PIPE else [
        "up to seven more rewordings, the",
        "next most different first,",
        "each the same whole pipeline",
        "",
        "run only when wave 1's vote is",
        "unsettled (no majority, or one run),",
        "a wave is left and time is left",
        "",
        "the Judge then reads every group",
        "again, and the vote is recomputed",
        "over every candidate",
    ]
    run_ids.append(vertex(CR, x, 50, pw, 262, title(name, sub) + bullets(*[l for l in lines if l]), style))
for r in run_ids[:4]:
    edge(WAVE, r, exit=(0.5, 1), entry=(0.5, 0), style=EDGE_FAN)
edge(WAVE, run_ids[4], "wave 2", exit=(0.9, 1), entry=(0.5, 0), style=EDGE_FAN + "dashed=1;",
     points=((LX + CX - LX - 270 + 486, cr_y + 40), (LX + 16 + 4 * (pw + pgap) + pw // 2, cr_y + 40)), label_at=0.3)
vertex(CR, 16, 330, LW - 32, 18,
       "Each run keeps its whole state: sql, rows, completeness, audit, attempt history and trace. "
       "Progress events and trace spans carry the candidate's index [k]; MLflow shows one trace per question with a run per wording inside it.",
       TEXT)
vertex(CR, 16, 352, LW - 32, 18,
       "* grain: the grain check (Phase 6, section 22.14; on by default) -- tables of different grains joined on their dates, the finer "
       "not rolled up, or on no date, the coarser still by period, go to the Repair Agent.",
       TEXT)

# --- stage 6 -----------------------------------------------------------------------
s6_y = cr_y + cr_h + 40
s6_h = 910
S6 = vertex("1", LX, s6_y, LW, s6_h,
            "STAGE 6 &#8212; VALIDATION, THE JUDGE, THE VOTE AND FUSION  (sections 22.5-22.8: the groups in code; "
            "the Judge on every question, before the vote; the vote in code, over the accepted)",
            LANE)
GX, GW = 70, 500           # left column (relative)
gcx = GX + GW // 2
ADM = vertex(S6, GX, 50, GW, 150,
             title("validate, tier 1: admissibility", "no LLM, per candidate") + bullets(
                 "E1 answered: proceed, no error (a give-up does not vote)",
                 "E2 faithful: its wording passed the gate (the original trivially)",
                 "E3 complete enough: the anchor contract's R1-R4 find no fatal gap -- rows at all",
                 "E4 audited: no semantic_issue outstanding (the audit found the rows right)",
                 "E5 comparable: not truncated, unless the question is ranked",
                 "a gap or a dropped claim ranks a candidate lower; only the fatal cases exclude it"),
             DET)
AGR = vertex(S6, GX, 230, GW, 140,
             title("validate, tier 2: agreement", "no LLM, every pair") + bullets(
                 "result_matches(a, b, ordered) or (b, a): the benchmark's own scorer --",
                 "columns as presentation, extra columns allowed, rows the same under some",
                 "reading of the columns, numbers within 1e-6 or at two decimals",
                 "groups = connected components (union-find), largest first, ties to the original's",
                 "signature per group: a scalar's value, or row count + first row's labels and measure"),
             DET)
JUDGE = vertex(S6, GX, 400, GW, 160,
               title("judge", "LLM, every question, before the vote") + bullets(
                   "the question, what every answer was held to, the knowledge the original's run had,",
                   "and per group, by letter, the original's first: its SQL, columns, first five rows --",
                   "never how many runs gave it",
                   "-> a verdict per letter: accepted, or set aside with the mistake in its query named",
                   "a letter that names no answer is ignored; an answer with no verdict stands",
                   "cannot write SQL, call an agent or start a run (W3); once a wave",
                   "model: heavy, always; if it fails, the runs vote alone"),
               LLM)
VOTE = vertex(S6, GX, 590, GW, 150,
              title("vote", "no LLM, the accepted only") + bullets(
                  "A = the admissible less those set aside; g = the largest accepted group",
                  "unanimous (g = A), majority (g > A/2 and g >= 2), single (A = 1)",
                  "judged: the winner is not the runs' own choice -- the Judge set that aside",
                  "accepted none: the runs' own choice, 'contested', with the Judge's objection",
                  "none admissible: none -- the original's give-up is delivered",
                  "the Judge off, or nothing to judge: the runs vote alone"),
              DET)
D2 = vertex(S6, gcx - 70, 778, 140, 104, "no majority:<br>a wave left,<br>time left?", DECISION)

RX, RW = 650, 450          # right column (relative)
rcx = RX + RW // 2
FUSE = vertex(S6, RX, 400, RW, 204,
              title("fuse", "no LLM") + bullets(
                  "select the representative: complete > audited > the original >",
                  "fewer attempts > cheaper plan > lower index; its SQL, rows, chart",
                  "no SQL is ever fused (the generator is the only place SQL is written)",
                  "columns other members carried, joined on the entity's key when every",
                  "row matches once (ENSEMBLE_FUSE_COLUMNS: a toggle, on by default)",
                  "claims: the others' about rows not yet spoken of, checked cell by cell,",
                  "re-audited; at most ENSEMBLE_MAX_CLAIMS (8); assumptions once",
                  "dissent: each loser's key fact + its tables, filters, roll-ups and joins"),
              DET)
DEL = vertex(S6, RX, 772, RW, 118,
             title("deliver", "no LLM") + bullets(
                 "render_answer for the original question: the fused claims, the table,",
                 "the agreement line first ('agreed by 4 of 4 runs'; what the Judge set aside),",
                 "the dissent among the notes; folded beneath: every candidate's wording, what",
                 "it changed, outcome, SQL, key fact, the Judge's verdict on it; the discarded",
                 "rewordings with the check that discarded them"),
             DET)

for r in run_ids[:4]:
    edge(r, ADM, exit=(0.5, 1), entry=(0.5, 0), style=EDGE_FAN)
edge(run_ids[4], ADM, exit=(0.5, 1), entry=(0.9, 0), style=EDGE_FAN + "dashed=1;")
edge(ADM, AGR, "the admissible", exit=(0.5, 1), entry=(0.5, 0))
edge(AGR, JUDGE, "the groups", exit=(0.5, 1), entry=(0.5, 0))
edge(AGR, VOTE, "Judge off", exit=(0, 0.6), entry=(0, 0.3),
     points=((LX + GX - 45, s6_y + 314), (LX + GX - 45, s6_y + 635)), label_at=0)
edge(JUDGE, VOTE, "a verdict per group", exit=(0.5, 1), entry=(0.5, 0))
edge(VOTE, FUSE, "a winner", exit=(1, 0.4), entry=(0, 0.4),
     points=((LX + RX - 50, s6_y + 650), (LX + RX - 50, s6_y + 482)), label_at=-0.3)
edge(VOTE, D2, "unsettled: no majority among the accepted, or one run", exit=(0.5, 1), entry=(0.5, 0))
edge(D2, FUSE, "no: 'contested'", exit=(1, 0.5), entry=(0, 0.8),
     points=((LX + RX - 25, s6_y + 830), (LX + RX - 25, s6_y + 563)), label_at=-0.55)
edge(D2, WAVE, "yes: a second wave, then validate, judge and vote again (one counter: waves)", style=EDGE_BACK,
     exit=(0, 0.5), entry=(0, 0.5),
     points=((LX - 20, s6_y + 830), (LX - 20, s0_y + 470 + 39)), label_at=-0.45)
edge(FUSE, DEL, exit=(0.5, 1), entry=(0.5, 0))
edge(VOTE, DEL, "none admissible", exit=(1, 0.9), entry=(0, 0.3),
     points=((LX + RX - 10, s6_y + 725), (LX + RX - 10, s6_y + 807)), label_at=-0.75)

# --- output -------------------------------------------------------------------------
out_y = s6_y + s6_h + 40
OUTN = vertex("1", CX - 300, out_y, 600, 44,
              "Answer: the chosen rows, widened with columns other agreeing runs carried, and SQL + the fused, re-audited claims + 'k of n' + every candidate's record (REST: Answer.ensemble)",
              TERM_OK)
edge(DEL, OUTN, "answer", exit=(0.5, 1), entry=(0.5, 0),
     points=((LX + rcx, out_y - 18), (CX, out_y - 18)))

# --- stage 5 (unchanged): human review -------------------------------------------------
s5_y = out_y + 44 + 40
s5_h = 120
S5 = vertex("1", LX, s5_y, LW, s5_h,
            "STAGE 5 &#8212; HUMAN REVIEW (v5.1-5.6; unchanged by arch7.3)  (the verdict is on the delivered answer)",
            LANE)
vertex(S5, 20, 44, LW - 40, 60,
       bullets(
           "the three verdicts, the staging database, the review and curation pages: as arch6 section 14",
           "the snapshot gains the ensemble record, so a reviewer fixing a wrong answer sees the dissent and the other queries that agreed",
           "promotion into the golden set uses the original wording and the chosen SQL: a golden pair records what a person asked, in their words",
       ).lstrip("<br>"),
       DET)
edge(OUTN, S5, "a person judges the answer", exit=(0.5, 1), entry=(0.5, 0))

# --- input edge ----------------------------------------------------------------------
edge(Q, SCR, exit=(0.5, 1), entry=(0.5, 0))

# --- assemble -------------------------------------------------------------------------
page_h = s5_y + s5_h + 40
xml = (
    '<mxfile host="Electron" agent="arch7.3 generator" version="31.4.5">\n'
    '  <diagram name="Multi-Agent NL2SQL v7.3" id="arch7_3-v7_3">\n'
    f'    <mxGraphModel dx="1400" dy="900" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" '
    f'arrows="1" fold="1" page="1" pageScale="1" pageWidth="1350" pageHeight="{page_h}" math="0" shadow="0">\n'
    '      <root>\n'
    '        <mxCell id="0"/>\n'
    '        <mxCell id="1" parent="0"/>\n'
    + "".join(f"        {c}\n" for c in cells)
    + '      </root>\n'
    '    </mxGraphModel>\n'
    '  </diagram>\n'
    '</mxfile>\n'
)
with open(OUT, "w") as fh:
    fh.write(xml)
print(f"wrote {OUT}: {len(cells)} cells, page height {page_h}")
