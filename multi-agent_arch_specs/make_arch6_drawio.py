#!/usr/bin/env python3
"""Emit Multi-Agent_NL2SQL_arch6.drawio next to this script.

    python multi-agent_arch_specs/make_arch6_drawio.py

arch5.2's diagram, as built in 6.1.0: a third deployment row for sign-in,
the snippet store, MLflow and the stack's CA; a band for who is asking
above Stage 1 (section 20); the fifth retriever; the Repair Agent emptying
an attempt's fields; the narrator's failures in `node_errors`; and Stage 5
with taking a judgement back and the curation page. Everything else is
arch5.2's, drawn by the same code.

Render it with draw.io, or without a desktop app:

    docker run --rm -v "$PWD/multi-agent_arch_specs":/data rlespinasse/drawio-export -f svg

Same cell styles as the owner's Multi-agent NL2SQL.drawio, so the two open
side by side in draw.io and look like the same family. Coordinates are
computed here rather than typed, so a box can be resized in one place.
"""
from __future__ import annotations

from xml.sax.saxutils import escape

from pathlib import Path

OUT = str(Path(__file__).resolve().parent / "Multi-Agent_NL2SQL_arch6.drawio")

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
       '<font style="font-size: 18px;"><b>Multi-Agent NL2SQL, v6</b></font>'
       '<font color="#666666"> &#8212; arch5.2 as built (6.1.0): plus the console, review undo, tracing, snippets and curation, sign-in, and a TLS identity per server; supersedes arch1&#8211;5.2</font>',
       "text;html=1;align=left;verticalAlign=middle;strokeColor=none;fillColor=none;")

vertex("1", LX, 62, 300, 20, "DEPLOYMENT &#8212; WHAT RUNS WHERE", TEXT + "fontStyle=1;letterSpacing=1;")
dep_y, dep_h, dep_w, dep_gap = 86, 96, 216, 20
deploy = [
    ("Chat models", "routed per call (5.2)", ["Ollama 192.168.10.82:11434",
     "reference qwen3.8-256k; the ladder", "picks from the catalog's ten (15.1)"]),
    ("Embedding model", "bge-m3", ["Ollama host.docker.internal:11434",
     "Schema, Knowledge and", "Example Retrievers"]),
    ("Retail database", "nl2sql-postgres v1_2", ["19 tables; reader role; a role per person",
     "TLS required; superuser has no password", "pg_hba: hostssl sign-in (6.1)"]),
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
    ("Model catalog", "catalog.json (v5.2)", ["the host's models: facts, a prior",
     "per task and rung, and what the", "calibration measured; mounted, read-only"]),
]
dep2_y = dep_y + dep_h + 16
for i, (name, sub, lines) in enumerate(review_deploy):
    vertex("1", LX + i * (dep_w + dep_gap), dep2_y, dep_w, dep_h,
           f'<b><font style="font-size: 13px;">{escape(name)}</font></b> '
           f'<font style="font-size: 11px;" color="#666666">{escape(sub)}</font>'
           + "".join(f"<br><font style=\"font-size: 11px;\">{escape(l)}</font>" for l in lines),
           STORE)

# Who is asking, and the rest of what 5.3-6.1 added to the deployment: a
# third row, because sign-in is a third system beside the pipeline and the
# review side, and these are the containers it and the later releases run.
signin_deploy = [
    ("Directory", "nl2sql-ldap (6.0)", ["people, Argon2 passwords, 4 groups;",
     "standalone, or a replica of the", "organisation's; StartTLS required"]),
    ("Auth service", "nl2sql-auth (6.0)", ["signs people in by connecting to the",
     "retail DB as them; signs the session;", "role sync; directory API on 8447 only"]),
    ("Stack CA", "nl2sql-pki (6.1)", ["one-shot: a key and certificate per",
     "server, each in its own volume;", "clients trust ca.crt once"]),
    ("Snippet store", "nl2sql-snippetsdb (5.6)", ["verified joins, filters, measures,",
     "dimensions + meanings; rebuilt from", "context_questions/sql_snippets.md"]),
    ("MLflow", "behind its front door (5.5)", ["a trace per run, a span per agent",
     "and per model call; verdicts on traces;", "the front door asks the auth service"]),
]
dep3_y = dep2_y + dep_h + 16
for i, (name, sub, lines) in enumerate(signin_deploy):
    vertex("1", LX + i * (dep_w + dep_gap), dep3_y, dep_w, dep_h,
           f'<b><font style="font-size: 13px;">{escape(name)}</font></b> '
           f'<font style="font-size: 11px;" color="#666666">{escape(sub)}</font>'
           + "".join(f"<br><font style=\"font-size: 11px;\">{escape(l)}</font>" for l in lines),
           STORE)

