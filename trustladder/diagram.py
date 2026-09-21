"""
The TrustLadder architecture diagram, drawn as SVG.

One drawing is used everywhere: the portal's "How decisions are made" screen
shows it directly, and the capstone deck renders it to PNG for slide 5. Which
components are BUILT (solid) and which are TARGET design (dashed) is read from
architecture.json, so the diagram, the portal and the deck can never disagree.

Layout (canvas 1680 x 760):

    HOSPITAL (issuer)   NEUTRAL REGISTRY   INSURER / TPA: TrustLadder engine
    billing system      issuer directory   intake: portal · API · batch
    publisher           signed code files  doc intelligence → 1 Detect → 2 Decide ⇄ 3 Verify → 4 Explain+review
    private ledger                         verdict row
                                           governance + MLOps
    customer ─────────────────────────────▶ (submits through the portal)

Numbered flows ① to ⑧ follow one bill from issue to decision.

    python -m trustladder.diagram      # writes assets/architecture.svg
"""

from __future__ import annotations

import json
import textwrap
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = json.loads((Path(__file__).resolve().parent / "architecture.json").read_text())

W, H = 1680, 760
INK, TEAL, TEAL_D, SLATE = "#1B2A41", "#0F766E", "#134E4A", "#334155"
MUTED, LINE, BG, AMBER, AI = "#64748B", "#CBD5E1", "#F8FAFC", "#B45309", "#7C3AED"
FONT = "Helvetica Neue, Helvetica, Arial, sans-serif"

# Which components are AI models (tagged with the violet "AI" pill).
AI_COMPONENTS = {
    "Vision-language model reading", "Layout transformer (LayoutLM / Donut)", "Visual tamper models",
    "Generative-AI forgery detector", "Issuer knowledge graph", "Tool-calling evidence agent",
    "LLM reason writer, GraphRAG over policy", "Feedback to retraining",
}


def _built() -> dict[str, bool]:
    return {c["label"]: c["built"] for layer in SPEC["layers"] + [SPEC["foundation"]]
            for c in layer["components"]}


def _text(x, y, s, size=12, weight=400, fill=INK, anchor="start", italic=False) -> str:
    style = ' font-style="italic"' if italic else ""
    return (f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" font-weight="{weight}" '
            f'fill="{fill}" text-anchor="{anchor}"{style}>{escape(s)}</text>')


def _lines(x, y, lines, size=12, weight=400, fill=INK, anchor="middle", gap=None) -> str:
    gap = gap or size * 1.22
    return "".join(_text(x, y + i * gap, ln, size, weight, fill, anchor) for i, ln in enumerate(lines))


def _card(x, y, w, h, label, built, dark=False) -> str:
    """One component. Solid teal = built; dashed outline = target design."""
    ai = label in AI_COMPONENTS
    if built:
        fill, stroke, dash, tc = TEAL, TEAL, "", "white"
    else:
        fill, stroke, dash, tc = ("#0B1B2E" if dark else "white"), ("#5EEAD4" if dark else TEAL), \
            ' stroke-dasharray="6 4"', ("#5EEAD4" if dark else TEAL)
    lines = textwrap.wrap(label, 24 if w < 200 else 30)[:3]
    ty = y + h / 2 - (len(lines) - 1) * 7 + 4
    out = (f'<g class="comp {"built" if built else "target"}" data-label="{escape(label)}">'
           f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{fill}" stroke="{stroke}" '
           f'stroke-width="1.6"{dash} filter="url(#sh)"/>'
           + _lines(x + w / 2, ty, lines, 11.5, 700, tc))
    if ai:
        out += (f'<rect x="{x + w - 30}" y="{y - 7}" width="26" height="15" rx="7.5" fill="{AI}"/>'
                + _text(x + w - 17, y + 4, "AI", 9, 800, "white", "middle"))
    return out + "</g>"


def _cyl(x, y, w, h, title, sub, fill="white", stroke=SLATE) -> str:
    """A data store, drawn as a cylinder."""
    ry = 9
    return (f'<g filter="url(#sh)"><path d="M{x},{y + ry} v{h - 2 * ry} a{w / 2},{ry} 0 0 0 {w},0 v{-(h - 2 * ry)}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.6"/>'
            f'<ellipse cx="{x + w / 2}" cy="{y + ry}" rx="{w / 2}" ry="{ry}" fill="{fill}" stroke="{stroke}" stroke-width="1.6"/></g>'
            + _text(x + w / 2, y + h / 2 + 6, title, 12.5, 700, INK, "middle")
            + _lines(x + w / 2, y + h / 2 + 23, textwrap.wrap(sub, 30), 10.5, 400, MUTED))


