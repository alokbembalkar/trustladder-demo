"""
Runs one document up the ladder and returns the audit record.

    read -> Detect (screen) -> Decide (the rule) -> Verify (ask the issuer) -> verdict
                                     ^                         |
                                     +------ issuer answer ----+
                                                                   -> Human Review for every
                                                                      verdict except Authentic

Stage order follows the proposal: Detect, Decide, Verify, Human Review. Decide
owns the rule. Screening can only ever give consistency-grade evidence, so the
rule asks the issuer registry (Verify) for proof and then issues the verdict.
In code this means the registry answer is fetched first and handed to decide().
"""

from __future__ import annotations

import time
from dataclasses import replace

from . import RULE_VERSION
from .decide import decide
from .detect import screen
from .explain import check_back, write_reason
from .models import Finding, LadderResult, RISK_LEVEL, ReadStatus, RegistryAnswer, Verdict
from .reader import read_bill
from .registry import Verifier


def run_ladder(pdf_bytes: bytes, verifier: Verifier, file_name: str = "document.pdf") -> LadderResult:
    """Carry one PDF through every stage and return the full, auditable result."""
    t = {}
    t0 = time.perf_counter()

    status, fields, read_note = read_bill(pdf_bytes)
    t["read"] = (time.perf_counter() - t0) * 1000

    t1 = time.perf_counter()
    findings = screen(pdf_bytes, fields)
    t["detect"] = (time.perf_counter() - t1) * 1000

    t2 = time.perf_counter()
    if status is ReadStatus.OK:
        answer, registry_note = verifier.lookup(fields)
    else:
        # Never compare a bill we could not read: that is how a misread
        # would become a wrongful "mismatch".
        answer, registry_note = RegistryAnswer.NOT_ASKED, "The registry was not consulted."
    t["verify"] = (time.perf_counter() - t2) * 1000

    decision = decide(status, answer, findings)
    reason = write_reason(decision, answer, registry_note, findings, read_note)
    if not check_back(reason, decision):          # would catch a model that drifted
        reason = decision.rule_text

    return LadderResult(
        file_name=file_name,
        read_status=status,
        fields=fields,
        read_note=read_note,
        findings=findings,
        registry_answer=answer,
        registry_note=registry_note,
        verdict=decision.verdict,
        risk=RISK_LEVEL[decision.verdict],
        evidence_grade=decision.grade,
        rule_applied=f"{decision.rule_id}: {decision.rule_text}",
        reason=reason,
        needs_human=decision.verdict is not Verdict.AUTHENTIC,
        queue=decision.queue,
        rule_version=RULE_VERSION,
        timings_ms={k: round(v, 1) for k, v in t.items()},
    )


def with_extra_findings(result: LadderResult, extra: list[Finding]) -> LadderResult:
    """Re-apply the rule to an already-judged document, with findings the document
    itself could not reveal.

    Some evidence does not live in the PDF. "This same bill has already been claimed
    on this policy" is known only to the insurer's claim history, yet it is exactly
    the kind of independent check the rule is meant to weigh. Rather than let the
    ladder reach into the claims database (which would make `run_ladder` impure and
    untestable on a bare file), the caller carries that evidence in afterwards and
    the rule is applied again over the fuller set.

    Nothing is re-read and nothing is re-screened: `decide` and `write_reason` are
    pure functions, so this is arithmetic on what we already have. The document's
    own findings are kept; `extra` is added to them.

    Returns a new result; the one passed in is not modified.
    """
    if not extra:
        return result
    findings = list(result.findings) + list(extra)
    decision = decide(result.read_status, result.registry_answer, findings)
    reason = write_reason(decision, result.registry_answer, result.registry_note,
                          findings, result.read_note)
    if not check_back(reason, decision):
        reason = decision.rule_text
    return replace(
        result,
        findings=findings,
        verdict=decision.verdict,
        risk=RISK_LEVEL[decision.verdict],
        evidence_grade=decision.grade,
        rule_applied=f"{decision.rule_id}: {decision.rule_text}",
        reason=reason,
        needs_human=decision.verdict is not Verdict.AUTHENTIC,
        queue=decision.queue,
    )
