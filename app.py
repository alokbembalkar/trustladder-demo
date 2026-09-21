"""
TrustLadder demo: the screen the panel sees.

    .venv/bin/streamlit run app.py --server.port 8765

Five tabs, following one hospital bill through the whole system:

    1 Check a bill      the insurer's view: a bill goes up the ladder and comes
                        back with a verdict, an evidence grade and a reason
    2 Human review      every verdict except Authentic lands here; the officer's
                        decision is logged next to the machine's
    3 Hospital          the issuer's view: issue a bill, print its ticket,
                        publish two codes (and make an altered copy to test with)
    4 Registry          what the neutral registry actually holds: codes and
                        signatures, no patient data
    5 Policy & impact   the institution sets what happens to each verdict and
                        sees the cost of being wrong in both directions, over a
                        simulated month, against an edit-detection tool

Everything is synthetic and runs offline on a laptop.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import hashlib
import json
import shutil
from pathlib import Path

import pandas as pd
import pypdfium2 as pdfium
import streamlit as st

from trustladder import RULE_VERSION, crypto
from trustladder import evidence
from trustladder.bills import format_inr, render_altered, render_genuine
from trustladder.cases import DATA_DIR, HOSPITALS, SHOWCASE_JOINED, build_showcase
from trustladder.models import BillFields, LineItem, Verdict
from trustladder.pipeline import run_ladder
from trustladder.policy import (ALLOWED, COST_PER_HOUR_RS, DEFAULT_POLICY,
                                MINUTES_PER_REVIEW, baseline, evaluate)
from trustladder.registry import Publisher, RegistryStore, Verifier

st.set_page_config(page_title="TrustLadder demo", page_icon="🪜", layout="wide")

# The architecture (layers, components, built vs target, stage wording) is shared
# with the capstone deck through this one file, so the two always agree.
ARCH = json.loads((Path(__file__).parent / "trustladder" / "architecture.json").read_text())

# --------------------------------------------------------------------------
# Look and feel
# --------------------------------------------------------------------------

INK = "#1B2A41"
TEAL = "#0F766E"
VERDICT_COLOUR = {
    "Authentic": "#15803D",
    "Suspicious": "#B45309",
    "Tampered": "#B91C1C",
    "Inconclusive": "#475569",
}
REGISTRY_COLOUR = {
    "Verified": "#15803D", "Mismatch": "#B91C1C", "No record": "#B45309",
    "Not covered": "#475569", "Not asked": "#475569",
}

st.markdown(f"""
<style>
  .block-container {{ padding-top: 1.6rem; max-width: 1280px; }}
  h1, h2, h3 {{ color: {INK}; }}
  .tl-chip {{ display:inline-block; padding:2px 10px; border-radius:999px; color:white;
             font-weight:600; font-size:0.85rem; }}
  .tl-verdict {{ border-radius:10px; padding:16px 20px; color:white; margin:6px 0 10px 0; }}
  .tl-verdict .big {{ font-size:1.7rem; font-weight:700; }}
  .tl-verdict .sub {{ font-size:0.95rem; opacity:0.95; }}
  .tl-stage-title {{ font-weight:700; color:{INK}; font-size:1.02rem; }}
  .tl-muted {{ color:#64748B; font-size:0.86rem; }}
  .tl-ai {{ color:{TEAL}; font-size:0.78rem; font-style:italic; margin:2px 0 6px 0; }}
  .tl-layer {{ display:flex; gap:8px; align-items:stretch; background:#F1F5F9; border-radius:8px;
              padding:8px; margin-bottom:6px; }}
  .tl-layer.found {{ background:{INK}; }}
  .tl-lname {{ width:190px; flex:none; font-weight:700; color:{INK}; font-size:0.92rem; }}
  .tl-layer.found .tl-lname {{ color:white; }}
  .tl-lname span {{ display:block; font-weight:400; font-style:italic; color:#64748B; font-size:0.78rem; }}
  .tl-comp {{ flex:1; border-radius:7px; padding:8px 6px; text-align:center; font-weight:700;
             font-size:0.8rem; display:flex; align-items:center; justify-content:center; }}
  .tl-comp.built {{ background:{TEAL}; color:white; border:1.5px solid {TEAL}; }}
  .tl-comp.target {{ background:white; color:{TEAL}; border:1.5px dashed {TEAL}; }}
</style>
""", unsafe_allow_html=True)


def chip(text: str, colour: str) -> str:
    return f'<span class="tl-chip" style="background:{colour}">{text}</span>'


# --------------------------------------------------------------------------
# Demo state
# --------------------------------------------------------------------------

def ensure_world() -> None:
    """Build the demo world on first run (showcase + simulated month)."""
    if not (DATA_DIR / "showcase" / "manifest.json").exists():
        from trustladder.cases import build_all
        with st.spinner("First start: generating the synthetic hospitals, bills and simulated "
                        "month (about 30 seconds, once)..."):
            build_all(DATA_DIR)


ensure_world()


@st.cache_resource
def get_store() -> RegistryStore:
    return RegistryStore(DATA_DIR)


@st.cache_resource
def get_verifier() -> Verifier:
    return Verifier(get_store())


def reset_showcase() -> None:
    """Throw away everything issued during the session; rebuild the showcase."""
    for part in ("registry", "issuer_keys", "showcase"):
        shutil.rmtree(DATA_DIR / part, ignore_errors=True)
    (DATA_DIR / "directory.json").unlink(missing_ok=True)
    build_showcase(DATA_DIR)
    get_verifier().refresh()
    for key in ("queue", "audit", "last_result", "issued"):
        st.session_state.pop(key, None)


st.session_state.setdefault("queue", [])    # documents waiting for a person
st.session_state.setdefault("audit", [])    # every verdict and every human decision


@st.cache_data(show_spinner=False)
def preview_png(pdf_bytes: bytes):
    """First page of a PDF as an image, for display."""
    page = pdfium.PdfDocument(pdf_bytes)[0]
    img = page.render(scale=1.15).to_pil()
    # The bills use the top part of the A4 page; trim the empty lower part so
    # the preview sits beside the result at a readable size.
    return img.crop((0, 0, img.width, int(img.height * 0.64)))


def short_codes(batch: dict, limit: int | None = None) -> str:
    """A published batch as display JSON, with each code shortened to fit the screen."""
    entries = batch["entries"][:limit] if limit else batch["entries"]
    shown = {k: batch[k] for k in ("issuer", "published", "count") if k in batch}
    shown["entries"] = [[key[:20] + "…", value[:20] + "…"] for key, value in entries]
    shown["signature"] = batch["signature"][:20] + "…"
    return json.dumps(shown, indent=2, ensure_ascii=False)


def result_to_record(r) -> dict:
    """The audit record, as JSON-friendly data."""
    rec = dataclasses.asdict(r)
    for k in ("read_status", "registry_answer", "verdict", "evidence_grade"):
        rec[k] = getattr(r, k).value
    return rec


def check_document(pdf_bytes: bytes, name: str):
    """Run the ladder, log it, and queue it for a person if the rule says so."""
    r = run_ladder(pdf_bytes, get_verifier(), name)
    doc_id = hashlib.sha256(pdf_bytes).hexdigest()[:10]
    stamp = _dt.datetime.now().strftime("%H:%M:%S")
    st.session_state.audit.append({
        "time": stamp, "document": name, "actor": "TrustLadder " + RULE_VERSION,
        "event": f"{r.verdict.value} ({r.rule_applied.split(':')[0]})", "detail": r.queue,
    })
    if r.needs_human and not any(q["id"] == doc_id for q in st.session_state.queue):
        st.session_state.queue.append({
            "id": doc_id, "file": name, "verdict": r.verdict.value, "risk": r.risk,
            "reason": r.reason, "queue": r.queue, "registry": r.registry_answer.value,
            "findings": [f.detail for f in r.findings], "status": "Waiting", "time": stamp,
        })
    return r


# --------------------------------------------------------------------------
# Header
# --------------------------------------------------------------------------

st.title("TrustLadder")
st.markdown(f'<div style="color:{TEAL};font-weight:700;font-size:1.1rem;margin-top:-0.6rem">'
            f'{ARCH["tagline"]}</div>', unsafe_allow_html=True)
st.markdown(
    "**From document detection to digital trust.** A bill is carried up four stages, "
    "**Detect → Decide → Verify → Human review**, and comes back with a verdict, the "
    "grade of the evidence behind it, and a reason in plain words.")
st.markdown('<div class="tl-muted">IIM Visakhapatnam · Executive Program in Leadership with AI · '
            "Capstone Group 7 · synthetic data only, runs offline</div>", unsafe_allow_html=True)

with st.sidebar:
    st.markdown("**Demo controls**")
    if st.button("Reset demo", key="reset", help="Discard bills issued in this session"):
        reset_showcase()
        st.rerun()
    st.caption("Reset discards bills issued in this session and rebuilds the six demo bills.")

tab_check, tab_review, tab_hospital, tab_registry, tab_policy, tab_arch = st.tabs([
    "1 · Check a bill", "2 · Human review", "3 · Hospital (issuer)",
    "4 · Registry", "5 · Policy & impact", "6 · AI architecture",
])


def stage_header(n: int, name: str) -> None:
    """Stage title + the deck's 'AI inside' line, both from architecture.json."""
    stage = ARCH["stages"][name]
    st.markdown(f'<div class="tl-stage-title">{n} · {name} ({stage["sub"]})</div>'
                f'<div class="tl-ai">AI inside (target design): {stage["ai_inside"]}</div>',
                unsafe_allow_html=True)


# --------------------------------------------------------------------------
# Stage cards
# --------------------------------------------------------------------------

def render_result(r, pdf_bytes: bytes) -> None:
    """Show one ladder result: the bill, the four stages and the verdict."""
    col_doc, col_ladder = st.columns([2, 3], gap="large")
    with col_doc:
        st.image(preview_png(pdf_bytes), width="stretch")
    with col_ladder:
        colour = VERDICT_COLOUR[r.verdict.value]
        st.markdown(
            f'<div class="tl-verdict" style="background:{colour}">'
            f'<div class="big" data-testid="tl-verdict">{r.verdict.value}</div>'
            f'<div class="sub">Risk: {r.risk} · Evidence: {r.evidence_grade.value} · '
            f'Next: {r.queue}</div></div>', unsafe_allow_html=True)
        st.markdown(f"**Reason.** {r.reason}")
        st.caption("Reason drafted from the findings only and checked back against the verdict "
                   "(a template in this demo; target design: an LLM grounded by GraphRAG over the "
                   "institution's policy, with the same guardrail check-back).")

        s1, s2 = st.columns(2)
        with s1.container(border=True):
            stage_header(1, "Detect")
            if r.read_status.value != "Read":
                st.markdown(chip("Could not read", "#475569"), unsafe_allow_html=True)
                st.caption(r.read_note)
            elif not r.findings:
                st.markdown(chip("Nothing found", "#475569"), unsafe_allow_html=True)
                st.caption("No edit traces. This is not proof: a fake typed from a blank page "
                           "leaves none either.")
            else:
                fams = sorted({f.family for f in r.findings})
                label = (f"{len(r.findings)} finding{'s' if len(r.findings) != 1 else ''} in "
                         f"{len(fams)} famil{'ies' if len(fams) != 1 else 'y'}")
                st.markdown(chip(label, "#B45309"),
                            unsafe_allow_html=True)
                for f in r.findings:
                    st.caption(f"• [{f.family}] {f.detail}")
        with s2.container(border=True):
            stage_header(2, "Decide")
            graph = evidence.build(r)
            st.markdown(chip(r.evidence_grade.value, TEAL) + "&nbsp;" +
                        chip(f"{graph.independent_against} independent line"
                             f"{'s' if graph.independent_against != 1 else ''} against", "#475569"),
                        unsafe_allow_html=True)
            if r.registry_answer.value == "Not asked":
                st.caption("Nothing could be read, so there was no evidence to grade and no "
                           f"proof to ask for. Rule {r.rule_applied}")
            else:
                st.caption("Screening is consistency-grade at best, so the rule asked the issuer "
                           f"for proof (stage 3) before deciding. Rule {r.rule_applied}")
        s3, s4 = st.columns(2)
        with s3.container(border=True):
            stage_header(3, "Verify")
            ans = r.registry_answer.value
            st.markdown(chip(ans, REGISTRY_COLOUR[ans]), unsafe_allow_html=True)
            st.caption(r.registry_note + " Built today: a local-mirror registry lookup. "
                       "Target: an agent that also checks signatures, issuer QR codes and DigiLocker.")
        with s4.container(border=True):
            stage_header(4, "Human review")
            if r.needs_human:
                st.markdown(chip("Sent to a person", "#B45309"), unsafe_allow_html=True)
                st.caption(r.queue + ". See tab 2.")
            else:
                st.markdown(chip("Not needed", "#15803D"), unsafe_allow_html=True)
                st.caption("Proof-grade evidence: paid straight through.")

    # Full width under the bill and the stage cards, so the graph is readable.
    st.markdown("**Evidence graph** · the neuro-symbolic core: findings grouped into independent "
                "families, which the published rule turns into the verdict. The rule counts "
                "families, so two symptoms of one edit never count twice.")
    st.graphviz_chart(evidence.to_dot(r, evidence.build(r)), width="stretch")
    with st.expander("Audit record (what gets written to the claim file)"):
        st.json(result_to_record(r))


# --------------------------------------------------------------------------
# Tab 1: check a bill
# --------------------------------------------------------------------------

with tab_check:
    manifest = json.loads((DATA_DIR / "showcase" / "manifest.json").read_text())
    options = [f"{i + 1}. {m['title']}" for i, m in enumerate(manifest)]
    c1, c2 = st.columns([3, 2])
    with c1:
        choice = st.selectbox("Pick one of the six demo bills", options, key="showcase_pick")
    with c2:
        upload = st.file_uploader("…or upload a PDF bill", type=["pdf"], key="upload")

    if upload is not None:
        pdf_bytes, name, shows = upload.getvalue(), upload.name, None
    else:
        m = manifest[options.index(choice)]
        pdf_bytes = (DATA_DIR / "showcase" / m["file"]).read_bytes()
        name, shows = m["file"], m["shows"]

    if shows:
        st.info(f"**What this case shows:** {shows}")
    if st.button("Run it up the ladder", type="primary", key="run"):
        st.session_state.last_result = (name, check_document(pdf_bytes, name), pdf_bytes)
    last = st.session_state.get("last_result")
    if last and last[0] == name:
        render_result(last[1], last[2])
    else:
        st.image(preview_png(pdf_bytes), width=430)


# --------------------------------------------------------------------------
# Tab 2: human review
# --------------------------------------------------------------------------

with tab_review:
    st.subheader("Human review queue")
    st.caption("Every verdict other than Authentic comes here. The officer decides; the machine's "
               "verdict and the officer's decision are both logged, and every genuine customer "
               "held is counted.")
    queue = st.session_state.queue
    if not queue:
        st.write("Nothing waiting. Run a bill in tab 1.")
    for i, item in enumerate(queue):
        colour = VERDICT_COLOUR[item["verdict"]]
        with st.container(border=True):
            top_l, top_r = st.columns([4, 1])
            top_l.markdown(f"{chip(item['verdict'], colour)} &nbsp; **{item['file']}** "
                           f"&nbsp;·&nbsp; registry: {item['registry']} &nbsp;·&nbsp; {item['queue']}",
                           unsafe_allow_html=True)
            top_r.markdown(f"**{item['status']}**")
            st.caption(item["reason"])
            if item["status"] == "Waiting":
                b1, b2, b3 = st.columns(3)
                decision = None
                if b1.button("Genuine: release for payment", key=f"rel_{i}"):
                    decision = "Released for payment"
                if b2.button("Fraud: reject and refer", key=f"rej_{i}"):
                    decision = "Rejected and referred to investigation"
                if b3.button("Ask the issuer / customer", key=f"ask_{i}"):
                    decision = "Confirmation requested"
                if decision:
                    item["status"] = decision
                    st.session_state.audit.append({
                        "time": _dt.datetime.now().strftime("%H:%M:%S"), "document": item["file"],
                        "actor": "Claims officer (demo)", "event": decision,
                        "detail": f"machine verdict was {item['verdict']}",
                    })
                    st.rerun()
    if st.session_state.audit:
        st.markdown("#### Audit trail")
        st.dataframe(pd.DataFrame(st.session_state.audit), hide_index=True, width="stretch")


# --------------------------------------------------------------------------
# Tab 3: the hospital
# --------------------------------------------------------------------------

with tab_hospital:
    st.subheader("Hospital: issue a bill and publish its codes")
    st.caption("This runs inside the hospital. It prints a random ticket on the bill and publishes "
               "two scrambled codes, signed with the hospital's own key. No patient data leaves.")
    store = get_store()
    joined = [i for i in SHOWCASE_JOINED if i in store.load_directory()]
    h1, h2 = st.columns([1, 1])
    with h1:
        issuer_id = st.selectbox("Hospital", joined, format_func=lambda i: HOSPITALS[i][0],
                                 key="h_issuer")
        patient = st.text_input("Patient", "Ravi Deshmukh", key="h_patient")
        bill_date = st.date_input("Bill date", _dt.date(2026, 3, 14), key="h_date")
    with h2:
        charges = st.data_editor(pd.DataFrame({
            "Description": ["Room charges (semi-private)", "Surgeon fee", "Pharmacy and consumables",
                            "Laboratory investigations"],
            "Amount (Rs)": [24000, 55000, 18250, 6400],
        }), num_rows="dynamic", key="h_items", width="stretch")
    if st.button("Issue bill and publish", type="primary", key="h_issue"):
        name, _, _, bill_prefix = HOSPITALS[issuer_id]
        publisher = Publisher(store, issuer_id)
        items = [LineItem(str(d), int(round(float(a) * 100)))
                 for d, a in zip(charges["Description"], charges["Amount (Rs)"]) if str(d).strip()]
        serial = 5000 + len(st.session_state.get("issued_log", []))
        bill = BillFields(issuer_id, name, f"{bill_prefix}/2026/{serial:06d}", bill_date.isoformat(),
                          patient, items, sum(i.amount_paise for i in items), publisher.new_ticket())
        batch = publisher.publish([bill])
        get_verifier().refresh()
        st.session_state.setdefault("issued_log", []).append(bill.bill_no)
        st.session_state.issued = (bill, render_genuine(bill, joined=True), batch)

    issued = st.session_state.get("issued")
    if issued:
        bill, pdf, batch = issued
        st.success(f"Issued {bill.bill_no} for Rs {format_inr(bill.total_paise)}. "
                   f"Ticket printed on the bill: **{bill.ticket}**")
        p1, p2 = st.columns([2, 3], gap="large")
        with p1:
            st.image(preview_png(pdf), width="stretch")
        with p2:
            st.markdown("**What the hospital published** (this is all anyone outside ever sees):")
            st.code(short_codes(batch), language="json")
            st.caption("Key = code of the ticket. Value = code of the ticket plus bill number, date, "
                       "patient and total. Neither can be turned back into the bill.")
            d1, d2 = st.columns(2)
            d1.download_button("Download the bill", pdf, f"{bill.bill_no.replace('/', '_')}.pdf",
                               mime="application/pdf", key="h_dl")
            new_total = d2.number_input("Altered total (Rs), for testing",
                                        value=float(bill.total_paise / 100 + 50000), step=1000.0,
                                        key="h_newtotal")
            a1, a2 = st.columns(2)
            if a1.button("Check the genuine bill", key="h_check_genuine"):
                st.session_state.h_result = (check_document(pdf, bill.bill_no + ".pdf"), pdf)
            if a2.button("Alter the total and check it", key="h_check_altered"):
                altered = render_altered(bill, int(round(new_total * 100)), joined=True)
                st.session_state.h_result = (check_document(altered, bill.bill_no + "_altered.pdf"),
                                             altered)
        if st.session_state.get("h_result"):
            st.divider()
            render_result(*st.session_state.h_result)


# --------------------------------------------------------------------------
# Tab 4: the registry
# --------------------------------------------------------------------------

with tab_registry:
    st.subheader("The registry: what it holds, and what it does not")
    st.caption("A neutral host. It stores each hospital's signed files and the directory of "
               "who has joined. It holds codes only, so even a complete theft reveals nothing.")
    directory = get_store().load_directory()
    rows = []
    for iid, (name, city, _, _) in HOSPITALS.items():
        if iid in ("IN-HOSP-KAVERI-KOP-0577", "IN-HOSP-NIRAMAY-SGL-0612"):
            continue                      # only used in the simulated month
        entry = directory.get(iid)
        f = get_store().load_file(iid) if entry else {"batches": []}
        rows.append({
            "Hospital": name, "City": city, "Joined": "Yes" if entry else "No",
            "Public key": (entry["public_key"][:18] + "…") if entry else "-",
            "Signed batches": len(f["batches"]),
            "Bills covered": sum(b["count"] for b in f["batches"]),
            "Patient data held": "None",
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    sample_id = SHOWCASE_JOINED[0]
    sample = get_store().load_file(sample_id)
    if sample["batches"]:
        b = sample["batches"][0]
        g1, g2 = st.columns([3, 2], gap="large")
        with g1:
            st.markdown(f"**One day's file from {HOSPITALS[sample_id][0]}** (first 3 of {b['count']} lines)")
            st.code(short_codes(b, limit=3), language="json")
        with g2:
            st.markdown("**Can someone slip a fake line into the file?**")
            st.caption("Try it: add one invented entry to the day's file and check the hospital's seal.")
            if st.button("Insert a forged entry", key="forge"):
                forged = {k: v for k, v in b.items() if k != "signature"}
                forged["entries"] = forged["entries"] + [[crypto.new_salt() * 2, crypto.new_salt() * 2]]
                forged["count"] += 1
                ok = crypto.verify_signature(forged, b["signature"], directory[sample_id]["public_key"])
                st.session_state.forge_result = ok
            if "forge_result" in st.session_state:
                if st.session_state.forge_result:
                    st.error("Signature still valid (this should never happen).")
                else:
                    st.success("Rejected: the hospital's signature no longer matches, so every "
                               "verifier ignores the altered file. Only the hospital can add lines.")


# --------------------------------------------------------------------------
# Tab 5: policy and impact
# --------------------------------------------------------------------------

with tab_policy:
    st.subheader("Policy & impact: the cost of being wrong, in both directions")
    sim = json.loads((DATA_DIR / "simulation.json").read_text())
    st.caption(
        f"A simulated month of {sim['batch_size']} synthetic claims from five fictional hospitals, "
        "each run through the real ladder above. The mix (about 10% fraud, 8% blurred photos, "
        "8% genuine files re-saved by a phone app) is our assumption, not a statistic. "
        f"Officer time is assumed at {MINUTES_PER_REVIEW} minutes per case at "
        f"Rs {COST_PER_HOUR_RS} an hour.")

    pc1, pc2 = st.columns([1, 2], gap="large")
    with pc1:
        scenario = st.radio("How many hospitals have joined the registry?",
                            list(sim["results"]), index=1, key="scenario")
        st.markdown("**The institution's policy** (set by its risk head)")
        policy = {}
        for verdict in ("Authentic", "Suspicious", "Tampered", "Inconclusive"):
            policy[verdict] = st.selectbox(verdict, ALLOWED[verdict],
                                           index=ALLOWED[verdict].index(DEFAULT_POLICY[verdict]),
                                           key=f"pol_{verdict}")
        st.caption("'Pay' is offered only for Authentic. That is the guarantee: no policy "
                   "setting can pay a document the issuer has not confirmed.")
    rows = sim["results"][scenario]
    tl, base = evaluate(rows, policy), baseline(rows)
    with pc2:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Paid straight through", f"{tl['straight_through_pct']}%")
        m1.caption(f"{tl['paid_straight_through']} of {tl['claims']} claims")
        m2.metric("Frauds paid", tl["fraud_paid"])
        m2.caption(f"of {tl['fraud_total']} frauds in the month")
        m3.metric("Genuine customers held", tl["genuine_held"])
        m3.caption(f"{tl['genuine_delayed']} more sent to a person")
        m4.metric("Officer time", f"{tl['officer_hours']} h")
        m4.caption(f"≈ Rs {format_inr(tl['officer_cost_rs'] * 100)[:-3]} at the assumed rate")

        compare = pd.DataFrame({
            "TrustLadder (this policy)": [tl["paid_straight_through"], tl["fraud_paid"],
                                          tl["fraud_stopped"], tl["genuine_held"],
                                          tl["genuine_delayed"], tl["officer_hours"]],
            "Edit-detection tool (pay if nothing looks wrong)": [
                base["paid_straight_through"], base["fraud_paid"], base["fraud_stopped"],
                base["genuine_held"], base["genuine_delayed"], base["officer_hours"]],
        }, index=["Paid straight through", "Frauds paid", "Frauds stopped",
                  "Genuine customers held", "Genuine sent to a person", "Officer hours"])
        st.dataframe(compare, width="stretch")
        st.caption("The edit-detection tool pays every fake typed from a blank page (it has no edit "
                   "traces) and holds every genuine customer whose file was re-saved. TrustLadder "
                   "pays none of the fakes; its cost is the people it must refer, which falls as "
                   "more hospitals join.")

        trend = pd.DataFrame({
            label: {
                "Paid straight through": evaluate(r, policy)["paid_straight_through"],
                "Sent to a person or held": evaluate(r, policy)["to_person"] + evaluate(r, policy)["held"],
            } for label, r in sim["results"].items()
        }).T
        st.markdown("**As hospitals join, straight-through payment rises and review work falls**")
        st.bar_chart(trend, color=["#15803D", "#B45309"], stack=False, height=260)


# --------------------------------------------------------------------------
# Tab 6: the AI architecture (same source as the deck's slide 5)
# --------------------------------------------------------------------------

with tab_arch:
    st.subheader("AI architecture: neural models read and gather evidence, a symbolic rule decides")
    st.caption("The same diagram as slide 5 of the deck, drawn from the same file. Solid = running in "
               "this demo today. Dashed = target design using current AI models, not claimed as built.")

    def layer_html(layer: dict, found: bool = False) -> str:
        comps = "".join(
            f'<div class="tl-comp {"built" if c["built"] else "target"}">{c["label"]}</div>'
            for c in layer["components"])
        sub = f'<span>{layer["sub"]}</span>' if layer.get("sub") else ""
        return (f'<div class="tl-layer{" found" if found else ""}">'
                f'<div class="tl-lname">{layer["name"]}{sub}</div>{comps}</div>')

    arch_html = "".join(layer_html(layer) for layer in ARCH["layers"])
    arch_html += layer_html(ARCH["foundation"], found=True)
    st.markdown(f'<div data-testid="tl-architecture">{arch_html}</div>', unsafe_allow_html=True)
    built = [c["label"] for layer in ARCH["layers"] + [ARCH["foundation"]]
             for c in layer["components"] if c["built"]]
    target = [c["label"] for layer in ARCH["layers"] + [ARCH["foundation"]]
              for c in layer["components"] if not c["built"]]
    a1, a2 = st.columns(2)
    a1.markdown(f"**Built in this demo ({len(built)})**")
    a1.caption(" · ".join(built))
    a2.markdown(f"**Target design ({len(target)})**")
    a2.caption(" · ".join(target))
    st.info("Trust network, outside the verifier: the hospital's publisher signs two codes per bill, "
            "the neutral registry hosts the signed files, and the Verify layer mirrors them. "
            "Only codes travel; patient data never leaves the hospital (see tabs 3 and 4).")
