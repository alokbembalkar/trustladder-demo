"""
Reusable screen pieces: the verdict view, the bill preview, the architecture
diagram and the published rule. Every role that shows a verdict uses the same
verdict view, so a verdict looks identical to the officer, the auditor and the
presenter.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pypdfium2 as pdfium
import streamlit as st

from trustladder import evidence
from trustladder.decide import RULE_TEXT
from trustladder.models import LadderResult

from .theme import REGISTRY_COLOUR, TEAL, VERDICT_COLOUR, chip

ARCH = json.loads((Path(__file__).resolve().parent.parent / "trustladder" / "architecture.json").read_text())


@st.cache_data(show_spinner=False)
def preview_png(pdf_bytes: bytes):
    """First page of a PDF as an image (top part, where the bill is)."""
    page = pdfium.PdfDocument(pdf_bytes)[0]
    img = page.render(scale=1.15).to_pil()
    return img.crop((0, 0, img.width, int(img.height * 0.64)))


def _stage_header(n: int, name: str) -> None:
    stage = ARCH["stages"][name]
    st.markdown(f'<div class="tl-stage-title">{n} · {name} ({stage["sub"]})</div>'
                f'<div class="tl-ai">AI inside (target design): {stage["ai_inside"]}</div>',
                unsafe_allow_html=True)


def verdict_banner(r: LadderResult, next_step: str = "") -> None:
    colour = VERDICT_COLOUR[r.verdict.value]
    st.markdown(
        f'<div class="tl-verdict" style="background:{colour}">'
        f'<div class="big" data-testid="tl-verdict">{r.verdict.value}</div>'
        f'<div class="sub">Risk: {r.risk} · Evidence: {r.evidence_grade.value}'
        f'{" · Next: " + next_step if next_step else ""}</div></div>', unsafe_allow_html=True)


def verdict_view(r: LadderResult, pdf_bytes: bytes, next_step: str = "", show_graph: bool = True) -> None:
    """The full explanation of one verdict: bill, banner, reason, four stages, graph."""
    col_doc, col_ladder = st.columns([2, 3], gap="large")
    with col_doc:
        st.image(preview_png(pdf_bytes), width="stretch")
    with col_ladder:
        verdict_banner(r, next_step or r.queue)
        st.markdown(f"**Reason.** {r.reason}")
        st.caption("Reason drafted from the findings only and checked back against the verdict "
                   "(a template in this demo; target design: an LLM grounded by GraphRAG over the "
                   "institution's policy, with the same guardrail check-back).")
        graph = evidence.build(r)
        s1, s2 = st.columns(2)
        with s1.container(border=True):
            _stage_header(1, "Detect")
            if r.read_status.value != "Read":
                st.markdown(chip("Could not read", "#475569"), unsafe_allow_html=True)
                st.caption(r.read_note)
            elif not r.findings:
                st.markdown(chip("Nothing found", "#475569"), unsafe_allow_html=True)
                st.caption("No edit traces. This is not proof: a fake typed from a blank page leaves none either.")
            else:
                fams = sorted({f.family for f in r.findings})
                st.markdown(chip(f"{len(r.findings)} finding{'s' if len(r.findings) != 1 else ''} in "
                                 f"{len(fams)} famil{'ies' if len(fams) != 1 else 'y'}", "#B45309"),
                            unsafe_allow_html=True)
                for f in r.findings:
                    st.caption(f"• [{f.family}] {f.detail}")
        with s2.container(border=True):
            _stage_header(2, "Decide")
            st.markdown(chip(r.evidence_grade.value, TEAL) +
                        chip(f"{graph.independent_against} independent line"
                             f"{'s' if graph.independent_against != 1 else ''} against", "#475569"),
                        unsafe_allow_html=True)
            st.caption(f"Rule {r.rule_applied}")
        s3, s4 = st.columns(2)
        with s3.container(border=True):
            _stage_header(3, "Verify")
            ans = r.registry_answer.value
            st.markdown(chip(ans, REGISTRY_COLOUR[ans]), unsafe_allow_html=True)
            st.caption(r.registry_note)
        with s4.container(border=True):
            _stage_header(4, "Human review")
            if r.needs_human:
                st.markdown(chip("Sent to a person", "#B45309"), unsafe_allow_html=True)
                st.caption(r.queue)
            else:
                st.markdown(chip("Not needed", "#15803D"), unsafe_allow_html=True)
                st.caption("Proof-grade evidence: paid straight through.")
    if show_graph:
        st.markdown("**Evidence graph** · findings grouped into independent families, which the published "
                    "rule turns into the verdict. Two symptoms of one edit never count twice.")
        st.graphviz_chart(evidence.to_dot(r, graph), width="stretch")
    with st.expander("Audit record (what is written to the claim file)"):
        rec = dataclasses.asdict(r)
        for k in ("read_status", "registry_answer", "verdict", "evidence_grade"):
            rec[k] = getattr(r, k).value
        st.json(rec)


def architecture_diagram() -> None:
    """The architecture drawing (trustladder/diagram.py), the same one as slide 5 of the deck."""
    import base64
    from trustladder.diagram import build_svg
    svg = build_svg()
    uri = "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()
    st.markdown(f'<div data-testid="tl-architecture"><img src="{uri}" style="width:100%" '
                f'alt="TrustLadder architecture diagram"/></div>', unsafe_allow_html=True)
    comps = [c for l in ARCH["layers"] + [ARCH["foundation"]] for c in l["components"]]
    built = [c["label"] for c in comps if c["built"]]
    target = [c["label"] for c in comps if not c["built"]]
    a1, a2 = st.columns(2)
    a1.markdown(f"**Built in this demo ({len(built)})**")
    a1.caption(" · ".join(built))
    a2.markdown(f"**Target design ({len(target)})**")
    a2.caption(" · ".join(target))


RULE_TABLE = [
    ("R0", "The bill could not be read", "Inconclusive"),
    ("R1", "The issuer confirms the bill (Verified)", "Authentic"),
    ("R2", "Issuer: Mismatch, plus at least one independent screening finding", "Tampered"),
    ("R3", "Issuer: Mismatch, nothing else", "Suspicious"),
    ("R4", "Issuer: No record, plus at least one independent screening finding", "Tampered"),
    ("R5", "Issuer: No record, nothing else", "Suspicious"),
    ("R6", "Issuer not joined, two or more independent screening findings", "Tampered"),
    ("R7", "Issuer not joined, one screening finding", "Suspicious"),
    ("R8", "Issuer not joined, nothing looks wrong", "Inconclusive"),
]


def rule_table() -> None:
    rows = "".join(
        f"<tr><td><b>{rid}</b></td><td>{cond}</td><td>{chip(v, VERDICT_COLOUR[v])}</td>"
        f"<td style='color:#64748B'>{RULE_TEXT[rid]}</td></tr>" for rid, cond, v in RULE_TABLE)
    st.markdown(
        "<table style='width:100%;border-collapse:collapse;font-size:0.88rem'>"
        "<tr style='text-align:left;border-bottom:1px solid #CBD5E1'><th>Rule</th><th>When</th>"
        f"<th>Verdict</th><th>Why</th></tr>{rows}</table>", unsafe_allow_html=True)
