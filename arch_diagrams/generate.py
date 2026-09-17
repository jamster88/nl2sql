#!/usr/bin/env python3
"""Regenerate arch_v1.svg and arch_v2.svg.

    python arch_diagrams/generate.py

Pure standard library, no arguments, writes both files next to this script.

The SVGs are computed rather than hand-placed: text is wrapped against real
Helvetica advance widths, each row takes the height its content needs, and the
rails are drawn only once the rows they connect have known positions. That is
the point of keeping this script -- editing a 60 KB SVG by hand to reword one
"why" card is not something anyone will do twice, whereas editing the string
in build_v2() below and re-running this takes a second.

Both diagrams describe agent/nl2sql_agent/. v1 is the schema-only pipeline;
v2 adds retrieve_knowledge in front of it and threads that context into table
selection, SQL generation and validation. When graph.py gains or renames a
node, or a step's rationale changes, the content lives in build_v1()/build_v2()
at the bottom of this file -- everything above them is layout machinery.
"""
from __future__ import annotations

from pathlib import Path

W = 1300
MARGIN = 48
RAIL_X = 74                    # control flow (retry) rail, left of the steps
STEP_X, STEP_W = 140, 450
KFLOW_X = 618                  # v2 knowledge data-flow rail
WHY_X, WHY_W = 660, 592
CONTENT_R = WHY_X + WHY_W      # 1252

INK, MUTED, FAINT = "#1b1b1d", "#65656b", "#8e8e94"
BG, CARD, LINE = "#fbfaf8", "#ffffff", "#dcd8cf"
WHY_BG, WHY_RULE = "#f4f1e9", "#bf9b30"
SLATE, BLUE, PURPLE, TEAL, RED, AMBER = "#43566e", "#2f5d8f", "#6a4c93", "#0f7268", "#a8422f", "#8a6a14"

SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace"


def esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def runs(text: str):
    """Split on backticks into (word, is_code) tokens."""
    out = []
    for i, part in enumerate(text.split("`")):
        code = i % 2 == 1
        for w in part.split(" "):
            if w:
                out.append((w, code))
    return out


# Helvetica advance widths per 1000 em -- close enough to the system sans that
# a wrapped line actually fits the box it was measured against.
_AW = {" ": 278, "!": 278, '"': 355, "#": 556, "$": 556, "%": 889, "&": 667, "'": 191,
       "(": 333, ")": 333, "*": 389, "+": 584, ",": 278, "-": 333, ".": 278, "/": 278,
       ":": 278, ";": 278, "<": 584, "=": 584, ">": 584, "?": 556, "@": 1015,
       "[": 278, "\\": 278, "]": 278, "_": 556, "`": 333, "{": 334, "|": 260, "}": 334,
       "\u2014": 1000, "\u2013": 556, "\u2026": 1000, "\u00b7": 278, "\u2190": 800, "\u2192": 800}
for _c in "0123456789":
    _AW[_c] = 556
for _c, _w in zip("abcdefghijklmnopqrstuvwxyz",
                  (556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833,
                   556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500)):
    _AW[_c] = _w
for _c, _w in zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ",
                  (667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833,
                   722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611)):
    _AW[_c] = _w


def measure(word: str, size: float, code: bool) -> float:
    if code:
        return len(word) * size * 0.601
    return sum(_AW.get(c, 556) for c in word) * size / 1000.0


def wrap(text: str, width: float, size: float):
    """Greedy wrap to `width` px; returns lines of (word, is_code) tokens."""
    sp = measure(" ", size, False)
    lines, cur, used = [], [], 0.0
    for word, code in runs(text):
        cw = measure(word, size, code)
        add = cw if not cur else cw + sp
        if cur and used + add > width:
            lines.append(cur)
            cur, used = [(word, code)], cw
        else:
            cur.append((word, code))
            used += add
    if cur:
        lines.append(cur)
    return lines


def text_block(x, y, text, width, size, color=INK, weight="400", lh=None, anchor="start"):
    lh = lh or size * 1.48
    svg, yy = [], y
    for line in wrap(text, width, size):
        spans = []
        for j, (word, code) in enumerate(line):
            pre = " " if j else ""
            if code:
                spans.append(f'<tspan font-family="{MONO}" font-size="{size*0.93:.1f}">{esc(pre+word)}</tspan>')
            else:
                spans.append(esc(pre + word))
        svg.append(f'<text x="{x}" y="{yy:.1f}" font-family="{SANS}" font-size="{size}" '
                   f'font-weight="{weight}" fill="{color}" text-anchor="{anchor}" '
                   f'xml:space="preserve">{"".join(spans)}</text>')
        yy += lh
    return "\n".join(svg), yy - y


def block_h(text, width, size, lh=None):
    return len(wrap(text, width, size)) * (lh or size * 1.48)


def card(x, y, w, h, fill=CARD, stroke=LINE, r=10, sw=1, extra=""):
    return (f'<rect x="{x}" y="{y:.1f}" width="{w}" height="{h:.1f}" rx="{r}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}" {extra}/>')


def pill(x, y, label, color):
    tw = len(label) * 6.4 + 20
    return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{tw:.1f}" height="21" rx="10.5" '
            f'fill="{color}14" stroke="{color}55" stroke-width="1"/>'
            f'<text x="{x+tw/2:.1f}" y="{y+14.5:.1f}" font-family="{SANS}" font-size="11" '
            f'font-weight="600" fill="{color}" text-anchor="middle">{esc(label)}</text>'), tw


def label(x, y, t, color=FAINT, size=10.5):
    return (f'<text x="{x}" y="{y:.1f}" font-family="{SANS}" font-size="{size}" font-weight="700" '
            f'fill="{color}" letter-spacing="1.3">{esc(t.upper())}</text>')


