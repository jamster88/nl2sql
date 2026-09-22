#!/usr/bin/env python3
"""Regenerate arch_v1.svg, arch_v2.svg, arch_v3.svg and arch_v4.svg.

    python arch_diagrams/generate.py

Pure standard library, no arguments, writes every file next to this script.

The SVGs are computed rather than hand-placed: text is wrapped against real
Helvetica advance widths, each row takes the height its content needs, and the
rails are drawn only once the rows they connect have known positions. That is
the point of keeping this script -- editing a 60 KB SVG by hand to reword one
"why" card is not something anyone will do twice, whereas editing the string
in build_v2() below and re-running this takes a second.

Every diagram describes agent/nl2sql_agent/. v1 is the schema-only pipeline;
v2 adds retrieve_knowledge in front of it and threads that context into table
selection, SQL generation and validation; v3 adds the golden-pair ensemble;
v4 is the multi-agent restructure -- four stages, one shared state, one repair
loop. When graph.py gains or renames a node, or a step's rationale changes,
the content lives in the build_v*() functions at the bottom of this file --
everything above them is layout machinery, shared by all four.

The layout machinery only ever grows by optional arguments. A test compares
each committed SVG against what this file produces right now, so a helper
that changed shape for v4 would rewrite three older diagrams as a side
effect; every parameter added here therefore defaults to what v1-v3 already
drew.
"""
from __future__ import annotations

import sys
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
# v3's accent, for the golden-pair ensemble. Distinct from TEAL so a v3 diagram
# still reads which parts arrived with retrieval and which with the examples.
INDIGO = "#3a4b9c"
# v4's accent. A red-violet, which is the one hue slot nothing else here uses:
# against v2's teal and v3's indigo it says "new in v4" at a glance, and it is
# far enough from PURPLE (a blue-violet) that a model-call pill is never
# mistaken for a v4 badge.
MAGENTA = "#9b2f6b"
# The success green v1-v3 use as a literal. Named because v4 draws an arrow in
# it, and an arrowhead needs a marker declared for its colour.
GREEN = "#2e7d4f"

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


def step_row(y, n, name, does, tags, why, accent=SLATE, badge=None, badge_color=TEAL):
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
        p, _ = pill(bx, y + PADY + 1, badge, badge_color)
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


def decision_row(y, arms, why, title="_route_after_validation()"):
    ww = WHY_W - 2 * PADX - 6
    arm_h = 21
    dec_h = PADY + 20 + 8 + len(arms) * arm_h + PADY - 4
    why_h = PADY + 16 + block_h(why, ww, 13) + PADY - 2
    h = max(dec_h, why_h)

    s = [card(STEP_X, y, STEP_W, h, fill="#fdf8ec", stroke="#e0cf9e", sw=1.4)]
    s.append(f'<text x="{STEP_X+PADX}" y="{y+PADY+13:.1f}" font-family="{MONO}" font-size="14.5" '
             f'font-weight="700" fill="{AMBER}">{esc(title)}</text>')
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


def fan_row(y, n, boxes, caption, accent=SLATE):
    """One superstep: a bracket out to N parallel nodes and a bracket back in.

    The step column is 450px, which fits one node name at a readable size and
    not four, so this row spans the full content width -- which is also the
    honest thing for it to do, since it is the one row that is not a step.
    The incoming flow line enters at the step column's centre (cx) and the
    fan-in returns to it, so the row drops into pipeline()'s vertical run
    without the rest of the column moving.
    """
    w = CONTENT_R - STEP_X
    cx = STEP_X + STEP_W / 2
    gap = 16
    bw = (w - (len(boxes) - 1) * gap) / len(boxes)
    iw = bw - 28
    centres = [STEP_X + i * (bw + gap) + bw / 2 for i in range(len(boxes))]

    head = 34                      # badge, caption, and the fan-out bracket
    bh = max(12 + 17 + 4 + block_h(b["does"], iw, 11.5, 16) + 26 + 12 for b in boxes)
    foot = 30                      # the fan-in bracket
    h = head + bh + foot

    bar_y = y + head - 10
    top = y + head
    s = [f'<circle cx="{STEP_X+PADX+11}" cy="{y+13:.1f}" r="11" fill="{accent}"/>',
         f'<text x="{STEP_X+PADX+11}" y="{y+17:.1f}" font-family="{SANS}" font-size="12" '
         f'font-weight="700" fill="#fff" text-anchor="middle">{n}</text>',
         label(STEP_X + PADX + 30, y + 17, "one superstep", accent),
         f'<text x="{CONTENT_R-PADX}" y="{y+17:.1f}" font-family="{SANS}" font-size="11.5" '
         f'fill="{MUTED}" text-anchor="end">{esc(caption)}</text>']

    # fan-out: down from the flow line, across, and into each branch
    s.append(path(f"M {cx:.1f} {y:.1f} L {cx:.1f} {bar_y:.1f}", color=SLATE, wdt=1.6, marker=None))
    s.append(path(f"M {centres[0]:.1f} {bar_y:.1f} L {centres[-1]:.1f} {bar_y:.1f}",
                  color=SLATE, wdt=1.6, marker=None))
    for c in centres:
        s.append(arrow(c, bar_y, c, top - 4, color=SLATE, wdt=1.6))

    for i, b in enumerate(boxes):
        bx = STEP_X + i * (bw + gap)
        col = b["color"]
        s.append(card(bx, top, bw, bh, fill=f"{col}0a", stroke=f"{col}66", sw=1.4))
        s.append(f'<text x="{bx+14}" y="{top+12+14:.1f}" font-family="{MONO}" font-size="13" '
                 f'font-weight="700" fill="{col}">{esc(b["name"])}</text>')
        blk, _ = text_block(bx + 14, top + 12 + 17 + 4 + 10, b["does"], iw, 11.5, MUTED, lh=16)
        s.append(blk)
        p, _ = pill(bx + 14, top + bh - 12 - 21, b["tag"], col)
        s.append(p)

    # fan-in: out of each branch, across, and back to the flow line
    join_y = top + bh + 18
    for c in centres:
        s.append(path(f"M {c:.1f} {top+bh:.1f} L {c:.1f} {join_y:.1f}",
                      color=SLATE, wdt=1.6, marker=None))
    s.append(path(f"M {centres[0]:.1f} {join_y:.1f} L {centres[-1]:.1f} {join_y:.1f}",
                  color=SLATE, wdt=1.6, marker=None))
    s.append(path(f"M {cx:.1f} {join_y:.1f} L {cx:.1f} {y+h:.1f}",
                  color=SLATE, wdt=1.6, marker=None))
    return "\n".join(s), h