# --- legend ---------------------------------------------------------------------
leg_y = dep3_y + dep_h + 18
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

# --- model routing band (v5.2) ----------------------------------------------------
# Above the pipeline because it is not a stage: every LLM agent below asks it,
# per call, which model to use. The catalog is built by a script the operator
# runs against the Ollama host; the router only ever reads it.
rt_y = leg_y + 44
rt_h = 194
RT = vertex("1", LX, rt_y, LW, rt_h,
            "MODEL ROUTING (v5.2, built 5.2.0)  (not a stage: asked by every LLM agent, per call; calls no model; off = v5.1)",
            LANE)
rw, rgap = 360, 30
rx0 = (LW - (3 * rw + 2 * rgap)) // 2
CAT = vertex(RT, rx0, 44, rw, 134,
             title("Model catalog", "models/catalog.json") + bullets(
                 "built by models/build_catalog.py, measured by calibrate.py:",
                 "/api/tags -> /api/show per model -> the library page",
                 "-> a prior per task and rung -> calibration probes",
                 "suited[task] = highest rung measured within one question",
                 "of the reference model; reviewed, committed, mounted"),
             STORE)
RTR = vertex(RT, rx0 + rw + rgap, 44, rw, 134,
             title("Model Router", "no LLM") + bullets(
                 "route(task, rung) -> model, num_ctx, think, fallback, reason",
                 "table built at startup: cheapest suited model per rung",
                 "rung from state, by code: the Aggregator scores the generator",
                 "(tables, bridge, intent, ranked, trap rule; near exemplar -2)",
                 "routing off -> every rung is OLLAMA_MODEL (v5.1)"),
             DET)
LAD = vertex(RT, rx0 + 2 * (rw + rgap), 44, rw, 134,
             title("The ladder", "light -> standard -> heavy") + bullets(
                 "every repair climbs one rung; nothing descends within a run",
                 "(a Tier 1 completeness gap retries once at the same rung)",
                 "audit send-back: one rung up; diagnosis: above the generator",
                 "at most three distinct models, kept warm on the host",
                 "a routed model that fails -> its fallback -> OLLAMA_MODEL"),
             DET)
edge(CAT, RTR, exit=(1, 0.5), entry=(0, 0.5))
edge(RTR, LAD, exit=(1, 0.5), entry=(0, 0.5))

# --- who is asking (v6.0, section 20) ----------------------------------------------
# Above the pipeline for the same reason as routing: not a stage, but what
# every question carries in -- a person, signed in, whose SQL runs as them.
id_y = rt_y + rt_h + 24
id_h = 176
ID = vertex("1", LX, id_y, LW, id_h,
            "WHO IS ASKING (v6.0; section 20)  (not a stage: every page, the API, the console and MLflow need a person; off = --no-auth)",
            LANE)
iw, igap = 264, 18
ix0 = (LW - (4 * iw + 3 * igap)) // 2
SIGN = vertex(ID, ix0, 44, iw, 116,
              title("Sign-in", "the auth service") + bullets(
                  "connects to the retail DB as the person:",
                  "pg_hba ldap binds to the directory",
                  "hostssl, verify-full, StartTLS (6.1)",
                  "throttle 5/name, 50/address; lockout"),
              DET)
SESS = vertex(ID, ix0 + iw + igap, 44, iw, 116,
              title("Session", "Ed25519, 8 h") + bullets(
                  "verified by every service with the",
                  "public half; header compared whole",
                  "groups re-read from Postgres <= 60 s",
                  "cookie HttpOnly, SameSite=Strict, Secure"),
              DET)
ASIT = vertex(ID, ix0 + 2 * (iw + igap), 44, iw, 116,
              title("Runs as the person", "SET LOCAL ROLE") + bullets(
                  "the reader becomes them per question:",
                  "SET TRUE, INHERIT FALSE",
                  "attribution, not yet isolation:",
                  "no row-level policies"),
              DET)
TLSI = vertex(ID, ix0 + 3 * (iw + igap), 44, iw, 116,
              title("TLS identities", "the stack's CA (6.1)") + bullets(
                  "a key per server, in its own volume",
                  "proxies verify upstreams by name",
                  "browsers and the desktop trust",
                  "ca.crt once; the CA key is pki's"),
              STORE)
edge(SIGN, SESS, exit=(1, 0.5), entry=(0, 0.5))
edge(SESS, ASIT, exit=(1, 0.5), entry=(0, 0.5))

# --- input ------------------------------------------------------------------------
in_y = id_y + id_h + 40
Q = vertex("1", CX - 150, in_y, 300, 40, "Natural-language question", TERM)

