"""
Stage 3, Verify: the tool-calling evidence agent.

What it does
------------
For every claim that is going to a person, a language model is given the bill's
summary and a small set of tools, and asked to gather the evidence an officer will
need: ask the issuer's registry, run the screening checks, consult the trained model,
look up the claim history. It chooses the order and says why at each step. The steps
and their results are recorded as a trace, and the agent closes with a short case note
for the officer.

What it cannot do, and how that is enforced
-------------------------------------------
It cannot decide. The verdict was reached by the published rule in `decide.py` before
the agent runs, on evidence gathered deterministically, and nothing the agent does is
fed back into that. Every tool here answers from the SAME findings the rule used, so
the trace can never disagree with the verdict: the agent is choosing which drawers to
open, not what is inside them.

It cannot skip. If the agent stops before it has opened every drawer, a guard runs
the remaining tools and marks those steps as the guard's, so the trace is always
complete and the officer can see what the agent chose to ask first.

It cannot pronounce. The case note is checked for verdict words that belong to a
verdict other than the one decided; a note that argues with the rule is dropped and
the trace is kept.

It cannot stall the demo. No key, no network, or an overloaded model, and the trace
is produced in a fixed order with no case note, and the screen says so.

Why run it at all, if the rule has decided
------------------------------------------
Because the officer reads the trace, not the code. "Asked the registry first, because
the issuer's answer settles most claims; the hospital has not joined; ran the
arithmetic, which fails; ran the file history, which shows a second save by an editor"
is the account a person needs, in the order a careful investigator would work. That is
what the architecture's "agentic orchestration" box is for, and it is where a full
product would add the tools the demo does not have: signature validation, an issuer
QR code, a DigiLocker check.
"""

from __future__ import annotations

from . import llm, screener
from .models import CLAIM_HISTORY, LadderResult, RegistryAnswer, Verdict

MAX_ROUNDS = 6

# Every tool answers from the result the rule already saw. The "why" argument is
# the agent's own stated reason for the step; it is shown on the officer's screen.
_WHY = {"type": "string", "description": "One short sentence: why you are running this check now."}

TOOLS = [
    {"name": "ask_registry",
     "description": "Ask the issuer's registry whether the hospital confirms this exact bill. "
                    "The issuer's answer is proof either way; silence is not evidence.",
     "parameters": {"type": "object", "properties": {"why": _WHY}, "required": ["why"]}},
    {"name": "check_arithmetic",
     "description": "Do the printed charges add up to the printed total?",
     "parameters": {"type": "object", "properties": {"why": _WHY}, "required": ["why"]}},
    {"name": "check_file_history",
     "description": "Was the file saved in layers, by a different program, or after it was created? "
                    "Only meaningful for a PDF, not for a photograph.",
     "parameters": {"type": "object", "properties": {"why": _WHY}, "required": ["why"]}},
    {"name": "check_fonts",
     "description": "Is the total set in a typeface used nowhere else on the bill? PDF only.",
     "parameters": {"type": "object", "properties": {"why": _WHY}, "required": ["why"]}},
    {"name": "screening_model",
     "description": "What does the trained screening model say about whether the file was edited "
                    "after issue? It can corroborate; it can never clear. PDF only.",
     "parameters": {"type": "object", "properties": {"why": _WHY}, "required": ["why"]}},
    {"name": "claim_history",
     "description": "Has this bill been claimed before on this policy? Known only to the insurer.",
     "parameters": {"type": "object", "properties": {"why": _WHY}, "required": ["why"]}},
]
TOOL_NAMES = [t["name"] for t in TOOLS]

SYSTEM = (
    "You are the evidence-gathering assistant of an insurer's claims team. A published rule "
    "has ALREADY decided this claim; you do not decide and you never state a verdict. Your job "
    "is to gather the evidence an officer will read, by calling the tools in the order a careful "
    "investigator would choose, giving a one-sentence reason for each. Ask the issuer's registry "
    "first, on its own: its answer is proof either way. After that you may call several tools in "
    "one turn. Call each tool at most once. When "
    "you have what you need, stop calling tools and write a case note of two or three plain "
    "sentences summarising what was found and what the officer should look at first. Do not use "
    "the words Authentic, Tampered, Suspicious or Inconclusive."
)