def offramp_row(y, name, does, tag, why, color=AMBER):
    """A terminal that leaves the column without interrupting it.

    Narrow enough to clear the flow line at cx, so the arrow from the row
    above continues past it to the next step rather than through it.
    """
    bw = 210
    iw = bw - 28
    ww = WHY_W - 2 * PADX - 6
    box_h = 14 + 18 + 6 + block_h(does, iw, 12, 16.5) + 26 + 14
    why_h = PADY + 16 + block_h(why, ww, 13) + PADY - 2
    h = max(box_h, why_h)

    s = [card(STEP_X, y, bw, box_h, fill=f"{color}0c", stroke=f"{color}55", sw=1.4),
         f'<text x="{STEP_X+14}" y="{y+14+14:.1f}" font-family="{MONO}" font-size="14" '
         f'font-weight="700" fill="{color}">{esc(name)}</text>']
    blk, _ = text_block(STEP_X + 14, y + 14 + 18 + 6 + 10, does, iw, 12, MUTED, lh=16.5)
    s.append(blk)
    p, _ = pill(STEP_X + 14, y + box_h - 14 - 21, tag, color)
    s.append(p)

    s.append(card(WHY_X, y, WHY_W, h, fill=WHY_BG, stroke="none"))
    s.append(f'<rect x="{WHY_X}" y="{y:.1f}" width="3" height="{h:.1f}" fill="{WHY_RULE}"/>')
    s.append(label(WHY_X + PADX + 6, y + PADY + 10, "why"))
    blk, _ = text_block(WHY_X + PADX + 6, y + PADY + 30, why, ww, 13, "#4a4a50")
    s.append(blk)
    return "\n".join(s), h


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

def pipeline(y, rows, knowledge_from=None, knowledge_to=(), rails=(),
             retry_label="retry &#183; up to --max-attempts",
             loop_from=(), loop_into=None):
    """Draw the node column, the retry rail, and any data-flow rails.

    A rail is {from, to, color, label, x, side}: where the data originates,
    which nodes consume it, and which vertical track to run it on. v1 has none,
    v2 has one (knowledge), v3 has two -- so the track position and the side the
    label sits on are per-rail rather than constants.

    `loop_from` names rows that hand their failure to the rail the decision row
    already runs on, and `loop_into` the row the rail arrows into. v4 has four
    failure sources and one Repair Agent spending one counter, and drawing that
    as one track rather than four is the point of the picture.
    """
    if knowledge_from:
        rails = (*rails, {"from": knowledge_from, "to": knowledge_to, "color": TEAL,
                          "label": "state.knowledge &#8594; 3 consumers",
                          "x": KFLOW_X, "side": 1})
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
                              r["why"], r.get("accent", SLATE), r.get("badge"),
                              r.get("badge_color", TEAL))
            drawn.append(r["name"])
        elif k == "note":
            svg, h = note_row(yy, r.get("title"), r["body"], r.get("color", TEAL),
                              w=r.get("w", CONTENT_R - STEP_X))
        elif k == "decision":
            svg, h = decision_row(yy, r["arms"], r["why"],
                                  r.get("title", "_route_after_validation()"))
        elif k == "branch":
            svg, h, bw = branch_row(yy, r["left"], r["right"], r["why"])
            drawn += [r["left"]["name"], r["right"]["name"]]
            r["_bw"] = bw
        elif k == "fan":
            svg, h = fan_row(yy, r["n"], r["boxes"], r["caption"], r.get("accent", SLATE))
            drawn += [b["name"] for b in r["boxes"]]
        elif k == "offramp":
            svg, h = offramp_row(yy, r["name"], r["does"], r["tag"], r["why"],
                                 r.get("color", AMBER))
            drawn.append(r["name"])
        # An incoming arrow is the column's flow, so a row may move it (an
        # off-ramp is entered from one side) or recolour it (v4's audit hands
        # its failure down, and that is not the happy path).
        flow = r.get("flow", {})
        fx = flow.get("x", cx)
        s.append(arrow(fx, prev, fx, yy - 5, color=flow.get("color", SLATE),
                       dash=flow.get("dash")))
        s.append(svg)
        pos[r["id"]] = (yy, h)
        if k == "offramp":
            # The column does not pass through it: the next row is still
            # drawn from the row above, so the flow line runs past its side.
            yy += h + 34
        else:
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

    # Failure sources feed the rail the retry runs back on -- one track.
    for src in loop_from:
        sy, sh = pos[src]
        s.append(path(f"M {STEP_X-4} {sy+sh/2:.1f} L {RAIL_X} {sy+sh/2:.1f}",
                      color=RED, dash="7 5", wdt=1.7, marker=None))
    if loop_into:
        iy, ih = pos[loop_into]
        s.append(arrow(RAIL_X, iy + ih / 2, STEP_X - 8, iy + ih / 2,
                       color=RED, dash="7 5", wdt=1.7))

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
                 f'transform="rotate(-90 {RAIL_X-9} {my:.1f})">{retry_label}</text>')

    # data-flow rails: a producing node out to each of its consumers
    for rail in rails:
        rx, col = rail["x"], rail["color"]
        dash = rail.get("dash", "5 4")
        ky, kh = pos[rail["from"]]
        s.append(path(f"M {STEP_X+STEP_W+4} {ky+kh/2:.1f} L {rx} {ky+kh/2:.1f}",
                      color=col, dash=dash, wdt=1.7, marker=None))
        last = max(pos[t][0] + pos[t][1] / 2 for t in rail["to"])
        s.append(path(f"M {rx} {ky+kh/2:.1f} L {rx} {last:.1f}",
                      color=col, dash=dash, wdt=1.7, marker=None))
        for t in rail["to"]:
            ty, th = pos[t]
            s.append(arrow(rx, ty + th / 2, STEP_X + STEP_W + 6, ty + th / 2,
                           color=col, dash=dash, wdt=1.7))
        mid = (ky + kh / 2 + last) / 2
        lx = rx + 15 * rail.get("side", 1)
        s.append(f'<text x="{lx}" y="{mid:.1f}" font-family="{SANS}" font-size="11.5" '
                 f'font-weight="700" fill="{col}" text-anchor="middle" '
                 f'transform="rotate(-90 {lx} {mid:.1f})">{rail["label"]}</text>')
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


def document(title, subtitle, body, height, nodes, extra_colors=()):
    # An arrow finds its head by colour, so a diagram that draws one in a
    # colour with no marker declared here renders the line and no head.
    cols = [SLATE, RED, TEAL, BLUE, PURPLE, AMBER, "#0f7268", "#2f5d8f", *extra_colors]
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
               "desc": "qwen3.8-256k on a remote host, served with a 256k context window. Picks the tables, "
                       "writes the SQL, and reviews it. "
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


