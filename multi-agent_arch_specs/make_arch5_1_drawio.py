#!/usr/bin/env python3
"""Emit Multi-Agent_NL2SQL_arch5_1.drawio next to this script.

    python multi-agent_arch_specs/make_arch5_1_drawio.py

arch5's diagram with one stage added below the answer: human review, where a
user's verdict on an answer is judged and turned into a golden pair, a
correction or a completion. Everything above that stage is arch5's, drawn by
the same code.

Render it with draw.io, or without a desktop app:

    docker run --rm -v "$PWD/multi-agent_arch_specs":/data rlespinasse/drawio-export -f svg

Same cell styles as the owner's Multi-agent NL2SQL.drawio, so the two open
side by side in draw.io and look like the same family. Coordinates are
computed here rather than typed, so a box can be resized in one place.
"""
from __future__ import annotations

from xml.sax.saxutils import escape

from pathlib import Path

OUT = str(Path(__file__).resolve().parent / "Multi-Agent_NL2SQL_arch5_1.drawio")

# --- styles ----------------------------------------------------------------
BOX = ("rounded=0;whiteSpace=wrap;html=1;align=left;verticalAlign=top;"
       "spacingLeft=5;spacingTop=0;spacingRight=5;spacingBottom=0;")
LLM = BOX + "fillColor=#fff2cc;strokeColor=#d6b656;"
DET = BOX + "fillColor=#dae8fc;strokeColor=#6c8ebf;"
JOIN = BOX + "fillColor=#e1d5e7;strokeColor=#9673a6;"
STORE = BOX + "fillColor=#f5f5f5;strokeColor=#666666;fontColor=#333333;"
HEX = ("shape=hexagon;perimeter=hexagonPerimeter2;whiteSpace=wrap;html=1;shapeInside=1;"
       "fixedSize=1;align=left;verticalAlign=top;spacingLeft=23;spacingRight=23;"
       "fillColor=#d5e8d4;strokeColor=#82b366;")
DECISION = "strokeWidth=2;html=1;shape=mxgraph.flowchart.decision;whiteSpace=wrap;fillColor=#ffffff;"
TERM = ("strokeWidth=2;html=1;shape=mxgraph.flowchart.terminator;whiteSpace=wrap;"
        "fontStyle=1;fontSize=13;")
TERM_BAD = TERM + "fillColor=#f8cecc;strokeColor=#b85450;"
TERM_OK = TERM + "fillColor=#d5e8d4;strokeColor=#82b366;"
LANE = ("swimlane;html=1;startSize=30;align=left;spacingLeft=10;fontStyle=1;fontSize=13;"
        "strokeColor=#666666;fillColor=#ffffff;swimlaneFillColor=#ffffff;")
EDGE = "edgeStyle=orthogonalEdgeStyle;curved=1;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;"
EDGE_BACK = EDGE + "dashed=1;strokeColor=#b85450;fontColor=#b85450;fontStyle=1;"
EDGE_DATA = EDGE + "strokeColor=#6c8ebf;fontColor=#333333;"
TEXT = "text;html=1;align=left;verticalAlign=middle;resizable=0;points=[];autosize=1;strokeColor=none;fillColor=none;fontSize=11;fontColor=#666666;"

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


# --- canvas plan -------------------------------------------------------------
LX, LW = 40, 1160          # swimlane x and width
CX = LX + LW // 2          # pipeline centre line (620)
RAIL_RETRY = LX + LW + 40  # x of the retry loop-back rail
RAIL_AUDIT = LX + LW + 80  # x of the audit re-entry rail

# --- title and deployment band ------------------------------------------------
vertex("1", LX, 20, LW, 30,
       '<font style="font-size: 18px;"><b>Multi-Agent NL2SQL, v5.1</b></font>'
       '<font color="#666666"> &#8212; arch5 plus human review: correct &#8594; golden set, wrong &#8594; corrections, incomplete &#8594; completions; supersedes arch1&#8211;5</font>',
       "text;html=1;align=left;verticalAlign=middle;strokeColor=none;fillColor=none;")

