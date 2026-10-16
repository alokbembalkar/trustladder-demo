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

Two doors in
------------
A born-digital PDF is read from its text layer, exactly. A photograph, a scan, or a
PDF that is only a picture is read by the vision model (`vision.py`), and that
reading carries a trust level of its own: the rule may clear it on the issuer's
word but may condemn it on the issuer's word only when the reading is corroborated.
The submission screen reads the picture first, shows the fields to the customer for
confirmation, and hands the confirmed reading in here, so the model is called once
and the customer's corrections are part of the record.
"""

from __future__ import annotations

import time
from dataclasses import replace

from . import RULE_VERSION, agent, vision
from .decide import decide
from .detect import screen
from .explain import check_back, write_reason, write_reason_best
from .models import Finding, LadderResult, RISK_LEVEL, ReadStatus, RegistryAnswer, Verdict
from .registry import Verifier


def run_ladder(pdf_bytes: bytes, verifier: Verifier, file_name: str = "document.pdf",
               reading: vision.Reading | None = None, narrate: bool = True) -> LadderResult:
    """Carry one document through every stage and return the full, auditable result.

    `reading` is the already-read (and customer-confirmed) fields when the screen has
    done the reading; otherwise the document is read here, by whichever door fits it.

    `narrate=False` skips the language model when writing the reason, so the result
    comes back at once with the written reason; `narrate()` below adds the model's
    text afterwards. The verdict is identical either way: the writer runs after the
    rule and cannot touch it.
    """
    t = {}
    t0 = time.perf_counter()

    if reading is None:
        reading = vision.read_any(pdf_bytes, file_name)
    status, fields, read_note = reading.status, reading.fields, reading.note
    t["read"] = (time.perf_counter() - t0) * 1000

    t1 = time.perf_counter()
    # File-container checks (revision layers, producer, fonts) only mean something
    # for a PDF. A JPEG has none of that structure, so only the arithmetic is screened.
    findings = screen(pdf_bytes, fields, container=vision.mime_of(file_name) is None)
    t["detect"] = (time.perf_counter() - t1) * 1000

    t2 = time.perf_counter()
    if status is ReadStatus.OK:
        answer, registry_note = verifier.lookup(fields)
    else:
        # Never compare a bill we could not read: that is how a misread
        # would become a wrongful "mismatch".
        answer, registry_note = RegistryAnswer.NOT_ASKED, "The registry was not consulted."
    t["verify"] = (time.perf_counter() - t2) * 1000

    decision = decide(status, answer, findings, corroborated=reading.corroborated)
    if narrate:
        reason, writer, reason_model = write_reason_best(decision, answer, registry_note, findings, read_note)
    else:
        reason, writer, reason_model = write_reason(decision, answer, registry_note, findings, read_note), "template", ""
    if not check_back(reason, decision):          # would catch a model that drifted
        reason, writer, reason_model = decision.rule_text, "template", ""

    models_used = {}
    if reading.from_picture and reading.model:
        models_used["reader"] = reading.model
    if reason_model:
        models_used["reason"] = reason_model

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
        reason_writer=writer,
        needs_human=decision.verdict is not Verdict.AUTHENTIC,
        queue=decision.queue,
        rule_version=RULE_VERSION,
        timings_ms={k: round(v, 1) for k, v in t.items()},
        read_report=reading.report(),
        models_used=models_used,
    )


def goes_to_person(result: LadderResult) -> bool:
    """Will an officer read this claim? Every verdict but Authentic, and an Authentic
    bill that was claimed before (the policy sends a repeat to a person, never pays it
    twice automatically). The agent's account is written for exactly these."""
    from .models import CLAIM_HISTORY
    return result.needs_human or any(f.family == CLAIM_HISTORY for f in result.findings)


def narrate(result: LadderResult, earlier_claims: list[dict] | None = None,
            is_pdf: bool = True) -> LadderResult:
    """Add the model-written parts to a decided result: the reason and the agent's account.

    Runs after the rule, reads only what the rule produced, and returns a new result in
    which the verdict, the rule and the findings are untouched. It is the slow part (a
    few network calls), so the submission screen runs it in the background and the
    officer's screen shows the written reason until it is done.
    """
    from .decide import Decision
    from .models import EvidenceGrade
    rule_id, _, rule_text = result.rule_applied.partition(": ")
    decision = Decision(result.verdict, result.evidence_grade, rule_id, rule_text, result.queue)
    reason, writer, reason_model = write_reason_best(decision, result.registry_answer,
                                                     result.registry_note, result.findings,
                                                     result.read_note)
    if not check_back(reason, decision):
        reason, writer, reason_model = result.reason, "template", ""
    models_used = {k: v for k, v in result.models_used.items() if k != "reason"}
    if reason_model:
        models_used["reason"] = reason_model
    trace, note, agent_model = result.agent_trace, result.agent_note, ""
    if goes_to_person(result):
        trace, note, agent_model = agent.investigate(result, earlier_claims, is_pdf)
        if agent_model:
            models_used["agent"] = agent_model
    return replace(result, reason=reason, reason_writer=writer, models_used=models_used,
                   agent_trace=trace, agent_note=note)


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
    decision = decide(result.read_status, result.registry_answer, findings,
                      corroborated=bool(result.read_report.get("corroborated", True)))
    # The written reason only: the caller narrates (with the model) once, at the end.
    reason, writer, reason_model = write_reason(decision, result.registry_answer, result.registry_note,
                                                findings, result.read_note), "template", ""
    if not check_back(reason, decision):
        reason = decision.rule_text
    models_used = {k: v for k, v in result.models_used.items() if k != "reason"}
    return replace(
        result,
        findings=findings,
        verdict=decision.verdict,
        risk=RISK_LEVEL[decision.verdict],
        evidence_grade=decision.grade,
        rule_applied=f"{decision.rule_id}: {decision.rule_text}",
        reason=reason,
        reason_writer=writer,
        needs_human=decision.verdict is not Verdict.AUTHENTIC,
        queue=decision.queue,
        models_used=models_used,
    )