# --- v3: the golden-pair ensemble -----------------------------------------

CHUNKDB = {"name": "nl2sql-chunkdb", "color": INDIGO,
           "desc": "Plain Postgres, the context store. golden_pairs holds the 45 question/SQL pairs "
                   "one row per pair — chunk_id, type, tables, keywords, question, reasoning_target, "
                   "sql_code, result — beside the BM25 term statistics over the keyword column and the "
                   "golden_pairs_bm25() function that ranks against them.",
           "link": "← BM25 ranking + row hydration"}

VECTORDB_V3 = {"name": "nl2sql-vectordb", "color": TEAL,
               "desc": "pgvector, now holding two stores. The knowledge collections as in v2 (3 "
                       "collections, 53 chunks), plus golden_pair_question_vectors and "
                       "golden_pair_reasoning_vectors — 45 rows each, the same pair embedded twice. "
                       "All HNSW, vector_cosine_ops.",
               "link": "← cosine search, knowledge + both pair fields"}

RERANK = ("Fusing three rankings is not reranking them. Every score the fusion combines was "
          "produced independently -- query against the pair's question, against its reasoning "
          "target, against its keywords -- and nothing in that ever looks at the query and the "
          "whole pair together, or at the retrieved set as a set. So the top 8 fused candidates "
          "get a second pass. Grounding scores the query against the pair's tables and SQL, which "
          "no first-stage retriever indexes: a question naming \"Produce\" should favour the pair "
          "whose SQL says department_name = 'Produce'. Then MMR trades a little relevance for "
          "coverage, because three near-identical exemplars teach one pattern three times.")

RERANK_NUMBERS = ("Measured, not assumed. MMR's first pick has nothing to be redundant with, so "
                  "rank 1 is untouched at any lambda and recall stayed flat from 1.0 down to 0.3 "
                  "on every evaluation set; lambda 0.5 therefore buys coverage for free, cutting "
                  "mean pairwise similarity among the three chosen pairs from 0.560 to 0.514. "
                  "Grounding at 0.25 lifts questions that name a table or column outright from "
                  "5/8 to 6/8 at rank 3 -- the one thing BM25 cannot see, since it indexes only "
                  "the keywords column -- and changes nothing elsewhere.")

MULTISHOT = ("The examples are not pasted into the prompt as text. They are replayed as real "
             "conversation turns -- human asks, assistant answers with SQL, repeated -- in front "
             "of the actual question. That is the shape instruct models are tuned on: a text "
             "block invites the model to describe the examples, a turn sequence invites it to "
             "continue the pattern. Every human turn is [the rule that applies] + [the question] "
             "and every assistant turn is bare SQL, so the exemplars are the same task the model "
             "is about to be given rather than a differently-shaped cousin of it. Nothing the "
             "assistant turns contain is safe from imitation, which is why they carry no "
             "commentary and no leading SQL comment: ensure_read_only rejects anything that does "
             "not begin with SELECT or WITH.")

ENSEMBLE = ("Three retrievers over the same 45 pairs, each answering a different question about a "
            "pair. Question similarity (0.50) matches what the user is asking for. BM25 over the "
            "keyword column (0.35) catches the vocabulary a paraphrase preserves but an embedding "
            "blurs — \"slotting\", \"fan-out\", \"BOGO\". Reasoning similarity (0.15) matches what the "
            "query has to get right rather than what it asks, so a question that never says "
            "\"fan-out\" can still reach the pair that warns about it.")

FUSION = ("Each retriever's scores are min-max normalised across its own candidates, then weighted "
          "and summed. Normalising first is what makes the weights mean anything: a cosine "
          "similarity sits in a narrow band near 0.5 and a BM25 score is unbounded, so weighting the "
          "raw numbers would weight incomparable units. Weighted reciprocal rank fusion — what "
          "LangChain's EnsembleRetriever does — is implemented too and measured worse here: at "
          "w/(60+rank), rank 1 and rank 10 differ by 15%, less than a third retriever contributes by "
          "voting at all, so the weights start counting how many retrievers matched instead of how "
          "strongly. Self-retrieval over all 45: score fusion 45/45, RRF 25/45.")

RETRIEVE_EX_WHY = ("Prose tells the model the rule; a worked example shows it applied. The knowledge "
                   "base can say costs are monthly and sales are daily, and the model can still emit "
                   "the join that matches 24 days a year. A pair that has already reconciled the two "
                   "grains carries the shape of the answer, not just the warning. These 45 pairs each "
                   "ran against this database and returned rows, so the pattern being copied is one "
                   "that works here specifically — not one recalled from training.")

EX_DEGRADE = ("ExamplesUnavailableError → state.examples_error set, examples = \"\", flow proceeds to "
              "select_tables. The context store, the vector store and the embedding host are three "
              "separate things that can be down; all three land here. A run without examples is a v2 "
              "run, and a run without either retrieval is a v1 run.")

TWO_SWITCHES = ("EXAMPLES_ENABLED controls retrieval; MULTI_SHOT_ENABLED controls whether the "
                "retrieved pairs are replayed as turns. Both default on, but they stay separate: "
                "with the second off the examples are still fetched, ranked and visible in --json, "
                "so the ranking can be inspected without it steering generation. Off, the prompt "
                "is byte-for-byte the zero-shot one -- system turn, then the question.")

SELECT_WHY_V3 = (SELECT_WHY + " v3 adds a second hint alongside the chunk_meta tables: the tables a "
                 "closely-matching worked example actually queries. Both are merged into the model's "
                 "choice rather than replacing it.")

GEN_WHY_V3 = (GEN_WHY + " The knowledge block is authoritative over the model's assumptions; the "
              "examples block, when multi-shot is on, sits beneath it as pattern rather than rule — "
              "the prompt says to follow the patterns but answer the question actually asked, because "
              "the nearest pair is never the question being asked.")

EX_PAYOFF = ("\"how much did we make on fruit and veg after cost last month\"  —  no keyword in that "
             "sentence appears in the pair's keyword list verbatim, and the phrase \"fiscal month\" "
             "never occurs. Question similarity ranks the right pair first, BM25 confirms it on "
             "\"cost\" and \"month\", and the pair that comes back already aggregates daily sales and "
             "monthly costs to fiscal month before dividing.")


