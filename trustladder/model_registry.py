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
            "active": llm_on,
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
            "active": llm_on,
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
