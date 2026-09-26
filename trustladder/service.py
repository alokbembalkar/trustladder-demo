"""
The actions people take, shared by the screens and by the sample-data seeder.

Keeping these in one place means the seeded history and a live click go through
exactly the same code: a seeded claim is judged by the same ladder, routed by
the same policy and logged the same way as one submitted during the demo.
"""

from __future__ import annotations

from .bills import render_genuine
from .cases import HOSPITALS
from .evidence import build as build_evidence
from .models import CLAIM_HISTORY, BillFields, Finding
from .pipeline import run_ladder, with_extra_findings
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
                 actor: str = "") -> str:
    """A customer submits a bill: TrustLadder checks it on attach, the policy routes it.

    The bill number is taken from the uploaded document itself when it can be read,
    so an uploaded claim is linked to the same bill as the hospital's record. A bill
    that has been claimed before on this policy is accepted and checked, with the
    repeat recorded as one more finding for the rule to weigh.
    """
    customer = store.customer(customer_id)
    result = run_ladder(pdf, verifier, file_name)
    bill_no = bill_no or (result.fields.bill_no if result.fields else "")
    # The insurer knows something the PDF cannot say: whether this bill came in
    # before. Fold that in and let the published rule weigh it with the rest.
    result = with_extra_findings(result, duplicate_findings(store, customer_id, bill_no))
    graph = build_evidence(result)
    return store.record_claim(customer_id, customer["name"], file_name, pdf, result,
                              graph.independent_against, bill_no=bill_no, truth=truth,
                              submitted_at=submitted_at, actor=actor or customer["name"])


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


def enrol_hospital(store: Store, registry: RegistryStore, verifier: Verifier, issuer_id: str,
                   actor: str, when: str | None = None) -> None:
    """The registry operator signs a hospital up: key pair, salt, a line in the directory."""
    name, city, prefix, _ = HOSPITALS[issuer_id]
    registry.enrol_issuer(issuer_id, name, city, prefix)
    verifier.refresh()
    store.log(actor, "registry", "Hospital enrolled", "", f"{name}, {city}", when)