def arrow(x1, y1, x2, y2, color=SLATE, dash=None, marker="a", wdt=1.6):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<path d="M {x1:.1f} {y1:.1f} L {x2:.1f} {y2:.1f}" fill="none" stroke="{color}" '
            f'stroke-width="{wdt}"{d} marker-end="url(#{marker}-{color[1:]})"/>')


def path(d, color=SLATE, dash=None, marker="a", wdt=1.6):
    ds = f' stroke-dasharray="{dash}"' if dash else ""
    m = f' marker-end="url(#{marker}-{color[1:]})"' if marker else ""
    return f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{wdt}"{ds}{m}/>'


# --------------------------------------------------------------------------
# Row renderers.  Each returns (svg, height) and is laid out at a given y.
# --------------------------------------------------------------------------

PADX, PADY = 18, 16


def step_row(y, n, name, does, tags, why, accent=SLATE, badge=None):
    iw = STEP_W - 2 * PADX
    ww = WHY_W - 2 * PADX - 6

    title_h = 22
    does_h = block_h(does, iw, 13)
    tag_h = 30 if tags else 0
    step_h = PADY + title_h + 8 + does_h + tag_h + PADY

    why_h = PADY + 16 + block_h(why, ww, 13) + PADY - 2
    h = max(step_h, why_h)

    s = [card(STEP_X, y, STEP_W, h),
         f'<rect x="{STEP_X}" y="{y:.1f}" width="4" height="{h:.1f}" rx="2" fill="{accent}"/>']

    # number badge
    cy = y + PADY + 11
    s.append(f'<circle cx="{STEP_X+PADX+11}" cy="{cy:.1f}" r="11" fill="{accent}"/>')
    s.append(f'<text x="{STEP_X+PADX+11}" y="{cy+4:.1f}" font-family="{SANS}" font-size="12" '
             f'font-weight="700" fill="#fff" text-anchor="middle">{n}</text>')
    s.append(f'<text x="{STEP_X+PADX+30}" y="{cy+5:.1f}" font-family="{MONO}" font-size="15.5" '
             f'font-weight="700" fill="{INK}">{esc(name)}</text>')
    if badge:
        bx = STEP_X + PADX + 30 + len(name) * 9.6 + 10
        p, _ = pill(bx, y + PADY + 1, badge, TEAL)
        s.append(p)

    ty = y + PADY + title_h + 8 + 13
    blk, _ = text_block(STEP_X + PADX, ty, does, iw, 13, MUTED)
    s.append(blk)

    if tags:
        tx = STEP_X + PADX
        tagy = y + step_h - PADY - 21
        for t, c in tags:
            p, tw = pill(tx, tagy, t, c)
            s.append(p)
            tx += tw + 7

    s.append(card(WHY_X, y, WHY_W, h, fill=WHY_BG, stroke="none"))
    s.append(f'<rect x="{WHY_X}" y="{y:.1f}" width="3" height="{h:.1f}" fill="{WHY_RULE}"/>')
    s.append(label(WHY_X + PADX + 6, y + PADY + 10, "why"))
    blk, _ = text_block(WHY_X + PADX + 6, y + PADY + 30, why, ww, 13, "#4a4a50")
    s.append(blk)
    return "\n".join(s), h


def note_row(y, title, body, color=TEAL, x=STEP_X, w=CONTENT_R - STEP_X, dashed=True):
    iw = w - 2 * PADX
    h = PADY + (18 if title else 0) + block_h(body, iw, 13) + PADY
    s = [card(x, y, w, h, fill=f"{color}0b", stroke=f"{color}66", sw=1.3,
              extra='stroke-dasharray="6 4"' if dashed else "")]
    yy = y + PADY + 12
    if title:
        s.append(f'<text x="{x+PADX}" y="{yy:.1f}" font-family="{SANS}" font-size="12.5" '
                 f'font-weight="700" fill="{color}">{esc(title)}</text>')
        yy += 19
    blk, _ = text_block(x + PADX, yy + 1, body, iw, 13, "#4a4a50")
    s.append(blk)
    return "\n".join(s), h


def decision_row(y, arms, why):
    ww = WHY_W - 2 * PADX - 6
    arm_h = 21
    dec_h = PADY + 20 + 8 + len(arms) * arm_h + PADY - 4
    why_h = PADY + 16 + block_h(why, ww, 13) + PADY - 2
    h = max(dec_h, why_h)

    s = [card(STEP_X, y, STEP_W, h, fill="#fdf8ec", stroke="#e0cf9e", sw=1.4)]
    s.append(f'<text x="{STEP_X+PADX}" y="{y+PADY+13:.1f}" font-family="{MONO}" font-size="14.5" '
             f'font-weight="700" fill="{AMBER}">_route_after_validation()</text>')
    yy = y + PADY + 20 + 18
    for cond, dest, col in arms:
        s.append(f'<circle cx="{STEP_X+PADX+5}" cy="{yy-4:.1f}" r="4" fill="{col}"/>')
        s.append(f'<text x="{STEP_X+PADX+18}" y="{yy:.1f}" font-family="{SANS}" font-size="12.5" '
                 f'fill="{MUTED}">{esc(cond)}</text>')
        s.append(f'<text x="{STEP_X+STEP_W-PADX}" y="{yy:.1f}" font-family="{MONO}" font-size="12.5" '
                 f'font-weight="700" fill="{col}" text-anchor="end">{esc(dest)}</text>')
        yy += arm_h
    s.append(card(WHY_X, y, WHY_W, h, fill=WHY_BG, stroke="none"))
    s.append(f'<rect x="{WHY_X}" y="{y:.1f}" width="3" height="{h:.1f}" fill="{WHY_RULE}"/>')
    s.append(label(WHY_X + PADX + 6, y + PADY + 10, "why"))
    blk, _ = text_block(WHY_X + PADX + 6, y + PADY + 30, why, ww, 13, "#4a4a50")
    s.append(blk)
    return "\n".join(s), h


