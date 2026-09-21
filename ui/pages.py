"""
The screens, one set per role. Each role sees at most three menu items and
lands on the screen it uses most.

    Hospital billing      My bills · Issue a bill
    Customer              My claims · Submit a claim
    Claims officer        Claims inbox · All claims · Try a sample bill
    Risk head             Impact dashboard · Simulated month
    Registry operator     Network · Integrity check
    Auditor / regulator   Audit trail · How decisions are made

Deliberate limits (they are part of the design, not missing features):
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
from trustladder.policy import (ALLOWED, COST_PER_HOUR_RS, DEFAULT_POLICY, MINUTES_PER_REVIEW,
                                baseline, evaluate)
from trustladder.registry import RegistryStore, Verifier
from trustladder.service import enrol_hospital, issue_bill, resubmit_claim, submit_claim
from trustladder.store import CUSTOMER_TEXT, OFFICER_DECISIONS, OPEN_STATUSES, Store, result_from_json

from . import components as C
from .theme import STATUS_COLOUR, VERDICT_COLOUR, chip, inr, kpis, page_title


@dataclass
class Ctx:
    store: Store
    registry: RegistryStore
    verifier: Verifier
    user: dict
    data_dir: Path


def _status_chip(status: str) -> str:
    return chip(status, STATUS_COLOUR.get(status, "#475569"))


def _claims_frame(rows: list[dict], cols: list[str]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    names = {"claim_id": "Claim", "customer_name": "Customer", "issuer_name": "Hospital",
             "submitted_at": "Submitted", "verdict": "Verdict", "status": "Status",
             "initial_action": "Routed to", "officer_decision": "Officer decision"}
    return df[cols].rename(columns=names)


# ==========================================================================
# Hospital billing
# ==========================================================================

def hospital_my_bills(ctx: Ctx) -> None:
    issuer_id = ctx.user["issuer_id"]
    name = HOSPITALS[issuer_id][0]
    joined = issuer_id in ctx.registry.load_directory()
    page_title("My bills", f"{name} · every bill you issue gets a random ticket and two signed codes "
                           "in the registry. Patient data never leaves the hospital.")
    bills = ctx.store.bills_of_issuer(issuer_id)
    kpis([(str(len(bills)), "bills issued"),
          (str(sum(b["published"] for b in bills)), "published to the registry"),
          ("Joined" if joined else "Not joined", "registry status"),
          ("0", "patient records shared")])
    st.write("")
    if bills:
        df = pd.DataFrame(bills)
        df["Amount"] = df["total_paise"].map(inr)
        df["Published"] = df["published"].map(lambda p: "Yes, codes only" if p else "No")
        st.dataframe(df[["bill_no", "patient", "bill_date", "Amount", "ticket", "Published"]].rename(
            columns={"bill_no": "Bill no", "patient": "Patient", "bill_date": "Bill date",
                     "ticket": "Ticket printed on bill"}), hide_index=True, width="stretch")
    st.caption("By design you do not see who checked a bill or when: verifiers look up codes on their "
               "own copy of the registry, so the hospital learns nothing about its patients' claims.")


def hospital_issue(ctx: Ctx) -> None:
    issuer_id = ctx.user["issuer_id"]
    name, _, _, bill_prefix = HOSPITALS[issuer_id]
    page_title("Issue a bill", "Fill in the bill as usual. TrustLadder prints a random ticket on it and "
                               "publishes two scrambled codes, signed with the hospital's key.")
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
        pdf = issue_bill(ctx.store, ctx.registry, ctx.verifier, bill, cust["customer_id"],
                         ctx.user["name"])
        st.session_state.h_issued = (bill, pdf)
    issued = st.session_state.get("h_issued")
    if issued:
        bill, pdf = issued
        st.success(f"Issued {bill.bill_no} to {bill.patient} for {inr(bill.total_paise)}. Ticket printed on "
                   f"the bill: **{bill.ticket}**. A copy is now in {bill.patient}'s documents.")
        p1, p2 = st.columns([2, 3], gap="large")
        p1.image(C.preview_png(pdf), width="stretch")
        with p2:
            batch = ctx.registry.load_file(issuer_id)["batches"][-1]
            st.markdown("**What the hospital published** (all anyone outside ever sees):")
            st.code(json.dumps({"issuer": batch["issuer"], "published": batch["published"],
                                "entries": [[k[:20] + "…", v[:20] + "…"] for k, v in batch["entries"]],
                                "signature": batch["signature"][:20] + "…"}, indent=2, ensure_ascii=False),
                    language="json")
            st.download_button("Download the bill (PDF)", pdf, f"{bill.bill_no.replace('/', '_')}.pdf",
                               mime="application/pdf", key="h_dl")


# ==========================================================================
# Customer
# ==========================================================================

def customer_claims(ctx: Ctx) -> None:
    cid = ctx.user["customer_id"]
    page_title(f"Hello, {ctx.user['name'].split()[0]}",
               "Your claims and where each one stands, in plain words.")
    claims = ctx.store.claims(customer_id=cid)
    paid = [c for c in claims if c["status"].startswith("Paid")]
    kpis([(str(len(claims)), "claims"), (str(len(paid)), "paid"),
          (str(sum(c["status"] in OPEN_STATUSES for c in claims)), "in progress"),
          (str(sum(c["status"] == "Waiting for customer" for c in claims)), "need something from you")])
    st.write("")
    docs = {d["bill_no"]: d for d in ctx.store.documents_of_customer(cid)}
    for c in claims:
        with st.container(border=True):
            left, right = st.columns([4, 1])
            left.markdown(f"**{c['issuer_name']}** · claim {c['claim_id']} · submitted {c['submitted_at']}")
            right.markdown(_status_chip(c["status"]), unsafe_allow_html=True)
            st.write(CUSTOMER_TEXT[c["status"]])
            if c["status"] not in ("Paid",):
                with st.expander("Why? (the reason recorded on your claim)"):
                    st.write(c["reason"])
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
    cid = ctx.user["customer_id"]
    page_title("Submit a claim", "Choose a bill your hospital sent you, or upload a PDF. It is checked the "
                                 "moment you submit it.")
    docs = ctx.store.documents_of_customer(cid)
    claimed = {c["bill_no"] for c in ctx.store.claims(customer_id=cid) if c["bill_no"]}
    options = {f"{d['bill_no']} · {d['issuer_name']} · {d['bill_date']} · {inr(d['total_paise'])}"
               + ("  (already claimed)" if d["bill_no"] in claimed else ""): d["bill_no"] for d in docs}
    c1, c2 = st.columns(2, gap="large")
    with c1:
        pick = st.radio("From my documents", list(options) or ["(no documents yet)"], key="cs_pick")
    with c2:
        up = st.file_uploader("…or upload a bill (PDF)", type=["pdf"], key="cs_upload")
    if st.button("Submit claim", type="primary", key="cs_submit"):
        if up is not None:
            pdf, name, bill_no = up.getvalue(), up.name, ""
        elif pick in options:
            bill_no = options[pick]
            if bill_no in claimed:
                # The insurer's ordinary duplicate check: one bill, one claim.
                st.warning(f"{bill_no} has already been claimed. See My claims for where it stands.")
                return
            pdf, name = ctx.store.bill_pdf(bill_no), f"{bill_no.replace('/', '_')}.pdf"
        else:
            st.warning("Choose a document or upload a PDF first.")
            return
        claim_id = submit_claim(ctx.store, ctx.verifier, cid, pdf, name, bill_no, actor=ctx.user["name"])
        st.session_state.cs_last = claim_id
    last = st.session_state.get("cs_last")
    if last:
        c = ctx.store.claim(last)
        st.markdown(f"Claim **{c['claim_id']}** submitted. " + _status_chip(c["status"]),
                    unsafe_allow_html=True)
        st.info(CUSTOMER_TEXT[c["status"]])


# ==========================================================================
# Claims officer
# ==========================================================================

def _claim_detail(ctx: Ctx, claim_id: str, allow_decision: bool) -> None:
    c = ctx.store.claim(claim_id)
    r = result_from_json(json.loads(c["result_json"]))
    st.markdown(f"#### {c['claim_id']} · {c['customer_name']} · {c['issuer_name']}  " +
                _status_chip(c["status"]), unsafe_allow_html=True)
    C.verdict_view(r, c["pdf"], next_step=c["initial_action"])
    if c["officer_decision"]:
        st.info(f"Decision: {c['officer_decision']} by {c['decided_by']} on {c['decided_at']}.")
    if allow_decision and c["status"] in OPEN_STATUSES:
        st.markdown("**Your decision** (logged next to the machine's verdict)")
        b = st.columns(4)
        labels = [("release", "Genuine: release payment"), ("reject", "Fraud: reject and refer"),
                  ("ask_issuer", "Ask the hospital to confirm"), ("ask_customer", "Ask customer for a clearer copy")]
        for col, (key, label) in zip(b, labels):
            if col.button(label, key=f"dec_{key}_{claim_id}"):
                ctx.store.decide(claim_id, key, ctx.user["name"])
                st.rerun()


def officer_inbox(ctx: Ctx) -> None:
    page_title("Claims inbox", "Everything TrustLadder could not clear on proof. Paid claims never reach you.")
    open_claims = ctx.store.claims(statuses=OPEN_STATUSES)
    m = ctx.store.measures()
    kpis([(str(len(open_claims)), "waiting for a decision"),
          (str(sum(c["status"] == "On hold" for c in open_claims)), "on hold (possible fraud)"),
          (str(m["auto_paid"]), "paid straight through on proof"),
          (f"{m['straight_through_pct']}%", "straight-through rate")])
    st.write("")
    if not open_claims:
        st.success("Inbox empty.")
        return
    order = {"On hold": 0, "In review": 1, "Waiting for hospital": 2, "Waiting for customer": 3}
    open_claims.sort(key=lambda c: (order.get(c["status"], 9), c["submitted_at"]))
    labels = {f"{c['claim_id']} · {c['status']} · {c['verdict']} · {c['customer_name']} · {c['issuer_name']}":
              c["claim_id"] for c in open_claims}
    pick = st.selectbox("Open a claim", list(labels), key="inbox_pick")
    _claim_detail(ctx, labels[pick], allow_decision=True)


def officer_all(ctx: Ctx) -> None:
    page_title("All claims", "Every claim this month, with the machine's verdict and any human decision.")
    rows = ctx.store.claims()
    st.dataframe(_claims_frame(rows, ["claim_id", "submitted_at", "customer_name", "issuer_name", "verdict",
                                      "status", "officer_decision"]), hide_index=True, width="stretch")
    labels = [c["claim_id"] for c in rows]
    pick = st.selectbox("Look at one claim", labels, key="all_pick")
    _claim_detail(ctx, pick, allow_decision=False)


def officer_samples(ctx: Ctx) -> None:
    page_title("Try a sample bill", "Six prepared bills, each showing one principle of the rule. Nothing "
                                    "here is saved as a claim.")
    manifest = json.loads((ctx.data_dir / "showcase" / "manifest.json").read_text())
    options = [f"{i + 1}. {m['title']}" for i, m in enumerate(manifest)]
    c1, c2 = st.columns([3, 2])
    choice = c1.selectbox("Sample bill", options, key="showcase_pick")
    up = c2.file_uploader("…or upload any PDF bill", type=["pdf"], key="sample_upload")
    if up is not None:
        pdf, name, shows = up.getvalue(), up.name, None
    else:
        m = manifest[options.index(choice)]
        pdf, name, shows = (ctx.data_dir / "showcase" / m["file"]).read_bytes(), m["file"], m["shows"]
    if shows:
        st.info(f"**What this case shows:** {shows}")
    if st.button("Run it up the ladder", type="primary", key="run"):
        st.session_state.sample_result = (name, run_ladder(pdf, ctx.verifier, name), pdf)
    last = st.session_state.get("sample_result")
    if last and last[0] == name:
        C.verdict_view(last[1], last[2])
    else:
        st.image(C.preview_png(pdf), width=430)


# ==========================================================================
# Risk head (CRO)
# ==========================================================================

def risk_dashboard(ctx: Ctx) -> None:
    page_title("Impact dashboard", "The cost of being wrong in both directions, measured on this month's "
                                   "claims. You set what happens to each verdict.")
    m = ctx.store.measures()
    kpis([(f"{m['straight_through_pct']}%", f"paid straight through ({m['auto_paid']} of {m['claims']})"),
          (str(m["rejected"]), "rejected as fraud after review"),
          (str(m["wrongful_holds"]), "wrongful holds (held, then released as genuine)"),
          (f"{m['officer_hours']} h", f"officer time (≈ ₹{int(m['officer_hours'] * COST_PER_HOUR_RS):,})")])
    st.caption(f"Sample data: of the {m['sample_frauds']} known frauds in this month, "
               f"{m['sample_frauds_paid']} were paid automatically. Officer time assumes "
               f"{MINUTES_PER_REVIEW} minutes per case at ₹{COST_PER_HOUR_RS}/hour (the group's assumption).")
    c1, c2 = st.columns([1, 1], gap="large")
    with c1:
        st.markdown("**Policy: what happens to each verdict**")
        policy = ctx.store.policy()
        for verdict in ("Authentic", "Suspicious", "Tampered", "Inconclusive"):
            choice = st.selectbox(verdict, ALLOWED[verdict], index=ALLOWED[verdict].index(policy[verdict]),
                                  key=f"pol_{verdict}")
            if choice != policy[verdict]:
                ctx.store.set_policy(verdict, choice, ctx.user["name"])
                st.toast(f"Policy saved: {verdict} → {choice}")
        st.caption("'Pay' is offered only for Authentic: no setting can pay a document the issuer has not "
                   "confirmed. Changes apply to claims submitted from now on and are logged for audit.")
    with c2:
        st.markdown("**Where this month's claims are now**")
        rows = ctx.store.claims()
        counts = pd.Series([r["status"] for r in rows]).value_counts()
        st.bar_chart(counts, color="#0F766E", horizontal=True, height=280)


def risk_simulation(ctx: Ctx) -> None:
    page_title("Simulated month", "150 synthetic claims run through the real ladder at three levels of "
                                  "hospital sign-up, compared with an edit-detection tool.")
    sim = json.loads((ctx.data_dir / "simulation.json").read_text())
    policy = ctx.store.policy()
    scenario = st.radio("How many hospitals have joined the registry?", list(sim["results"]), index=1,
                        key="scenario", horizontal=True)
    rows = sim["results"][scenario]
    tl, base = evaluate(rows, policy), baseline(rows)
    kpis([(f"{tl['straight_through_pct']}%", "paid straight through"),
          (str(tl["fraud_paid"]), f"frauds paid (of {tl['fraud_total']})"),
          (str(tl["genuine_held"]), "genuine customers held"),
          (f"{tl['officer_hours']} h", "officer time")])
    st.write("")
    compare = pd.DataFrame({
        "TrustLadder (your policy)": [tl["paid_straight_through"], tl["fraud_paid"], tl["fraud_stopped"],
                                      tl["genuine_held"], tl["genuine_delayed"], tl["officer_hours"]],
        "Edit-detection tool": [base["paid_straight_through"], base["fraud_paid"], base["fraud_stopped"],
                                base["genuine_held"], base["genuine_delayed"], base["officer_hours"]],
    }, index=["Paid straight through", "Frauds paid", "Frauds stopped", "Genuine customers held",
              "Genuine sent to a person", "Officer hours"])
    st.dataframe(compare, width="stretch")
    trend = pd.DataFrame({label: {
        "Paid straight through": evaluate(r, policy)["paid_straight_through"],
        "Sent to a person or held": evaluate(r, policy)["to_person"] + evaluate(r, policy)["held"]}
        for label, r in sim["results"].items()}).T
    st.bar_chart(trend, color=["#15803D", "#B45309"], stack=False, height=240)
    st.caption("The mix (about 10% fraud, 8% photos, 8% re-saved genuine files) is our assumption, not a "
               "statistic. The edit-detection tool pays every fake made from nothing and holds genuine "
               "customers whose files were merely re-saved.")


# ==========================================================================
# Registry operator
# ==========================================================================

def registry_network(ctx: Ctx) -> None:
    page_title("Network", "Who has joined, and what the registry holds: signed codes, never a bill, a "
                          "name or an amount.")
    directory = ctx.registry.load_directory()
    rows = []
    for iid, (name, city, _, _) in HOSPITALS.items():
        entry = directory.get(iid)
        f = ctx.registry.load_file(iid) if entry else {"batches": []}
        rows.append({"Hospital": name, "City": city, "Joined": "Yes" if entry else "No",
                     "Public key": (entry["public_key"][:16] + "…") if entry else "-",
                     "Signed batches": len(f["batches"]),
                     "Bills covered": sum(b["count"] for b in f["batches"]),
                     "Patient data held": "None"})
    joined = sum(r["Joined"] == "Yes" for r in rows)
    kpis([(f"{joined} of {len(rows)}", "hospitals joined"),
          (str(sum(r["Bills covered"] for r in rows)), "bills covered"),
          ("0", "patient records held")])
    st.write("")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    not_joined = [iid for iid in HOSPITALS if iid not in directory]
    if not_joined:
        st.markdown("**Enrol a hospital** (onboarding is a key pair and a line in the directory: no "
                    "integration project, no data sharing)")
        pick = st.selectbox("Hospital", not_joined, format_func=lambda i: HOSPITALS[i][0], key="enrol_pick")
        if st.button("Enrol hospital", type="primary", key="enrol"):
            enrol_hospital(ctx.store, ctx.registry, ctx.verifier, pick, ctx.user["name"])
            st.rerun()


def registry_integrity(ctx: Ctx) -> None:
    page_title("Integrity check", "Every file is signed by its hospital. Check the signatures, and try to "
                                  "slip in a forged line.")
    directory = ctx.registry.load_directory()
    rows = []
    for iid, entry in directory.items():
        for b in ctx.registry.load_file(iid)["batches"]:
            unsigned = {k: v for k, v in b.items() if k != "signature"}
            rows.append({"Hospital": entry["name"], "Published": b["published"], "Lines": b["count"],
                         "Signature": "Valid" if crypto.verify_signature(unsigned, b["signature"],
                                                                         entry["public_key"]) else "INVALID"})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    iid = next(iter(directory))
    b = ctx.registry.load_file(iid)["batches"][0]
    c1, c2 = st.columns([3, 2], gap="large")
    with c1:
        st.markdown(f"**One day's file from {directory[iid]['name']}** (first 3 of {b['count']} lines)")
        st.code(json.dumps({"issuer": b["issuer"], "published": b["published"], "count": b["count"],
                            "entries": [[k[:20] + "…", v[:20] + "…"] for k, v in b["entries"][:3]],
                            "signature": b["signature"][:20] + "…"}, indent=2, ensure_ascii=False), language="json")
    with c2:
        st.markdown("**Can someone slip a fake line in?**")
        if st.button("Insert a forged entry", key="forge"):
            forged = {k: v for k, v in b.items() if k != "signature"}
            forged["entries"] = forged["entries"] + [[crypto.new_salt() * 2, crypto.new_salt() * 2]]
            forged["count"] += 1
            st.session_state.forge_result = crypto.verify_signature(forged, b["signature"],
                                                                    directory[iid]["public_key"])
        if "forge_result" in st.session_state:
            if st.session_state.forge_result:
                st.error("Signature still valid (this should never happen).")
            else:
                st.success("Rejected: the hospital's signature no longer matches, so every verifier ignores "
                           "the altered file. Only the hospital can add lines.")


# ==========================================================================
# Auditor / regulator
# ==========================================================================

def auditor_trail(ctx: Ctx) -> None:
    page_title("Audit trail", "Every machine verdict, human decision, policy change and enrolment. "
                              "Read-only.")
    events = pd.DataFrame(ctx.store.audit())
    roles = ["All"] + sorted(events["role"].unique()) if not events.empty else ["All"]
    c1, c2 = st.columns([1, 2])
    role = c1.selectbox("Who", roles, key="aud_role")
    q = c2.text_input("Search (claim, event, detail)", key="aud_q")
    view = events
    if role != "All":
        view = view[view["role"] == role]
    if q:
        mask = view.apply(lambda r: q.lower() in " ".join(map(str, r.values)).lower(), axis=1)
        view = view[mask]
    st.dataframe(view.rename(columns={"ts": "When", "actor": "Who", "role": "Role", "event": "Event",
                                      "claim_id": "Claim", "detail": "Detail"}),
                 hide_index=True, width="stretch", height=420)
    st.download_button("Download as CSV", view.to_csv(index=False), "trustladder_audit.csv",
                       mime="text/csv", key="aud_csv")
    m = ctx.store.measures()
    st.caption(f"{m['claims']} claims · {m['auto_paid']} paid on proof · {m['released_after_review']} "
               f"released after review · {m['rejected']} rejected · {m['wrongful_holds']} wrongful hold(s).")


def auditor_rules(ctx: Ctx) -> None:
    page_title("How decisions are made", "The published rule (a table, not a score) and the AI "
                                         "architecture around it.")
    st.markdown("**The rule, version TL-RULE-1.0** · first matching line wins")
    C.rule_table()
    st.write("")
    st.markdown("**AI architecture** · solid = running in this demo; dashed = target design using current "
                "AI models, not claimed as built")
    C.architecture_diagram()


# ==========================================================================
# Menus
# ==========================================================================

MENUS = {
    "hospital": [("My bills", hospital_my_bills), ("Issue a bill", hospital_issue)],
    "customer": [("My claims", customer_claims), ("Submit a claim", customer_submit)],
    "officer": [("Claims inbox", officer_inbox), ("All claims", officer_all),
                ("Try a sample bill", officer_samples)],
    "riskhead": [("Impact dashboard", risk_dashboard), ("Simulated month", risk_simulation)],
    "registry": [("Network", registry_network), ("Integrity check", registry_integrity)],
    "auditor": [("Audit trail", auditor_trail), ("How decisions are made", auditor_rules)],
}