def build_v3():
    parts, y = [], 56
    parts.append(f'<text x="{MARGIN}" y="{y}" font-family="{SANS}" font-size="27" font-weight="700" '
                 f'fill="{INK}">NL2SQL Agent v3 <tspan fill="{MUTED}" font-weight="400">— ensemble-retrieved worked examples</tspan></text>')
    y += 26
    blk, h = text_block(MARGIN, y, "The v2 pipeline with a second, independent retrieval step after it. "
                        "v2 retrieves prose about the database; v3 also retrieves worked question/SQL "
                        "pairs, through an ensemble of three retrievers across two databases. Everything "
                        "marked in indigo is new in v3; teal is v2's retrieval, unchanged.",
                        CONTENT_R - MARGIN, 13.5, MUTED)
    parts.append(blk); y += h + 26

    svg, h = deployment(y, {"name": "nl2sql-agent:v3",
                            "desc": "python -m nl2sql_agent. Started per question by "
                                    "docker compose run --rm agent, after compose has all three "
                                    "databases passing their health checks."},
                        [PG, VECTORDB_V3, CHUNKDB], [OLLAMA_CHAT, OLLAMA_EMBED])
    parts.append(svg); y += h + 18
    svg, h = note_row(y, "Two models, two hosts", TWO_HOSTS, color=TEAL, x=MARGIN,
                      w=CONTENT_R - MARGIN); parts.append(svg); y += h + 18
    svg, h = note_row(y, "The ensemble — three retrievers, one question", ENSEMBLE, color=INDIGO,
                      x=MARGIN, w=CONTENT_R - MARGIN, dashed=False); parts.append(svg); y += h + 18
    svg, h = note_row(y, "How the three are fused, and why not RRF", FUSION, color=INDIGO,
                      x=MARGIN, w=CONTENT_R - MARGIN); parts.append(svg); y += h + 18
    svg, h = note_row(y, "What the rerank is worth", RERANK_NUMBERS, color=INDIGO,
                      x=MARGIN, w=CONTENT_R - MARGIN); parts.append(svg); y += h + 36
    svg, h = startup(y, STARTUP, FAIL2); parts.append(svg); y += h + 40

    rows = [
        {"id": "retrieve_knowledge", "kind": "step", "n": 0, "name": "retrieve_knowledge",
         "badge": "v2", "accent": TEAL,
         "does": "Embed the question once with bge-m3, then cosine-search every knowledge collection: "
                 "ORDER BY embedding <=> $vec LIMIT k, merged across collections and re-sorted by "
                 "distance. Produces the context block, the tables chunk_meta names, and the provenance "
                 "of each chunk. Unchanged from v2.",
         "tags": [("Ollama · embed_query", TEAL), ("pgvector · <=> cosine", TEAL)],
         "why": RETRIEVE_WHY},
        {"id": "retrieve_examples", "kind": "step", "n": 1, "name": "retrieve_examples",
         "badge": "new in v3", "accent": INDIGO,
         "does": "Run three retrievers over the 45 golden pairs and fuse them. One embed_query feeds "
                 "both vector searches; BM25 runs inside the context store as golden_pairs_bm25(). "
                 "Each returns up to candidate_k ids with scores; the fused winners are hydrated back "
                 "into full rows — question, reasoning, SQL and expected result — in one query.",
         "tags": [("Ollama · embed_query", TEAL), ("pgvector · 2 collections", TEAL),
                  ("Postgres · BM25", INDIGO), ("fuse · 0.50 / 0.35 / 0.15", INDIGO)],
         "why": RETRIEVE_EX_WHY},
        {"id": "degrade", "kind": "note", "w": STEP_W, "color": AMBER,
         "title": "If either retrieval fails — the run continues anyway",
         "body": EX_DEGRADE},
        {"id": "select_tables", "kind": "step", "n": 2, "name": "select_tables",
         "badge": "changed", "accent": INDIGO,
         "does": "Ask the model which tables the question needs, from the catalog plus the retrieved "
                 "knowledge. Keep only names that exist, then merge in the tables the chunks document "
                 "and the tables the retrieved examples query. Fall back to all tables if none survive.",
         "tags": [("LLM · TableSelection", PURPLE), ("Postgres · pg_class", BLUE),
                  ("+ chunk_meta.table", TEAL), ("+ example tables", INDIGO)],
         "why": SELECT_WHY_V3},
        {"id": "fetch_schema", "kind": "step", "n": 3, "name": "fetch_schema",
         "does": "Pull full detail for the chosen tables only: column types, NOT NULL, primary and foreign "
                 "key definitions, any COMMENT ON text, and 3 sample rows per table. Unchanged from v1.",
         "tags": [("Postgres · pg_attribute, pg_constraint", BLUE)], "why": FETCH_WHY},
        {"id": "generate_sql", "kind": "step", "n": 4, "name": "generate_sql",
         "badge": "changed", "accent": INDIGO,
         "does": "Ask the model for one SELECT, given the schema, the knowledge block, and — when "
                 "MULTI_SHOT_ENABLED is on — the retrieved pairs as worked examples. strip_sql() removes "
                 "any fence and trailing semicolon. On a retry, RETRY_FEEDBACK prepends the rejected SQL "
                 "and the specific problems found with it.",
         "tags": [("LLM · free-form", PURPLE), ("+ knowledge", TEAL), ("+ examples", INDIGO)],
         "why": GEN_WHY_V3},
        {"id": "multishot", "kind": "note", "w": STEP_W, "color": INDIGO,
         "title": "Two switches, not one", "body": TWO_SWITCHES},
        {"id": "validate_sql", "kind": "step", "n": 5, "name": "validate_sql",
         "badge": "v2", "accent": TEAL,
         "does": "Three checks, cheapest first. (a) ensure_read_only: a single statement starting with "
                 "SELECT or WITH, no embedded semicolon. (b) EXPLAIN it — planned, not run. (c) Only "
                 "then a model review, against the knowledge block as well as the schema.",
         "tags": [("Postgres · EXPLAIN", BLUE), ("LLM · SqlReview", PURPLE),
                  ("+ knowledge", TEAL)], "why": VAL_WHY_V2},
        {"id": "route", "kind": "decision", "arms": ARMS, "why": ROUTE_WHY},
        {"id": "terminals", "kind": "branch", "left": TERM_L, "right": TERM_R, "why": TERM_WHY},
    ]
    svg, h, nodes = pipeline(
        y, rows,
        rails=[
            {"from": "retrieve_knowledge", "to": ("select_tables", "generate_sql", "validate_sql"),
             "color": TEAL, "label": "state.knowledge &#8594; 3 consumers", "x": 604, "side": -1},
            {"from": "retrieve_examples", "to": ("select_tables", "generate_sql"),
             "color": INDIGO, "label": "state.examples &#8594; 2 consumers", "x": 636, "side": 1},
        ])
    parts.append(svg); y += h + 34
    svg, h = outcomes(y, OUTCOMES, OUT_NOTE); parts.append(svg); y += h + 22
    svg, h = note_row(y, "What the examples buy, on top of the knowledge", EX_PAYOFF, color=INDIGO,
                      x=MARGIN, w=CONTENT_R - MARGIN, dashed=False); parts.append(svg); y += h + 26
    svg, h = legend(y, [("solid", "control flow", SLATE), ("line", "retry loop", RED),
                        ("line", "knowledge (data flow)", TEAL), ("line", "examples (data flow)", INDIGO),
                        ("pill", "model call", PURPLE), ("pill", "database access", BLUE),
                        ("pill", "new or changed in v3", INDIGO)])
    parts.append(svg); y += h
    return document("NL2SQL Agent v3 architecture", "Ensemble-retrieved worked examples",
                    "\n".join(parts), y + 34, nodes)