def branch_row(y, left, right, why):
    """Two terminal nodes side by side inside the step column."""
    bw = (STEP_W - 14) / 2
    ww = WHY_W - 2 * PADX - 6
    inner = bw - 2 * 14

    def one_h(b):
        return 14 + 20 + 6 + block_h(b["does"], inner, 12.5) + (26 if b.get("tag") else 0) + 14

    bh = max(one_h(left), one_h(right))
    why_h = PADY + 16 + block_h(why, ww, 13) + PADY - 2
    h = max(bh, why_h)

    s = []
    for i, b in enumerate((left, right)):
        bx = STEP_X + i * (bw + 14)
        col = b["color"]
        s.append(card(bx, y, bw, bh, fill=f"{col}0c", stroke=f"{col}55", sw=1.4))
        s.append(f'<text x="{bx+14}" y="{y+14+15:.1f}" font-family="{MONO}" font-size="14" '
                 f'font-weight="700" fill="{col}">{esc(b["name"])}</text>')
        blk, _ = text_block(bx + 14, y + 14 + 20 + 6 + 12, b["does"], inner, 12.5, MUTED)
        s.append(blk)
        if b.get("tag"):
            p, _ = pill(bx + 14, y + bh - 14 - 21, b["tag"], col)
            s.append(p)
    s.append(card(WHY_X, y, WHY_W, h, fill=WHY_BG, stroke="none"))
    s.append(f'<rect x="{WHY_X}" y="{y:.1f}" width="3" height="{h:.1f}" fill="{WHY_RULE}"/>')
    s.append(label(WHY_X + PADX + 6, y + PADY + 10, "why"))
    blk, _ = text_block(WHY_X + PADX + 6, y + PADY + 30, why, ww, 13, "#4a4a50")
    s.append(blk)
    return "\n".join(s), h, bw


# --------------------------------------------------------------------------
# Deployment band (hub and spoke) and startup chain
# --------------------------------------------------------------------------

SIDE_W, HUB_W = 258, 320
HUB_X = (W - HUB_W) // 2
LEFT_X, RIGHT_X = MARGIN, CONTENT_R - SIDE_W


def side_box(x, y, b):
    iw = SIDE_W - 28
    h = 14 + 18 + 4 + block_h(b["desc"], iw, 11.5, 16) + 20 + 14
    col = b["color"]
    s = [card(x, y, SIDE_W, h, fill=f"{col}0a", stroke=f"{col}55", sw=1.3)]
    s.append(f'<text x="{x+14}" y="{y+14+13:.1f}" font-family="{MONO}" font-size="12.5" '
             f'font-weight="700" fill="{col}">{esc(b["name"])}</text>')
    blk, _ = text_block(x + 14, y + 14 + 18 + 4 + 10, b["desc"], iw, 11.5, MUTED, lh=16)
    s.append(blk)
    s.append(f'<text x="{x+14}" y="{y+h-14:.1f}" font-family="{MONO}" font-size="10.5" '
             f'fill="{FAINT}">{esc(b["link"])}</text>')
    return "\n".join(s), h


def deployment(y, hub, lefts, rights):
    s = [label(MARGIN, y, "deployment — what runs where")]
    top = y + 20

    def stack(x, boxes):
        out, yy, geo = [], top, []
        for b in boxes:
            svg, h = side_box(x, yy, b)
            out.append(svg)
            geo.append((yy, h, b["color"]))
            yy += h + 16
        return "\n".join(out), yy - top, geo

    lsvg, lh, lgeo = stack(LEFT_X, lefts)
    rsvg, rh, rgeo = stack(RIGHT_X, rights)

    iw = HUB_W - 32
    hub_h = 18 + 20 + 6 + block_h(hub["desc"], iw, 12, 17) + 18
    band = max(lh, rh, hub_h)
    hub_y = top + (band - hub_h) / 2

    s += [lsvg, rsvg]
    s.append(card(HUB_X, hub_y, HUB_W, hub_h, fill="#eef1f6", stroke=SLATE, sw=1.8, r=12))
    s.append(f'<text x="{HUB_X+16}" y="{hub_y+18+15:.1f}" font-family="{MONO}" font-size="14" '
             f'font-weight="700" fill="{SLATE}">{esc(hub["name"])}</text>')
    blk, _ = text_block(HUB_X + 16, hub_y + 18 + 20 + 6 + 11, hub["desc"], iw, 12, MUTED, lh=17)
    s.append(blk)

    hub_mid = hub_y + hub_h / 2
    for yy, h, col in lgeo:
        s.append(path(f"M {HUB_X-6} {hub_mid:.1f} L {LEFT_X+SIDE_W+10:.1f} {yy+h/2:.1f}",
                      color=col, wdt=1.5))
    for yy, h, col in rgeo:
        s.append(path(f"M {HUB_X+HUB_W+6} {hub_mid:.1f} L {RIGHT_X-10:.1f} {yy+h/2:.1f}",
                      color=col, wdt=1.5))
    return "\n".join(s), 20 + band


