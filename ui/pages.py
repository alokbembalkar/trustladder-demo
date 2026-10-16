"""
The screens, one set per role. Designed to be demonstrated live: each screen
carries ONE idea and ONE main action; anything technical sits behind the
sidebar switch "Show technical details" or in a closed section.

    Hospital billing      Issue a bill · My bills
    Customer              My claims · Submit a claim   <- the ONE place a bill is uploaded
    Claims officer        Claims inbox
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

from trustladder import crypto, vision
from trustladder.cases import HOSPITALS
from trustladder.models import BillFields, LineItem
from trustladder.policy import (ALLOWED, COST_PER_HOUR_RS, HOLD, MINUTES_PER_REVIEW, PAY,
                                REQUEST, REVIEW, baseline, evaluate, route)
from trustladder.registry import RegistryStore, Verifier
from trustladder.service import (enrol_hospital, issue_bill, resubmit_claim,
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
    """Offer the seven sample bills as a zip, so anyone can try the upload path."""
    pack = ctx.data_dir / "TrustLadder_sample_bills.zip"
    if pack.exists():
        st.download_button("Download sample bills (zip)", pack.read_bytes(),
                           "TrustLadder_sample_bills.zip", mime="application/zip", key=key,
                           help="What the demo's patient has on her phone: six PDFs and two photos, "
                                "one of them printed in Marathi")


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
    # The list is alphabetical, but the bill issued here is the one the demo's own
    # customer then claims, and the six prepared sample bills are for that same
    # person. Defaulting to anyone else produced a bill in one patient's name being
    # claimed by another, which is the first thing a reviewer asks about.
    demo_cid = ctx.store.user("customer")["customer_id"]
    default = next((i for i, c in enumerate(customers) if c["customer_id"] == demo_cid), 0)
    c1, c2 = st.columns([1, 1], gap="large")
    with c1:
        cust = st.selectbox("Patient", customers, index=default,
                            format_func=lambda c: f"{c['name']} ({c['city']})",
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
                         f"{name.split()[0]} billing")
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
            st.markdown("**Give the bill to the patient.** In this demo you download it here, and upload "
                        "it as the customer in the next step.")
            st.download_button("Download the bill (PDF)", pdf, f"{bill.bill_no.replace('/', '_')}.pdf",
                               mime="application/pdf", key="h_dl", type="primary")


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
                    resubmit_claim(ctx.store, ctx.verifier, c["claim_id"], pdf, name,
                                   c["customer_name"])
                    st.rerun()


_FORM_FIELDS = [
    # key, label, confidence key, help
    ("issuer_id", "Hospital's issuer ID", "issuer_id", "Printed under the hospital's name"),
    ("bill_no", "Bill number", "bill_no", ""),
    ("bill_date", "Bill date (YYYY-MM-DD)", "bill_date", ""),
    ("patient", "Patient", "patient", ""),
    ("total", "Grand total (Rs)", "total", "Rupees, as printed"),
    ("ticket", "Ticket printed on the bill", "ticket", "Leave empty if the bill has none"),
]


def _conf_chip(value: float | None) -> str:
    """A small confidence pill beside a field the model read."""
    if value is None:
        return ""
    colour = "#15803D" if value >= 0.9 else "#B45309" if value >= 0.7 else "#B91C1C"
    return f'<span class="tl-conf" style="background:{colour}">{value:.0%} sure</span>'


def _is_picture(data: bytes, name: str) -> bool:
    """A JPEG or PNG, or a PDF with no text layer (a scan)."""
    from trustladder.reader import pdf_text
    return vision.mime_of(name) is not None or len(pdf_text(data).strip()) < 20


def _reading_form(reading: vision.Reading, form_key: str) -> tuple[dict, bool]:
    """Show what the vision model read, let the customer correct it, and ask them to confirm.

    Returns (values as typed, confirmed). The form is keyed on the file, so a new upload
    starts a new form instead of carrying the previous bill's corrections across.
    """
    model = reading.model or "the vision model"
    lang = f" · printed in <b>{reading.language}</b>" if reading.language else ""
    if reading.status.value == "Read":
        title = "Here is what the vision model read from your photo"
        sub = (f'<span class="ai">AI</span> Read by <b>{model}</b>{lang}. Check each line, correct '
               'anything that is wrong, then confirm.')
    elif reading.partial:
        title = "The picture was read, but not every detail"
        sub = (f'{reading.note} <span class="ai">AI</span> Reader: <b>{model}</b>{lang}.')
    else:
        title = "This picture could not be read"
        sub = reading.note
    st.markdown(f'<div class="tl-read"><div class="t">{title}</div><div class="s">{sub}</div></div>',
                unsafe_allow_html=True)
    values = vision.form_values(reading)
    typed = {}
    cols = st.columns(2)
    for i, (key, label, ckey, help_) in enumerate(_FORM_FIELDS):
        with cols[i % 2]:
            shown = values.get(key, "")
            if key == "total":
                shown = f"{shown / 100:,.2f}" if shown else ""
            conf = reading.confidence.get(ckey) if reading.status.value == "Read" else None
            st.markdown(f'<div style="font-size:0.84rem;color:#64748B;margin-bottom:-10px">{label}'
                        f'{_conf_chip(conf)}</div>', unsafe_allow_html=True)
            typed[key] = st.text_input(label, value=str(shown), key=f"{form_key}_{key}",
                                       label_visibility="collapsed", help=help_ or None)
    if reading.fields and reading.fields.items:
        with st.expander(f"The {len(reading.fields.items)} charges it read"):
            st.dataframe(pd.DataFrame([{"Description": i.description, "Amount (Rs)": f"{i.amount_paise / 100:,.2f}"}
                                       for i in reading.fields.items]), hide_index=True, width="stretch")
    confirmed = st.checkbox("These are the details on my bill", key=f"{form_key}_ok")
    st.caption("In this demo the reading model is hosted by Google and the bill is synthetic. In the "
               "product the reader runs inside the insurer, and your photo never leaves it.")
    return typed, confirmed


def customer_submit(ctx: Ctx) -> None:
    """The customer's only way in: upload the bill the hospital gave them, as a PDF or a photo.

    A PDF with a text layer is read exactly and only needs a glance before submitting.
    A photograph is read by the vision model, and what it read is shown back as a form
    the customer can correct and must confirm: the reading is the customer's statement
    once they submit it, and the number of corrections is the reader's measured error.
    """
    cid = ctx.user["customer_id"]
    page_title("Submit a claim", "Upload the bill your hospital gave you, or a photo of it. It is "
                                 "checked the moment you submit, and you are told straight away where "
                                 "your claim stands.")
    up = st.file_uploader("Your bill (PDF, or a photo as JPG or PNG)", type=["pdf", "jpg", "jpeg", "png"],
                          key="cs_upload")
    reading = None
    typed, confirmed = {}, False
    picture = False
    if up is not None:
        data = up.getvalue()
        picture = _is_picture(data, up.name)
        # The bill is shown BEFORE the Submit button, not after it. With the button first,
        # the obvious next click came before the chance to check the file, which is the
        # wrong order for the one irreversible thing a customer does on this screen.
        if not picture:
            st.markdown("**Check this is the right bill, then submit it.**")
            st.image(C.preview_png(data), width=430)
            st.write("")
        else:
            # Read once per file. The model is slow and must not re-run on every click.
            file_key = f"{up.name}:{len(data)}:{hash(data[:4096])}"
            if st.session_state.get("cs_read_key") != file_key:
                with st.spinner("Reading your photo with a vision model…"):
                    st.session_state.cs_reading = vision.read_any(data, up.name)
                st.session_state.cs_read_key = file_key
            reading = st.session_state.cs_reading
            left, right = st.columns([2, 3], gap="large")
            with left:
                st.image(C.preview_any(data, up.name), width="stretch")
            with right:
                typed, confirmed = _reading_form(reading, f"rf_{abs(hash(file_key))}")
            st.write("")
    c1, c2 = st.columns([1, 3])
    can_submit = up is not None and (not picture or confirmed)
    submitted = c1.button("Submit claim", type="primary", key="cs_submit", disabled=not can_submit)
    with c2:
        sample_pack_button(ctx, "cs_pack")
        if picture and not confirmed:
            st.caption("Confirm the details above to submit.")
    if submitted:
        # A bill that came in before is not refused: it is checked like any other,
        # and "already claimed on this policy" becomes one more piece of evidence.
        # The audit trail names the PARTY who claimed, not the login they used:
        # "Customer" is a role label for the screen, and an auditor needs the person.
        # submit_claim falls back to the customer's own name when no actor is given.
        confirmed_reading = vision.apply_corrections(reading, typed) if picture else None
        with st.spinner("Checking your bill…"):
            st.session_state.cs_last = submit_claim(ctx.store, ctx.verifier, cid, up.getvalue(), up.name,
                                                    reading=confirmed_reading, background=True)
    last = st.session_state.get("cs_last")
    if last:
        c = ctx.store.claim(last)
        st.write("")
        st.divider()
        st.markdown(f"**Your last submission** · {c['issuer_name']} · claim {c['claim_id']} · "
                    f"you uploaded `{c['file_name']}`", unsafe_allow_html=True)
        # The customer sees the STATUS of their claim, never the verdict, the findings or
        # the stage ladder. See components.claim_receipt for why.
        C.claim_receipt(c["status"], c["pdf"], CUSTOMER_TEXT[c["status"]], c["file_name"])


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
        def record(action: str) -> None:
            """Decide, then hand the officer back to the queue with confirmation.

            The claim leaves the queue the moment it is decided, so without this the
            officer saw it disappear and had no way to tell what had been recorded.
            """
            ctx.store.decide(claim_id, action, ctx.user["name"])
            st.session_state.officer_done = claim_id
            st.session_state.pop("officer_open", None)
            st.rerun()

        st.write("")
        b1, b2 = st.columns(2)
        if b1.button("Approve payment", key=f"dec_release_{claim_id}", type="primary", width="stretch"):
            record("release")
        if b2.button("Reject as fraud", key=f"dec_reject_{claim_id}", width="stretch"):
            record("reject")
        with st.expander("Other options"):
            if st.button("Ask the hospital to confirm", key=f"dec_ask_issuer_{claim_id}"):
                record("ask_issuer")
            if st.button("Ask the customer for a clearer copy", key=f"dec_ask_customer_{claim_id}"):
                record("ask_customer")

    # The officer is the one person asked to call something fraud, so they see what
    # was found without having to turn anything on.
    C.verdict_view(r, c["pdf"], next_step=c["initial_action"], actions=decision_buttons,
                   show_findings=True)
    st.write("")
    C.reading_line(r)
    C.agent_panel(r, pending=bool(c.get("narrative_pending")))


SORT_ORDERS = ("Newest first", "Longest waiting first", "Worst first")


def _waited(submitted_at: str) -> str:
    """How long this claim has been sitting, in words an officer thinks in."""
    try:
        when = _dt.datetime.strptime(submitted_at[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return ""
    days = (_dt.datetime.now() - when).days
    if days <= 0:
        return "today"
    return "1 day" if days == 1 else f"{days} days"


def _open_claims(ctx: Ctx, order: str = "Newest first") -> list[dict]:
    """The officer's queue, in the order they asked for.

    Newest first puts the claim just submitted at the top, which is what a demo and
    a same-day triage both want. Longest waiting first is how a real queue is worked
    when a service level is in play. Worst first ignores the clock and brings the
    possible fraud up. The date is on screen either way, so the order is never a
    hidden assumption.
    """
    rows = ctx.store.claims(statuses=OPEN_STATUSES)
    rows.sort(key=lambda c: (c["submitted_at"], c["claim_id"]), reverse=True)
    if order == "Longest waiting first":
        rows.reverse()
    elif order == "Worst first":
        rank = {"On hold": 0, "In review": 1, "Waiting for hospital": 2, "Waiting for customer": 3}
        rows.sort(key=lambda c: rank.get(c["status"], 9))
    return rows


def officer_inbox(ctx: Ctx) -> None:
    """A work queue first, then one claim.

    The screen used to open the top claim straight away, so an officer saw a verdict
    before seeing what was waiting. Now it lists what is pending, like any work queue,
    and the verdict and the evidence appear only once a claim is chosen.
    """
    page_title("Claims inbox", "Only the claims that could not be proven. Proven claims are paid without you.")
    order = st.session_state.get("inbox_sort", SORT_ORDERS[0])
    open_claims = _open_claims(ctx, order)
    m = ctx.store.measures()
    kpis([(str(len(open_claims)), "waiting for you"),
          (str(sum(c["status"] == "On hold" for c in open_claims)), "on hold (possible fraud)"),
          (str(m["auto_paid"]), "paid automatically on proof")])
    st.write("")

    just_done = st.session_state.pop("officer_done", None)
    if just_done:
        d = ctx.store.claim(just_done)
        st.success(f"**{d['officer_decision']}.** Claim {just_done}, {d['customer_name']} · "
                   f"{d['issuer_name']}. The machine verdict was {d['verdict']}; the decision is yours "
                   f"and both are in the audit trail. It has left your queue and is listed under "
                   f"**Decided recently** below.")

    chosen = st.session_state.get("officer_open")
    if chosen and any(c["claim_id"] == chosen for c in open_claims):
        if st.button("◂ Back to the queue", key="inbox_back"):
            st.session_state.pop("officer_open", None)
            st.rerun()
        _claim_detail(ctx, chosen)
        return

    if not open_claims:
        st.success("Nothing is waiting for you.")
        return

    st.markdown("**Waiting for a decision.** Open one to see the verdict and the evidence "
                "behind it.")
    # A radio, not a dropdown. A dropdown's open list hangs over the first row and
    # swallows the click meant for its Open button, which is exactly what happened the
    # first time this was built. Three short options also read better than a closed menu.
    st.radio("Sort by", SORT_ORDERS, key="inbox_sort", horizontal=True)
    st.write("")

    widths = [1.9, 2.0, 2.4, 1.3, 1.7, 1.9, 1.1]
    head = st.columns(widths)
    for col, label in zip(head, ("Claim", "Customer", "Hospital", "Amount", "Submitted",
                                 "Why it is here", "")):
        col.markdown(f'<span class="tl-muted"><b>{label}</b></span>', unsafe_allow_html=True)
    for c in open_claims:
        row = st.columns(widths)
        row[0].write(c["claim_id"])
        row[1].write(c["customer_name"])
        row[2].write(c["issuer_name"])
        # "could not read" claims have no amount, and saying so is the honest answer
        row[3].write(inr(c["total_paise"]) if c["total_paise"] else "not readable")
        waited = _waited(c["submitted_at"])
        row[4].markdown(f'{c["submitted_at"][:10]}<br><span class="tl-muted">{waited}</span>',
                        unsafe_allow_html=True)
        row[5].markdown(_status_chip(c["status"]), unsafe_allow_html=True)
        if row[6].button("Open", key=f"open_{c['claim_id']}"):
            st.session_state.officer_open = c["claim_id"]
            st.rerun()
    st.caption(f"{len(open_claims)} claims are waiting. Everything else was settled without a person: "
               f"{m['auto_paid']} paid on the issuer's proof.")
    _decided_recently(ctx)


def _decided_recently(ctx: Ctx) -> None:
    """What this officer has already settled. A decision that vanishes cannot be checked."""
    done = ctx.store.decided()
    if not done:
        return
    st.write("")
    with st.expander(f"Decided recently ({len(done)})"):
        head = st.columns([2.1, 2.2, 2.6, 1.4, 1.6, 2.6])
        for col, label in zip(head, ("Claim", "Customer", "Hospital", "Amount",
                                     "Machine verdict", "Your decision")):
            col.markdown(f'<span class="tl-muted"><b>{label}</b></span>', unsafe_allow_html=True)
        for c in done:
            row = st.columns([2.1, 2.2, 2.6, 1.4, 1.6, 2.6])
            row[0].write(c["claim_id"])
            row[1].write(c["customer_name"])
            row[2].write(c["issuer_name"])
            row[3].write(inr(c["total_paise"]) if c["total_paise"] else "not readable")
            row[4].write(c["verdict"])
            row[5].write(f"{c['officer_decision']} · {c['decided_at']}")
        st.caption("The machine's verdict and the person's decision are kept side by side, and the "
                   "auditor sees the same pair. Where they differ, the person's decision stands and "
                   "the disagreement is on the record.")


# ==========================================================================
# Risk head (CRO)
# ==========================================================================

def risk_dashboard(ctx: Ctx) -> None:
    """The risk head's screen.

    Twice now the complaint was that the role and the job were unclear. The screen
    named the role but never said what this person DOES here, and its only control,
    four policy menus, had no visible consequence, so it read as a page of numbers.

    It is now three numbered things: what the month looks like, the one setting that
    is theirs to make, and what changes as coverage grows. Changing the setting moves
    a line of counts underneath it, so the trade-off is on screen instead of implied.
    """
    m = ctx.store.measures()
    page_title("Risk head's dashboard",
               "The insurer's Chief Risk Officer. A claims officer decides one claim at a time; "
               "you set the rule that routes every claim, and you answer for both kinds of mistake.")

    st.markdown("#### 1. Where this month stands")
    st.caption(f"Every number here counts the {m['claims']} claims in this demo's sample month. "
               "Nothing on this line is a projection.")
    held_word = "customer" if m["wrongful_holds"] == 1 else "customers"
    kpis([(f"{m['straight_through_pct']}%",
           f"paid automatically on the issuer's proof<br>{m['auto_paid']} of {m['claims']} claims, "
           f"no officer involved"),
          (f"{m['rejected']} of {m['sample_frauds']}",
           f"known frauds rejected after review<br>{m['sample_frauds_paid']} were paid automatically"),
          (f"{m['wrongful_holds']}",
           f"honest {held_word} wrongly held<br>genuine bills stopped by the rule, then released"),
          (f"{m['readings_corrected']} of {m['picture_claims']}",
           "photo readings the customer corrected<br>the vision model's error rate, as customers see it")])
    st.caption(f"The other {m['to_person']} claims went to a person: {m['open']} are still open and "
               f"{m['released_after_review']} were released after review. Officer time so far: "
               f"about {m['officer_hours']} hours at {MINUTES_PER_REVIEW} minutes a case.")
    st.write("")

    st.markdown("#### 2. Your decision: what happens to each verdict")
    st.caption("This is the only thing on this screen you change, and it is the institution's call, "
               "not the software's. Pick an action for each verdict and the line underneath shows "
               "what that choice would do. It applies to claims checked from now on.")
    policy = ctx.store.policy()
    cols = st.columns(4)
    for col, verdict in zip(cols, ("Authentic", "Suspicious", "Tampered", "Inconclusive")):
        choice = col.selectbox(verdict, ALLOWED[verdict], index=ALLOWED[verdict].index(policy[verdict]),
                               key=f"pol_{verdict}")
        if choice != policy[verdict]:
            ctx.store.set_policy(verdict, choice, ctx.user["name"])
            st.toast(f"Policy saved: {verdict} → {choice}")
            st.rerun()
    policy = ctx.store.policy()
    verdicts = [c["verdict"] for c in ctx.store.claims()]
    r = route(verdicts, policy)
    st.markdown(
        f'<div class="tl-reason"><b>With this setting, this month\'s {len(verdicts)} claims would go:</b> '
        f'{r[PAY]} paid automatically · {r[REVIEW]} to a person · {r[HOLD]} held and investigated · '
        f'{r[REQUEST]} sent back for a clearer copy.</div>', unsafe_allow_html=True)
    st.caption("Move one menu and watch that line. Sending more to a person catches more, and costs "
               "officer time and delays honest customers; sending less does the opposite. "
               "**'Pay' is offered only for Authentic: no setting can pay a bill the hospital has not "
               "confirmed.** That limit is in the code, not in this menu.")
    st.write("")

    st.markdown("#### 3. What changes as more hospitals join")
    with st.expander("A separate what-if: a larger month of 150 claims"):
        st.caption("This is NOT the sample month above. It is a synthetic month of 150 claims, used to "
                   "show how much is settled without a person as coverage grows.")
        sim = json.loads((ctx.data_dir / "simulation.json").read_text())
        scenario = st.radio("Hospitals joined", list(sim["results"]), index=1, key="scenario", horizontal=True)
        rows = sim["results"][scenario]
        tl, base = evaluate(rows, policy), baseline(rows)
        kpis([(f"{tl['straight_through_pct']}%", "paid automatically on proof"),
              (f"{tl['fraud_paid']} of {tl['fraud_total']}", "frauds paid automatically by TrustLadder"),
              (f"{base['fraud_paid']} of {base['fraud_total']}",
               f"frauds paid by an edit-detection tool,<br>which also holds "
               f"{base['genuine_held']} honest customers")])
        st.caption("Synthetic month; the mix (about 10% fraud, 8% photos, 8% re-saved files) is our "
                   f"assumption. Officer time assumed at {MINUTES_PER_REVIEW} minutes per case at "
                   f"₹{COST_PER_HOUR_RS}/hour.")


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
    page_title("How decisions are made",
               "A published rule, not a score: anyone can apply it by hand. The models around it are "
               "published here too, with their versions and their limits.")
    C.rule_table()
    st.write("")
    st.divider()
    C.model_registry_table()
    st.write("")
    with st.expander("The screening model's weights, in full"):
        C.model_card()
    with st.expander("The AI architecture around the rule", expanded=C.details_on()):
        C.architecture_diagram()


# ==========================================================================
# Menus
# ==========================================================================

MENUS = {
    "hospital": [("Issue a bill", hospital_issue), ("My bills", hospital_my_bills)],
    "customer": [("My claims", customer_claims), ("Submit a claim", customer_submit)],
    "officer": [("Claims inbox", officer_inbox)],
    "riskhead": [("Dashboard", risk_dashboard)],
    "registry": [("Network", registry_network)],
    "auditor": [("Audit trail", auditor_trail), ("How decisions are made", auditor_rules)],
}
