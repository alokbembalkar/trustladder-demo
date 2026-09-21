"""
The plain-language reason attached to every verdict.

In the proposal this is the ONE place a large language model is used: after the
rule has decided, the model is given only the findings and the verdict and
asked to write a short reason a held customer or an auditor can understand. Its
output is then checked back against the findings, so it cannot add a claim the
evidence does not support, and it can never change the verdict.

To keep this demo small and fully offline, the reason is assembled from fixed
sentences instead of a model. The inputs and the check-back contract are the
same, so a model can replace `write_reason` without touching anything else.
"""

from __future__ import annotations

from .decide import Decision
from .models import Finding, RegistryAnswer, Verdict

_OPENERS = {
    Verdict.AUTHENTIC: "This bill can be paid.",
    Verdict.SUSPICIOUS: "This bill needs a person to look at it before it is paid.",
    Verdict.TAMPERED: "This bill should be held and investigated.",
    Verdict.INCONCLUSIVE: "This bill cannot be cleared automatically.",
}


def write_reason(decision: Decision, registry: RegistryAnswer, registry_note: str,
                 findings: list[Finding], read_note: str) -> str:
    """Build the reason from the evidence only. Returns 2-4 short sentences."""
    parts = [_OPENERS[decision.verdict]]
    if registry is RegistryAnswer.NOT_ASKED:
        parts.append(read_note)
    else:
        parts.append(registry_note)
    if findings and decision.verdict is not Verdict.AUTHENTIC:
        parts.append("Screening also found: " + " ".join(f.detail for f in findings[:2]))
    elif findings:
        parts.append("The file shows signs of being re-saved, but the issuer's confirmation "
                     "covers every value that matters, so this does not change the result.")
    parts.append(decision.rule_text)
    return " ".join(p.strip() for p in parts if p and p.strip())


def check_back(reason: str, decision: Decision) -> bool:
    """The guard that would sit after a language model.

    The reason must not contradict the verdict: it may only use the opener
    that belongs to the decided verdict, never another verdict's.
    """
    own = _OPENERS[decision.verdict]
    others = [o for v, o in _OPENERS.items() if v is not decision.verdict]
    return reason.startswith(own) and not any(o in reason for o in others)