def startup(y, steps, fail):
    s = [label(MARGIN, y, "startup — before the graph runs")]
    top = y + 20
    n = len(steps)
    gap = 46
    bw = (CONTENT_R - MARGIN - (n - 1) * gap) / n
    iw = bw - 26
    bh = max(12 + 17 + 4 + block_h(d, iw, 11.5, 16) + 12 for _, d in steps)
    for i, (name, desc) in enumerate(steps):
        x = MARGIN + i * (bw + gap)
        s.append(card(x, top, bw, bh))
        s.append(f'<text x="{x+13}" y="{top+12+13:.1f}" font-family="{MONO}" font-size="12.5" '
                 f'font-weight="700" fill="{INK}">{esc(name)}</text>')
        blk, _ = text_block(x + 13, top + 12 + 17 + 4 + 10, desc, iw, 11.5, MUTED, lh=16)
        s.append(blk)
        if i < n - 1:
            s.append(arrow(x + bw + 8, top + bh / 2, x + bw + gap - 8, top + bh / 2))
    fx = MARGIN + 2 * (bw + gap)
    fy = top + bh + 22
    fsvg, fh = note_row(fy, None, fail, color=RED, x=fx, w=CONTENT_R - fx, dashed=False)
    s.append(path(f"M {fx+bw/2:.1f} {top+bh+2:.1f} L {fx+bw/2:.1f} {fy-4:.1f}", color=RED, wdt=1.6))
    s.append(fsvg)
    return "\n".join(s), 20 + bh + 22 + fh


# --------------------------------------------------------------------------
# Pipeline assembly
# --------------------------------------------------------------------------

def pipeline(y, rows, knowledge_from=None, knowledge_to=()):
    s = [label(MARGIN, y, "the pipeline — a compiled langgraph stategraph")]
    yy = y + 26
    s.append(f'<text x="{MARGIN}" y="{yy:.1f}" font-family="{MONO}" font-size="11.5" fill="{FAINT}">'
             f'state: AgentState (TypedDict) &#183; every node returns a partial update</text>')
    yy += 22

    drawn: list[str] = []
    pos, prev, cx = {}, None, STEP_X + STEP_W / 2
    s.append(f'<circle cx="{cx}" cy="{yy+9:.1f}" r="9" fill="{SLATE}"/>')
    s.append(f'<text x="{cx+16}" y="{yy+13:.1f}" font-family="{MONO}" font-size="12" '
             f'font-weight="700" fill="{SLATE}">START</text>')
    prev = yy + 18
    yy += 34

    for r in rows:
        k = r["kind"]
        if k == "step":
            svg, h = step_row(yy, r["n"], r["name"], r["does"], r.get("tags", []),
                              r["why"], r.get("accent", SLATE), r.get("badge"))
            drawn.append(r["name"])
        elif k == "note":
            svg, h = note_row(yy, r.get("title"), r["body"], r.get("color", TEAL),
                              w=r.get("w", CONTENT_R - STEP_X))
        elif k == "decision":
            svg, h = decision_row(yy, r["arms"], r["why"])
        elif k == "branch":
            svg, h, bw = branch_row(yy, r["left"], r["right"], r["why"])
            drawn += [r["left"]["name"], r["right"]["name"]]
            r["_bw"] = bw
        s.append(arrow(cx, prev, cx, yy - 5))
        s.append(svg)
        pos[r["id"]] = (yy, h)
        prev, yy = yy + h + 2, yy + h + 34

    # END
    br = next((r for r in rows if r["kind"] == "branch"), None)
    if br:
        by, bh = pos[br["id"]]
        bw = br["_bw"]
        for i in range(2):
            s.append(path(f"M {STEP_X+i*(bw+14)+bw/2:.1f} {by+bh+2:.1f} "
                          f"L {STEP_X+i*(bw+14)+bw/2:.1f} {yy-14:.1f} L {cx:.1f} {yy-14:.1f} "
                          f"L {cx:.1f} {yy-4:.1f}", color=SLATE, wdt=1.5))
    s.append(f'<circle cx="{cx}" cy="{yy+9:.1f}" r="9" fill="none" stroke="{SLATE}" stroke-width="2.5"/>')
    s.append(f'<circle cx="{cx}" cy="{yy+9:.1f}" r="4" fill="{SLATE}"/>')
    s.append(f'<text x="{cx+16}" y="{yy+13:.1f}" font-family="{MONO}" font-size="12" '
             f'font-weight="700" fill="{SLATE}">END</text>')
    yy += 30

    # retry rail (control flow), decision -> generate_sql
    dec = next((r for r in rows if r["kind"] == "decision"), None)
    if dec:
        dy, dh = pos[dec["id"]]
        gy, gh = pos["generate_sql"]
        s.append(path(f"M {STEP_X-4} {dy+dh/2:.1f} L {RAIL_X} {dy+dh/2:.1f} "
                      f"L {RAIL_X} {gy+gh/2:.1f} L {STEP_X-8} {gy+gh/2:.1f}",
                      color=RED, dash="7 5", wdt=1.7))
        my = (dy + dh / 2 + gy + gh / 2) / 2
        s.append(f'<text x="{RAIL_X-9}" y="{my:.1f}" font-family="{SANS}" font-size="11.5" '
                 f'font-weight="700" fill="{RED}" text-anchor="middle" '
                 f'transform="rotate(-90 {RAIL_X-9} {my:.1f})">retry &#183; up to --max-attempts</text>')

    # knowledge rail (data flow), retrieve_knowledge -> its three consumers
    if knowledge_from:
        ky, kh = pos[knowledge_from]
        s.append(path(f"M {STEP_X+STEP_W+4} {ky+kh/2:.1f} L {KFLOW_X} {ky+kh/2:.1f}",
                      color=TEAL, dash="5 4", wdt=1.7, marker=None))
        last = max(pos[t][0] + pos[t][1] / 2 for t in knowledge_to)
        s.append(path(f"M {KFLOW_X} {ky+kh/2:.1f} L {KFLOW_X} {last:.1f}",
                      color=TEAL, dash="5 4", wdt=1.7, marker=None))
        for t in knowledge_to:
            ty, th = pos[t]
            s.append(arrow(KFLOW_X, ty + th / 2, STEP_X + STEP_W + 6, ty + th / 2,
                           color=TEAL, dash="5 4", wdt=1.7))
        mid = (ky + kh / 2 + last) / 2
        s.append(f'<text x="{KFLOW_X+15}" y="{mid:.1f}" font-family="{SANS}" font-size="11.5" '
                 f'font-weight="700" fill="{TEAL}" text-anchor="middle" '
                 f'transform="rotate(-90 {KFLOW_X+15} {mid:.1f})">state.knowledge &#8594; 3 consumers</text>')
    return "\n".join(s), yy - y, drawn


