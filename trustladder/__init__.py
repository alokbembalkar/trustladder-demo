"""
TrustLadder demo engine (IIM-V Capstone, Group 7).

A small, self-contained demonstration of the four-stage workflow in the group's
proposal:

    Detect  ->  Decide  ->  Verify  ->  Human Review

Package layout (one job per module):

    crypto.py    slow salted codes and issuer signatures (the only "security" code)
    registry.py  the issuer directory and the signed, append-only code files
    bills.py     synthetic hospital bills rendered as PDFs (no real people or hospitals)
    reader.py    reads the fields off a bill and normalises them to one standard form
    detect.py    light, open screening checks; they flag, they never decide
    decide.py    the published decision rule: evidence grade -> one of four verdicts
    explain.py   turns the findings into a plain-language reason
    pipeline.py  runs one document up the ladder and returns an auditable result
    cases.py     builds the demo world: issuers, registry files, the five showcase
                 bills and the synthetic month used for the policy simulation

Everything is synthetic. Nothing here connects to a live system, and no code is
shared with any other product.
"""

__version__ = "1.0.0"

# The rule version is written into every audit record, so a verdict can always be
# traced back to the exact rule that produced it.
RULE_VERSION = "TL-RULE-1.0"