vertex("1", LX, 62, 300, 20, "DEPLOYMENT &#8212; WHAT RUNS WHERE", TEXT + "fontStyle=1;letterSpacing=1;")
dep_y, dep_h, dep_w, dep_gap = 86, 96, 216, 20
deploy = [
    ("Chat model", "qwen3.8-256k, 256k ctx", ["Ollama 192.168.10.82:11434",
     "Supervisor, Generator, Reviewer,", "Narrator, Repair (unclassified)"]),
    ("Embedding model", "bge-m3", ["Ollama host.docker.internal:11434",
     "Schema, Knowledge and", "Example Retrievers"]),
    ("Retail database", "nl2sql-postgres", ["nl2sql_retail, 19 tables, reader role",
     "Literal catalog, FK closure, label map,", "fiscal calendar, Planner Gate, Executor"]),
    ("Vector store", "nl2sql-vectordb", ["pgvector: DDL chunks, knowledge",
     "chunks, golden-pair question and", "reasoning vectors"]),
    ("Context store", "nl2sql-chunkdb", ["golden pairs as rows +", "BM25 statistics over keywords",
     "Example Retriever"]),
]
for i, (name, sub, lines) in enumerate(deploy):
    vertex("1", LX + i * (dep_w + dep_gap), dep_y, dep_w, dep_h,
           f'<b><font style="font-size: 13px;">{escape(name)}</font></b> '
           f'<font style="font-size: 11px;" color="#666666">{escape(sub)}</font>'
           + "".join(f"<br><font style=\"font-size: 11px;\">{escape(l)}</font>" for l in lines),
           STORE)

# The human-review side (v5.1): a second row, because it is a second system
# -- it runs after the answer, on what a person said about it.
review_deploy = [
    ("Staging database", "nl2sql-feedbackdb", ["verdicts from the web and desktop",
     "clients, with a snapshot of the job;", "the API may only INSERT"]),
    ("Review service", "nl2sql-review + GUI", ["one pane per verdict; validates a",
     "fix on the retail DB as the reader", "role; writes the golden set or a store"]),
    ("Corrections store", "correctionsdb", ["pgvector: each wrong answer, its",
     "validated fix, and a RAG vector", "of its question"]),
    ("Completions store", "completionsdb", ["pgvector: each incomplete answer,",
     "its validated completion, and a", "RAG vector of its question"]),
]
dep2_y = dep_y + dep_h + 16
for i, (name, sub, lines) in enumerate(review_deploy):
    vertex("1", LX + i * (dep_w + dep_gap), dep2_y, dep_w, dep_h,
           f'<b><font style="font-size: 13px;">{escape(name)}</font></b> '
           f'<font style="font-size: 11px;" color="#666666">{escape(sub)}</font>'
           + "".join(f"<br><font style=\"font-size: 11px;\">{escape(l)}</font>" for l in lines),
           STORE)

# --- legend ---------------------------------------------------------------------
leg_y = dep2_y + dep_h + 18
vertex("1", LX, leg_y, 60, 22, "LEGEND", TEXT + "fontStyle=1;letterSpacing=1;")
legend = [("calls the chat model (LLM)", LLM), ("deterministic code", DET), ("join / fan-in", JOIN),
          ("database gate / executor", HEX + "spacingLeft=14;spacingRight=14;align=center;verticalAlign=middle;"),
          ("stops the run", TERM_BAD + "fontStyle=0;fontSize=11;")]
lx = LX + 70
for text, st in legend:
    w = 190 if "gate" in text else 170
    vertex("1", lx, leg_y, w, 22, f'<font style="font-size: 11px;">{escape(text)}</font>',
           st.replace("verticalAlign=top", "verticalAlign=middle").replace("align=left", "align=center"))
    lx += w + 14

# --- input ------------------------------------------------------------------------
in_y = leg_y + 44
Q = vertex("1", CX - 150, in_y, 300, 40, "Natural-language question", TERM)

# --- stage 1 ---------------------------------------------------------------------
s1_y = in_y + 70
s1_h = 470
S1 = vertex("1", LX, s1_y, LW, s1_h,
            "STAGE 1 &#8212; INTAKE AND CONTEXT GATHERING  (state: gathering; four retrievers run in parallel)",
            LANE)