# --- v4: the multi-agent pipeline ------------------------------------------

PG_V4 = {"name": "nl2sql-postgres", "color": BLUE,
         "desc": "Postgres 18. The synthetic retail dataset — 19 tables, ~2M rows — is baked into "
                 "the image. v4 asks it three things v3 never did: pg_constraint for the "
                 "foreign-key closure, every low-cardinality text column for the literal catalog, "
                 "and EXPLAIN for a plan cost.",
         "link": "← SQLAlchemy + psycopg · pg_trgm"}

VECTORDB_V4 = {"name": "nl2sql-vectordb", "color": TEAL,
               "desc": "pgvector, the same stores as v3. The DDL collection now has a reader: it "
                       "is the Schema Retriever's index, one chunk per table, and it is excluded "
                       "from the knowledge search so no table is described twice.",
               "link": "← cosine search, DDL + knowledge + both pair fields"}

OLLAMA_CHAT_V4 = {"name": "Ollama — chat model", "color": PURPLE,
                  "desc": "qwen3.8-256k on a remote host, 256k context. In v4 it screens the "
                          "question, writes the SQL and narrates the result — and diagnoses a "
                          "repair only when the classifier cannot. Reasoning stays off: Qwen3 "
                          "returns it separately, so disabling it costs no quality.",
                  "link": "→ 3 prompts on the happy path"}

OLLAMA_EMBED_V4 = {"name": "Ollama — embedding model", "color": TEAL,
                   "desc": "bge-m3 on the machine running Docker, reached as host.docker.internal "
                           "via extra_hosts: host-gateway. 1024 dimensions. Three of the four "
                           "retrievers embed the question; only the two vector searches over the "
                           "golden pairs share one call.",
                   "link": "→ one embed_query per retriever"}

V4_REMOVED = ("Two v3 model calls are gone. select_tables asked the model which tables a question "
              "needed — 364 of the benchmark's 1499 seconds — and is now vector search over the "
              "DDL chunks plus a foreign-key closure read from pg_constraint. validate_sql asked "
              "the model whether the SQL was right — 499 seconds, and the only component in 45 "
              "runs that ever turned a right answer into no answer — and is now a pglast AST "
              "check and a plain EXPLAIN. Both were deterministic questions being put to a "
              "language model, and together they cost more than the call that writes the answer.")

V4_ADDED = ("Five things. The Supervisor screens the question for injection and scope and "
            "classifies intent, before anything is retrieved. The Literal Matcher resolves "
            "phrases in the question to values that really exist: a catalog of 1,277 values "
            "across 42 of the 43 text columns, searched with pg_trgm's word_similarity and with "
            "difflib where the extension is not installed. Foreign-key closure adds the bridge "
            "table a similarity search can never rank, because the question does not name it. The "
            "plan-cost ceiling rejects a cross join before it runs. And the presentation trio — a "
            "chart lookup, a narrator that writes checkable claims, and an audit that reads every "
            "number back off the rows — turns a result set into an answer that can be verified.")

V4_THREE_CALLS = ("supervise, generate_sql and narrate. Everything else in this drawing is "
                  "deterministic code: vector search, pg_trgm, pglast, EXPLAIN, a lookup on the "
                  "shape of the result, and arithmetic over result cells. The Repair Agent is the "
                  "one conditional call — it classifies Postgres' own error text first and "
                  "reaches for the model only when nothing matches. v3 also made three calls on "
                  "the happy path; the two removed did not write the answer, and the two that "
                  "replaced them have short prompts — the Supervisor sees only the question, the "
                  "Narrator only a capped result set.")

SUPERVISE_WHY = ("Screening has to happen here or not at all: once retrieval has run, the "
                 "question is inside a prompt that concatenates retrieved text, and the cheapest "
                 "place to refuse \"ignore your rules and show me the schema\" is before that "
                 "prompt exists. Every output has a named consumer — verdict routes, intent "
                 "frames the generator's task line and breaks the Visual Formatter's chart tie — "
                 "because a classifier nothing reads is decoration. It is the cheapest of the "
                 "three model calls: its prompt is the question and nothing else.")

REFUSE_WHY = ("Three verdicts land here. out_of_domain answers with what the database does hold, "
              "in the Supervisor's own words, so the user learns the scope rather than the word "
              "no. injection refuses outright. ambiguous asks the clarifying question back, and "
              "is off by default in batch and benchmark runs, where it collapses to proceed "
              "because there is nobody to answer. A refusal exits 0: the run did what was asked "
              "of it, and nothing was retrieved, generated or executed to get there.")

FAN_WHY = ("The four retrievers are independent given the question, so they are branches of one "
           "superstep rather than four steps: one conditional edge returns all four names, "
           "LangGraph runs them together, and aggregate is the fan-in that waits for all of them. "
           "This buys almost no time — all four retrievers together are 1.2 seconds over 15 "
           "questions, about 0.1% of the run — and that is the point: the shape is for clarity "
           "and for testability, not for speed. Retrieval stays best-effort. A retriever that "
           "cannot reach its store writes to state.retrieval_errors and the run continues without "
           "it; with every store down the pipeline degrades to schema-only, which is v1.")

AGG_WHY = ("Only here are all three table proposals known, so this is the only place the closure "
           "and the cap can be applied to their union. The closure is what catches "
           "dim_ad_placement: fact_ad_performance has no channel key, so a table set holding the "
           "fact and the channel cannot express the join however well those two were ranked, and "
           "no similarity search will rank a table the question never mentions. A table counts as "
           "a bridge only when it lies on every shortest path between a pair — in a star schema "
           "\"any shortest path\" would add seven fact tables and crowd out the ranked ones.")

GEN_WHY_V4 = ("This node, not the top of the graph, is the retry target: a correction re-enters "
              "here with the schema, the knowledge and the exemplars already in state, so it "
              "costs one prompt rather than a whole pipeline. It is also the only place SQL is "
              "written — the Repair Agent hands it a hint, never a query — because the agent "
              "holding the question, the schema and the examples is the one that should be "
              "choosing the columns.")

