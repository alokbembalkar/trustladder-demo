"""
The claim packet: a bill and, when the customer attaches one, a discharge summary.

Why a packet
------------
A hospitalisation claim is never one document. The insurer receives the bill and the
discharge summary, and the two must agree: the same patient, the same hospital, a bill
dated inside the stay, and a surgeon's fee only where an operation took place. Those
are the checks a claims officer makes by eye today, and here they are one more
independent line of evidence, in their own family, because nothing inside the bill
alone can produce them.

What the summary can and cannot do to the verdict
-------------------------------------------------
The rule is applied to the BILL: the money is on the bill, and the bill's registry
answer drives R1 to R7. The summary contributes evidence in two families.

    cross-document              the two documents disagree with each other
    discharge summary registry  the issuer's record contradicts the summary

Both count when the bill's issuer is silent (R4, R5). When the bill is Verified, the
verdict stays Authentic, because the document really is what the hospital issued, but
the claim still goes to a person rather than being paid automatically: a proven bill
inside a packet that does not add up is a money question, not a document question,
and `store.record_claim` settles it the same way it settles a repeat claim.

A summary the issuer confirms is recorded as such and adds nothing against the claim.
A summary read from a picture that nobody corroborated cannot condemn anything on the
issuer's word, exactly as a bill cannot (rule R7); its contradiction is shown but not
counted, and the officer sees why.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

from .models import (CROSS_DOCUMENT, SUMMARY_REGISTRY, BillFields, DischargeFields, Finding,
                     LadderResult, ReadStatus, RegistryAnswer)
from .reader import canonical_name
from .registry import Verifier
from .vision import Reading

# A bill is issued at discharge, sometimes the next working day.
BILL_AFTER_DISCHARGE_DAYS = 2
# Charges that only make sense when an operation took place.
SURGICAL_ITEMS = ("surgeon", "operation theatre", "anaesthesia")


@dataclass
class SummaryResult:
    """What the ladder established about the discharge summary in a packet."""
    file_name: str
    read_status: ReadStatus
    fields: DischargeFields | None
    read_note: str
    registry_answer: RegistryAnswer
    registry_note: str
    read_report: dict = field(default_factory=dict)
    models_used: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"file_name": self.file_name, "read_status": self.read_status.value,
                "read_note": self.read_note, "registry_answer": self.registry_answer.value,
                "registry_note": self.registry_note, "read_report": self.read_report,
                "models_used": self.models_used,
                "fields": None if self.fields is None else self.fields.__dict__}

    @classmethod
    def from_json(cls, d: dict) -> "SummaryResult":
        f = d.get("fields")
        return cls(d["file_name"], ReadStatus(d["read_status"]), DischargeFields(**f) if f else None,
                   d["read_note"], RegistryAnswer(d["registry_answer"]), d["registry_note"],
                   d.get("read_report") or {}, d.get("models_used") or {})


def judge_summary(reading: Reading, verifier: Verifier, file_name: str) -> SummaryResult:
    """Read (already done) and verify a discharge summary on its own."""
    if reading.status is ReadStatus.OK and reading.fields is not None:
        answer, note = verifier.lookup(reading.fields)
    else:
        answer, note = RegistryAnswer.NOT_ASKED, "The registry was not consulted."
    models = {"reader": reading.model} if reading.from_picture and reading.model else {}
    return SummaryResult(file_name, reading.status, reading.fields, reading.note, answer, note,
                         reading.report(), models)


def packet_findings(bill: BillFields | None, summary: SummaryResult) -> list[Finding]:
    """The evidence the packet adds to the bill's own: the two documents compared, and
    what the issuer said about the summary."""
    findings: list[Finding] = []
    s = summary.fields

    # --- the issuer's word on the summary --------------------------------------
    if summary.registry_answer in (RegistryAnswer.MISMATCH, RegistryAnswer.NOT_ISSUED):
        corroborated = bool(summary.read_report.get("corroborated", True))
        findings.append(Finding(
            SUMMARY_REGISTRY, "summary_contradicted",
            f"The discharge summary: {summary.registry_note}" + (
                "" if corroborated else
                " (read from a picture nobody corroborated, so this is shown and not counted)"),
            independent=corroborated))

    if bill is None or s is None:
        return findings

    # --- the two documents against each other -----------------------------------
    if canonical_name(bill.patient) != canonical_name(s.patient):
        findings.append(Finding(CROSS_DOCUMENT, "patient_differs",
                                f"The bill is for {bill.patient} but the discharge summary is for {s.patient}."))
    if bill.issuer_id != s.issuer_id:
        findings.append(Finding(CROSS_DOCUMENT, "issuer_differs",
                                f"The bill is from {bill.issuer_name} but the discharge summary is from "
                                f"{s.issuer_name}."))
    try:
        b = _dt.date.fromisoformat(bill.bill_date)
        a, d = _dt.date.fromisoformat(s.admitted), _dt.date.fromisoformat(s.discharged)
        if not (a <= b <= d + _dt.timedelta(days=BILL_AFTER_DISCHARGE_DAYS)):
            findings.append(Finding(
                CROSS_DOCUMENT, "bill_outside_stay",
                f"The bill is dated {_nice(b)}, but the discharge summary says the patient was in "
                f"hospital from {_nice(a)} to {_nice(d)}. A bill is issued at discharge, not "
                f"{abs((b - d).days) if b > d else abs((a - b).days)} days away from it."))
    except ValueError:
        pass
    surgical = [i.description for i in bill.items
                if any(w in i.description.lower() for w in SURGICAL_ITEMS)]
    if surgical and not s.procedure:
        findings.append(Finding(
            CROSS_DOCUMENT, "surgery_billed_no_operation",
            f"The bill charges for {', '.join(surgical[:2]).lower()}, but the discharge summary records "
            "no operation."))
    return findings


def _nice(d: _dt.date) -> str:
    return d.strftime("%d %b %Y")


def summary_line(summary: SummaryResult | None) -> str:
    """One sentence for the officer and the reason writer about the summary."""
    if summary is None:
        return "No discharge summary was attached to this claim."
    if summary.read_status is not ReadStatus.OK:
        return f"A discharge summary was attached but could not be read: {summary.read_note}"
    s = summary.fields
    stay = f"{_nice(_dt.date.fromisoformat(s.admitted))} to {_nice(_dt.date.fromisoformat(s.discharged))}"
    return (f"The discharge summary ({s.summary_no}) records {s.patient} in {s.issuer_name} from {stay}, "
            f"diagnosis {s.diagnosis}, " + (f"operation {s.procedure}. " if s.procedure else "no operation. ")
            + f"Registry: {summary.registry_answer.value}.")
