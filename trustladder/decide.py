"""
Step 2, Decide: the published decision rule.

This is the core of TrustLadder. It is a RULE, not a score: a short table that
a claims officer, an auditor, an ombudsman or a regulator can read and apply by
hand, and that gives the same answer every time.

Three principles shape it:

  * Only proof clears. A document is Authentic only when the issuer itself
    confirms it (registry: Verified). "Nothing looks wrong" is consistency-grade
    evidence, and a fabricated bill can be perfectly consistent, so it never
    clears anything. No findings means refer, never clear.

  * A conviction needs two independent findings. Tampered requires at least two
    pieces of evidence from different families (the registry counts as one
    family, each screening family as another). One finding alone is Suspicious.

  * Unreadable is not wrong. A document that could not be read is Inconclusive
    and goes to its own queue; it never reaches the registry comparison.

The table (checked top to bottom, first match wins):

  R0  bill could not be read                        -> Inconclusive
  R1  registry Verified                             -> Authentic
  R2  registry Mismatch  + >=1 screening family     -> Tampered
  R3  registry Mismatch  alone                      -> Suspicious
  R4  registry No record + >=1 screening family     -> Tampered
  R5  registry No record alone                      -> Suspicious
  R6  Not covered        + >=2 screening families   -> Tampered
  R7  Not covered        + 1 screening family       -> Suspicious
  R8  Not covered        + no findings              -> Inconclusive
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
    "R3": "Human review: re-read, then confirm with the issuer",
    "R4": "Hold and investigate",
    "R5": "Human review: confirm with the issuer",
    "R6": "Hold and investigate",
    "R7": "Human review",
    "R8": "Human review: no proof available, so it is never cleared automatically",
}

RULE_TEXT = {
    "R0": "The bill could not be read, so nothing was compared.",
    "R1": "The issuer confirmed the bill. Only proof clears, and this is proof.",
    "R2": "The issuer's record contradicts the bill, and an independent check agrees.",
    "R3": "The issuer's record contradicts the bill, but nothing else does, so a person "
          "re-reads it before anyone is held.",
    "R4": "The issuer never issued this ticket, and an independent check also found a problem.",
    "R5": "'No record' is one finding, and one finding is not enough to convict.",
    "R6": "No issuer record exists, but two independent checks found problems.",
    "R7": "No issuer record exists and one check found a problem. One finding is not "
          "enough to convict.",
    "R8": "No issuer record exists and nothing looks wrong. 'Nothing looks wrong' is not "
          "proof, so the bill is referred, not cleared.",
}


def _decision(rule_id: str, verdict: Verdict, grade: EvidenceGrade) -> Decision:
    return Decision(verdict, grade, rule_id, RULE_TEXT[rule_id], DEFAULT_QUEUE[rule_id])


def decide(read_status: ReadStatus, registry: RegistryAnswer,
           findings: list[Finding]) -> Decision:
    """Apply the rule table. Pure function: same inputs, same verdict, always."""
    n_families = len({f.family for f in findings})

    if read_status is ReadStatus.COULD_NOT_READ:
        return _decision("R0", Verdict.INCONCLUSIVE, EvidenceGrade.NONE)

    if registry is RegistryAnswer.VERIFIED:
        return _decision("R1", Verdict.AUTHENTIC, EvidenceGrade.PROOF)

    if registry is RegistryAnswer.MISMATCH:
        if n_families >= 1:
            return _decision("R2", Verdict.TAMPERED, EvidenceGrade.CONTRADICTION)
        return _decision("R3", Verdict.SUSPICIOUS, EvidenceGrade.CONTRADICTION)

    if registry is RegistryAnswer.NO_RECORD:
        if n_families >= 1:
            return _decision("R4", Verdict.TAMPERED, EvidenceGrade.CONTRADICTION)
        return _decision("R5", Verdict.SUSPICIOUS, EvidenceGrade.CONTRADICTION)

    # Not covered (or not asked): only the screening checks are available.
    if n_families >= 2:
        return _decision("R6", Verdict.TAMPERED, EvidenceGrade.CONTRADICTION)
    if n_families == 1:
        return _decision("R7", Verdict.SUSPICIOUS, EvidenceGrade.CONTRADICTION)
    return _decision("R8", Verdict.INCONCLUSIVE, EvidenceGrade.CONSISTENCY)