# --- stage 1 ---------------------------------------------------------------------
s1_y = in_y + 70
s1_h = 484
S1 = vertex("1", LX, s1_y, LW, s1_h,
            "STAGE 1 &#8212; INTAKE AND CONTEXT GATHERING  (state: gathering; five retrievers run in parallel)",
            LANE)
SUP = vertex(S1, CX - LX - 170, 50, 340, 124,
             title("Supervisor", "LLM") + bullets(
                 "in-domain? prompt-injected? ambiguous?",
                 "intent: lookup / aggregate / compare / trend / narrative",
                 "entities + measure + period -> answer contract",
                 "one structured call; the only input screen",
                 "model: light; standard when the pre-screen flags it (v5.2)"),
             LLM)
STOP = vertex(S1, LW - 20 - 300, 68, 300, 60,
              "Refuse (out of domain, injected)<br>or ask to clarify (ambiguous; off in batch mode)",
              TERM_BAD + "fontSize=11;")
edge(SUP, STOP, "verdict &#8800; proceed", exit=(1, 0.5), entry=(0, 0.5))

ret_y, ret_h, ret_w, ret_gap = 196, 140, 208, 21
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
    ("Snippet Retriever", ["verified SQL pieces (v5.6): joins,",
                           "filters, measures, dimensions",
                           "keyword + meaning; no model call",
                           "only in-scope ones reach the generator"]),
]
ret_ids = []
for i, (name, lines) in enumerate(retrievers):
    x = 16 + i * (ret_w + ret_gap)
    ret_ids.append(vertex(S1, x, ret_y, ret_w, ret_h, title(name) + bullets(*lines), DET))
for r in ret_ids:
    edge(SUP, r, exit=(0.5, 1), entry=(0.5, 0))

AGG = vertex(S1, CX - LX - 250, 362, 500, 94,
             title("Context Aggregator", "join, no LLM") + bullets(
                 "fan-in of the five branches; dedupe tables, apply FK closure and the cap",
                 "fetch schema + sample rows; trim rules and exemplars to their budgets",
                 "score the generator's rung from what it has seen (v5.2)"),
             JOIN)
for r in ret_ids:
    edge(r, AGG, exit=(0.5, 1), entry=(0.5, 0))

# --- stage 2 ---------------------------------------------------------------------
s2_y = s1_y + s1_h + 40
s2_h = 186
S2 = vertex("1", LX, s2_y, LW, s2_h, "STAGE 2 &#8212; SYNTHESIS  (state: compiling)", LANE)
GEN_H = 122
GEN = vertex(S2, CX - LX - 340, 48, 680, GEN_H,
             title("SQL Generator", "LLM, multi-shot") + bullets(
                 "system: rules + dialect + pruned schema and sample rows",
                 "turns: up to 3 exemplars replayed as human/assistant pairs (bare SQL answers)",
                 "human: knowledge + literal map + intent framing + answer contract + question + repair hint (on retries)",
                 "the only agent that writes SQL",
                 "model: the rung the Aggregator scored, one rung up per repair; same prompt, num_ctx follows the model (v5.2)"),
             LLM)
edge(AGG, GEN, "pruned DDL + literal map + rules + exemplars", exit=(0.5, 1), entry=(0.5, 0))

# --- stage 3 ---------------------------------------------------------------------
s3_y = s2_y + s2_h + 40
s3_h = 1132
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
REV = vertex(S3, GX, 822, GW, 164,
             title("Completeness Reviewer", "rules, then one LLM reflection") + bullets(
                 "R1 a label beside every key (sku_id -> product_name)",
                 "R2 a ranked list shows the measure it was ranked by",
                 "R3 the period is stated; default = latest complete FY -> assumptions",
                 "R4 the row count fits the ask (top 10 -> 10 rows)",
                 "then one reflection: what would the reader ask next? schema columns only",
                 "same-result guard: never sends one gap back twice",
                 "reflection model: the generation's rung, capped at standard (v5.2)"),
             LLM)
D5_Y = 1020
D5 = vertex(S3, gcx - 60, D5_Y, 120, 90, "answers the<br>question?", DECISION)

RX, RW = 690, 430          # repair column (relative)
rcx = RX + RW // 2         # 905
REP_Y, REP_H = 306, 150
REP = vertex(S3, RX, REP_Y, RW, REP_H,
             title("Repair Agent", "classifier first; LLM only if unclassified") + bullets(
                 "error class -> hint (syntax position, nearest column,",
                 "GROUP BY, cast, cost, timeout, audit, missing columns)",
                 "appends {sql, issues} to attempt_history",
                 "attempts += 1; never writes SQL itself",
                 "empties the attempt's fields: rows, chart, claims, audit (v6.1)",
                 "diagnosis model: one rung above the generator's, standard at least (v5.2)"),
             LLM)
