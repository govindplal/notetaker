import re
import xml.etree.ElementTree as ET

from notetaker.notes.model import Block, FigEdge, FigLayer, FigNode, Figure, LectureNotes, Section
from notetaker.render import html as render
from notetaker.render.figures import draw_figure


def _flow(**kw) -> Figure:
    nodes = [
        FigNode(id="a", label="Prompt"),
        FigNode(id="b", label="Retriever", accent="highlight"),
        FigNode(id="c", label="LLM"),
    ]
    edges = [FigEdge(source="a", target="b", label="query"), FigEdge(source="b", target="c")]
    return Figure(
        kind="flow",
        nodes=kw.get("nodes", nodes),
        edges=kw.get("edges", edges),
        direction=kw.get("direction", "LR"),
    )


def _rects(svg: str):
    root = ET.fromstring(svg)
    ns = "{http://www.w3.org/2000/svg}"
    return [
        tuple(float(r.get(k)) for k in ("x", "y", "width", "height"))
        for r in root.iter(f"{ns}rect")
    ]


def test_flow_is_valid_xml_with_all_labels_and_arrows():
    svg = draw_figure(_flow(), "f1")
    ET.fromstring(svg)  # well-formed
    for text in ("Prompt", "Retriever", "LLM", "query"):
        assert text in svg
    assert svg.count("marker-end") == 2
    assert 'id="f1-a"' in svg and "url(#f1-a)" in svg


def test_left_to_right_layers_increase_in_x_and_boxes_do_not_overlap():
    rects = _rects(draw_figure(_flow(), "f1"))
    xs = [r[0] for r in rects]
    assert xs == sorted(xs) and len(set(xs)) == 3
    for i, a in enumerate(rects):
        for b in rects[i + 1 :]:
            overlap_x = a[0] < b[0] + b[2] and b[0] < a[0] + a[2]
            overlap_y = a[1] < b[1] + b[3] and b[1] < a[1] + a[3]
            assert not (overlap_x and overlap_y)


def test_top_to_bottom_layers_increase_in_y():
    rects = _rects(draw_figure(_flow(direction="TB"), "f1"))
    ys = [r[1] for r in rects]
    assert ys == sorted(ys) and len(set(ys)) == 3


def test_branches_share_a_layer_and_stack_vertically():
    nodes = [FigNode(id=i, label=i.upper()) for i in "abcd"]
    edges = [
        FigEdge(source="a", target="b"),
        FigEdge(source="a", target="c"),
        FigEdge(source="b", target="d"),
        FigEdge(source="c", target="d"),
    ]
    rects = _rects(draw_figure(_flow(nodes=nodes, edges=edges), "f1"))
    b, c = rects[1], rects[2]
    assert b[0] == c[0] and b[1] != c[1]


def test_loop_edges_are_drawn_dashed_instead_of_crashing():
    edges = [
        FigEdge(source="a", target="b"),
        FigEdge(source="b", target="c"),
        FigEdge(source="c", target="a", label="repeat"),
    ]
    svg = draw_figure(_flow(edges=edges), "f1")
    assert 'stroke-dasharray="5 4"' in svg and "repeat" in svg
    assert len(_rects(svg)) == 3


def test_text_is_escaped_and_unknown_edges_ignored():
    nodes = [FigNode(id="a", label="<script>x</script> & co"), FigNode(id="b", label="B")]
    edges = [
        FigEdge(source="a", target="b"),
        FigEdge(source="a", target="zzz"),
        FigEdge(source="a", target="a"),
    ]
    svg = draw_figure(_flow(nodes=nodes, edges=edges), "f1")
    assert "<script>" not in svg and "&lt;script&gt;" in svg
    ET.fromstring(svg)
    assert svg.count("marker-end") == 1


def test_long_labels_wrap_and_truncate():
    nodes = [
        FigNode(id="a", label="A very long label that keeps going and going and going on"),
        FigNode(id="b", label="B"),
    ]
    svg = draw_figure(_flow(nodes=nodes, edges=[FigEdge(source="a", target="b")]), "f1")
    assert "…" in svg
    assert max(len(t) for t in re.findall(r">([^<]+)</text>", svg)) <= 24


def test_too_small_or_missing_figures_return_none():
    assert draw_figure(None, "f") is None
    assert draw_figure(_flow(nodes=[FigNode(id="a", label="Only")], edges=[]), "f") is None
    assert draw_figure(Figure(kind="layers", layers=[FigLayer(label="In", units=3)]), "f") is None


def test_layers_figure_draws_capped_circles_and_labels():
    fig = Figure(
        kind="layers",
        layers=[
            FigLayer(label="Input", units=784, sub="28x28 pixels"),
            FigLayer(label="Hidden", units=3),
            FigLayer(label="Output", units=10, accent="highlight"),
        ],
    )
    svg = draw_figure(fig, "f1")
    ET.fromstring(svg)
    assert svg.count("<circle") == 6 + 3 + 6  # at most 6 drawn per layer
    assert "28x28 pixels" in svg and "10 units" in svg and "⋮" in svg


def test_figure_blocks_render_in_the_page_and_bad_ones_are_dropped():
    notes = LectureNotes(
        title="T",
        lead="L",
        sections=[
            Section(
                heading="H",
                status="new",
                concept="c",
                summary="s",
                blocks=[
                    Block(type="figure", figure=_flow(), caption="first"),
                    Block(
                        type="figure",
                        figure=_flow(nodes=[FigNode(id="a", label="x")], edges=[]),
                        caption="dropped",
                    ),
                    Block(type="figure", figure=_flow(), caption="second"),
                ],
            )
        ],
    )
    out = render.render_lecture(notes, course_title="C", lecture_idx=1)
    assert out.count('<figure class="canvas">') == 2
    assert 'id="f1-a"' in out and 'id="f3-a"' in out  # unique per figure
    assert "first" in out and "second" in out and "dropped" not in out
