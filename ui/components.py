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

from .theme import REGISTRY_COLOUR, STATUS_COLOUR, TEAL, VERDICT_COLOUR, chip

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


def details_on() -> bool:
    """The one switch that reveals technical detail (sidebar: 'Show technical details')."""
    return bool(st.session_state.get("details", False))


def verdict_banner(r: LadderResult, next_step: str = "") -> None:
    colour = VERDICT_COLOUR[r.verdict.value]
    st.markdown(
        f'<div class="tl-verdict" style="background:{colour}">'
        f'<div class="big" data-testid="tl-verdict">{r.verdict.value}</div>'
        f'<div class="sub">{next_step or r.queue}</div></div>', unsafe_allow_html=True)


def claim_receipt(status: str, pdf_bytes: bytes, next_step: str) -> None:
    """What the CUSTOMER sees after submitting a claim: the status, and nothing more.

    A policyholder is never shown the verdict, the findings or the four-stage ladder.
    Two reasons, and the second one matters more.

    It is not their language. "Tampered", "R2" and "Verify" are an insurer's words, and a
    customer wants to know whether they are getting paid and whether they must do anything.

    And it would coach a fraudster. Someone who has just uploaded an edited bill and is told
    the machine called it "Tampered", with the checks that caught it, learns exactly what to
    change next time. An honest customer gains nothing from that detail either. So the
    customer sees the status an insurer would really send them, and the evidence stays with
    the people who act on it: the claims officer and the auditor.
    """
    colour = STATUS_COLOUR.get(status, "#475569")
    col_doc, col_status = st.columns([2, 3], gap="large")
    with col_doc:
        st.image(preview_png(pdf_bytes), width="stretch")
    with col_status:
        st.markdown(
            f'<div class="tl-verdict" style="background:{colour}">'
            f'<div class="big" data-testid="tl-claim-status">{status}</div>'
            f'<div class="sub">Your claim</div></div>', unsafe_allow_html=True)
        st.markdown(f'<div class="tl-reason">{next_step}</div>', unsafe_allow_html=True)
        st.caption("Questions about this claim? Contact your insurer and quote the claim number.")


_REGISTRY_PLAIN = {
    "Verified": ("Hospital confirms it", "ok"),
    "Mismatch": ("Hospital's record differs", "bad"),
    "Not issued": ("Hospital says it issued no such bill", "bad"),
    "No record": ("Outside what the hospital has published", "neutral"),
    "Not covered": ("Hospital not in registry yet", "neutral"),
    "Not asked": ("Not asked (unreadable)", "neutral"),
}
_TONE = {"ok": "#15803D", "warn": "#B45309", "bad": "#B91C1C", "neutral": "#475569"}


def ladder_strip(r: LadderResult) -> None:
    """The four stages as one line anyone can read: what each step concluded."""
    if r.read_status.value != "Read":
        detect = ("Could not read the copy", "neutral")
    elif r.findings:
        n = len({f.family for f in r.findings})
        detect = (f"{n} kind{'s' if n != 1 else ''} of problem seen", "warn")
    else:
        detect = ("Nothing wrong seen", "ok")
    verify = _REGISTRY_PLAIN[r.registry_answer.value]
    decide = (r.verdict.value, {"Authentic": "ok", "Suspicious": "warn", "Tampered": "bad",
                                "Inconclusive": "neutral"}[r.verdict.value])
    person = ("Not needed", "ok") if not r.needs_human else ("A person decides", "warn")
    cells = [("1 Detect", detect), ("2 Decide", decide), ("3 Verify", verify), ("4 Review", person)]
    html = "".join(
        f'<div class="tl-step"><div class="n">{name}</div>'
        f'<div class="v" style="color:{_TONE[tone]}">{text}</div></div>' for name, (text, tone) in cells)
    st.markdown(f'<div class="tl-strip">{html}</div>', unsafe_allow_html=True)


def plain_reason(r: LadderResult) -> str:
    """One or two sentences: the verdict's meaning and what the hospital's record says."""
    opener = r.reason.split(". ")[0].rstrip(".") + "."
    second = r.read_note if r.registry_answer.value == "Not asked" else r.registry_note
    return f"{opener} {second}"