D4_Y = 494
D4 = vertex(S3, rcx - 80, D4_Y, 160, 96, "attempts<br>&lt; max (7)?", DECISION)
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
     points=((LX + RX - 60, s3_y + 741), (LX + RX - 60, s3_y + REP_Y + round(0.75 * REP_H))), label_at=-0.55)
edge(D3, REV, "yes: result set", exit=(0.5, 1), entry=(0.5, 0))
edge(REV, D5, exit=(0.5, 1), entry=(0.5, 0))
edge(D5, REP, "no: incomplete (missing columns, wrong period or count)", exit=(1, 0.5), entry=(0, 0.9),
     points=((LX + RX - 30, s3_y + D5_Y + 45), (LX + RX - 30, s3_y + REP_Y + round(0.9 * REP_H))), label_at=-0.6)
# repair -> budget -> loop or stop
edge(REP, D4, exit=(0.5, 1), entry=(0.5, 0))
edge(D4, FAIL, "no", exit=(0.5, 1), entry=(0.5, 0))
edge(D4, GEN, "yes: regenerate with the hint, one rung up", style=EDGE_BACK, exit=(1, 0.5), entry=(1, 0.5),
     points=((RAIL_RETRY, s3_y + D4_Y + 48), (RAIL_RETRY, s2_y + 48 + GEN_H // 2)))

# --- stage 4 ---------------------------------------------------------------------
s4_y = s3_y + s3_h + 40
s4_h = 226
S4 = vertex("1", LX, s4_y, LW, s4_h, "STAGE 4 &#8212; PRESENTATION AND AUDIT  (state: formatting)", LANE)
pw, pgap = 350, 30
px0 = (LW - (3 * pw + 2 * pgap)) // 2
P_H = 130
VIS = vertex(S4, px0, 56, pw, P_H,
             title("Visual Formatter", "no LLM") + bullets(
                 "result shape -> scalar / bar / line / grouped bar / scatter / table",
                 "intent breaks ties; emits ChartSpec + markdown table",
                 "reads state; calls no upstream agent"),
             DET)
NAR = vertex(S4, px0 + pw + pgap, 56, pw, P_H,
             title("Insight Narrator", "LLM") + bullets(
                 "structured claims: {text, value, cells, formula}",
                 "sees the capped rows, the chart, the top exemplar's",
                 "reasoning target and the assumptions, all stated",
                 "model: light or standard by result size; audit retry one rung up (v5.2)",
                 "a failure costs the narrative, not the rows: node_errors (v6.1)"),
             LLM)
AUD = vertex(S4, px0 + 2 * (pw + pgap), 56, pw, P_H,
             title("Audit Checker", "no LLM") + bullets(
                 "every claim value == a cell, or formula over cells",
                 "every assumption stated (FY default, etc.)",
                 "unsupported claim -> narrator once, then dropped",
                 "(sensitive-column policy withdrawn: section 7.3)"),
             DET)
edge(D5, VIS, "yes: complete result + assumptions", exit=(0.5, 1), entry=(0.5, 0), label_at=-0.5)
edge(VIS, NAR, exit=(1, 0.5), entry=(0, 0.5))
edge(NAR, AUD, exit=(1, 0.5), entry=(0, 0.5))
edge(AUD, NAR, "claim unsupported (once)", style=EDGE_BACK, exit=(0.5, 0), entry=(0.5, 0),
     points=((LX + px0 + 2 * (pw + pgap) + pw // 2, s4_y + 44), (LX + px0 + pw + pgap + pw // 2, s4_y + 44)))
edge(AUD, REP, "SQL judged wrong: semantic_issue (same budget)", style=EDGE_BACK,
     exit=(1, 0.5), entry=(1, 0.5),
     points=((RAIL_AUDIT, s4_y + 56 + P_H // 2), (RAIL_AUDIT, s3_y + REP_Y + REP_H // 2)))

# --- output -----------------------------------------------------------------------
out_y = s4_y + s4_h + 40
OUTN = vertex("1", CX - 220, out_y, 440, 44,
             "Markdown answer + chart spec + SQL + trace (per-agent timings, the model behind each call)", TERM_OK)
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
            "STAGE 5 &#8212; HUMAN REVIEW (v5.1; undo 5.4; curation 5.6)  (after the answer: a person's verdict, a reviewer's judgement)",
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
                 "back to pending or delete undoes it (5.4)",
                 "the curation page writes directly (5.6)"),
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
    '<mxfile host="Electron" agent="arch6 generator" version="31.4.5">\n'
    '  <diagram name="Multi-Agent NL2SQL v6" id="arch6-v6">\n'
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