STATIC_WHY = ("v3 put this question to a model: 499 of 1499 benchmark seconds, and the only "
              "component in 45 runs that ever turned a right answer into no answer. It is a "
              "deterministic question. pglast wraps PostgreSQL's own parser, so what is accepted "
              "here is what the server accepts, and the tree sees what a prefix check cannot: "
              "WITH d AS (DELETE FROM dim_store RETURNING *) SELECT * FROM d is one statement, "
              "begins with WITH, and deletes a table. A rejection at this stage costs nothing and "
              "never reaches the database.")

PLANNER_WHY = ("Calibrated, not guessed. The most expensive of the 45 golden pairs plans at "
               "125,767, a full scan of the sales fact at 20,096, and that fact cross-joined with "
               "dim_product at 1.6 million — so a ceiling of 1,000,000 sits eight times above the "
               "hardest known-good query and below the cheapest cross join that touches the fact "
               "table. Plain EXPLAIN, never EXPLAIN ANALYZE: ANALYZE executes the statement to "
               "time it, which is the one thing this gate exists to prevent. And its error text "
               "is the best feedback in the pipeline — unknown column, type mismatch, aggregate "
               "outside GROUP BY — none of which the parser can see.")

EXEC_WHY_V4 = ("The second, independent safety layer, and the one that actually holds: the AST "
               "check rejects a writing CTE, but it is SET TRANSACTION READ ONLY that makes the "
               "rejection unnecessary. A runtime failure — a timeout the planner could not "
               "predict, a division by zero that only shows on real rows — is an Issue like any "
               "other and goes to the same Repair Agent. principal is in the state contract so "
               "that SET ROLE and row-level security are a deployment change rather than a "
               "rewrite; the test system has no RLS policies to apply.")

VIS_WHY = ("arch3 had an agent infer chart configuration. A lookup should not cost a model call, "
           "and a deterministic one can be pinned row by row against the table in section 7.1 of "
           "the architecture — which is what the unit tests do. Nothing is rendered here either: "
           "the output is a spec, so the consumer draws it with whatever it has.")

NARRATE_WHY = ("A narrative nobody can check is this system's worst failure mode, because it "
               "looks exactly like an answer. Claims that name their cells are what make the next "
               "node a program rather than a second opinion. The narrator reads the exemplar's "
               "reasoning_target straight out of state: the draw.io design had both presentation "
               "agents call back into stage 1, and that edge is removed — everything they need is "
               "already in the state object.")

AUDIT_WHY = ("This is where v3's LLM semantic review went: after execution, with rows in hand "
             "instead of guessed from a schema, and inside the retry budget. It asks no model. A "
             "claim that cannot be reproduced from the cells it names is dropped, and the report "
             "says how many were; a formula arrives from a language model, so it is walked as an "
             "AST against a whitelist and never passed to eval. When the rows say the SQL is "
             "wrong rather than the prose, that is a repair like any other and spends the same "
             "counter.")

ONE_LOOP = ("Four failure sources — the AST check, the planner, the executor, the audit — route "
            "to one agent and spend one counter: MAX_ATTEMPTS = 4, one draft and three repairs. "
            "The source diagram let planner failures skip repair entirely and let the audit "
            "re-enter generation uncounted, which is two ways to loop without paying for it. "
            "There is no such path here, and the red track on the left is the whole of it.")

REPAIR_WHY = ("A repair that called the model would double the model calls of every retry, on "
              "exactly the questions that are already slow. Most failures do not need one: "
              "Postgres says column \"store_nam\" does not exist and the pruned schema says "
              "store_name — the gap between those two facts is a trigram match, not an act of "
              "reasoning. So the classifier runs first and the model is asked only for what it "
              "cannot name. Every attempt is appended to attempt_history, which is how the hint "
              "on attempt 3 can say that attempts 1 and 2 failed the same way.")

ROUTE_WHY_V4 = ("Bounded on purpose, and bounded in one place. Some questions the data simply "
                "cannot answer, and retrying one of those spends tokens without converging. Four "
                "generations is the budget for the whole run rather than for each gate, because "
                "every failure source shares the counter — a query that passes the AST check on "
                "its third try and then fails the planner has one attempt left, not three.")

TERM_WHY_V4 = ("give_up is a real outcome rather than a failure of nerve: the alternative is "
               "returning a query already known to be wrong. It carries the whole "
               "attempt_history, so a user who gets no answer can still see what was tried and "
               "why each try was rejected. finish is the only node that produces output, which is "
               "what makes it the only place HTML escaping has to happen — the rows and the "
               "question are untrusted text right up until here.")

V4_FAN = [
    {"name": "retrieve_schema", "color": MAGENTA, "tag": "pgvector · ddl_index",
     "does": "Embed the question and search the one collection of DDL chunks — a CREATE TABLE "
             "with its comments, grain and keywords, one chunk per table — keeping the top 6 by "
             "cosine distance. No model call."},
    {"name": "retrieve_literals", "color": MAGENTA, "tag": "Postgres · pg_trgm",
     "does": "Take candidate phrases out of the question and match them against the literal "
             "catalog: 1,277 values across 42 of the 43 text columns, scored by pg_trgm "
             "word_similarity, or by difflib where the extension is not installed."},
    {"name": "retrieve_knowledge", "color": TEAL, "tag": "pgvector · <=> cosine",
     "does": "v2's retriever, unchanged but for what it searches: the data dictionary and "
             "business index collections, the DDL index having moved next door. Top-k per "
             "collection, merged and re-sorted by distance."},
    {"name": "retrieve_examples", "color": INDIGO, "tag": "pgvector + BM25 · 45 pairs",
     "does": "v3's ensemble, unchanged: question vectors 0.50, BM25 over keywords 0.35, "
             "reasoning vectors 0.15, min-max fused, then grounding and MMR reranked to the "
             "top 3 worked pairs."},
]

ARMS_V4 = [("attempts < max (4 by default)", "generate_sql", RED),
           ("attempts = max", "give_up", AMBER)]

TERM_L_V4 = {"name": "give_up", "color": AMBER,
             "does": "Record \"Could not produce a valid query in N attempts\" with the last set "
                     "of problems, and keep attempt_history: every rejected query and the hint it "
                     "was given.",
             "tag": "no answer · exit 1"}
TERM_R_V4 = {"name": "finish", "color": GREEN,
             "does": "Render the answer: the claims the audit let through, the result as a "
                     "markdown table, the chart spec, the SQL and the trace. Everything is "
                     "HTML-escaped here.",
             "tag": "the only output · exit 0"}

