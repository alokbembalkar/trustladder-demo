"""
The actions people take, shared by the screens and by the sample-data seeder.

Keeping these in one place means the seeded history and a live click go through
exactly the same code: a seeded claim is judged by the same ladder, routed by
the same policy and logged the same way as one submitted during the demo.
"""

from __future__ import annotations

import threading

from dataclasses import replace

from . import llm, packet, vision
from .bills import render_discharge_summary, render_genuine
from .cases import HOSPITALS
from .evidence import build as build_evidence
from .models import CLAIM_HISTORY, KIND_SUMMARY, BillFields, DischargeFields, Finding
from .pipeline import narrate, run_ladder, with_extra_findings
from .registry import Publisher, RegistryStore, Verifier
from .store import Store


def duplicate_findings(store: Store, customer_id: str, bill_no: str) -> list[Finding]:
    """Evidence the document cannot carry: has this bill been claimed before?

    A customer may legitimately upload the same bill twice by mistake, so this is
    reported as a FINDING and never as a refusal. What it does NOT do is clear or
    convict on its own: a repeat of a bill the issuer confirms is still Authentic
    (the rule reaches "registry Verified" first), and a repeat on its own is still
    only one line of evidence.
    """
    if not bill_no:
        return []
    earlier = [c for c in store.claims(customer_id=customer_id) if c["bill_no"] == bill_no]
    if not earlier:
        return []
    first = earlier[0]
    return [Finding(
        family=CLAIM_HISTORY,
        check="duplicate_claim",
        detail=f"Bill {bill_no} was already claimed on this policy "
               f"(claim {first['claim_id']}, {first['submitted_at']}).",
    )]


def submit_claim(store: Store, verifier: Verifier, customer_id: str, pdf: bytes, file_name: str,
                 bill_no: str = "", truth: str = "", submitted_at: str | None = None,
                 actor: str = "", reading: vision.Reading | None = None,
                 background: bool = False,
                 attachments: list[tuple[bytes, str, "vision.Reading | None"]] | None = None) -> str:
    """A customer submits a bill: TrustLadder checks it on attach, the policy routes it.

    The bill number is taken from the uploaded document itself when it can be read,
    so an uploaded claim is linked to the same bill as the hospital's record. A bill
    that has been claimed before on this policy is accepted and checked, with the
    repeat recorded as one more finding for the rule to weigh.

    `reading` is the customer-confirmed reading of a picture, when the screen has
    already read it; a PDF needs none.

    `background=True` records the claim at once with the written reason and lets the
    models write the reason and the agent's account afterwards, in a thread. The
    customer's status never waits on a network call, and the verdict is the same
    either way because the models run after the rule. The seeder uses the foreground
    path so the snapshot is complete when it is taken.

    `attachments` are the other documents of the claim packet, each as (bytes, file
    name, reading-or-None): today the discharge summary. Each is read and looked up on
    its own, and the packet checks in `packet.py` add their findings to the bill's.
    """
    customer = store.customer(customer_id)
    result = run_ladder(pdf, verifier, file_name, reading, narrate=False)
    bill_no = bill_no or (result.fields.bill_no if result.fields else "")
    # The insurer knows something the PDF cannot say: whether this bill came in
    # before. Fold that in and let the published rule weigh it with the rest.
    earlier = [c for c in store.claims(customer_id=customer_id) if c["bill_no"] == bill_no] if bill_no else []
    extra = duplicate_findings(store, customer_id, bill_no)
    summaries: list[tuple[bytes, str, packet.SummaryResult]] = []
    for data, name, att_reading in (attachments or []):
        att_reading = att_reading or vision.read_any(data, name)
        if att_reading.kind != KIND_SUMMARY:
            # Only a discharge summary is a packet document today; anything else is
            # kept with the claim for the officer but takes no part in the checks.
            att_reading = vision.Reading(**{**att_reading.__dict__, "kind": KIND_SUMMARY,
                                            "status": att_reading.status})
        judged = packet.judge_summary(att_reading, verifier, name)
        summaries.append((data, name, judged))
        extra += packet.packet_findings(result.fields, judged)
    result = with_extra_findings(result, extra)
    if summaries:
        result = replace(result, read_report={**result.read_report,
                                              "summary": summaries[0][2].to_json()})
    is_pdf = vision.mime_of(file_name) is None

    if not background:
        # The reason and the agent's account, written now (with the models when they
        # are configured, from fixed sentences otherwise).
        result = narrate(result, earlier, is_pdf)
        graph = build_evidence(result)
        claim_id = store.record_claim(customer_id, customer["name"], file_name, pdf, result,
                                      graph.independent_against, bill_no=bill_no, truth=truth,
                                      submitted_at=submitted_at, actor=actor or customer["name"])
        _attach(store, claim_id, summaries)
        return claim_id

    graph = build_evidence(result)
    pending = llm.narrates()              # narration runs only in full "on" mode
    claim_id = store.record_claim(customer_id, customer["name"], file_name, pdf, result,
                                  graph.independent_against, bill_no=bill_no, truth=truth,
                                  submitted_at=submitted_at, actor=actor or customer["name"],
                                  narrative_pending=pending)
    _attach(store, claim_id, summaries)
    if pending:
        threading.Thread(target=_narrate_later, args=(store.path, claim_id, result, earlier, is_pdf),
                         daemon=True).start()
    else:
        # No model to wait for: the fixed-order trace is the account, and it is ready now.
        store.update_narrative(claim_id, narrate(result, earlier, is_pdf))
    return claim_id