def verdict_view(r: LadderResult, pdf_bytes: bytes, next_step: str = "", show_graph: bool = True,
                 actions=None, show_findings: bool = False) -> None:
    """One verdict. Simple by default; the technical detail appears with the switch.

    `show_findings` lists what was actually found, in plain words, without the switch.
    The claims officer gets it: they are the person being asked to call something fraud,
    and a count like "3 kinds of problem seen" is not something anyone can act on. Rule
    numbers and the evidence graph stay behind the switch, so the screen stays readable.
    """
    col_doc, col_ladder = st.columns([2, 3], gap="large")
    with col_doc:
        st.image(preview_png(pdf_bytes), width="stretch")
    with col_ladder:
        verdict_banner(r, next_step or r.queue)
        st.markdown(f'<div class="tl-reason">{plain_reason(r)}</div>', unsafe_allow_html=True)
        if show_findings and not details_on():
            if r.findings:
                st.markdown("**What was found**")
                for f in r.findings:
                    st.markdown(f"- {f.detail}")
            elif r.read_status.value != "Read":
                st.markdown("**What was found**")
                st.markdown(f"- {r.read_note}")
            else:
                st.markdown("**What was found**")
                st.markdown("- Nothing wrong in the document itself. That is not proof: a bill typed "
                            "from a blank page leaves no traces either, which is why it is not cleared.")
        ladder_strip(r)
        if actions:
            actions()
    if not details_on():
        return
    st.divider()
    st.markdown("**Technical details**")
    st.markdown(f"**Full reason.** {r.reason}")
    st.caption("Reason drafted from the findings only and checked back against the verdict "
               "(a template in this demo; target design: an LLM grounded by GraphRAG over the "
               "institution's policy, with the same guardrail check-back).")
    graph = evidence.build(r)
    s1, s2, s3, s4 = st.columns(4)
    with s1.container(border=True):
        _stage_header(1, "Detect")
        if r.read_status.value != "Read":
            st.caption(r.read_note)
        elif not r.findings:
            st.caption("No edit traces. This is not proof: a fake typed from a blank page leaves none either.")
        for f in r.findings:
            st.caption(f"• [{f.family}] {f.detail}")
    with s2.container(border=True):
        _stage_header(2, "Decide")
        st.markdown(chip(r.evidence_grade.value, TEAL) +
                    chip(f"{graph.independent_against} independent line"
                         f"{'s' if graph.independent_against != 1 else ''} against", "#475569"),
                    unsafe_allow_html=True)
        st.caption(f"Rule {r.rule_applied}")
    with s3.container(border=True):
        _stage_header(3, "Verify")
        st.markdown(chip(r.registry_answer.value, REGISTRY_COLOUR[r.registry_answer.value]),
                    unsafe_allow_html=True)
        st.caption(r.registry_note)
    with s4.container(border=True):
        _stage_header(4, "Human review")
        st.caption(r.queue)
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
    st.download_button("Download the full-size diagram (SVG)", svg, "TrustLadder_architecture.svg",
                       mime="image/svg+xml", key="arch_svg")
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
    ("R2", "The issuer issued that ticket, but with other details (Mismatch)", "Tampered"),
    ("R3", "The issuer published every bill it issued and this is not one (Not issued)", "Tampered"),
    ("R4", "No issuer answer, and two independent screening findings", "Tampered"),
    ("R5", "No issuer answer, one screening finding", "Suspicious"),
    ("R6", "No issuer answer, nothing looks wrong", "Inconclusive"),
]


def rule_table() -> None:
    rows = "".join(
        f"<tr><td><b>{rid}</b></td><td>{cond}</td><td>{chip(v, VERDICT_COLOUR[v])}</td>"
        f"<td style='color:#64748B'>{RULE_TEXT[rid]}</td></tr>" for rid, cond, v in RULE_TABLE)
    st.markdown(
        "<table style='width:100%;border-collapse:collapse;font-size:0.88rem'>"
        "<tr style='text-align:left;border-bottom:1px solid #CBD5E1'><th>Rule</th><th>When</th>"
        f"<th>Verdict</th><th>Why</th></tr>{rows}</table>", unsafe_allow_html=True)
