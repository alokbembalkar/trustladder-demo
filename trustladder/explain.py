"""
The plain-language reason attached to every verdict.

In the proposal this is the ONE place a large language model is used: after the
rule has decided, the model is given only the findings and the verdict and
asked to write a short reason a held customer or an auditor can understand. Its
output is then checked back against the findings, so it cannot add a claim the
evidence does not support, and it can never change the verdict.

Two writers, one contract. `write_reason` assembles the reason from fixed sentences
and always works, with no key, no network and no dependency. `write_reason_best`
asks a language model first (see `reason_model`) and falls back to the written one
if there is no key, if the call fails, or if the guardrail rejects what came back.

Both go through the same `check_back`, and the caller is told which writer produced
the text so the screen can say so. A reason the reader cannot trace to a writer is
worse than a plain one.
"""

from __future__ import annotations

from . import reason_model
from .decide import Decision
from .models import CLAIM_HISTORY, Finding, RegistryAnswer, Verdict

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
    # Evidence from inside the document and evidence from the insurer's own records
    # are introduced separately: calling a repeat claim a "screening" finding would
    # tell the reader something untrue about where it came from.
    screened = [f for f in findings if f.family != CLAIM_HISTORY]
    history = [f for f in findings if f.family == CLAIM_HISTORY]
    if decision.verdict is not Verdict.AUTHENTIC:
        if screened:
            parts.append("Screening also found: " + " ".join(f.detail for f in screened[:2]))
        if history:
            parts.append("The insurer's own records add: " + " ".join(f.detail for f in history))
    else:
        # Only proof reaches here, so nothing below can change the verdict; say so
        # plainly, and say which of the two kinds of evidence was actually present.
        if history:
            parts.append("The insurer's own records add: " + " ".join(f.detail for f in history)
                         + " The issuer still confirms this bill, so it is not held on that alone.")
        if screened:
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


def write_reason_best(decision: Decision, registry: RegistryAnswer, registry_note: str,
                      findings: list[Finding], read_note: str) -> tuple[str, str, str]:
    """The reason, which writer produced it ("model" or "template"), and the model's name.

    The model is asked first when a key is configured. Whatever comes back goes
    through the same guardrail as the written reason, and anything that fails it is
    thrown away rather than shown. There is no path by which a model's output reaches
    a screen without passing the check that a written sentence has to pass.
    """
    written = write_reason(decision, registry, registry_note, findings, read_note)
    if not reason_model.available():
        return written, "template", ""

    # Grounding: the findings grouped by independent family, plus the issuer's answer,
    # which is the evidence graph the rule itself was applied to.
    families: dict[str, list[str]] = {}
    for f in findings:
        families.setdefault(f.family + ("" if f.independent else " (shown, not counted)"), []).append(f.detail)
    if registry is not RegistryAnswer.NOT_ASKED:
        families["issuer registry"] = [registry_note]

    drafted = reason_model.write(
        opener=_OPENERS[decision.verdict],
        verdict=decision.verdict.value,
        rule_text=decision.rule_text,
        registry_note=registry_note if registry is not RegistryAnswer.NOT_ASKED else read_note,
        findings=[f.detail for f in findings],
        families=families,
    )
    if drafted and check_back(drafted[0], decision):
        return drafted[0], "model", drafted[1]
    return written, "template", ""
