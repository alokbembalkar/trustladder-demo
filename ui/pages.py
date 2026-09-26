"""
The screens, one set per role. Designed to be demonstrated live: each screen
carries ONE idea and ONE main action; anything technical sits behind the
sidebar switch "Show technical details" or in a closed section.

    Hospital billing      Issue a bill · My bills
    Customer              My claims · Submit a claim
    Claims officer        Claims inbox · Try a sample bill
    Risk head             Dashboard
    Registry operator     Network
    Auditor / regulator   Audit trail · How decisions are made

Deliberate limits (part of the design, not missing features):
  * the hospital never sees who checked its bills;
  * the registry never sees a bill, a patient or an amount;
  * the customer sees plain words, never rule numbers;
  * the auditor can read everything and change nothing.
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import streamlit as st

from trustladder import crypto
from trustladder.bills import render_altered
from trustladder.cases import HOSPITALS
from trustladder.models import BillFields, LineItem
from trustladder.pipeline import run_ladder
from trustladder.policy import ALLOWED, COST_PER_HOUR_RS, MINUTES_PER_REVIEW, baseline, evaluate
from trustladder.registry import RegistryStore, Verifier
from trustladder.service import (DuplicateClaim, enrol_hospital, issue_bill, resubmit_claim,
                                 submit_claim)
from trustladder.store import CUSTOMER_TEXT, OPEN_STATUSES, Store, result_from_json

from . import components as C
from .theme import STATUS_COLOUR, chip, inr, kpis, page_title


@dataclass
class Ctx:
    store: Store
    registry: RegistryStore
    verifier: Verifier
    user: dict
    data_dir: Path


def sample_pack_button(ctx: Ctx, key: str) -> None:
    """Offer the six sample bills as a zip, so anyone can try the upload path."""
    pack = ctx.data_dir / "TrustLadder_sample_bills.zip"
    if pack.exists():
        st.download_button("Download 6 sample bills (zip)", pack.read_bytes(),
                           "TrustLadder_sample_bills.zip", mime="application/zip", key=key,
                           help="Genuine, altered, made-from-nothing, not-joined and blurred examples")


def _status_chip(status: str) -> str:
    return chip(status, STATUS_COLOUR.get(status, "#475569"))


# ==========================================================================
# Hospital billing
# ==========================================================================

def hospital_issue(ctx: Ctx) -> None:
    issuer_id = ctx.user["issuer_id"]
    name, _, _, bill_prefix = HOSPITALS[issuer_id]
    page_title("Issue a bill", "Fill in the bill as usual. TrustLadder adds a random ticket and tells the "
                               "registry, without sharing any patient data.")
    customers = ctx.store.customers()
    c1, c2 = st.columns([1, 1], gap="large")
    with c1:
        cust = st.selectbox("Patient", customers, format_func=lambda c: f"{c['name']} ({c['city']})",
                            key="h_patient")
        bill_date = st.date_input("Bill date", _dt.date(2026, 3, 21), key="h_date")
    with c2:
        charges = st.data_editor(pd.DataFrame({
            "Description": ["Room charges (semi-private)", "Surgeon fee", "Pharmacy and consumables",
                            "Laboratory investigations"],
            "Amount (Rs)": [24000, 55000, 18250, 6400]}), num_rows="dynamic", key="h_items",
            width="stretch")
    if st.button("Issue bill and publish", type="primary", key="h_issue"):
        items = [LineItem(str(d), int(round(float(a) * 100)))
                 for d, a in zip(charges["Description"], charges["Amount (Rs)"]) if str(d).strip()]
        serial = 7000 + len(ctx.store.bills_of_issuer(issuer_id))
        bill = BillFields(issuer_id, name, f"{bill_prefix}/2026/{serial:06d}", bill_date.isoformat(),
                          cust["name"], items, sum(i.amount_paise for i in items))
        pdf = issue_bill(ctx.store, ctx.registry, ctx.verifier, bill, cust["customer_id"], ctx.user["name"])
        st.session_state.h_issued = (bill, pdf)
        st.session_state.story_bill = bill.bill_no        # the demo story follows this one bill
        st.session_state.story_customer = cust["customer_id"]
    issued = st.session_state.get("h_issued")
    if issued:
        bill, pdf = issued
        st.success(f"Issued {bill.bill_no} for {inr(bill.total_paise)}. "
                   f"Ticket printed on the bill: **{bill.ticket}**")
        p1, p2 = st.columns([2, 3], gap="large")
        p1.image(C.preview_png(pdf), width="stretch")
        with p2:
            st.markdown("**What the registry received:** two scrambled codes and the hospital's signature. "
                        "No name, no amount, no diagnosis.")
            with st.expander("Show the exact codes that were published"):
                batch = ctx.registry.load_file(issuer_id)["batches"][-1]
                st.code(json.dumps({"issuer": batch["issuer"], "published": batch["published"],
                                    "entries": [[k[:20] + "…", v[:20] + "…"] for k, v in batch["entries"]],
                                    "signature": batch["signature"][:20] + "…"}, indent=2, ensure_ascii=False),
                        language="json")
            st.markdown("**Give the bill to the patient.** In this demo you download it here and upload "
                        "it as the customer in the next step.")
            d1, d2 = st.columns(2)
            d1.download_button("Download the bill (PDF)", pdf, f"{bill.bill_no.replace('/', '_')}.pdf",
                               mime="application/pdf", key="h_dl", type="primary")
            forged = render_altered(bill, bill.total_paise + 5000000, joined=True)
            d2.download_button("Download a tampered copy", forged,
                               f"{bill.bill_no.replace('/', '_')}_tampered.pdf", mime="application/pdf",
                               key="h_dl_forged",
                               help="Demo only: the same bill with its total raised by Rs 50,000, "
                                    "as a forger would send it")
            st.caption("The tampered copy exists only so the demo can show what happens to a forged bill. "
                       "A hospital would never produce one.")


def hospital_my_bills(ctx: Ctx) -> None:
    issuer_id = ctx.user["issuer_id"]
    page_title("My bills", f"{HOSPITALS[issuer_id][0]} · every bill carries a ticket the insurer can check.")
    bills = ctx.store.bills_of_issuer(issuer_id)
    kpis([(str(len(bills)), "bills issued"),
          (str(sum(b["published"] for b in bills)), "confirmable by insurers"),
          ("0", "patient records shared")])
    st.write("")
    if bills:
        df = pd.DataFrame(bills)
        df["Amount"] = df["total_paise"].map(inr)
        st.dataframe(df[["bill_no", "patient", "bill_date", "Amount", "ticket"]].rename(
            columns={"bill_no": "Bill no", "patient": "Patient", "bill_date": "Bill date",
                     "ticket": "Ticket on the bill"}), hide_index=True, width="stretch")
    st.caption("You never see who checked a bill: insurers look it up on their own copy of the registry.")


# ==========================================================================
# Customer
# ==========================================================================

def customer_claims(ctx: Ctx) -> None:
    cid = ctx.user["customer_id"]
    page_title("Your claims", "Where each claim stands, in plain words.")
    claims = ctx.store.claims(customer_id=cid)
    kpis([(str(len(claims)), "claims"),
          (str(sum(c["status"].startswith("Paid") for c in claims)), "paid"),
          (str(sum(c["status"] == "Waiting for customer" for c in claims)), "need something from you")])
    st.write("")
    docs = {d["bill_no"]: d for d in ctx.store.documents_of_customer(cid)}
    for c in claims:
        with st.container(border=True):
            left, right = st.columns([4, 1])
            left.markdown(f"**{c['issuer_name']}** · claim {c['claim_id']} · {c['submitted_at'][:10]}")
            right.markdown(_status_chip(c["status"]), unsafe_allow_html=True)
            st.write(CUSTOMER_TEXT[c["status"]])
            if c["status"] == "Waiting for customer":
                options = {"": None} | {f"{d['bill_no']} · {d['issuer_name']} · {inr(d['total_paise'])}": b
                                        for b, d in docs.items()}
                pick = st.selectbox("Send a clearer copy from your documents", list(options),
                                    key=f"cc_pick_{c['claim_id']}")
                up = st.file_uploader("…or upload the original PDF", type=["pdf"], key=f"cc_up_{c['claim_id']}")
                if st.button("Send clearer copy", key=f"cc_send_{c['claim_id']}", type="primary"):
                    if up is not None:
                        pdf, name = up.getvalue(), up.name
                    elif options[pick]:
                        pdf, name = ctx.store.bill_pdf(options[pick]), f"{options[pick].replace('/', '_')}.pdf"
                    else:
                        st.warning("Choose a document or upload a PDF first.")
                        return
                    resubmit_claim(ctx.store, ctx.verifier, c["claim_id"], pdf, name, ctx.user["name"])
                    st.rerun()


def customer_submit(ctx: Ctx) -> None:
    """The customer's only way in: upload the bill PDF the hospital gave them."""
    cid = ctx.user["customer_id"]
    page_title("Submit a claim", "Upload the bill your hospital gave you. It is checked the moment you "
                                 "submit, and you see the result straight away.")
    up = st.file_uploader("Your bill (PDF)", type=["pdf"], key="cs_upload")
    c1, c2 = st.columns([1, 3])
    submitted = c1.button("Submit claim", type="primary", key="cs_submit", disabled=up is None)
    with c2:
        sample_pack_button(ctx, "cs_pack")
    if up is not None and not submitted:
        st.caption("Ready to submit:")
        st.image(C.preview_png(up.getvalue()), width=430)
    if submitted:
        try:
            st.session_state.cs_last = submit_claim(ctx.store, ctx.verifier, cid, up.getvalue(), up.name,
                                                    actor=ctx.user["name"])
        except DuplicateClaim as dup:
            st.warning(f"Bill {dup} has already been claimed on this policy. "
                       "See My claims for where it stands.")
            return
    last = st.session_state.get("cs_last")
    if last:
        c = ctx.store.claim(last)
        r = result_from_json(json.loads(c["result_json"]))
        st.write("")
        st.markdown(f"**{c['issuer_name']}** · claim {c['claim_id']} · you uploaded `{c['file_name']}` "
                    + _status_chip(c["status"]), unsafe_allow_html=True)
        # The bill that was uploaded, beside what the check concluded.
        C.verdict_view(r, c["pdf"], next_step=CUSTOMER_TEXT[c["status"]], show_graph=C.details_on())


