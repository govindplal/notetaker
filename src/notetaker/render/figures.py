"""Draw simple diagrams (flows, layered networks) as inline SVG in the notes palette.

The model only describes a figure (nodes, edges, layers); this module lays it out and draws it, so
every figure has the same look as the reference illustrations and no model-written markup ever
reaches the page. All text is escaped.
"""

import re
from collections import defaultdict
from html import escape

from notetaker.notes.model import FigNode, Figure

INK, BODY, MUTED = "#141413", "#3D3D3A", "#87867F"
BOX, OAT, CLAY, OLIVE, IVORY, FAINT = (
    "#F0EEE6",
    "#E3DACC",
    "#D97757",
    "#788C5D",
    "#FAF9F5",
    "#D1CFC5",
)
# accent -> (fill, stroke, stroke width, text colour)
STYLES = {
    "none": (BOX, BODY, 1.5, BODY),
    "secondary": (OAT, BODY, 1.5, INK),
    "highlight": (CLAY, INK, 2, IVORY),
    "good": (OLIVE, INK, 2, IVORY),
}
MAX_NODES, MAX_LAYERS, PAD = 12, 7, 24
LINE_H, CHAR_W = 16, 6.6


def _wrap(text: str, width: int = 20, max_lines: int = 2) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if len(trial) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][: width - 1].rstrip() + "…"
    return lines or [""]


def _size(n: FigNode) -> tuple[int, int]:
    lines = _wrap(n.label)
    sub = _wrap(n.sub, 26, 1) if n.sub else []
    longest = max([len(x) for x in lines] + [len(x) for x in sub])
    w = max(92, min(200, int(longest * CHAR_W + 34)))
    h = 22 + LINE_H * len(lines) + (LINE_H if sub else 0) + 8
    return w, max(h, 52)


def _text(x: float, y: float, s: str, cls: str, fill: str, extra: str = "") -> str:
    return f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle" class="{cls}" fill="{fill}"{extra}>{escape(s)}</text>'


def _layer_assignment(fig: Figure):
    ids = {n.id for n in fig.nodes}
    edges = [
        (e.source, e.target, e.label)
        for e in fig.edges
        if e.source in ids and e.target in ids and e.source != e.target
    ]
    adj = defaultdict(list)
    for i, (s, t, _) in enumerate(edges):
        adj[s].append((t, i))
    state: dict[str, int] = {}
    back: set[int] = set()

    def dfs(u: str) -> None:
        state[u] = 1
        for v, i in adj[u]:
            if state.get(v) == 1:
                back.add(i)
            elif v not in state:
                dfs(v)
        state[u] = 2

    targets = {t for _, t, _ in edges}
    for nid in [n.id for n in fig.nodes if n.id not in targets] + [n.id for n in fig.nodes]:
        if nid not in state:
            dfs(nid)
    fwd = [(s, t, lab) for i, (s, t, lab) in enumerate(edges) if i not in back]
    back_edges = [edges[i] for i in sorted(back)]
    layer = {nid: 0 for nid in ids}
    for _ in range(len(ids)):
        for s, t, _lab in fwd:
            layer[t] = max(layer[t], layer[s] + 1)
    return layer, fwd, back_edges


def _marker(prefix: str) -> str:
    return (
        f'<defs><marker id="{prefix}-a" markerWidth="10" markerHeight="10" refX="8" refY="4" orient="auto" '
        f'markerUnits="userSpaceOnUse"><path d="M0,0 L0,8 L8,4 z" fill="{MUTED}"/></marker></defs>'
    )


