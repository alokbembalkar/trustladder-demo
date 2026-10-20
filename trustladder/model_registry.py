"""
The model registry: every model in this build, what it does, and what it cannot do.

Why it is published
-------------------
The product's promise is that a decision can be read by the person it affects. The
rule is printed on a slide; the models that feed it are printed here, on the auditor's
screen, with the same candour: version, where it runs, what it was measured on, and
the limit a careful reader should know about. The audit trail then names, per claim,
which of these did which job, so any decision can be replayed against the model that
made it.

Why it is a list in code and not a database
-------------------------------------------
This demo has five models and they change only with a release. A table that a person
edits would invite the registry to drift from the code; a list beside the code cannot.
A full product keeps this in a model store with drift monitoring, which the diagram
marks as the target.
"""

from __future__ import annotations

from . import llm, ocr, screener

# A model's "where" is where the computation runs. The hosted ones are the demo's
# shortcut; the product runs them inside the insurer's boundary.
HOSTED = "hosted (Google AI Studio); the product runs it inside the insurer"
LOCAL = "inside this app, on the CPU"


def models() -> list[dict]:
    """The registry, as rows. `active` says whether it can run on this deployment."""
    llm_on = llm.available()
    narrating = llm.narrates()
    first = llm.MODELS[0]
    rows = [
        {
            "name": "Vision-language reader",
            "job": "Reads a photographed or scanned bill, in any language, and reports a "
                   "confidence per field.",
            "kind": "Multimodal large language model (vision + text)",
            "version": f"{first} (first of {len(llm.MODELS)} in the fallback list)",
            "where": HOSTED,
            "measured": "16 Oct 2026: an English sample bill and a Marathi printout, every field "
                        "and the ticket read exactly, about 6 s each. Live error rate: the "
                        "readings customers correct, on the risk head's dashboard.",
            "cannot": "Vouch for itself. A picture it read can be cleared by the issuer but "
                      "condemned only when a second reader agrees and the customer confirms.",
            "active": llm_on,
        },
        {
            "name": "Deep OCR, second reader",
            "job": "Reads the same picture independently, inside the boundary, so two readers "
                   "must agree before a picture can be condemned.",
            "kind": "Text-detection and text-recognition neural networks (ONNX)",
            "version": ocr.MODEL_NAME,
            "where": LOCAL + " (optional install: requirements-ocr.txt)",
            "measured": "Corroborates the ticket, bill number and total on Latin-script fields.",
            "cannot": "Read Devanagari labels without language-specific weights; detect anything. "
                      "It reads text, nothing more.",
            "active": ocr.available(),
        },
        {
            "name": "Trained screening model",
            "job": "Says how likely a PDF is to have been edited after issue, from eight file "
                   "features. It corroborates; it never clears.",
            "kind": "Logistic regression, trained by us, shipped as readable coefficients",
            "version": "screener.json, trained by tools/train_screener.py",
            "where": LOCAL,
            "measured": _screener_measured(),
            "cannot": "See a bill typed from a blank page: nothing was edited, so there is "
                      "nothing in the file to find. Measured at chance. That is why the "
                      "registry exists.",
            "active": screener.load() is not None,
        },
        {
            "name": "Evidence agent",
            "job": "Chooses the order of the checks for a claim going to a person, says why at "
                   "each step, and writes the officer's case note.",
            "kind": "Large language model with tool calling (six tools)",
            "version": f"{first} (fallback list), tools in agent.py",
            "where": HOSTED,
            "measured": "Every tool answers from the findings the rule already weighed, so the "
                        "trace cannot disagree with the verdict. A guard runs any tool it skips.",
            "cannot": "Decide, or pronounce a verdict: a case note that names one is dropped.",
            "active": narrating,
        },
        {
            "name": "Reason writer",
            "job": "Turns the verdict, the rule and the evidence graph into two or three plain "
                   "sentences for the officer, the auditor and the customer's letter.",
            "kind": "Large language model, grounded on the evidence graph and the rule's text",
            "version": f"{first} (fallback list), prompt in reason_model.py",
            "where": HOSTED,
            "measured": "Every draft passes the check-back guard or is replaced by the template; "
                        "the screen says which one you are reading.",
            "cannot": "Change the verdict, add a finding, or mention a rule number.",
            "active": narrating,
        },
    ]
    return rows


def _screener_measured() -> str:
    m = screener.load()
    if m is None:
        return "not shipped in this build"
    d = m.measured
    return (f"Edited bills caught {d.get('edited_caught_at_threshold', 0):.0%}; genuine re-saved bills "
            f"wrongly flagged {d.get('genuine_false_alarm_at_threshold', 0):.0%}; fabricated bills "
            f"AUC {d.get('auc_fabricated_vs_genuine', 0):.2f} (chance is 0.50).")


# --------------------------------------------------------------------------
# The techniques used on one claim
# --------------------------------------------------------------------------