OUTCOMES_V4 = [("exit 0", "Answered — narrative, table and chart spec. A refusal is also exit 0: "
                "the run did what was asked.", GREEN),
               ("exit 1", "Ran, but could not answer — give_up after four generations, or the "
                "query failed at execution.", AMBER),
               ("exit 2", "Ollama misconfigured. Raised during startup; the graph never runs.",
                RED)]

OUT_NOTE_V4 = ("Progress lines go to stderr and the answer to stdout, so `... 2>/dev/null` leaves "
               "just the result. `--json` carries the whole state instead: the verdict and "
               "intent, the literal map, the selected tables, the plan cost, every attempt with "
               "the hint it was given, the claims, the audit report, and a trace entry per node "
               "with its milliseconds and model calls. That last field is what lets the benchmark "
               "attribute time per agent rather than per stage, which is how the claim that this "
               "pipeline is cheaper than v3's gets tested rather than asserted.")


def build_v4():
    parts, y = [], 56
    parts.append(f'<text x="{MARGIN}" y="{y}" font-family="{SANS}" font-size="27" '
                 f'font-weight="700" fill="{INK}">NL2SQL Agent v4 '
                 f'<tspan fill="{MUTED}" font-weight="400">— multi-agent pipeline</tspan></text>')
    y += 26
    blk, h = text_block(MARGIN, y, "Four stages — intake and context gathering, synthesis, "
                        "validation and repair, presentation and audit — over one shared state "
                        "object, with one repair loop. v3's linear pipeline asked the model three "
                        "times: which tables, what SQL, and was the SQL right. v4 asks it three "
                        "times too — but about the question, the SQL, and the result — and "
                        "answers the other two questions with a "
                        "parser and a planner. Everything marked in magenta is new in v4; teal is "
                        "v2's retrieval and indigo v3's examples, both carried over as they were.",
                        CONTENT_R - MARGIN, 13.5, MUTED)
    parts.append(blk); y += h + 26

    svg, h = deployment(y, {"name": "nl2sql-agent:v4",
                            "desc": "python -m nl2sql_agent. Started per question by "
                                    "docker compose run --rm agent, after compose has all three "
                                    "databases passing their health checks."},
                        [PG_V4, VECTORDB_V4, CHUNKDB], [OLLAMA_CHAT_V4, OLLAMA_EMBED_V4])
    parts.append(svg); y += h + 18
    svg, h = note_row(y, "Two models, two hosts", TWO_HOSTS, color=TEAL, x=MARGIN,
                      w=CONTENT_R - MARGIN); parts.append(svg); y += h + 18
    svg, h = note_row(y, "What v4 removes, and what it cost", V4_REMOVED, color=MAGENTA,
                      x=MARGIN, w=CONTENT_R - MARGIN, dashed=False); parts.append(svg); y += h + 18
    svg, h = note_row(y, "What v4 adds", V4_ADDED, color=MAGENTA, x=MARGIN,
                      w=CONTENT_R - MARGIN); parts.append(svg); y += h + 18
    svg, h = note_row(y, "Three model calls on the happy path", V4_THREE_CALLS, color=PURPLE,
                      x=MARGIN, w=CONTENT_R - MARGIN); parts.append(svg); y += h + 36
    svg, h = startup(y, STARTUP, FAIL2); parts.append(svg); y += h + 40

    rows = [
        {"id": "supervise", "kind": "step", "n": 1, "name": "supervise",
         "badge": "new in v4", "badge_color": MAGENTA, "accent": MAGENTA,
         "does": "One structured-output call on the raw question, returning three fields: "
                 "verdict (proceed, out_of_domain, injection, ambiguous), intent (lookup, "
                 "aggregate, compare, trend, narrative) and the clarification to ask back. The "
                 "only agent that sees the question before retrieval.",
         "tags": [("LLM · Supervision", PURPLE), ("stage 1 · intake", MAGENTA)],
         "why": SUPERVISE_WHY},
        {"id": "refuse", "kind": "offramp", "name": "refuse", "color": AMBER,
         "flow": {"x": STEP_X + 105, "color": AMBER},
         "does": "Answer with the scope sentence, the refusal, or the clarifying question, and "
                 "stop. No retrieval, no SQL, no database access. → END",
         "tag": "terminal · exit 0", "why": REFUSE_WHY},
        {"id": "retrievers", "kind": "fan", "n": 2, "boxes": V4_FAN, "accent": MAGENTA,
         "caption": "all four read the question and nothing else — LangGraph joins them "
                    "at aggregate"},
        # Step-width, like v2 and v3's notes: the gap to the right of the
        # column is where the rails run, and a full-width note sits in it.
        {"id": "fan_note", "kind": "note", "color": MAGENTA, "w": STEP_W,
         "title": "Stage 1 fans out — and what a store being down costs",
         "body": FAN_WHY},
        {"id": "aggregate", "kind": "step", "n": 3, "name": "aggregate",
         "badge": "new in v4", "badge_color": MAGENTA, "accent": MAGENTA,
         "does": "The fan-in. Union the tables the three retrievers proposed, keep the ones the "
                 "live catalog knows, close over foreign keys to pull in the bridge tables, cap "
                 "at max_tables (10) dropping the lowest-ranked non-bridge first, then fetch the "
                 "schema: columns, types, keys, COMMENT ON text and sample rows.",
         "tags": [("Postgres · pg_constraint", BLUE), ("FK closure", MAGENTA)],
         "why": AGG_WHY},
        {"id": "generate_sql", "kind": "step", "n": 4, "name": "generate_sql",
         "badge": "changed", "badge_color": MAGENTA, "accent": MAGENTA,
         "does": "Ask the model for one SELECT. The system turn carries the rules and the pruned "
                 "schema; the exemplars are replayed as human/assistant turns; the human turn "
                 "carries the knowledge block, the literal map, the intent framing, the question "
                 "and — on a retry — the Repair Agent's hint. strip_sql() removes any fence.",
         "tags": [("stage 2 · synthesis", MAGENTA), ("LLM · free-form", PURPLE),
                  ("+ literals", MAGENTA)],
         "why": GEN_WHY_V4},
        {"id": "validate_static", "kind": "step", "n": 5, "name": "validate_static",
         "badge": "new in v4", "badge_color": MAGENTA, "accent": MAGENTA,
         "does": "Parse with pglast and reject: more than one statement, anything that is not a "
                 "SelectStmt, a CTE that writes, an INTO target, a denylisted function "
                 "(pg_sleep, pg_read_file, dblink …), or a relation outside selected_tables. The "
                 "message names the nearest allowed table by trigram.",
         "tags": [("stage 3 · validation", MAGENTA), ("pglast · AST", MAGENTA),
                  ("no model call", MAGENTA)], "why": STATIC_WHY},
        {"id": "planner_gate", "kind": "step", "n": 6, "name": "planner_gate",
         "badge": "new in v4", "badge_color": MAGENTA, "accent": MAGENTA,
         "does": "EXPLAIN (FORMAT JSON) inside a READ ONLY transaction — planned, never run. A "
                 "planner error goes to the Repair Agent verbatim; a Plan.Total Cost above "
                 "max_plan_cost (1,000,000 by default) is rejected as a cross join or an "
                 "unfiltered scan.",
         "tags": [("Postgres · EXPLAIN", BLUE), ("cost ceiling", MAGENTA)], "why": PLANNER_WHY},
        {"id": "execute_query", "kind": "step", "n": 7, "name": "execute_query",
         "badge": "carried over", "badge_color": SLATE, "accent": SLATE,
         "does": "run_select() inside SET TRANSACTION READ ONLY with SET LOCAL "
                 "statement_timeout, fetching max_rows + 1 so truncation is detected rather than "
                 "guessed at, and SET ROLE <principal> when the state carries one.",
         "tags": [("Postgres · READ ONLY tx", BLUE)], "why": EXEC_WHY_V4},
        {"id": "visualise", "kind": "step", "n": 8, "name": "visualise",
         "badge": "new in v4", "badge_color": MAGENTA, "accent": MAGENTA,
         "does": "A lookup on the shape of the result: one cell is a scalar, a category and a "
                 "measure is a bar, a date column and a measure is a line, two measures are a "
                 "scatter, more than 30 rows is a table. intent breaks the ties — trend prefers "
                 "a line. The output is a ChartSpec; nothing is drawn here.",
         "tags": [("no model call", MAGENTA), ("stage 4 · presentation", MAGENTA)],
         "why": VIS_WHY},
        {"id": "narrate", "kind": "step", "n": 9, "name": "narrate",
         "badge": "new in v4", "badge_color": MAGENTA, "accent": MAGENTA,
         "does": "The third and last model call on the happy path. It writes claims rather than "
                 "prose: every number points at the result cells it came from, with an optional "
                 "formula over them. Its prompt gets the question, the capped result, the chart "
                 "spec and the top exemplar's reasoning_target.",
         "tags": [("LLM · Claims", PURPLE), ("reads state, never re-enters", MAGENTA)],
         "why": NARRATE_WHY},
        {"id": "audit", "kind": "step", "n": 10, "name": "audit",
         "badge": "new in v4", "badge_color": MAGENTA, "accent": MAGENTA,
         "does": "Reproduce every claim from the cells it names, directly or through its "
                 "formula, to the benchmark's 2-decimal tolerance; drop the ones that do not "
                 "reproduce. Withhold columns tagged sensitive. When the rows say the SQL is "
                 "wrong — no rows where the question implied rows, a percentage over 100 — set "
                 "semantic_issue and send it to repair.",
         "tags": [("no model call", MAGENTA), ("→ repair on semantic_issue", RED)],
         "why": AUDIT_WHY},
        {"id": "loop_note", "kind": "note", "color": RED, "w": STEP_W,
         "flow": {"color": RED, "dash": "7 5"},
         "title": "One loop, one counter", "body": ONE_LOOP},
        {"id": "repair", "kind": "step", "n": 11, "name": "repair",
         "badge": "new in v4", "badge_color": MAGENTA, "accent": MAGENTA,
         "flow": {"color": RED, "dash": "7 5"},
         "does": "Turn the failure into a hint and hand it back. A classifier reads Postgres' "
                 "own message first — unknown column to the nearest column in the pruned schema, "
                 "add it to GROUP BY, cast one side, guard the denominator with NULLIF, filter "
                 "before joining — and the model is called only for what it cannot classify. "
                 "This agent never writes SQL.",
         "tags": [("classifier first", MAGENTA), ("LLM only if unclassified", PURPLE)],
         "why": REPAIR_WHY},
        {"id": "route", "kind": "decision", "title": "_route_after_repair()", "arms": ARMS_V4,
         "why": ROUTE_WHY_V4},
        {"id": "terminals", "kind": "branch", "left": TERM_L_V4, "right": TERM_R_V4,
         "flow": {"x": STEP_X + (STEP_W - 14) / 4, "color": AMBER}, "why": TERM_WHY_V4},
    ]
    svg, h, nodes = pipeline(
        y, rows,
        retry_label="retry &#183; one shared attempts budget",
        loop_from=("validate_static", "planner_gate", "execute_query"), loop_into="repair",
        # No data-flow rail here, unlike v2 and v3. state.intent really does
        # reach generate_sql and visualise, but its track would have to cross
        # the fan band, and the fan band is the more important thing to draw;
        # the Supervisor's why card names its consumers instead.
        rails=[
            {"from": "audit", "to": ("terminals",), "color": GREEN, "dash": None,
             "label": "audit passes &#8594; finish", "x": 628, "side": 1},
        ])
    parts.append(svg); y += h + 34
    svg, h = outcomes(y, OUTCOMES_V4, OUT_NOTE_V4); parts.append(svg); y += h + 26
    svg, h = legend(y, [("solid", "control flow", SLATE),
                        ("line", "repair loop — one counter", RED),
                        ("solid", "audit passes — finish", GREEN),
                        ("pill", "model call", PURPLE), ("pill", "database access", BLUE),
                        ("pill", "new or changed in v4", MAGENTA)])
    parts.append(svg); y += h
    return document("NL2SQL Agent v4 architecture", "Multi-agent pipeline",
                    "\n".join(parts), y + 34, nodes, extra_colors=(GREEN,))


#: What `main` writes, in the order the versions came.
DIAGRAMS = (("arch_v1.svg", "build_v1"), ("arch_v2.svg", "build_v2"),
            ("arch_v3.svg", "build_v3"), ("arch_v4.svg", "build_v4"))


def main(output_dir: Path | None = None) -> None:
    """Render every diagram beside this file, or into `output_dir`.

    The argument exists so a test can render somewhere disposable: without
    it, exercising this function means writing into the working tree, and a
    test that edits the files another test checks is a test that can hide a
    failure.
    """
    here = output_dir or Path(__file__).resolve().parent
    here.mkdir(parents=True, exist_ok=True)
    for name, builder in DIAGRAMS:
        svg = globals()[builder]()
        (here / name).write_text(svg)
        print(f"{name}: {len(svg) / 1024:.1f} KB")


if __name__ == "__main__":
    # An optional destination, so the diagrams can be rendered somewhere
    # other than the working tree -- which is what lets the tests run this
    # entry point without editing the files another test checks.
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
