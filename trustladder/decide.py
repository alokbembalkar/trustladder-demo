"""
Step 2, Decide: the published decision rule.

This is the core of TrustLadder. It is a RULE, not a score: a short table that a
claims officer, an auditor, an ombudsman or a regulator can read and apply by
hand, and that gives the same answer every time.

The principle, in one line:

    Only the issuer can clear a document, and only the issuer can condemn one.
    Without the issuer, a conviction needs two independent findings.

That symmetry is the point, and it used to be missing. A registry match cleared a
document outright, but a registry contradiction only made it "Suspicious" and
waited for corroboration from screening checks that are far weaker evidence. It
treated the strongest signal in the system as though it were the weakest. If the
hospital's own signed record says it never issued this bill, that is not a hint.

Three things the issuer can say, and all three are proof:

    VERIFIED     this is exactly what we issued             -> Authentic
    MISMATCH     we issued that ticket, with other details  -> Tampered
    NOT ISSUED   we have published every bill we issued in
                 this period, and this is not one of them   -> Tampered

And two kinds of silence, which are never evidence:

    NO RECORD    the ticket is absent, but the bill is dated outside the period
                 the issuer has published for
    NOT COVERED  the issuer has not joined

Silence is where screening does the work, and where the two-findings rule still
holds. A document typed from a blank page leaves no edit traces at all, so
"nothing looks wrong" can never clear anything, and one finding alone convicts
nobody.

Unreadable is not wrong. A document that could not be read is Inconclusive and
never reaches the registry comparison, so our own misreading can never be
reported as the issuer contradicting the document.

A picture is not a text layer. A bill read from a photograph by a model is a
transcription, and one misread character would turn the issuer's "Not issued" into
a false accusation. So a reading from a picture can be CLEARED by the issuer (a
misread cannot produce a false match) but can be CONDEMNED by the issuer only when
it is corroborated: a second, independent reader agreed and the customer confirmed
the fields. Otherwise the contradiction becomes "ask the customer for the original",
which is the same honesty as R0 applied to the reader itself.

The table (checked top to bottom, first match wins):

    R0  bill could not be read                      -> Inconclusive
    R1  issuer: verified                            -> Authentic
    R7  issuer contradicts an UNCORROBORATED
        reading from a picture                      -> Inconclusive (ask for the original)
    R2  issuer: mismatch                            -> Tampered
    R3  issuer: not issued                          -> Tampered
    R4  no issuer answer + >=2 screening families   -> Tampered
    R5  no issuer answer + 1 screening family       -> Suspicious
    R6  no issuer answer + no findings              -> Inconclusive
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import EvidenceGrade, Finding, ReadStatus, RegistryAnswer, Verdict


@dataclass
class Decision:
    verdict: Verdict
    grade: EvidenceGrade
    rule_id: str
    rule_text: str
    queue: str


# Where each verdict goes by default. The institution can change the actions in
# policy.py; it can never route an unproven document to "pay".
DEFAULT_QUEUE = {
    "R0": "Could-not-read queue: request a clearer copy or the original",
    "R1": "Straight-through: pay",
    "R2": "Hold and investigate",
    "R3": "Hold and investigate",
    "R4": "Hold and investigate",
    "R5": "Human review",
    "R6": "Human review: no proof available, so it is never cleared automatically",
    "R7": "Could-not-read queue: ask the customer for the original, so the issuer's answer "
          "can be checked against an exact reading",
}

RULE_TEXT = {
    "R0": "The bill could not be read, so nothing was compared.",
    "R1": "The issuer confirmed the bill. Only proof clears, and this is proof.",
    "R2": "The issuer issued this ticket, but not with these details. That is the issuer "
          "contradicting the document, which is proof, not a hint.",
    "R3": "The issuer has published every bill it issued in this period, and this one is not "
          "among them. The hospital did not issue this bill.",
    "R4": "No issuer answer is available, and two independent checks found problems.",
    "R5": "No issuer answer is available and one check found a problem. One finding is not "
          "enough to convict.",
    "R6": "No issuer answer is available and nothing looks wrong. 'Nothing looks wrong' is not "
          "proof, so the bill is referred, not cleared.",
    "R7": "The issuer's record disagrees with what was read from a picture, and that reading "
          "is not corroborated. A misread must never become an accusation, so the original "
          "is requested instead.",
}


def _decision(rule_id: str, verdict: Verdict, grade: EvidenceGrade) -> Decision:
    return Decision(verdict, grade, rule_id, RULE_TEXT[rule_id], DEFAULT_QUEUE[rule_id])


def decide(read_status: ReadStatus, registry: RegistryAnswer,
           findings: list[Finding], corroborated: bool = True) -> Decision:
    """Apply the rule table. Pure function: same inputs, same verdict, always.

    `corroborated` is True for a text-layer read, and for a picture only when a second
    reader agreed and the customer confirmed the fields (see vision.Reading).
    """
    n_families = len({f.family for f in findings if f.independent})

    if read_status is ReadStatus.COULD_NOT_READ:
        return _decision("R0", Verdict.INCONCLUSIVE, EvidenceGrade.NONE)

    # The issuer speaking, in either direction, is proof and decides on its own.
    if registry is RegistryAnswer.VERIFIED:
        return _decision("R1", Verdict.AUTHENTIC, EvidenceGrade.PROOF)
    if registry in (RegistryAnswer.MISMATCH, RegistryAnswer.NOT_ISSUED) and not corroborated:
        # The issuer contradicts a reading we cannot vouch for. Ask for the original.
        return _decision("R7", Verdict.INCONCLUSIVE, EvidenceGrade.NONE)
    if registry is RegistryAnswer.MISMATCH:
        return _decision("R2", Verdict.TAMPERED, EvidenceGrade.CONTRADICTION)
    if registry is RegistryAnswer.NOT_ISSUED:
        return _decision("R3", Verdict.TAMPERED, EvidenceGrade.CONTRADICTION)

    # Silence from the issuer: absent outside its published period, not joined, or not
    # asked. Only the screening checks are left, and they never clear anything.
    if n_families >= 2:
        return _decision("R4", Verdict.TAMPERED, EvidenceGrade.CONTRADICTION)
    if n_families == 1:
        return _decision("R5", Verdict.SUSPICIOUS, EvidenceGrade.CONTRADICTION)
    return _decision("R6", Verdict.INCONCLUSIVE, EvidenceGrade.CONSISTENCY)