# ==========================================================================
# Claims officer
# ==========================================================================

def _claim_detail(ctx: Ctx, claim_id: str) -> None:
    c = ctx.store.claim(claim_id)
    r = result_from_json(json.loads(c["result_json"]))
    # The bill number matters on screen: it shows the panel this is the same bill.
    bill = f" · bill {c['bill_no']}" if c["bill_no"] else ""
    st.markdown(f"#### {c['customer_name']} · {c['issuer_name']}{bill}")
    st.caption(f"Claim {c['claim_id']} · submitted {c['submitted_at']}")

    def decision_buttons() -> None:
        """Shown right under the verdict, so the decision is one glance away."""
        if c["status"] not in OPEN_STATUSES:
            if c["officer_decision"]:
                st.info(f"Decided: {c['officer_decision']} ({c['decided_by']}, {c['decided_at']}).")
            return
        st.write("")
        b1, b2 = st.columns(2)
        if b1.button("Approve payment", key=f"dec_release_{claim_id}", type="primary", width="stretch"):
            ctx.store.decide(claim_id, "release", ctx.user["name"])
            st.rerun()
        if b2.button("Reject as fraud", key=f"dec_reject_{claim_id}", width="stretch"):
            ctx.store.decide(claim_id, "reject", ctx.user["name"])
            st.rerun()
        with st.expander("Other options"):
            if st.button("Ask the hospital to confirm", key=f"dec_ask_issuer_{claim_id}"):
                ctx.store.decide(claim_id, "ask_issuer", ctx.user["name"])
                st.rerun()
            if st.button("Ask the customer for a clearer copy", key=f"dec_ask_customer_{claim_id}"):
                ctx.store.decide(claim_id, "ask_customer", ctx.user["name"])
                st.rerun()

    C.verdict_view(r, c["pdf"], next_step=c["initial_action"], actions=decision_buttons)