def outcomes(y, items, note):
    s = [label(MARGIN, y, "outcomes")]
    top = y + 20
    gap, n = 20, len(items)
    bw = (CONTENT_R - MARGIN - (n - 1) * gap) / n
    iw = bw - 26
    bh = max(12 + 17 + 4 + block_h(d, iw, 11.5, 16) + 12 for _, d, _ in items)
    for i, (code, desc, col) in enumerate(items):
        x = MARGIN + i * (bw + gap)
        s.append(card(x, top, bw, bh, fill=f"{col}0c", stroke=f"{col}55", sw=1.3))
        s.append(f'<text x="{x+13}" y="{top+12+13:.1f}" font-family="{MONO}" font-size="12.5" '
                 f'font-weight="700" fill="{col}">{esc(code)}</text>')
        blk, _ = text_block(x + 13, top + 12 + 17 + 4 + 10, desc, iw, 11.5, MUTED, lh=16)
        s.append(blk)
    ny = top + bh + 14
    blk, nh = text_block(MARGIN, ny + 11, note, CONTENT_R - MARGIN, 12, MUTED)
    s.append(blk)
    return "\n".join(s), 20 + bh + 14 + nh + 11


def legend(y, items):
    s = [f'<line x1="{MARGIN}" y1="{y:.1f}" x2="{CONTENT_R}" y2="{y:.1f}" stroke="{LINE}" stroke-width="1"/>']
    x, yy = MARGIN, y + 24
    for kind, text, col in items:
        if kind == "line":
            s.append(f'<line x1="{x}" y1="{yy-4:.1f}" x2="{x+24}" y2="{yy-4:.1f}" stroke="{col}" '
                     f'stroke-width="2" stroke-dasharray="6 4"/>')
        elif kind == "solid":
            s.append(f'<line x1="{x}" y1="{yy-4:.1f}" x2="{x+24}" y2="{yy-4:.1f}" stroke="{col}" stroke-width="2"/>')
        else:
            s.append(f'<rect x="{x}" y="{yy-12:.1f}" width="24" height="13" rx="6.5" '
                     f'fill="{col}22" stroke="{col}77"/>')
        s.append(f'<text x="{x+32}" y="{yy:.1f}" font-family="{SANS}" font-size="11.5" '
                 f'fill="{MUTED}">{esc(text)}</text>')
        x += 32 + len(text) * 6.1 + 34
    return "\n".join(s), 24 + 14