def _box(x, y, w, h, title, sub, fill="white", stroke=LINE, tc=INK, sc=MUTED) -> str:
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="9" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="1.4" filter="url(#sh)"/>'
            + _text(x + 14, y + 24, title, 13, 700, tc)
            + _lines(x + 14, y + 44, sub, 11, 400, sc, "start", 15))


def _arrow(points, color=SLATE, dash=False, width=1.8, marker="arr") -> str:
    d = "M" + " L".join(f"{px},{py}" for px, py in points)
    dash_attr = ' stroke-dasharray="7 5"' if dash else ""
    return (f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{width}"{dash_attr} '
            f'marker-end="url(#{marker})" stroke-linejoin="round"/>')


def _badge(x, y, n, color=INK) -> str:
    return (f'<circle cx="{x}" cy="{y}" r="11" fill="{color}" stroke="white" stroke-width="2"/>'
            + _text(x, y + 4.2, str(n), 11.5, 800, "white", "middle"))


def _zone(x, y, w, h, title, sub, colour, icon) -> str:
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{BG}" stroke="{LINE}" stroke-width="1.4"/>'
            f'<path d="M{x},{y + 14} a14,14 0 0 1 14,-14 h{w - 28} a14,14 0 0 1 14,14 v30 h{-w} z" fill="{colour}"/>'
            + icon(x + 16, y + 9)
            + _text(x + 46, y + 20, title, 13.5, 800, "white")
            + _text(x + 46, y + 36, sub, 10.5, 400, "#CBD5E1"))


# Small zone icons (white line drawings, 24 x 24).
def _ic_hospital(x, y):
    return (f'<g transform="translate({x},{y})" fill="none" stroke="white" stroke-width="1.8">'
            f'<rect x="3" y="5" width="18" height="17" rx="1.5"/><path d="M12 8v7M8.5 11.5h7"/>'
            f'<path d="M9 22v-4h6v4"/></g>')


def _ic_registry(x, y):
    return (f'<g transform="translate({x},{y})" fill="none" stroke="white" stroke-width="1.8">'
            f'<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6"/>'
            f'<path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/></g>')


def _ic_insurer(x, y):
    return (f'<g transform="translate({x},{y})" fill="none" stroke="white" stroke-width="1.8">'
            f'<path d="M12 2l8 3v6c0 5-3.4 9-8 11-4.6-2-8-6-8-11V5z"/><path d="M8.5 12l2.5 2.5 4.5-5"/></g>')


def _ic_person(x, y):
    return (f'<g transform="translate({x},{y})" fill="none" stroke="{AMBER}" stroke-width="2">'
            f'<circle cx="12" cy="7" r="4"/><path d="M4 22c0-4.4 3.6-8 8-8s8 3.6 8 8"/></g>')


