"""
TrustLadder demo engine (IIM-V Capstone, Group 7).

A small, self-contained demonstration of the four-stage workflow in the group's
proposal:

    Detect  ->  Decide  ->  Verify  ->  Human Review

Package layout (one job per module):

    crypto.py    slow salted codes and issuer signatures (the only "security" code)
    registry.py  the issuer directory and the signed, append-only code files
    bills.py     synthetic hospital bills rendered as PDFs (no real people or hospitals)
    reader.py    reads the fields off a bill's text layer and normalises them
    vision.py    reads a photograph or scan with a vision-language model, with the
                 confidence gate and the corroboration doctrine
    ocr.py       the optional second reader: a local deep OCR model
    llm.py       the one door to the hosted language model (Gemini, plain HTTPS)
    detect.py    light, open screening checks; they flag, they never decide
    screener.py  the trained screening model, shipped as readable coefficients
    decide.py    the published decision rule: evidence grade -> one of four verdicts
    agent.py     the tool-calling evidence agent: gathers and narrates, never decides
    explain.py   turns the findings into a plain-language reason (model or template)
    pipeline.py  runs one document up the ladder and returns an auditable result
    model_registry.py  every model in the build, with its version and its limits
    cases.py     builds the demo world: issuers, registry files, the showcase
                 bills and the synthetic month used for the policy simulation

Everything is synthetic. Nothing here connects to a live system, and no code is
shared with any other product.
"""

__version__ = "1.1.0"

# The rule version is written into every audit record, so a verdict can always be
# traced back to the exact rule that produced it. 1.1 added R7, the rule for a
# reading from a picture that the issuer contradicts but nobody has corroborated.
RULE_VERSION = "TL-RULE-1.1"