SUP = vertex(S1, CX - LX - 170, 50, 340, 110,
             title("Supervisor", "LLM") + bullets(
                 "in-domain? prompt-injected? ambiguous?",
                 "intent: lookup / aggregate / compare / trend / narrative",
                 "entities + measure + period -> answer contract",
                 "one structured call; the only input screen"),
             LLM)
STOP = vertex(S1, LW - 20 - 300, 68, 300, 60,
              "Refuse (out of domain, injected)<br>or ask to clarify (ambiguous; off in batch mode)",
              TERM_BAD + "fontSize=11;")
edge(SUP, STOP, "verdict &#8800; proceed", exit=(1, 0.5), entry=(0, 0.5))

ret_y, ret_h, ret_w, ret_gap = 196, 116, 262, 30
retrievers = [
    ("Schema Retriever", ["DDL-chunk vectors (ddl_index) top-6",
                          "+ tables named by knowledge and exemplars",
                          "+ foreign-key closure from pg_constraint",
                          "cap 10 tables; no model call"]),
    ("Literal Matcher", ["literal catalog: text columns with",
                         "<= 500 distinct values, trigram-indexed",
                         "pg_trgm word_similarity (difflib fallback)",
                         "phrase -> table.column = 'value'"]),
    ("Knowledge Retriever", ["question vector vs data dictionary",
                             "and business index chunks",
                             "top-4 per collection",
                             "best-effort: skipped if the store is down"]),
    ("Example Retriever", ["question 0.50 | BM25 keywords 0.35 |",
                           "reasoning 0.15 -> score fusion",
                           "-> grounding + MMR rerank -> top 3",
                           "the v3 ensemble, unchanged"]),
]
ret_ids = []
for i, (name, lines) in enumerate(retrievers):
    x = 16 + i * (ret_w + ret_gap)
    ret_ids.append(vertex(S1, x, ret_y, ret_w, ret_h, title(name) + bullets(*lines), DET))
for r in ret_ids:
    edge(SUP, r, exit=(0.5, 1), entry=(0.5, 0))

AGG = vertex(S1, CX - LX - 250, 362, 500, 80,
             title("Context Aggregator", "join, no LLM") + bullets(
                 "fan-in of the four branches; dedupe tables, apply FK closure and the cap",
                 "fetch schema + sample rows; trim rules and exemplars to their budgets"),
             JOIN)
for r in ret_ids:
    edge(r, AGG, exit=(0.5, 1), entry=(0.5, 0))

# --- stage 2 ---------------------------------------------------------------------
s2_y = s1_y + s1_h + 40
s2_h = 172
S2 = vertex("1", LX, s2_y, LW, s2_h, "STAGE 2 &#8212; SYNTHESIS  (state: compiling)", LANE)
GEN = vertex(S2, CX - LX - 340, 48, 680, 108,
             title("SQL Generator", "LLM, multi-shot") + bullets(
                 "system: rules + dialect + pruned schema and sample rows",
                 "turns: up to 3 exemplars replayed as human/assistant pairs (bare SQL answers)",
                 "human: knowledge + literal map + intent framing + answer contract + question + repair hint (on retries)",
                 "the only agent that writes SQL"),
             LLM)
edge(AGG, GEN, "pruned DDL + literal map + rules + exemplars", exit=(0.5, 1), entry=(0.5, 0))

# --- stage 3 ---------------------------------------------------------------------
s3_y = s2_y + s2_h + 40
s3_h = 1118
S3 = vertex("1", LX, s3_y, LW, s3_h,
            "STAGE 3 &#8212; VALIDATION AND REPAIR LOOP  (state: guarding; one counter, attempts, shared by every failure, "
            "including a result that ran but does not answer the question)",
            LANE)
GX, GW = 70, 380           # gate column (relative)
gcx = GX + GW // 2         # 260
AST = vertex(S3, GX, 48, GW, 100,
             title("Static Validator", "pglast AST") + bullets(
                 "exactly one statement; SelectStmt only",
                 "no writing CTE, no SELECT INTO, function denylist",
                 "every relation in the table allowlist",
                 "regex prefix check first, as a free pre-filter"),
             DET)
D1 = vertex(S3, gcx - 60, 180, 120, 90, "AST<br>passes?", DECISION)
PLAN = vertex(S3, GX, 306, GW, 100,
              title("Planner Gate", "EXPLAIN, never ANALYZE") + bullets(
                  "EXPLAIN (FORMAT JSON) in a READ ONLY txn",
                  "surfaces unknown columns, casts, GROUP BY",
                  "Plan.Total Cost <= MAX_PLAN_COST"),
              HEX)