def _attach(store: Store, claim_id: str, summaries: list) -> None:
    """Keep the packet's other documents with the claim, each with its own record."""
    for data, name, judged in summaries:
        store.add_claim_document(claim_id, KIND_SUMMARY, name, data, judged.to_json())
    if summaries:
        j = summaries[0][2]
        store.log("TrustLadder", "system", "Discharge summary checked", claim_id,
                  f"{j.registry_answer.value}: {j.registry_note[:120]}")


def _narrate_later(db_path, claim_id: str, result, earlier: list[dict], is_pdf: bool) -> None:
    """The background half of submit_claim. Its own Store, because SQLite connections
    are per thread; and nothing here can raise into the screen that started it."""
    try:
        narrated = narrate(result, earlier, is_pdf)
    except Exception:
        narrated = narrate_offline(result, earlier, is_pdf)
    try:
        Store(db_path).update_narrative(claim_id, narrated)
    except Exception:
        pass


def narrate_offline(result, earlier, is_pdf):
    """The fixed-order account, for when the models fail outright in the background."""
    from .pipeline import narrate as _n
    import os
    saved = os.environ.get("TRUSTLADDER_MODELS")
    os.environ["TRUSTLADDER_MODELS"] = "off"
    try:
        return _n(result, earlier, is_pdf)
    finally:
        if saved is None:
            os.environ.pop("TRUSTLADDER_MODELS", None)
        else:
            os.environ["TRUSTLADDER_MODELS"] = saved


def resubmit_claim(store: Store, verifier: Verifier, claim_id: str, pdf: bytes, file_name: str,
                   actor: str) -> None:
    """The customer sends a clearer copy for a claim waiting on them."""
    result = run_ladder(pdf, verifier, file_name)
    store.resubmit(claim_id, file_name, pdf, result, build_evidence(result).independent_against, actor)


def issue_bill(store: Store, registry: RegistryStore, verifier: Verifier, bill: BillFields,
               customer_id: str, actor: str, issued_at: str | None = None) -> bytes:
    """The hospital issues a bill: ticket printed, two codes published, copy to the patient.

    Only a hospital that has joined the registry can publish. Returns the PDF.
    """
    joined = bill.issuer_id in registry.load_directory()
    if joined:
        publisher = Publisher(registry, bill.issuer_id)
        if not bill.ticket:
            bill.ticket = publisher.new_ticket()
        publisher.publish([bill])
        verifier.refresh()
    pdf = render_genuine(bill, joined=joined)
    store.add_bill(bill.bill_no, bill.issuer_id, bill.issuer_name, customer_id, bill.patient,
                   bill.bill_date, bill.total_paise, bill.ticket, joined, pdf, issued_at)
    store.log(actor, "hospital", "Bill issued and published" if joined else "Bill issued",
              "", f"{bill.bill_no} for {bill.patient}", issued_at)
    return pdf


def issue_summary(store: Store, registry: RegistryStore, verifier: Verifier, summary: DischargeFields,
                  customer_id: str, actor: str, issued_at: str | None = None) -> bytes:
    """The hospital issues a discharge summary: ticket printed, two codes published, copy
    to the patient. The same path as a bill, which is the point: one mechanism, every
    document the hospital issues."""
    joined = summary.issuer_id in registry.load_directory()
    if joined:
        publisher = Publisher(registry, summary.issuer_id)
        if not summary.ticket:
            summary.ticket = publisher.new_ticket()
        publisher.publish([summary])
        verifier.refresh()
    pdf = render_discharge_summary(summary, joined=joined)
    store.add_summary(summary.summary_no, summary.issuer_id, summary.issuer_name, customer_id,
                      summary.patient, summary.admitted, summary.discharged, summary.ticket, joined,
                      pdf, issued_at)
    store.log(actor, "hospital", "Discharge summary issued and published" if joined else
              "Discharge summary issued", "", f"{summary.summary_no} for {summary.patient}", issued_at)
    return pdf


def enrol_hospital(store: Store, registry: RegistryStore, verifier: Verifier, issuer_id: str,
                   actor: str, when: str | None = None) -> None:
    """The registry operator signs a hospital up: key pair, salt, a line in the directory."""
    name, city, prefix, _ = HOSPITALS[issuer_id]
    registry.enrol_issuer(issuer_id, name, city, prefix, when)
    verifier.refresh()
    store.log(actor, "registry", "Hospital enrolled", "", f"{name}, {city}", when)
