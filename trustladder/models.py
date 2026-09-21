"""
Shared data shapes used across the ladder.

Kept in one small module so every stage speaks the same vocabulary and no module
has to import another stage just to get a type.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


# --------------------------------------------------------------------------
# What a bill says
# --------------------------------------------------------------------------

@dataclass
class LineItem:
    """One charge on a hospital bill. Amounts are whole paise, never floats,
    so arithmetic checks are exact."""
    description: str
    amount_paise: int


@dataclass
class BillFields:
    """The fields of a hospital bill, as issued or as read back from a PDF.

    `ticket` is empty for a hospital that has not joined the registry, because
    such a hospital prints no ticket.
    """
    issuer_id: str
    issuer_name: str
    bill_no: str
    bill_date: str          # ISO form, YYYY-MM-DD
    patient: str
    items: list[LineItem]
    total_paise: int
    ticket: str = ""


# --------------------------------------------------------------------------
# Stage outputs
# --------------------------------------------------------------------------

class ReadStatus(str, Enum):
    """Outcome of reading the fields off the document."""
    OK = "Read"
    COULD_NOT_READ = "Could not read"


class RegistryAnswer(str, Enum):
    """The four answers the issuer registry can give (step 3, Verify)."""
    VERIFIED = "Verified"          # ticket found and every field matches the issuer's record
    MISMATCH = "Mismatch"          # ticket found, but the details differ from what was issued
    NO_RECORD = "No record"        # the issuer has joined, but never issued this ticket
    NOT_COVERED = "Not covered"    # the issuer has not joined, so the registry cannot answer
    NOT_ASKED = "Not asked"        # the registry was not consulted (e.g. the bill could not be read)


class Verdict(str, Enum):
    """The four verdicts. Each carries a fixed risk level."""
    AUTHENTIC = "Authentic"
    SUSPICIOUS = "Suspicious"
    TAMPERED = "Tampered"
    INCONCLUSIVE = "Inconclusive"


RISK_LEVEL = {
    Verdict.AUTHENTIC: "Low",
    Verdict.SUSPICIOUS: "Medium",
    Verdict.TAMPERED: "High",
    Verdict.INCONCLUSIVE: "Medium",
}


class EvidenceGrade(str, Enum):
    """How strong the best evidence behind a verdict is.

    PROOF        the issuer itself confirmed the document (a registry match).
                 Only proof can clear a document.
    CONSISTENCY  the document is internally coherent and nothing looks wrong.
                 A document typed from a blank page can be perfectly coherent,
                 so this never clears anything.
    CONTRADICTION  independent evidence that the document is not as issued.
    NONE         nothing usable could be established (e.g. the bill was unreadable).
    """
    PROOF = "Proof-grade"
    CONSISTENCY = "Consistency-grade"
    CONTRADICTION = "Contradiction"
    NONE = "No usable evidence"


@dataclass
class Finding:
    """One observation from a screening check (step 1, Detect).

    `family` groups checks that could share a cause. The rule counts families,
    not findings, so two symptoms of the same edit never count as two
    independent pieces of evidence.
    """
    family: str        # "arithmetic" | "file history" | "fonts"
    check: str         # short machine name of the check
    detail: str        # one plain sentence a claims officer can read


@dataclass
class LadderResult:
    """Everything the ladder produced for one document: the audit record."""
    file_name: str
    read_status: ReadStatus
    fields: BillFields | None
    read_note: str
    findings: list[Finding]
    registry_answer: RegistryAnswer
    registry_note: str
    verdict: Verdict
    risk: str
    evidence_grade: EvidenceGrade
    rule_applied: str             # which clause of the rule fired, in words
    reason: str                   # the plain-language reason shown to people
    needs_human: bool             # True for every verdict except Authentic
    queue: str                    # where the document goes next
    rule_version: str = ""
    timings_ms: dict = field(default_factory=dict)