D2 = vertex(S3, gcx - 60, 438, 120, 90, "plan ok?", DECISION)
EXEC = vertex(S3, GX, 564, GW, 100,
              title("Safe Executor") + bullets(
                  "reader role; SET TRANSACTION READ ONLY",
                  "SET LOCAL statement_timeout; row cap",
                  "SET ROLE principal for RLS, when present"),
              HEX)
D3 = vertex(S3, gcx - 60, 696, 120, 90, "ran?", DECISION)
REV = vertex(S3, GX, 822, GW, 150,
             title("Completeness Reviewer", "rules, then one LLM reflection") + bullets(
                 "R1 a label beside every key (sku_id -> product_name)",
                 "R2 a ranked list shows the measure it was ranked by",
                 "R3 the period is stated; default = latest complete FY -> assumptions",
                 "R4 the row count fits the ask (top 10 -> 10 rows)",
                 "then one reflection: what would the reader ask next? schema columns only",
                 "same-result guard: never sends one gap back twice"),
             LLM)
D5 = vertex(S3, gcx - 60, 1006, 120, 90, "answers the<br>question?", DECISION)

RX, RW = 690, 430          # repair column (relative)
rcx = RX + RW // 2         # 905
REP = vertex(S3, RX, 306, RW, 118,
             title("Repair Agent", "classifier first; LLM only if unclassified") + bullets(
                 "error class -> hint (syntax position, nearest column,",
                 "GROUP BY, cast, cost, timeout, audit, missing columns)",
                 "appends {sql, issues} to attempt_history",
                 "attempts += 1; never writes SQL itself"),
             LLM)
D4 = vertex(S3, rcx - 80, 480, 160, 96, "attempts<br>&lt; max (7)?", DECISION)
FAIL = vertex(S3, RX, 640, RW, 60, "Give up: last SQL + full attempt history", TERM_BAD)

# gate column edges
edge(GEN, AST, "raw SQL", exit=(0.5, 1), entry=(0.5, 0), label_at=-0.85)
edge(AST, D1, exit=(0.5, 1), entry=(0.5, 0))
edge(D1, PLAN, "yes", exit=(0.5, 1), entry=(0.5, 0))
edge(PLAN, D2, exit=(0.5, 1), entry=(0.5, 0))
edge(D2, EXEC, "yes", exit=(0.5, 1), entry=(0.5, 0))
edge(EXEC, D3, exit=(0.5, 1), entry=(0.5, 0))
# failures into the repair agent (three heights on its left edge)
edge(D1, REP, "no: static issue", exit=(1, 0.5), entry=(0, 0.25))
edge(D2, REP, "no: planner error / over budget", exit=(1, 0.5), entry=(0, 0.6))
edge(D3, REP, "no: runtime error / timeout", exit=(1, 0.5), entry=(0, 0.75),
     points=((LX + RX - 60, s3_y + 741), (LX + RX - 60, s3_y + 306 + 88)), label_at=-0.55)
edge(D3, REV, "yes: result set", exit=(0.5, 1), entry=(0.5, 0))
edge(REV, D5, exit=(0.5, 1), entry=(0.5, 0))
edge(D5, REP, "no: incomplete (missing columns, wrong period or count)", exit=(1, 0.5), entry=(0, 0.9),
     points=((LX + RX - 30, s3_y + 1051), (LX + RX - 30, s3_y + 306 + 106)), label_at=-0.6)
# repair -> budget -> loop or stop
edge(REP, D4, exit=(0.5, 1), entry=(0.5, 0))
edge(D4, FAIL, "no", exit=(0.5, 1), entry=(0.5, 0))
edge(D4, GEN, "yes: regenerate with the hint", style=EDGE_BACK, exit=(1, 0.5), entry=(1, 0.5),
     points=((RAIL_RETRY, s3_y + 525), (RAIL_RETRY, s2_y + 48 + 48)))