# Why this exists: the officer's screen already shows the evidence agent's account of
# the checks. A panel asked "where is the AI, and what did it accept?" has to read that
# answer off the claim too, technique by technique, with the model or tool that did the
# work, what it did on THIS claim, and the threshold it applied and how that number is
# calculated. Every row is derived from the audit record, so it can never say more than
# the record holds; a technique that did not apply is still listed, greyed, because what
# deliberately did not run is part of the explanation.

def techniques_used(r, documents: list | None = None) -> list[dict]:
    """One row per technique: name, AI keyword, tool, what happened on this claim,
    the threshold and how it is calculated, and whether it was used at all."""
    from . import vision
    from .models import CLAIM_HISTORY, CROSS_DOCUMENT, SUMMARY_REGISTRY, ReadStatus, RegistryAnswer
    rep = r.read_report or {}
    picture = rep.get("source") == "vision model"
    first = llm.MODELS[0]
    fams = sorted({f.family for f in r.findings if f.independent})
    by_family: dict[str, list] = {}
    for f in r.findings:
        by_family.setdefault(f.family, []).append(f)
    rows: list[dict] = []

    def row(name, keyword, tool, on_claim, threshold, used=True):
        rows.append({"name": name, "keyword": keyword, "tool": tool, "on_claim": on_claim,
                     "threshold": threshold, "used": used})

    # 1. Reading ------------------------------------------------------------------------
    if picture:
        row("Text-layer reading", "Exact reading, no model", "pypdf, then pdfium (Chromium PDF engine)",
            "Not used: the document is a picture and has no text layer.",
            "Every lookup field must be found by its printed label; any missing → could not read (R0).", used=False)
        conf = rep.get("confidence") or {}
        low = min(conf.values()) if conf else None
        local_only = rep.get("model") == ocr.MODEL_NAME
        what = ("The hosted model did not answer (quota, outage or no key); the local OCR read what it could and "
                "the customer completed the rest." if local_only else f"Read by {rep.get('model') or first}" + (f", printed in {rep['language']}" if rep.get("language") else "")
                + (f"; lowest field confidence {low:.2f}" if low is not None else "")
                + ("; the customer confirmed" + (f" and corrected {rep.get('corrections')} field(s)" if rep.get("corrections") else ", no corrections")
                   if rep.get("customer_confirmed") else "; not yet confirmed by the customer") + ".")
        row("Vision-language reading", "Multimodal LLM, confidence gate",
            (f"{first} (Google AI Studio): did not answer" if local_only else f"{rep.get('model') or first} (Google AI Studio)"),
            what, f"Model states a confidence 0–1 per field; every lookup field must be ≥ {vision.CONFIDENCE_FLOOR:.2f} "
                  "and the ticket in the printed shape, or the customer completes the form.")
        if rep.get("second_reader"):
            agree = rep.get("agree")
            second = ("agreed" if agree else "did not agree" if agree is False else "gave no opinion")
            what2 = f"Second reader ({rep['second_reader']}) {second}; " + ("corroborated." if rep.get("corroborated") else "not corroborated.")
        else:
            what2 = "No second reader on this deployment: the reading is not corroborated. The issuer's word can clear it, not condemn it (R7)."
        row("Corroboration (rule R7)", "Independent second reader", ocr.MODEL_NAME + ", local",
            what2, "Corroborated only when the two readers agree exactly on ticket, bill number and total AND the customer confirmed.")
    else:
        if r.read_status is ReadStatus.OK:
            what = "Read exactly from the text layer: every lookup field found by its printed label."
        else:
            what = f"Could not read: {r.read_note}"
        row("Text-layer reading", "Exact reading, no model", "pypdf, then pdfium (Chromium PDF engine)", what,
            "Every lookup field must be found by its printed label; any missing → could not read (R0). No confidence: exact or absent.")
        row("Vision-language reading", "Multimodal LLM, confidence gate", f"{first} (Google AI Studio)",
            "Not used: a born-digital PDF needs no model to be read.",
            f"Model states a confidence 0–1 per field; every lookup field must be ≥ {vision.CONFIDENCE_FLOOR:.2f}.", used=False)
        row("Corroboration (rule R7)", "Independent second reader", ocr.MODEL_NAME + ", local",
            "Not needed: a text-layer reading is exact, so the issuer's word can clear or condemn it.",
            "Two readers must agree and the customer confirm before a picture can be condemned.", used=False)

    # 2. Screening -------------------------------------------------------------------------
    m = screener.load()
    thr = m.threshold if m else 0.60
    sm = by_family.get(screener.MODEL_FAMILY, [])
    if sm:
        import re
        pm = re.search(r"at (\d+)%", sm[0].detail)
        what = f"Scored p = {pm.group(1)}% likely edited after issue: a finding was raised." if pm else sm[0].detail
    elif picture:
        what = "Not run: a picture carries no file history for the eight features."
    else:
        what = f"Scored below {thr:.2f}: no finding. (It can corroborate a hold; it never clears.)"
    row("Screening model", "Supervised learning (logistic regression)", "Eight file features, trained by us on 1,600 generated bills",
        what, f"p = 1/(1+e^−z), z = intercept + Σ w·(x−mean)/scale; a finding when p ≥ {thr:.2f}. AUC 0.50 on fabricated bills.",
        used=bool(sm) or not picture)
    written = [fam for fam in ("arithmetic", "file history", "fonts") if fam in by_family]
    if written:
        what = "; ".join(f"{fam}: " + ", ".join(f.check.replace('_', ' ') for f in by_family[fam]) for fam in written) + "."
    else:
        what = "Not run on a picture (no file to inspect)." if picture else "Nothing found: charges add up, one clean save, one typeface."
    row("Written forensic checks", "Symbolic rules (deterministic)", "pypdf: metadata, revisions, text runs; arithmetic on the items",
        what, "Items ≠ total to the paisa; more than one %%EOF; Creator ≠ Producer; modified after created; the total's typeface used nowhere else.",
        used=not picture or bool(written))

    # 3. The packet and the insurer's records ------------------------------------------------
    cross = by_family.get(CROSS_DOCUMENT, []) + by_family.get(SUMMARY_REGISTRY, [])
    if documents:
        what = ("; ".join(f.detail for f in cross) if cross
                else "Bill and discharge summary agree: dates, patient, hospital; the summary's registry answer is recorded with the claim.")
    else:
        what = "No discharge summary attached."
    row("Cross-document consistency", "Claim packet, independent evidence family", "Field comparison after normalisation",
        what, "Bill date within [admitted, discharged + 2 days]; names and issuer equal when both read; a surgeon's fee against an operation line that says none. Blank = unknown.",
        used=bool(documents))
    ch = by_family.get(CLAIM_HISTORY, [])
    row("Claim history", "Insurer's own records", "Claims ledger (SQLite)",
        ch[0].detail if ch else "First claim of this bill on the policy.",
        "The same bill number claimed before on the policy is a finding; a repeat is never approved automatically.")

    # 4. Proof, graph, rule --------------------------------------------------------------------
    row("Registry proof", "Hash commitment, digital signature (no AI)", "scrypt N=2¹³ codes, per-hospital salt, Ed25519 signatures, local mirror",
        f"{r.registry_answer.value}: {r.registry_note}",
        "Two codes recomputed from the document and compared exactly; no tolerance. Not issued only inside the coverage window.")
    row("Evidence graph", "Independence families", "Findings grouped by cause",
        (f"{len(fams)} independent famil{'y' if len(fams) == 1 else 'ies'} against: {', '.join(fams)}." if fams else "No independent family against."),
        "Two symptoms of one cause count once; with the issuer silent, R4 needs ≥ 2 families, R5 = 1, R6 = 0.")
    rule_id = (r.rule_applied or "")[:2]
    row("Policy-as-code", "Symbolic decision rule, versioned", f"Rule table R0–R7" + (f", version {r.rule_version}" if r.rule_version else ""),
        f"{rule_id}: {r.verdict.value}. {RULE_TEXT_SHORT.get(rule_id, '')}".strip(),
        "Verified → R1; Mismatch/Not issued → R2/R3 when corroborated, else R7; silent → R4/R5/R6 by family count. Only Authentic can be approved automatically.")

    # 5. The narration ------------------------------------------------------------------------
    agent_model = r.models_used.get("agent")
    steps = [s for s in r.agent_trace if s.get("by", "agent") == "agent"]
    guard = [s for s in r.agent_trace if s.get("by") == "guard"]
    if agent_model and steps:
        what = f"{agent_model} chose the order of {len(steps)} checks and gave a reason for each" + (f"; the guard ran {len(guard)} it skipped." if guard else ".")
    elif r.agent_trace:
        what = "Checks ran in the rule's fixed order (no agent on this deployment)."
    else:
        what = "Not run: the issuer settled the claim and nothing needed gathering."
    row("Evidence agent", "Agentic AI, tool calling", f"{agent_model or first}, six tools",
        what, "Every tool answers from the findings the rule already weighed; any tool not called is run by the guard; a note naming a verdict is dropped.",
        used=bool(agent_model and steps))
    writer = r.models_used.get("reason")
    if r.reason_writer == "model":
        what = f"Written by {writer or first} from the evidence graph and the rule's text; passed the check-back."
    else:
        what = "Assembled from the written template (no model, or the model's draft failed the check-back)."
    row("Reason writer", "Grounded LLM with a guardrail (check-back)", f"{writer or first}, grounded on the evidence graph",
        what, "A draft must open with the decided verdict's sentence and no other's, be ≥ 60 characters and 2 sentences; else the template, labelled.",
        used=r.reason_writer == "model")
    return rows


RULE_TEXT_SHORT = {
    "R0": "The bill could not be read.", "R1": "The issuer confirmed the bill.",
    "R2": "The issuer issued this ticket, but not with these details.",
    "R3": "The issuer published every bill of the period; this is not among them.",
    "R4": "Issuer silent; two or more independent families against.",
    "R5": "Issuer silent; one family against: not enough to convict.",
    "R6": "Issuer silent; nothing wrong, which is not proof.",
    "R7": "The issuer contradicts an uncorroborated picture reading: ask for the original.",
}
