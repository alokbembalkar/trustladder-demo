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


@dataclass
class DischargeFields:
    """The fields of a hospital discharge summary, as issued or as read back.

    The second document in a hospitalisation claim. A real summary carries a page of
    clinical text; what the registry commits to, and what the cross-document check
    uses, are the few fields below. `bill_date` is a property so the registry, which
    was written for bills, can date a summary the same way: by its discharge date.
    """
    issuer_id: str
    issuer_name: str
    summary_no: str         # the hospital's document number
    uhid: str               # the hospital's patient id
    patient: str
    admitted: str           # ISO date
    discharged: str         # ISO date
    diagnosis: str
    procedure: str          # "" when no operation was performed
    consultant: str
    ticket: str = ""

    @property
    def bill_date(self) -> str:
        return self.discharged

    @property
    def doc_no(self) -> str:
        return self.summary_no


KIND_BILL = "bill"
KIND_SUMMARY = "discharge summary"


def kind_of(doc) -> str:
    """Which document type a fields object is, in the words the screens use."""
    return KIND_SUMMARY if isinstance(doc, DischargeFields) else KIND_BILL


# --------------------------------------------------------------------------
# Stage outputs
# --------------------------------------------------------------------------

class ReadStatus(str, Enum):
    """Outcome of reading the fields off the document."""
    OK = "Read"
    COULD_NOT_READ = "Could not read"


class RegistryAnswer(str, Enum):
    """What the issuer registry says about a document (step 3, Verify).

    Three of these are the ISSUER SPEAKING, and they are proof either way:

        VERIFIED     the issuer confirms this exact document   -> proof FOR
        MISMATCH     the issuer issued this ticket, with other details -> proof AGAINST
        NOT_ISSUED   the issuer published everything it issued for this period,
                     and this ticket is not among it            -> proof AGAINST

    The other two are SILENCE, and silence is never evidence:

        NO_RECORD    the ticket is not in the file, but the bill falls outside the
                     period the issuer has published for, so nothing follows
        NOT_COVERED  the issuer has not joined at all
    """
    VERIFIED = "Verified"
    MISMATCH = "Mismatch"
    NOT_ISSUED = "Not issued"
    NO_RECORD = "No record"
    NOT_COVERED = "Not covered"
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
    CONTRADICTION  the issuer's own record says the document is not as issued, or two
                 independent screening checks agree it is not.
    NONE         nothing usable could be established (e.g. the bill was unreadable).
    """
    PROOF = "Proof-grade"
    CONSISTENCY = "Consistency-grade"
    CONTRADICTION = "Contradiction"
    NONE = "No usable evidence"


# Evidence that does not come from the document at all. A repeat claim is known
# only to the insurer's own records, so it is kept in its own family: it can never
# share a cause with something found inside the PDF.
CLAIM_HISTORY = "claim history"
# Evidence from reading the packet as a whole: the bill against the discharge summary.
# Dates that do not line up, a different patient, a surgeon's fee with no operation.
CROSS_DOCUMENT = "cross-document"
# The issuer's registry speaking about the discharge summary (the bill's own answer
# drives the rule; the summary's answer is one more line of evidence).
SUMMARY_REGISTRY = "discharge summary registry"
# Families that must send a claim to a person even when the bill itself is proven:
# the money question is not the document question.
PACKET_FAMILIES = (CLAIM_HISTORY, CROSS_DOCUMENT, SUMMARY_REGISTRY)


@dataclass
class Finding:
    """One observation against a document: usually from a screening check (step 1,
    Detect), or from the insurer's own records (family CLAIM_HISTORY).

    `family` groups checks that could share a cause. The rule counts families,
    not findings, so two symptoms of the same edit never count as two
    independent pieces of evidence.
    """
    family: str        # "arithmetic" | "file history" | "fonts" | CLAIM_HISTORY
    check: str         # short machine name of the check
    detail: str        # one plain sentence a claims officer can read
    independent: bool = True
    """Does this finding count as an independent cause?

    Almost always yes. It is False for two things, and both are reported to the
    officer but excluded from the rule's count. A second opinion on a document the
    other checks already read is not a second cause. And a signal the screening
    model has identified as innocent — a file re-saved by a phone app rather than
    edited — is evidence the officer should see but not evidence of wrongdoing.
    """


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
    reason_writer: str = "template"
    """Who wrote `reason`: "model" or "template".

    Kept with the result because a reader is entitled to know whether a sentence was
    generated or assembled, and because the screen says so rather than leaving it to
    be assumed either way.
    """
    rule_version: str = ""
    timings_ms: dict = field(default_factory=dict)
    read_report: dict = field(default_factory=dict)
    """How the bill was read (see vision.Reading.report): source, model, language,
    per-field confidence, whether a second reader agreed, whether the customer
    confirmed, and how many fields they corrected. Empty for a text-layer read."""
    agent_trace: list = field(default_factory=list)
    """The evidence agent's steps, in order: which check it chose, why, what came
    back. Empty when the issuer settled the document and no gathering was needed."""
    agent_note: str = ""
    """The agent's case note for the officer. Never a verdict: the rule decides."""
    models_used: dict = field(default_factory=dict)
    """Which model version did which job on this claim: {"reader": ..., "agent": ...,
    "reason": ...}. Written to the audit record so any decision can be replayed."""