# --- stage 4 ---------------------------------------------------------------------
s4_y = s3_y + s3_h + 40
s4_h = 212
S4 = vertex("1", LX, s4_y, LW, s4_h, "STAGE 4 &#8212; PRESENTATION AND AUDIT  (state: formatting)", LANE)
pw, pgap = 350, 30
px0 = (LW - (3 * pw + 2 * pgap)) // 2
VIS = vertex(S4, px0, 56, pw, 116,
             title("Visual Formatter", "no LLM") + bullets(
                 "result shape -> scalar / bar / line / grouped bar / scatter / table",
                 "intent breaks ties; emits ChartSpec + markdown table",
                 "reads state; calls no upstream agent"),
             DET)
NAR = vertex(S4, px0 + pw + pgap, 56, pw, 116,
             title("Insight Narrator", "LLM") + bullets(
                 "structured claims: {text, value, cells, formula}",
                 "sees the capped result set, the chart spec,",
                 "the top exemplar's reasoning target, and the",
                 "assumptions, each of which it must state"),
             LLM)
AUD = vertex(S4, px0 + 2 * (pw + pgap), 56, pw, 116,
             title("Audit Checker", "no LLM") + bullets(
                 "every claim value == a cell, or formula over cells",
                 "every assumption stated (FY default, etc.)",
                 "sensitive columns: aggregates only",
                 "unsupported claim -> narrator once, then dropped"),
             DET)