def investigate(result: LadderResult, earlier_claims: list[dict] | None = None,
                is_pdf: bool = True) -> tuple[list[dict], str, str]:
    """Gather the evidence for one claim. Returns (trace, case note, model name).

    `earlier_claims` are prior claims of this bill on the policy, for the claim-history
    tool. The trace is a list of steps: {"n", "tool", "by", "why", "result"}.
    """
    answers = _answers(result, earlier_claims or [], is_pdf)

    if not llm.narrates():                # off, or reader-only: the rule's fixed order
        return _fixed_order(answers), "", ""

    brief = _brief(result, is_pdf)
    contents = [{"role": "user", "parts": [llm.text_part(brief)]}]
    trace: list[dict] = []
    called: set[str] = set()
    note, model = "", ""

    try:
        for _ in range(MAX_ROUNDS):
            reply = llm.generate(contents, system=SYSTEM, tools=TOOLS, max_tokens=600)
            model = reply.model
            if not reply.calls:
                note = reply.text
                break
            # The model's turn goes back exactly as it came, then one response per call.
            contents.append({"role": "model", "parts": reply.content.get("parts", [])})
            responses = []
            for name, args in reply.calls:
                if name not in answers:
                    responses.append(llm.function_response_part(name, {"error": "unknown tool"}))
                    continue
                called.add(name)
                trace.append({"n": len(trace) + 1, "tool": name, "by": "agent",
                              "why": str(args.get("why", "")).strip(), "result": answers[name]})
                responses.append(llm.function_response_part(name, {"result": answers[name]}))
            contents.append({"role": "user", "parts": responses})
    except (llm.ModelUnavailable, ValueError):
        if not trace:
            return _fixed_order(answers), "", ""

    # The guard: whatever the agent did not ask, the rule asked anyway.
    for name in TOOL_NAMES:
        if name not in called:
            trace.append({"n": len(trace) + 1, "tool": name, "by": "guard",
                          "why": "Run by the guard: the agent did not ask, and the rule needs it.",
                          "result": answers[name]})
    return trace, _vet_note(note, result.verdict), model


# --------------------------------------------------------------------------
# What each tool answers (from the evidence the rule already weighed)
# --------------------------------------------------------------------------


def _answers(result: LadderResult, earlier: list[dict], is_pdf: bool) -> dict[str, str]:
    def fam(name: str) -> list[str]:
        return [f.detail + ("" if f.independent else " (shown, not counted by the rule)")
                for f in result.findings if f.family == name]

    not_pdf = "Not applicable: this bill arrived as a photograph, which has no file structure to check."
    arithmetic = fam("arithmetic")
    history = fam("file history")
    fonts = fam("fonts")
    model_notes = fam(screener.MODEL_FAMILY)
    repeats = fam(CLAIM_HISTORY)
    return {
        "ask_registry": f"{result.registry_answer.value}. {result.registry_note}",
        "check_arithmetic": " ".join(arithmetic) if arithmetic else "The charges add up to the printed total.",
        "check_file_history": (" ".join(history) if history else
                               ("No sign that the file was re-saved or changed after it was created."
                                if is_pdf else not_pdf)),
        "check_fonts": (" ".join(fonts) if fonts else
                        ("The total is set in the same typeface as the rest of the bill." if is_pdf else not_pdf)),
        "screening_model": (" ".join(model_notes) if model_notes else
                            ("The screening model did not flag this file as edited after issue."
                             if is_pdf and screener.load() else
                             (not_pdf if not is_pdf else "No screening model is loaded in this build."))),
        "claim_history": (" ".join(repeats) if repeats else
                          "No earlier claim of this bill on this policy."),
    }


def _brief(result: LadderResult, is_pdf: bool) -> str:
    f = result.fields
    how = result.read_report.get("source", "text layer")
    bill = (f"Hospital {f.issuer_name} ({f.issuer_id}); bill {f.bill_no} dated {f.bill_date}; "
            f"total Rs {f.total_paise / 100:,.2f}; ticket {f.ticket or 'none printed'}."
            if f else "The bill's fields could not be read.")
    return (f"A claim has arrived. {bill} It was read from the {how}"
            f"{' of a PDF' if is_pdf else ' of a photograph'}. Gather the evidence the officer "
            "will need, one tool at a time, then write the case note.")


def _fixed_order(answers: dict[str, str]) -> list[dict]:
    """No model: the same checks in the order the rule asks them, labelled as such."""
    return [{"n": i + 1, "tool": name, "by": "fixed order",
             "why": "Fixed order: no agent is configured on this deployment.", "result": answers[name]}
            for i, name in enumerate(TOOL_NAMES)]


def _vet_note(note: str, verdict: Verdict) -> str:
    """Drop a case note that pronounces a verdict, or one belonging to another verdict."""
    text = " ".join((note or "").split())
    if not text:
        return ""
    for word in ("authentic", "tampered", "suspicious", "inconclusive"):
        if word in text.lower():
            return ""
    return text


def registry_settled(result: LadderResult) -> bool:
    """Did the issuer answer in a way that settles the document on its own?"""
    return result.registry_answer in (RegistryAnswer.VERIFIED, RegistryAnswer.MISMATCH,
                                      RegistryAnswer.NOT_ISSUED)