def document(title, subtitle, body, height, nodes):
    cols = [SLATE, RED, TEAL, BLUE, PURPLE, AMBER, "#0f7268", "#2f5d8f"]
    marks = "\n".join(
        f'<marker id="a-{c[1:]}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6.5" '
        f'markerHeight="6.5" orient="auto-start-reverse">'
        f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{c}"/></marker>' for c in dict.fromkeys(cols))
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{height:.0f}" viewBox="0 0 {W} {height:.0f}" role="img" aria-label="{esc(title)}">
<title>{esc(title)}</title>
<desc>{esc(subtitle)}</desc>
<metadata id="graph-nodes">{esc(" ".join(nodes))}</metadata>
<defs>{marks}</defs>
<rect width="{W}" height="{height:.0f}" fill="{BG}"/>
{body}
</svg>
'''


# --------------------------------------------------------------------------
# Content
# --------------------------------------------------------------------------

PG = {"name": "nl2sql-postgres", "color": BLUE,
      "desc": "Postgres 18. The synthetic retail dataset — 19 tables, ~2M rows — is baked into the "
              "image at build time, so it is queryable the moment the container starts.",
      "link": "← SQLAlchemy + psycopg"}

OLLAMA_CHAT = {"name": "Ollama — chat model", "color": PURPLE,
               "desc": "qwen3.8:latest on a remote host. Picks the tables, writes the SQL, and reviews it. "
                       "Reasoning off by default: Qwen3 returns it separately, so disabling it costs "
                       "no quality and saves most of the latency.",
               "link": "→ 3 prompts per attempt"}

STARTUP = [
    ("parse_args()", "CLI flags, each defaulting to the matching environment variable."),
    ("Settings.from_env()", "One dataclass holding every knob, so the same image runs anywhere."),
    ("build_llm()", "validate_model_on_init=True — reach Ollama and confirm the model exists."),
    ("Nl2SqlAgent()", "Open the database, build the tools, compile the StateGraph."),
]

FAIL2 = ("Unreachable host or a model that is not pulled raises LlmUnavailableError and exits 2 with "
         "the available model list. The preflight sits here deliberately: a wrong URL is the most common "
         "misconfiguration, and discovering it three nodes into the pipeline costs a catalog read and two "
         "prompts to learn what one HTTP call already knew.")

SELECT_WHY = ("The full schema with sample rows is far larger than the context window, so narrowing first "
              "is what makes the next step fit at all. Filtering the model's answer against the live "
              "catalog is what stops a hallucinated table name from ever reaching SQL generation, and "
              "falling back to every table means a bad pick degrades to slow-but-correct rather than to "
              "no answer at all.")

FETCH_WHY = ("Declared keys are what let the model join on the real relationship instead of guessing from "
             "column names that happen to match. The sample rows carry what a schema cannot: how a "
             "department name is actually spelled, what a date column really looks like — the details "
             "that decide whether a WHERE clause matches anything. COMMENT ON text, if the schema has any, "
             "arrives through the same path with no code change.")

GEN_WHY = ("This node, not the top of the graph, is the retry target. A rejected attempt re-enters here "
           "with the schema already in state, so a correction costs one prompt instead of a whole "
           "pipeline — and the model is shown the exact problems rather than simply being asked again.")

VAL_WHY = ("Three filters, ordered by cost. The static check is free and refuses the dangerous shapes "
           "outright. EXPLAIN then catches the common failure — an unknown column, a type mismatch "
           "— without executing anything, and a planner error is definitive, so the model is never "
           "consulted about a query the database has already refused. Only SQL that both parses and plans "
           "is worth spending a review on.")

ROUTE_WHY = ("Bounded on purpose. Some questions the data simply cannot answer, and retrying one of those "
             "forever spends tokens without converging. Three attempts covers the usual case — a wrong "
             "join key, a missing GROUP BY — which the model generally fixes on the second.")

TERM_WHY = ("execute_query is the second, independent safety layer, and the one that actually holds: the "
            "static check in step 4 cannot reject WITH d AS (DELETE ... RETURNING *) SELECT * FROM d, "
            "because that is a legitimate WITH query. The READ ONLY transaction is what refuses it. "
            "give_up is a real outcome rather than a failure of nerve — the alternative is running a "
            "query already known to be wrong. Together they are why \"delete every row from dim_store\" is "
            "safe to ask: it is refused, not attempted.")

ARMS = [("no issues", "execute_query", "#2e7d4f"),
        ("issues, attempts < max", "generate_sql", RED),
        ("issues, attempts = max", "give_up", AMBER)]

TERM_L = {"name": "execute_query", "color": "#2e7d4f",
          "does": "run_select() inside SET TRANSACTION READ ONLY with SET LOCAL statement_timeout, "
                  "fetching max_rows + 1 so truncation is detected rather than guessed at.",
          "tag": "Postgres · READ ONLY tx"}
TERM_R = {"name": "give_up", "color": AMBER,
          "does": "Record \"Could not produce a valid query in N attempts\" together with the last set "
                  "of problems. Nothing is executed.",
          "tag": "no database access"}

OUTCOMES = [("exit 0", "Answered. The result table goes to stdout.", "#2e7d4f"),
            ("exit 1", "Ran, but could not answer — give_up, or the query failed at execution.", AMBER),
            ("exit 2", "Ollama misconfigured. Raised during startup; the graph never runs.", RED)]

OUT_NOTE = ("Progress lines go to stderr and the answer to stdout, so `... 2>/dev/null` leaves just the "
            "result and `--json` replaces it with one machine-readable object. Because the exit code "
            "distinguishes \"could not answer\" from \"misconfigured\", the agent composes into a shell "
            "pipeline without parsing its output.")


def build_v1():
    parts, y = [], 56
    parts.append(f'<text x="{MARGIN}" y="{y}" font-family="{SANS}" font-size="27" font-weight="700" '
                 f'fill="{INK}">NL2SQL Agent v1 <tspan fill="{MUTED}" font-weight="400">— schema-only pipeline</tspan></text>')
    y += 26
    blk, h = text_block(MARGIN, y, "What each step does, why it is there, and how control flows. "
                        "Five steps, three of which call the model. The agent holds no state between "
                        "questions: every run starts from START with nothing but the question.",
                        CONTENT_R - MARGIN, 13.5, MUTED)
    parts.append(blk); y += h + 26

    svg, h = deployment(y, {"name": "nl2sql-agent:v1",
                            "desc": "python -m nl2sql_agent. Started per question by "
                                    "docker compose run --rm agent and removed when it exits."},
                        [PG], [OLLAMA_CHAT]); parts.append(svg); y += h + 36
    svg, h = startup(y, STARTUP, FAIL2); parts.append(svg); y += h + 40

    rows = [
        {"id": "select_tables", "kind": "step", "n": 1, "name": "select_tables",
         "does": "Ask the model which tables the question needs, from a compact catalog of every table: "
                 "name, description, column list and approximate row count. Keep only names that exist "
                 "in the live catalog; fall back to all tables if none survive.",
         "tags": [("LLM · TableSelection", PURPLE), ("Postgres · pg_class", BLUE)],
         "why": SELECT_WHY},
        {"id": "fetch_schema", "kind": "step", "n": 2, "name": "fetch_schema",
         "does": "Pull full detail for the chosen tables only: column types, NOT NULL, primary and foreign "
                 "key definitions, any COMMENT ON text, and 3 sample rows per table.",
         "tags": [("Postgres · pg_attribute, pg_constraint", BLUE)], "why": FETCH_WHY},
        {"id": "generate_sql", "kind": "step", "n": 3, "name": "generate_sql",
         "does": "Ask the model for one SELECT and nothing else. strip_sql() removes any markdown fence "
                 "and trailing semicolon regardless. On a retry, RETRY_FEEDBACK prepends the rejected SQL "
                 "and the specific problems found with it.",
         "tags": [("LLM · free-form", PURPLE)], "why": GEN_WHY},
        {"id": "validate_sql", "kind": "step", "n": 4, "name": "validate_sql",
         "does": "Three checks, cheapest first. (a) ensure_read_only: a single statement starting with "
                 "SELECT or WITH, no embedded semicolon. (b) EXPLAIN it — planned, not run. "
                 "(c) Only then a model review for semantics: right join key, right aggregation, does it "
                 "actually answer the question.",
         "tags": [("Postgres · EXPLAIN", BLUE), ("LLM · SqlReview", PURPLE)], "why": VAL_WHY},
        {"id": "route", "kind": "decision", "arms": ARMS, "why": ROUTE_WHY},
        {"id": "terminals", "kind": "branch", "left": TERM_L, "right": TERM_R, "why": TERM_WHY},
    ]
    svg, h, nodes = pipeline(y, rows); parts.append(svg); y += h + 34
    svg, h = outcomes(y, OUTCOMES, OUT_NOTE); parts.append(svg); y += h + 26
    svg, h = legend(y, [("solid", "control flow", SLATE), ("line", "retry loop", RED),
                        ("pill", "model call", PURPLE), ("pill", "database access", BLUE)])
    parts.append(svg); y += h
    return document("NL2SQL Agent v1 architecture", "Schema-only pipeline",
                    "\n".join(parts), y + 34, nodes)


VECTORDB = {"name": "nl2sql-vectordb", "color": TEAL,
            "desc": "pgvector. One collection per document in knowledge/ — 3 collections, 53 chunks "
                    "— each row a section with its bge-m3 embedding, heading path and chunk_meta. "
                    "HNSW index, vector_cosine_ops.",
            "link": "← cosine search, one query per collection"}

OLLAMA_EMBED = {"name": "Ollama — embedding model", "color": TEAL,
                "desc": "bge-m3 on the machine running Docker, reached as host.docker.internal via "
                        "extra_hosts: host-gateway. 1024 dimensions.",
                "link": "→ one embed_query per question"}

TWO_HOSTS = ("The chat model and the embedding model are configured separately — OLLAMA_BASE_URL "
             "against EMBED_BASE_URL — because they genuinely live in different places: the chat "
             "model on a remote box, the embedding model on the Docker host. The split is not merely "
             "convenience. Queries must be embedded with the same model the store was built with, or "
             "the question vector lands in a different space and retrieval returns confident nonsense; "
             "keeping the setting separate is what makes that pinnable, and a live test checks the "
             "store's recorded embedding_model against the configured one.")

RETRIEVE_WHY = ("A schema cannot say that fiscal year 2024 begins in April 2023, that a column is already "
                "aggregated, or that a join fans out five ways. Those facts live in knowledge/, and this "
                "is where they enter the pipeline. Searching every collection on one embedding is "
                "deliberate: a single question then picks up DDL detail, data-dictionary context and "
                "business rules together, rather than whichever source was guessed at. Collections are "
                "discovered from pg_catalog rather than hardcoded, so adding a document to the RAG "
                "pipeline makes it searchable with no change here — which is also why the discovered "
                "name is checked against a strict identifier pattern before it is interpolated into SQL.")

DEGRADE = ("search_knowledge catches KnowledgeUnavailableError and returns an empty context with the "
           "reason attached; the node records it in state.retrieval_error and the graph continues to "
           "select_tables. knowledge_block(\"\") renders as nothing at all, so the prompts lose the block "
           "rather than gaining an empty heading — which makes the degraded run byte-for-byte the v1 "
           "prompt. This is the whole design stance on retrieval: an unreachable vector store or "
           "embedding host makes the agent worse, never broken. It cannot fail a question that v1 would "
           "have answered.")

SELECT_WHY_V2 = (SELECT_WHY + " v2 adds one thing: table names the retrieved chunks explicitly document "
                 "are merged into the model's selection rather than replacing it. A chunk about "
                 "fact_market_share_weekly is strong evidence that table matters, but the model may still "
                 "have correctly spotted a dimension the chunks never mention — the union keeps both "
                 "signals.")

GEN_WHY_V2 = (GEN_WHY + " The knowledge block is marked authoritative over the model's own assumptions, "
              "because that is the entire point: where the retrieved text and the model's prior disagree, "
              "the text wins. Left to itself the model will SUM the sales column on "
              "fact_market_share_weekly; the business index is what says that column repeats once per "
              "competitor.")

VAL_WHY_V2 = (VAL_WHY + " v2 hands the reviewer the same knowledge block the writer had. Without it the "
              "reviewer would approve a query that is valid SQL, plans without error, and quietly answers "
              "the wrong question — giving both steps the same context is what turns a violated "
              "business rule into a reported problem instead of a pass.")

PAYOFF = ("\"What is our overall market share across all tracked products in fiscal year 2024?\"  —  "
          "v1, schema only, answers 1.0754: 107.5%, because it sums grocer_sales_amount across the five "
          "competitor rows present in every cell. v2 retrieves \"Market share fan-out: the five-row "
          "trap\", de-duplicates per (week, product, region) cell, and answers 0.2151 — 21.5% — "
          "correct on the first attempt. Same model, same database, same prompt budget. The only "
          "difference is what the model was told. Run --json to see which chunks shaped an answer: every "
          "chunk_id, source document and cosine distance is reported.")


def build_v2():
    parts, y = [], 56
    parts.append(f'<text x="{MARGIN}" y="{y}" font-family="{SANS}" font-size="27" font-weight="700" '
                 f'fill="{INK}">NL2SQL Agent v2 <tspan fill="{MUTED}" font-weight="400">— retrieval-augmented pipeline</tspan></text>')
    y += 26
    blk, h = text_block(MARGIN, y, "The v1 pipeline with a retrieval step in front of it and that step's "
                        "context threaded into three of the five that follow. Everything marked in teal "
                        "is new or changed; everything else behaves exactly as v1 does.",
                        CONTENT_R - MARGIN, 13.5, MUTED)
    parts.append(blk); y += h + 26

    svg, h = deployment(y, {"name": "nl2sql-agent:v2",
                            "desc": "python -m nl2sql_agent. Started per question by "
                                    "docker compose run --rm agent, after compose has both databases "
                                    "passing their health checks."},
                        [PG, VECTORDB], [OLLAMA_CHAT, OLLAMA_EMBED])
    parts.append(svg); y += h + 18
    svg, h = note_row(y, "Two models, two hosts", TWO_HOSTS, color=TEAL, x=MARGIN,
                      w=CONTENT_R - MARGIN); parts.append(svg); y += h + 36
    svg, h = startup(y, STARTUP, FAIL2); parts.append(svg); y += h + 40

    rows = [
        {"id": "retrieve_knowledge", "kind": "step", "n": 0, "name": "retrieve_knowledge",
         "badge": "new in v2", "accent": TEAL,
         "does": "Embed the question once with bge-m3, then cosine-search every collection: "
                 "ORDER BY embedding <=> $vec LIMIT k, merged across collections and re-sorted by "
                 "distance. Produces the context block, the tables chunk_meta names, and the provenance "
                 "of each chunk. The vector crosses the wire as a pgvector text literal, so no "
                 "client-side vector type is needed.",
         "tags": [("Ollama · embed_query", TEAL), ("pgvector · <=> cosine", TEAL)],
         "why": RETRIEVE_WHY},
        {"id": "degrade", "kind": "note", "w": STEP_W, "color": AMBER,
         "title": "If retrieval fails — the run continues anyway",
         "body": "KnowledgeUnavailableError → state.retrieval_error set, knowledge = \"\", flow "
                 "proceeds to select_tables. " + DEGRADE},
        {"id": "select_tables", "kind": "step", "n": 1, "name": "select_tables",
         "badge": "changed", "accent": TEAL,
         "does": "Ask the model which tables the question needs, from the catalog plus the retrieved "
                 "knowledge. Keep only names that exist, then merge in any table the chunks explicitly "
                 "document that the model missed. Fall back to all tables if none survive.",
         "tags": [("LLM · TableSelection", PURPLE), ("Postgres · pg_class", BLUE),
                  ("+ chunk_meta.table", TEAL)],
         "why": SELECT_WHY_V2},
        {"id": "fetch_schema", "kind": "step", "n": 2, "name": "fetch_schema",
         "does": "Pull full detail for the chosen tables only: column types, NOT NULL, primary and foreign "
                 "key definitions, any COMMENT ON text, and 3 sample rows per table. Unchanged from v1.",
         "tags": [("Postgres · pg_attribute, pg_constraint", BLUE)], "why": FETCH_WHY},
        {"id": "generate_sql", "kind": "step", "n": 3, "name": "generate_sql",
         "badge": "changed", "accent": TEAL,
         "does": "Ask the model for one SELECT, given the schema and the knowledge block. strip_sql() "
                 "removes any fence and trailing semicolon. On a retry, RETRY_FEEDBACK prepends the "
                 "rejected SQL and the specific problems found with it.",
         "tags": [("LLM · free-form", PURPLE), ("+ knowledge", TEAL)], "why": GEN_WHY_V2},
        {"id": "validate_sql", "kind": "step", "n": 4, "name": "validate_sql",
         "badge": "changed", "accent": TEAL,
         "does": "Three checks, cheapest first. (a) ensure_read_only: a single statement starting with "
                 "SELECT or WITH, no embedded semicolon. (b) EXPLAIN it — planned, not run. (c) Only "
                 "then a model review, now against the knowledge block as well as the schema.",
         "tags": [("Postgres · EXPLAIN", BLUE), ("LLM · SqlReview", PURPLE),
                  ("+ knowledge", TEAL)], "why": VAL_WHY_V2},
        {"id": "route", "kind": "decision", "arms": ARMS, "why": ROUTE_WHY},
        {"id": "terminals", "kind": "branch", "left": TERM_L, "right": TERM_R, "why": TERM_WHY},
    ]
    svg, h, nodes = pipeline(y, rows, knowledge_from="retrieve_knowledge",
                              knowledge_to=("select_tables", "generate_sql", "validate_sql"))
    parts.append(svg); y += h + 34
    svg, h = outcomes(y, OUTCOMES, OUT_NOTE); parts.append(svg); y += h + 22
    svg, h = note_row(y, "What the retrieval buys", PAYOFF, color=TEAL, x=MARGIN,
                      w=CONTENT_R - MARGIN, dashed=False); parts.append(svg); y += h + 26
    svg, h = legend(y, [("solid", "control flow", SLATE), ("line", "retry loop", RED),
                        ("line", "knowledge (data flow)", TEAL), ("pill", "model call", PURPLE),
                        ("pill", "database access", BLUE), ("pill", "new or changed in v2", TEAL)])
    parts.append(svg); y += h
    return document("NL2SQL Agent v2 architecture", "Retrieval-augmented pipeline",
                    "\n".join(parts), y + 34, nodes)


def main() -> None:
    here = Path(__file__).resolve().parent
    for name, build in (("arch_v1.svg", build_v1), ("arch_v2.svg", build_v2)):
        svg = build()
        (here / name).write_text(svg)
        print(f"{name}: {len(svg) / 1024:.1f} KB")


if __name__ == "__main__":
    main()