edge(D5, VIS, "yes: complete result + assumptions", exit=(0.5, 1), entry=(0.5, 0), label_at=-0.5)
edge(VIS, NAR, exit=(1, 0.5), entry=(0, 0.5))
edge(NAR, AUD, exit=(1, 0.5), entry=(0, 0.5))
edge(AUD, NAR, "claim unsupported (once)", style=EDGE_BACK, exit=(0.5, 0), entry=(0.5, 0),
     points=((LX + px0 + 2 * (pw + pgap) + pw // 2, s4_y + 44), (LX + px0 + pw + pgap + pw // 2, s4_y + 44)))
edge(AUD, REP, "SQL judged wrong: semantic_issue (same budget)", style=EDGE_BACK,
     exit=(1, 0.5), entry=(1, 0.5),
     points=((RAIL_AUDIT, s4_y + 108), (RAIL_AUDIT, s3_y + 306 + 59)))

# --- output -----------------------------------------------------------------------
out_y = s4_y + s4_h + 40
OUTN = vertex("1", CX - 220, out_y, 440, 44,
             "Markdown answer + chart spec + SQL + trace (per-agent timings)", TERM_OK)
edge(AUD, OUTN, "answer", exit=(0.5, 1), entry=(0.5, 0),
     points=((LX + px0 + 2 * (pw + pgap) + pw // 2, out_y - 18), (CX, out_y - 18)))

# --- input edge ---------------------------------------------------------------------
edge(Q, SUP, exit=(0.5, 1), entry=(0.5, 0))

# --- stage 5 (v5.1): human review ------------------------------------------------------
# Below the answer, because it happens after it: a person says whether the
# answer was right, and a reviewer turns that into something kept. Three
# verdicts, three ways forward, and only one of them reaches the golden set.
s5_y = out_y + 44 + 50
s5_h = 520
S5 = vertex("1", LX, s5_y, LW, s5_h,
            "STAGE 5 &#8212; HUMAN REVIEW (v5.1)  (after the answer: a person's verdict, a reviewer's judgement)",
            LANE)
cw, cgap = 320, 60
cx0 = (LW - (3 * cw + 2 * cgap)) // 2
col = [cx0, cx0 + cw + cgap, cx0 + 2 * (cw + cgap)]
mid = [LX + c + cw // 2 for c in col]

VERD = vertex(S5, col[0], 50, cw, 84,
              title("Verdict", "web or desktop client") + bullets(
                  "correct / wrong / correct but incomplete",
                  "+ an optional comment",
                  "one per answer; a revote replaces it"),
              DET)
STG = vertex(S5, col[1], 50, cw, 84,
             title("Staging database", "feedbackdb") + bullets(
                 "a snapshot of the job: question, SQL,",
                 "answer, result shape -- the job itself",
                 "is forgotten within the hour"),
             STORE)
RVW = vertex(S5, col[2], 50, cw, 84,
             title("Review service", "one pane per verdict") + bullets(
                 "a reviewer judges: accept or reject",
                 "nothing reaches a store unreviewed"),
             JOIN)
# Round the lane's left edge rather than through its title bar.
edge(OUTN, VERD, "a user judges the answer", exit=(0.5, 1), entry=(0, 0.5),
     points=((CX, s5_y - 24), (LX - 20, s5_y - 24), (LX - 20, s5_y + 92)), label_at=-0.35)
edge(VERD, STG, exit=(1, 0.5), entry=(0, 0.5))
edge(STG, RVW, exit=(1, 0.5), entry=(0, 0.5))

GP = vertex(S5, col[0], 180, cw, 84,
            title("Golden pair", "correct") + bullets(
                "keywords, reasoning target, expected result",
                "previewed with the loader's own parser;",
                "promote is refused until it parses"),
            DET)
FW = vertex(S5, col[1], 180, cw, 84,
            title("Corrected SQL", "wrong") + bullets(
                "the reviewer writes the query that should",
                "have been generated, starting from the",
                "agent's own"),
            DET)
FI = vertex(S5, col[2], 180, cw, 84,
            title("Completed SQL", "correct but incomplete") + bullets(
                "the query that would also have carried",
                "what the answer left out -- a name beside",
                "an id, the figure a ranking used"),
            DET)
bus = s5_y + 158
edge(RVW, GP, "correct", exit=(0.5, 1), entry=(0.5, 0),
     points=((mid[2], bus), (mid[0], bus)), label_at=0.75)
edge(RVW, FW, "wrong", exit=(0.5, 1), entry=(0.5, 0),
     points=((mid[2], bus), (mid[1], bus)), label_at=0.7)
edge(RVW, FI, "correct but incomplete", exit=(0.5, 1), entry=(0.5, 0), label_at=0.2)

GOLD = vertex(S5, col[0], 330, cw, 150,
              title("Golden set", "the benchmark") + bullets(
                  "context_questions/translated_questions.md,",
                  "written in the checkout and committed",
                  "reloaded into chunkdb + vectordb",
                  "what the agent is measured against --",
                  "so no fix ever goes here"),
              STORE)
VW = vertex(S5, col[1], 310, cw, 80,
            "<b>Validate on the retail DB</b><br>reader role, READ ONLY, timeout,<br>row cap; run again on save",
            HEX)
VI = vertex(S5, col[2], 310, cw, 80,
            "<b>Validate on the retail DB</b><br>reader role, READ ONLY, timeout,<br>row cap; run again on save",
            HEX)
CORR = vertex(S5, col[1], 420, cw, 84,
              title("Corrections store", "records + RAG") + bullets(
                  "question, incorrect answer, correct answer",
                  "a bge-m3 vector of each question beside",
                  "both queries, for a later agent to retrieve"),
              STORE)
COMP = vertex(S5, col[2], 420, cw, 84,
              title("Completions store", "records + RAG") + bullets(
                  "question, incomplete answer, complete one",
                  "its own database and RAG, apart from",
                  "the corrections and the golden set"),
              STORE)
edge(GP, GOLD, "promote", exit=(0.5, 1), entry=(0.5, 0))
edge(FW, VW, exit=(0.5, 1), entry=(0.5, 0))
edge(FI, VI, exit=(0.5, 1), entry=(0.5, 0))
edge(VW, CORR, "only SQL that runs", exit=(0.5, 1), entry=(0.5, 0))
edge(VI, COMP, "only SQL that runs", exit=(0.5, 1), entry=(0.5, 0))
for hexagon, box in ((VW, FW), (VI, FI)):
    edge(hexagon, box, "fails: edit and try again", style=EDGE_BACK, exit=(0, 0.5), entry=(0, 0.5),
         points=((LX + (col[1] if hexagon == VW else col[2]) - 22, s5_y + 350),
                 (LX + (col[1] if hexagon == VW else col[2]) - 22, s5_y + 222)))

# --- assemble -------------------------------------------------------------------------
page_h = s5_y + s5_h + 40
xml = (
    '<mxfile host="Electron" agent="arch5_1 generator" version="31.4.5">\n'
    '  <diagram name="Multi-Agent NL2SQL v5.1" id="arch5_1-v5_1">\n'
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