def officer_inbox(ctx: Ctx) -> None:
    page_title("Claims inbox", "Only the claims that could not be proven. Proven claims are paid without you.")
    open_claims = ctx.store.claims(statuses=OPEN_STATUSES)
    m = ctx.store.measures()
    kpis([(str(len(open_claims)), "waiting for you"),
          (str(sum(c["status"] == "On hold" for c in open_claims)), "on hold (possible fraud)"),
          (str(m["auto_paid"]), "paid automatically on proof")])
    st.write("")
    if not open_claims:
        st.success("Inbox empty.")
        return
    # Worst first, and newest first within each group: what an officer works through,
    # and it puts a claim submitted during the demo at the top.
    order = {"On hold": 0, "In review": 1, "Waiting for hospital": 2, "Waiting for customer": 3}
    open_claims.sort(key=lambda c: c["submitted_at"], reverse=True)
    open_claims.sort(key=lambda c: order.get(c["status"], 9))
    labels = {f"{c['customer_name']} · {c['issuer_name']} · {c['verdict']} · {c['status']} · {c['claim_id']}":
              c["claim_id"] for c in open_claims}
    story = st.session_state.get("story_claim")
    if story and story in labels.values() and st.session_state.get("inbox_pick") not in labels:
        st.session_state.inbox_pick = next(k for k, v in labels.items() if v == story)
    pick = st.selectbox("Claim", list(labels), key="inbox_pick")
    _claim_detail(ctx, labels[pick])