def _flow(fig: Figure, prefix: str) -> str | None:
    nodes = fig.nodes[:MAX_NODES]
    if len(nodes) < 2:
        return None
    fig = fig.model_copy(update={"nodes": nodes})
    layer, fwd, back = _layer_assignment(fig)
    size = {n.id: _size(n) for n in nodes}
    by_layer: dict[int, list[FigNode]] = defaultdict(list)
    for n in nodes:
        by_layer[layer[n.id]].append(n)
    n_layers = max(by_layer) + 1
    lr = fig.direction == "LR"
    gap_cross = 26 if lr else 30
    # Space between columns grows with the longest label on an edge crossing it.
    label_len: dict[int, int] = defaultdict(int)
    for s_id, t_id, lab in [*fwd, *back]:
        if lab:
            for k in range(min(layer[s_id], layer[t_id]), max(layer[s_id], layer[t_id])):
                label_len[k] = max(label_len[k], len(lab))
    gaps = [
        max(44, int(label_len[k] * 6.2 + 24)) if lr else (68 if label_len[k] else 56)
        for k in range(n_layers - 1)
    ]

    # Box sizes per layer along the main axis (width for LR, height for TB) and across it.
    main_len = {
        k: max((size[n.id][0] if lr else size[n.id][1]) for n in v) for k, v in by_layer.items()
    }
    cross_len = {
        k: sum((size[n.id][1] if lr else size[n.id][0]) for n in v) + gap_cross * (len(v) - 1)
        for k, v in by_layer.items()
    }
    total_main = sum(main_len[k] for k in range(n_layers)) + sum(gaps)
    total_cross = max(cross_len.values())

    pos: dict[str, tuple[float, float, int, int]] = {}  # id -> x, y (top-left), w, h
    cursor = PAD
    for k in range(n_layers):
        cross = PAD + (total_cross - cross_len[k]) / 2
        for n in by_layer[k]:
            w, h = size[n.id]
            if lr:
                pos[n.id] = (cursor + (main_len[k] - w) / 2, cross, w, h)
                cross += h + gap_cross
            else:
                pos[n.id] = (cross, cursor + (main_len[k] - h) / 2, w, h)
                cross += w + gap_cross
        cursor += main_len[k] + (gaps[k] if k < n_layers - 1 else 0)

    width = (total_main if lr else total_cross) + 2 * PAD
    height = (total_cross if lr else total_main) + 2 * PAD
    extra = 56 if back else 0
    if lr:
        height += extra
    else:
        width += extra

    out = [_marker(prefix)]
    # Boxes and labels.
    boxes = []
    for n in nodes:
        x, y, w, h = pos[n.id]
        fill, stroke, sw, ink = STYLES[n.accent]
        boxes.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w}" height="{h}" rx="10" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>'
        )
        lines = _wrap(n.label)
        sub = _wrap(n.sub, 26, 1) if n.sub else []
        block_h = LINE_H * len(lines) + (LINE_H if sub else 0)
        ty = y + (h - block_h) / 2 + 12
        for i, line in enumerate(lines):
            boxes.append(_text(x + w / 2, ty + i * LINE_H, line, "s", ink))
        if sub:
            boxes.append(
                _text(
                    x + w / 2,
                    ty + len(lines) * LINE_H,
                    sub[0],
                    "m",
                    ink if n.accent != "none" else MUTED,
                )
            )

    obstacles = [(x, y, x + w, y + h) for x, y, w, h in pos.values()]

    def put_label(lx: float, ly: float, anchor_x: str, text: str) -> str:
        """Place an edge label where it does not sit on a box or another label."""
        w = len(text) * 6.4 + 6
        x0 = lx - w / 2 if anchor_x == "middle" else lx
        box = (x0, ly - 11, x0 + w, ly + 4)
        for dy in (0, -14, 14, -28, 28, -42, 42):
            box = (x0, ly + dy - 11, x0 + w, ly + dy + 4)
            if not any(
                box[0] < o[2] and o[0] < box[2] and box[1] < o[3] and o[1] < box[3]
                for o in obstacles
            ):
                ly += dy
                break
        obstacles.append(box)
        return (
            f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor_x}" class="s" fill="{MUTED}" '
            f'stroke="{IVORY}" stroke-width="4" paint-order="stroke">{escape(text)}</text>'
        )

    def cross_center(nid: str) -> float:
        x, y, w, h = pos[nid]
        return y + h / 2 if lr else x + w / 2

    # Edges between neighbouring columns attach to the sides of their boxes. When several edges
    # share a side, their attachment points are spread out and ordered by where each edge goes.
    records: list[tuple[str, str, str | None, bool]] = [(s_, t_, lab, False) for s_, t_, lab in fwd]
    arcs: list[tuple[str, str, str | None]] = []
    for s_, t_, lab in back:
        if layer[t_] == layer[s_] - 1:
            records.append((s_, t_, lab, True))
        else:
            arcs.append((s_, t_, lab))
    sides: dict[tuple[str, str], list[tuple[float, int, str]]] = defaultdict(list)
    for i, (s_, t_, _lab, is_back) in enumerate(records):
        out_side, in_side = ("left", "right") if is_back else ("right", "left")
        if not lr:
            out_side, in_side = ("top", "bottom") if is_back else ("bottom", "top")
        sides[(s_, out_side)].append((cross_center(t_), i, "s"))
        sides[(t_, in_side)].append((cross_center(s_), i, "t"))
    port: dict[tuple[int, str], tuple[float, float]] = {}
    for (nid, side), items in sides.items():
        items.sort()
        x, y, w, h = pos[nid]
        for j, (_key, i, end) in enumerate(items):
            frac = (j + 1) / (len(items) + 1)
            if side in ("left", "right"):
                port[(i, end)] = (x if side == "left" else x + w, y + h * frac)
            else:
                port[(i, end)] = (x + w * frac, y if side == "top" else y + h)

    for i, (_s, _t, lab, _is_back) in enumerate(records):
        (x1, y1), (x2, y2) = port[(i, "s")], port[(i, "t")]
        if lr:
            tip = x2 - 3 if x2 > x1 else x2 + 3
            xm = (x1 + x2) / 2
            d = (
                f"M{x1:.1f},{y1:.1f} L{tip:.1f},{y2:.1f}"
                if abs(y1 - y2) < 1
                else f"M{x1:.1f},{y1:.1f} C{xm:.1f},{y1:.1f} {xm:.1f},{y2:.1f} {tip:.1f},{y2:.1f}"
            )
            lx, ly, anchor_x = xm, (y1 + y2) / 2 - 6, "middle"
        else:
            tip = y2 - 3 if y2 > y1 else y2 + 3
            ym = (y1 + y2) / 2
            d = (
                f"M{x1:.1f},{y1:.1f} L{x2:.1f},{tip:.1f}"
                if abs(x1 - x2) < 1
                else f"M{x1:.1f},{y1:.1f} C{x1:.1f},{ym:.1f} {x2:.1f},{ym:.1f} {x2:.1f},{tip:.1f}"
            )
            lx, ly, anchor_x = (x1 + x2) / 2 + 8, ym + 4, "start"
        out.append(
            f'<path d="{d}" fill="none" stroke="{MUTED}" stroke-width="1.5" marker-end="url(#{prefix}-a)"/>'
        )
        if lab:
            out.append(put_label(lx, ly, anchor_x, lab))

    # Loops that skip columns: a dashed arc around the figure.
    for s_id, t_id, lab in arcs:
        (sx, sy, sw_, sh), (tx, ty, tw, th) = pos[s_id][:4], pos[t_id][:4]
        if lr:
            x1, y1, x2, y2 = sx + sw_ / 2, sy + sh, tx + tw / 2, ty + th
            low = height - 22
            d = f"M{x1:.1f},{y1:.1f} C{x1:.1f},{low:.1f} {x2:.1f},{low:.1f} {x2:.1f},{y2 + 3:.1f}"
            lx, ly, anchor_x = (x1 + x2) / 2, low + 4, "middle"
        else:
            x1, y1, x2, y2 = sx + sw_, sy + sh / 2, tx + tw, ty + th / 2
            far = width - 22
            d = f"M{x1:.1f},{y1:.1f} C{far:.1f},{y1:.1f} {far:.1f},{y2:.1f} {x2 + 3:.1f},{y2:.1f}"
            lx, ly, anchor_x = far - 4, (y1 + y2) / 2, "middle"
        out.append(
            f'<path d="{d}" fill="none" stroke="{MUTED}" stroke-width="1.5" stroke-dasharray="5 4" '
            f'marker-end="url(#{prefix}-a)"/>'
        )
        if lab:
            out.append(put_label(lx, ly, anchor_x, lab))
    out.extend(boxes)
    return _wrap_svg(out, width, height)