def build_svg() -> str:
    built = _built()
    placed: list[str] = []

    def card(x, y, w, h, label, dark=False):
        placed.append(label)
        return _card(x, y, w, h, label, built[label], dark)

    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 52 {W} {H - 56}" width="{W}" height="{H - 56}" '
         f'role="img" aria-label="TrustLadder architecture">',
         '<defs>'
         '<filter id="sh" x="-10%" y="-10%" width="120%" height="130%">'
         '<feDropShadow dx="0" dy="1.2" stdDeviation="1.4" flood-color="#0F172A" flood-opacity="0.16"/></filter>'
         f'<marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
         f'<path d="M0,0 L10,5 L0,10 z" fill="{SLATE}"/></marker>'
         f'<marker id="arrT" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
         f'<path d="M0,0 L10,5 L0,10 z" fill="{TEAL}"/></marker>'
         f'<marker id="arrA" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
         f'<path d="M0,0 L10,5 L0,10 z" fill="{AMBER}"/></marker>'
         '</defs>',
         f'<rect width="{W}" height="{H}" fill="white"/>']

    # ---------------- zones
    s.append(_zone(16, 64, 300, 560, "HOSPITAL · ISSUER", "inside the hospital's own network", TEAL_D, _ic_hospital))
    s.append(_zone(340, 64, 240, 560, "NEUTRAL REGISTRY", "industry-run host", SLATE, _ic_registry))
    s.append(_zone(604, 64, 1060, 560, "INSURER / TPA · TRUSTLADDER ENGINE",
                   "inside the verifier's network: the claim document never leaves it", INK, _ic_insurer))

    # ---------------- hospital
    s.append(_box(36, 128, 260, 70, "Billing system (HIS)", ["issues the bill as today"]))
    s.append(_arrow([(166, 198), (166, 236)]))
    s.append(_badge(186, 217, 1))
    placed_pub = ("TrustLadder Publisher", ["prints a random ticket on each bill", "computes two scrambled codes",
                                            "signs the day's file (hospital key)"])
    s.append(_box(36, 238, 260, 108, placed_pub[0], placed_pub[1], fill=TEAL, stroke=TEAL, tc="white", sc="#CCFBF1"))
    s.append(_cyl(46, 380, 240, 96, "Private bill ledger", "salt, signing key and bills stay in the hospital"))
    s.append(_text(166, 520, "patient data never leaves", 11, 700, TEAL_D, "middle"))

    # ---------------- registry
    s.append(_cyl(358, 128, 204, 96, "Issuer directory", "name → public key → salt"))
    s.append(_cyl(358, 250, 204, 116, "Signed code files", "one line per bill: two codes, append-only"))
    s.append(_lines(460, 404, ["codes and signatures only", "no names, amounts or bills",
                               "a complete theft reveals nothing"], 11, 700, SLATE))
    # ② publisher -> code files
    s.append(_arrow([(296, 300), (358, 300)], TEAL, width=2.2, marker="arrT"))
    s.append(_badge(327, 285, 2, TEAL))
    s.append(_text(327, 326, "signed", 10, 700, TEAL, "middle"))
    s.append(_text(327, 339, "codes", 10, 700, TEAL, "middle"))

    # ---------------- engine: intake row
    x0, colw, gap = 624, 190, 17.5
    intake = SPEC["layers"][0]["components"]
    iw = (1020 - 2 * 12) / 3
    for i, c in enumerate(intake):
        s.append(card(x0 + i * (iw + 12), 114, iw, 44, c["label"]))
    # ④ intake -> document intelligence
    s.append(_arrow([(x0 + colw / 2, 158), (x0 + colw / 2, 204)]))

    # ---------------- engine: pipeline columns
    cols = [
        ("Document intelligence", "multimodal AI", SPEC["layers"][1]),
        ("1  Detect", "evidence models", SPEC["layers"][2]),
        ("2  Decide", "neuro-symbolic core", SPEC["layers"][3]),
        ("3  Verify", "agentic orchestration", SPEC["layers"][4]),
        ("4  Explain + review", "human-in-the-loop", SPEC["layers"][5]),
    ]
    xs = [x0 + i * (colw + gap) for i in range(5)]
    for (title, sub, layer), cx in zip(cols, xs):
        s.append(f'<rect x="{cx}" y="206" width="{colw}" height="38" rx="8" fill="#E2E8F0"/>')
        s.append(_text(cx + colw / 2, 222, title, 12.5, 800, INK, "middle"))
        s.append(_text(cx + colw / 2, 237, sub, 10, 400, MUTED, "middle", italic=True))
        for j, c in enumerate(layer["components"]):
            s.append(card(cx, 254 + j * 58, colw, 50, c["label"]))
    # arrows between columns (⑤ screen, ⑥ ask for proof)
    for i in range(4):
        a, b = xs[i] + colw, xs[i + 1]
        if i == 2:     # Decide <-> Verify
            s.append(_arrow([(a + 1, 318), (b - 1, 318)], TEAL, marker="arrT"))
            s.append(_arrow([(b - 1, 346), (a + 1, 346)], TEAL, marker="arrT"))
        else:
            s.append(_arrow([(a + 1, 332), (b - 1, 332)]))
    s.append(_badge(xs[0] + 2, 207, 4))
    s.append(_badge(xs[1] + 2, 207, 5))
    s.append(_badge(xs[3] + 2, 207, 6, TEAL))

    # verdict row under Decide..Explain
    vx, vw = xs[2], xs[4] + colw - xs[2]
    s.append(f'<rect x="{vx}" y="494" width="{vw}" height="32" rx="16" fill="white" stroke="{INK}" stroke-width="1.4"/>')
    s.append(_badge(vx + 18, 510, 7))
    s.append(_text(vx + 36, 515, "Verdict + evidence grade + reason:", 12, 800, INK))
    s.append(_text(vx + 262, 515, "Authentic → paid automatically   ·   all other verdicts → officer",
                   11.5, 400, INK))
    s.append(_badge(xs[4] + 2, 207, 8))

    # governance band
    s.append(f'<rect x="{x0}" y="540" width="1020" height="70" rx="10" fill="{INK}"/>')
    s.append(_text(x0 + 16, 572, "Governance + MLOps", 13, 800, "white"))
    s.append(_text(x0 + 16, 590, "across every stage", 10.5, 400, "#94A3B8", italic=True))
    gw = (1020 - 200 - 3 * 10) / 4
    for i, c in enumerate(SPEC["foundation"]["components"]):
        s.append(card(x0 + 190 + i * (gw + 10), 552, gw, 46, c["label"], dark=True))

    # ---------------- registry mirror -> Verify (teal, dashed)
    vc = xs[3] + colw / 2
    s.append(_arrow([(562, 300), (584, 300), (584, 184), (vc, 184), (vc, 204)], TEAL, dash=True, width=2, marker="arrT"))
    s.append(_text(900, 178, "mirror of signed codes: the lookup stays inside the insurer", 10.5, 700, TEAL))

    # ---------------- customer path (amber, dashed)
    s.append(_ic_person(154, 646))
    s.append(_text(166, 690, "Patient / customer", 12, 800, AMBER, "middle"))
    s.append(_text(166, 705, "receives the bill with its ticket", 10, 400, MUTED, "middle"))
    s.append(_arrow([(166, 624), (166, 642)], AMBER, width=2, marker="arrA"))
    s.append(_arrow([(196, 668), (592, 668), (592, 136), (620, 136)], AMBER, dash=True, width=2, marker="arrA"))
    s.append(_badge(400, 668, 3, AMBER))
    s.append(_text(400, 690, "submits the claim through the portal", 10.5, 700, AMBER, "middle"))

    # ---------------- legend
    lx, ly = 900, 646
    s.append(f'<rect x="{lx - 12}" y="{ly - 12}" width="776" height="96" rx="10" fill="white" stroke="{LINE}"/>')
    s.append(f'<rect x="{lx}" y="{ly}" width="46" height="22" rx="6" fill="{TEAL}"/>')
    s.append(_text(lx + 56, ly + 15, "Built: runs in the working demo today", 11.5, 400, INK))
    s.append(f'<rect x="{lx}" y="{ly + 32}" width="46" height="22" rx="6" fill="white" stroke="{TEAL}" '
             f'stroke-width="1.6" stroke-dasharray="6 4"/>')
    s.append(_text(lx + 56, ly + 47, "Target design using current AI models (not claimed as built)", 11.5, 400, INK))
    s.append(f'<rect x="{lx + 440}" y="{ly + 3}" width="26" height="15" rx="7.5" fill="{AI}"/>')
    s.append(_text(lx + 453, ly + 14, "AI", 9, 800, "white", "middle"))
    s.append(_text(lx + 474, ly + 15, "AI model component", 11.5, 400, INK))
    s.append(_badge(lx + 452, ly + 43, "1"))
    s.append(_text(lx + 474, ly + 47, "① to ⑧ follow one bill from issue to decision", 11.5, 400, INK))
    s.append(_text(lx + 474, ly + 63, "⇄ Decide asks Verify for proof, then decides", 11.5, 400, TEAL))
    s.append(_text(lx, ly + 76, "Only codes travel between the three parties. A forger can look up, never add.",
                   11.5, 700, TEAL))
    s.append("</svg>")

    missing = set(_built()) - set(placed)
    if missing:
        raise RuntimeError(f"architecture.json components not drawn: {sorted(missing)}")
    return "".join(s)


def write(path: Path | None = None) -> Path:
    path = path or ROOT / "assets" / "architecture.svg"
    path.write_text(build_svg())
    return path


if __name__ == "__main__":
    print(write())