def officer_samples(ctx: Ctx) -> None:
    page_title("Check any bill", "Upload a bill, or try one of the six prepared ones. Nothing here is "
                                 "saved as a claim.")
    manifest = json.loads((ctx.data_dir / "showcase" / "manifest.json").read_text())
    options = [f"{i + 1}. {m['title']}" for i, m in enumerate(manifest)]
    c1, c2 = st.columns([3, 2], gap="large")
    with c1:
        choice = st.selectbox("A prepared bill", options, key="showcase_pick")
        m = manifest[options.index(choice)]
        pdf, name = (ctx.data_dir / "showcase" / m["file"]).read_bytes(), m["file"]
        shows = m["shows"]
    with c2:
        up = st.file_uploader("…or upload a bill (PDF)", type=["pdf"], key="sample_upload")
        if up is not None:
            pdf, name, shows = up.getvalue(), up.name, ""
        sample_pack_button(ctx, "of_pack")
    if st.button("Check this bill", type="primary", key="run"):
        st.session_state.sample_result = (name, run_ladder(pdf, ctx.verifier, name), pdf, shows)
    last = st.session_state.get("sample_result")
    if last and last[0] == name:
        if last[3]:
            st.caption(f"What this case shows: {last[3]}")
        C.verdict_view(last[1], last[2])
    else:
        st.image(C.preview_png(pdf), width=430)


# ==========================================================================
# Risk head (CRO)
# ==========================================================================

def risk_dashboard(ctx: Ctx) -> None:
    page_title("Dashboard", "Both sides of the risk: fraud stopped, and honest customers wrongly held.")
    m = ctx.store.measures()
    kpis([(f"{m['straight_through_pct']}%", f"paid automatically on proof ({m['auto_paid']} of {m['claims']})"),
          (str(m["rejected"]), "stopped as fraud after review"),
          (str(m["wrongful_holds"]), "honest customers wrongly held")])
    st.caption(f"Sample month: {m['sample_frauds']} known frauds, {m['sample_frauds_paid']} paid automatically.")
    st.write("")
    st.markdown("**Your policy: what happens to each verdict**")
    policy = ctx.store.policy()
    cols = st.columns(4)
    for col, verdict in zip(cols, ("Authentic", "Suspicious", "Tampered", "Inconclusive")):
        choice = col.selectbox(verdict, ALLOWED[verdict], index=ALLOWED[verdict].index(policy[verdict]),
                               key=f"pol_{verdict}")
        if choice != policy[verdict]:
            ctx.store.set_policy(verdict, choice, ctx.user["name"])
            st.toast(f"Policy saved: {verdict} → {choice}")
    st.caption("'Pay' is offered only for Authentic: no setting can pay a bill the hospital has not confirmed.")
    with st.expander("What happens as more hospitals join (simulated month of 150 claims)"):
        sim = json.loads((ctx.data_dir / "simulation.json").read_text())
        scenario = st.radio("Hospitals joined", list(sim["results"]), index=1, key="scenario", horizontal=True)
        rows = sim["results"][scenario]
        tl, base = evaluate(rows, policy), baseline(rows)
        kpis([(f"{tl['straight_through_pct']}%", "paid automatically"),
              (str(tl["fraud_paid"]), f"frauds paid (of {tl['fraud_total']})"),
              (f"{base['fraud_paid']} / {base['genuine_held']}",
               "an edit-detection tool: frauds paid / honest customers held")])
        st.caption("Synthetic month; the mix (about 10% fraud, 8% photos, 8% re-saved files) is our assumption. "
                   f"Officer time assumed at {MINUTES_PER_REVIEW} minutes per case at ₹{COST_PER_HOUR_RS}/hour.")


# ==========================================================================
# Registry operator
# ==========================================================================

