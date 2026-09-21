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
from .models import BillFields
from .pipeline import run_ladder
from .registry import Publisher, RegistryStore, Verifier
from .store import Store


def submit_claim(store: Store, verifier: Verifier, customer_id: str, pdf: bytes, file_name: str,
                 bill_no: str = "", truth: str = "", submitted_at: str | None = None,
                 actor: str = "") -> str:
    """A customer submits a bill: TrustLadder checks it on attach, the policy routes it."""
    customer = store.customer(customer_id)
    result = run_ladder(pdf, verifier, file_name)
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