def _layers(fig: Figure, prefix: str) -> str | None:
    layers = fig.layers[:MAX_LAYERS]
    if len(layers) < 2:
        return None
    step_x, step_y, r, show_max = 150, 28, 9, 6
    shown = [min(max(layer.units, 1), show_max) for layer in layers]
    tallest = max(shown) * step_y
    width = PAD * 2 + step_x * (len(layers) - 1) + 40
    height = PAD * 2 + tallest + 54
    cx = [PAD + 20 + i * step_x for i in range(len(layers))]
    top = PAD

    def ys(i: int) -> list[float]:
        n = shown[i]
        start = top + (tallest - n * step_y) / 2 + step_y / 2
        return [start + k * step_y for k in range(n)]

    out = []
    for i in range(len(layers) - 1):  # connections first, so circles sit on top
        for ya in ys(i):
            for yb in ys(i + 1):
                out.append(
                    f'<line x1="{cx[i]}" y1="{ya:.1f}" x2="{cx[i + 1]}" y2="{yb:.1f}" stroke="{FAINT}" stroke-width="1"/>'
                )
    for i, layer in enumerate(layers):
        fill, stroke, sw, _ = STYLES[layer.accent]
        for y in ys(i):
            out.append(
                f'<circle cx="{cx[i]}" cy="{y:.1f}" r="{r}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>'
            )
        if layer.units > show_max:  # more units than we draw
            out.append(_text(cx[i], ys(i)[-2] + step_y / 2 + 4, "⋮", "s", MUTED))
        label_y = top + tallest + 22
        out.append(_text(cx[i], label_y, layer.label, "s", BODY))
        sub = layer.sub or (f"{layer.units} units" if layer.units > 1 else "")
        if sub:
            out.append(_text(cx[i], label_y + 16, sub, "m", MUTED))
    return _wrap_svg(out, width, height)


MAX_LR_WIDTH = 920


def _view_width(svg: str) -> int:
    m = re.search(r'viewBox="0 0 (\d+) ', svg)
    return int(m.group(1)) if m else 0


def _wrap_svg(parts: list[str], width: float, height: float) -> str:
    return (
        f'<svg viewBox="0 0 {int(width)} {int(height)}" role="img" xmlns="http://www.w3.org/2000/svg" '
        f'style="width:100%;max-width:{int(width)}px;margin:0 auto">{"".join(parts)}</svg>'
    )


def draw_figure(fig: Figure | None, prefix: str) -> str | None:
    """SVG markup for a figure, or None when it is too small or malformed to draw."""
    if fig is None:
        return None
    try:
        if fig.kind != "flow":
            return _layers(fig, prefix)
        svg = _flow(fig, prefix)
        if svg and fig.direction == "LR" and _view_width(svg) > MAX_LR_WIDTH:
            # Too wide to stay readable on a page: draw it top to bottom instead.
            svg = _flow(fig.model_copy(update={"direction": "TB"}), prefix) or svg
        return svg
    except (KeyError, ValueError, ZeroDivisionError):
        return None