def registry_network(ctx: Ctx) -> None:
    page_title("Network", "Which hospitals have joined. The registry holds scrambled codes only.")
    directory = ctx.registry.load_directory()
    rows = []
    for iid, (name, city, _, _) in HOSPITALS.items():
        entry = directory.get(iid)
        f = ctx.registry.load_file(iid) if entry else {"batches": []}
        rows.append({"Hospital": name, "City": city, "Joined": "Yes" if entry else "No",
                     "Bills covered": sum(b["count"] for b in f["batches"]), "Patient data held": "None"})
    kpis([(f"{sum(r['Joined'] == 'Yes' for r in rows)} of {len(rows)}", "hospitals joined"),
          (str(sum(r["Bills covered"] for r in rows)), "bills covered"),
          ("0", "patient records held")])
    st.write("")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    not_joined = [iid for iid in HOSPITALS if iid not in directory]
    if not_joined:
        c1, c2 = st.columns([3, 1])
        pick = c1.selectbox("Enrol a hospital (a key pair and a line in the directory: no integration project)",
                            not_joined, format_func=lambda i: HOSPITALS[i][0], key="enrol_pick")
        c2.write("")
        if c2.button("Enrol hospital", type="primary", key="enrol"):
            enrol_hospital(ctx.store, ctx.registry, ctx.verifier, pick, ctx.user["name"])
            st.rerun()
    with st.expander("Check that nobody has tampered with the registry"):
        sig_rows = []
        for iid, entry in directory.items():
            for b in ctx.registry.load_file(iid)["batches"]:
                unsigned = {k: v for k, v in b.items() if k != "signature"}
                ok = crypto.verify_signature(unsigned, b["signature"], entry["public_key"])
                sig_rows.append({"Hospital": entry["name"], "Published": b["published"], "Lines": b["count"],
                                 "Signature": "Valid" if ok else "INVALID"})
        st.dataframe(pd.DataFrame(sig_rows), hide_index=True, width="stretch", height=200)
        iid = next(iter(directory))
        b = ctx.registry.load_file(iid)["batches"][0]
        if st.button("Try to insert a forged entry", key="forge"):
            forged = {k: v for k, v in b.items() if k != "signature"}
            forged["entries"] = forged["entries"] + [[crypto.new_salt() * 2, crypto.new_salt() * 2]]
            forged["count"] += 1
            st.session_state.forge_result = crypto.verify_signature(forged, b["signature"],
                                                                    directory[iid]["public_key"])
        if "forge_result" in st.session_state:
            if st.session_state.forge_result:
                st.error("Signature still valid (this should never happen).")
            else:
                st.success("Rejected: the hospital's signature no longer matches, so every insurer ignores the "
                           "altered file. Only the hospital can add lines.")


# ==========================================================================
# Auditor / regulator
# ==========================================================================

def auditor_trail(ctx: Ctx) -> None:
    page_title("Audit trail", "Every machine verdict and every human decision. Read-only.")
    events = pd.DataFrame(ctx.store.audit())
    q = st.text_input("Search (a claim number, a name, an event)", key="aud_q")
    view = events
    if q:
        view = view[view.apply(lambda r: q.lower() in " ".join(map(str, r.values)).lower(), axis=1)]
    st.dataframe(view.rename(columns={"ts": "When", "actor": "Who", "role": "Role", "event": "Event",
                                      "claim_id": "Claim", "detail": "Detail"}),
                 hide_index=True, width="stretch", height=430)
    m = ctx.store.measures()
    st.caption(f"{m['claims']} claims · {m['auto_paid']} paid on proof · {m['released_after_review']} "
               f"released after review · {m['rejected']} rejected · {m['wrongful_holds']} wrongful hold(s).")
    st.download_button("Download as CSV", view.to_csv(index=False), "trustladder_audit.csv",
                       mime="text/csv", key="aud_csv")


def auditor_rules(ctx: Ctx) -> None:
    page_title("How decisions are made", "A published rule, not a score: anyone can apply it by hand.")
    C.rule_table()
    st.write("")
    with st.expander("The AI architecture around the rule", expanded=C.details_on()):
        C.architecture_diagram()


# ==========================================================================
# Menus
# ==========================================================================

MENUS = {
    "hospital": [("Issue a bill", hospital_issue), ("My bills", hospital_my_bills)],
    "customer": [("My claims", customer_claims), ("Submit a claim", customer_submit)],
    "officer": [("Claims inbox", officer_inbox), ("Check any bill", officer_samples)],
    "riskhead": [("Dashboard", risk_dashboard)],
    "registry": [("Network", registry_network)],
    "auditor": [("Audit trail", auditor_trail), ("How decisions are made", auditor_rules)],
}
