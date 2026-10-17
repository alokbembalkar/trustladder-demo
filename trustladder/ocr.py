"""
The second reader: a deep OCR model that runs inside the insurer's boundary.

Why a second reader at all
--------------------------
`vision.py` explains the doctrine: a picture read by one model can be cleared by the
registry but not condemned by it, unless an independent reader agrees. This is that
reader. It is a pair of neural networks, a text-detection network that finds the lines
on the page and a recognition network that reads each line, running locally on the CPU
with no network call. In the product it is also the reader of record when a bill must
not leave the building.

What it is, and what it is not
------------------------------
It is a generic, open text-recognition model (the RapidOCR packaging of the PaddleOCR
networks, in ONNX form), the kind that reads shop signs and receipts. It is not a
forgery detector and it carries no rule of its own: it reads text, the same regular
expressions that read a PDF's text layer pick the fields out of what it read, and the
only question asked of it is "do you see the same ticket, bill number and total?".

It is optional. The package is heavy (a few hundred megabytes with its runtime), so
it is installed from requirements-ocr.txt rather than requirements.txt, and every
function here answers gracefully when it is absent. The hosted free-tier demo runs
without it; a laptop presentation can run with it, and the screens say which.

Languages
---------
The default recognition weights read Latin script and digits, which is enough to
corroborate the ticket, the bill number and the total on any bill, including one whose
labels are printed in Marathi: codes and amounts are Latin on Indian bills. Reading
the Devanagari labels themselves would need the language-specific weights, which this
demo does not ship.
"""

from __future__ import annotations

import io
import re

from .models import BillFields, LineItem
from .reader import parse_amount_paise, parse_date_iso

MODEL_NAME = "RapidOCR (PaddleOCR detection + recognition networks, ONNX)"

_engine = None
_tried = False


def available() -> bool:
    """Is the OCR package installed? Loads the networks on first use, once."""
    global _engine, _tried
    if _tried:
        return _engine is not None
    _tried = True
    try:
        try:
            from rapidocr import RapidOCR            # the current package name
        except ImportError:
            from rapidocr_onnxruntime import RapidOCR  # the older one
        _engine = RapidOCR()
    except Exception:
        _engine = None
    return _engine is not None


def read_lines(image: bytes) -> list[str]:
    """Every line of text the networks found, top to bottom."""
    if not available():
        return []
    import numpy as np
    from PIL import Image
    img = np.array(Image.open(io.BytesIO(image)).convert("RGB"))
    try:
        result = _engine(img)
    except Exception:
        return []
    # The two package generations return different shapes.
    if hasattr(result, "txts"):
        return [t for t in (result.txts or []) if t]
    if isinstance(result, tuple):
        result = result[0]
    return [row[1] for row in (result or []) if len(row) > 1 and row[1]]


# The same field patterns as the PDF reader, applied to OCR lines. OCR tends to drop
# the colon or run words together, so the patterns are a little more forgiving.
_PATTERNS = {
    "issuer_id": re.compile(r"Issuer\s*ID\s*:?\s*([A-Z0-9\-]{8,})", re.I),
    "bill_no": re.compile(r"Bill\s*No\.?\s*:?\s*([A-Z]{2,4}/\d{4}/\d{4,6})", re.I),
    "bill_date": re.compile(r"Bill\s*Date\s*:?\s*([0-9]{1,2}[-/ ][A-Za-z0-9]{2,3}[-/ ][0-9]{4})", re.I),
    "patient": re.compile(r"Patient\s*:?\s*([A-Za-z][A-Za-z .]+)$", re.I | re.M),
    "ticket": re.compile(r"Ticket\s*:?\s*([A-Z0-9\-]{10,})", re.I),
    "total": re.compile(r"Grand\s*Total\s*:?\s*(?:Rs\.?\s*)?([\d,]+(?:\.\d{1,2})?)", re.I),
}


_SUMMARY_PATTERNS = {
    "issuer_id": re.compile(r"Issuer\s*ID\s*:?\s*([A-Z0-9\-]{8,})", re.I),
    "summary_no": re.compile(r"Summary\s*No\.?\s*:?\s*([A-Z]{2,4}/DS/\d{4}/\d{4,6})", re.I),
    "uhid": re.compile(r"UHID\s*:?\s*([A-Z0-9\-]{5,})", re.I),
    "patient": re.compile(r"Patient\s*:?\s*([A-Za-z][A-Za-z .]+)$", re.I | re.M),
    "admitted": re.compile(r"Admission\s*:?\s*([0-9]{1,2}[-/ ][A-Za-z0-9]{2,3}[-/ ][0-9]{4})", re.I),
    "discharged": re.compile(r"Discharge\s*:?\s*([0-9]{1,2}[-/ ][A-Za-z0-9]{2,3}[-/ ][0-9]{4})", re.I),
    "ticket": re.compile(r"Ticket\s*:?\s*([A-Z0-9\-]{10,})", re.I),
}


def read_summary_fields(lines: list[str]):
    """A discharge summary's lookup fields from OCR lines, or None if it is not one."""
    from .models import DischargeFields
    text = "\n".join(lines)
    if not re.search(r"DISCHARGE\s*SUMMARY", text, re.I):
        return None
    found = {k: (p.search(text).group(1).strip() if p.search(text) else "") for k, p in _SUMMARY_PATTERNS.items()}
    admitted = parse_date_iso(found["admitted"].replace(" ", "-")) if found["admitted"] else None
    discharged = parse_date_iso(found["discharged"].replace(" ", "-")) if found["discharged"] else None
    patient = found["patient"].strip()
    if " " not in patient:
        patient = ""
    return DischargeFields(found["issuer_id"].upper(), lines[0] if lines else "", found["summary_no"].upper(),
                           found["uhid"].upper(), patient, admitted or "", discharged or "", "", "", "",
                           found["ticket"].upper().replace(" ", ""))


def read_fields(image: bytes):
    """The lookup fields as this reader saw them (BillFields or DischargeFields), or None
    if neither layout was found."""
    lines = read_lines(image)
    if not lines:
        return None
    summary = read_summary_fields(lines)
    if summary is not None:
        return summary
    text = "\n".join(lines)
    found = {}
    for key, pat in _PATTERNS.items():
        m = pat.search(text)
        found[key] = m.group(1).strip() if m else ""
    if not found["total"]:
        # The total often lands on its own line after "Grand Total".
        for i, ln in enumerate(lines):
            if "grand total" in ln.lower() and i + 1 < len(lines):
                amt = re.search(r"([\d,]+(?:\.\d{1,2})?)", lines[i + 1])
                found["total"] = amt.group(1) if amt else ""
                break
    total = parse_amount_paise(found["total"]) if found["total"] else None
    date_iso = parse_date_iso(found["bill_date"]) if found["bill_date"] else None
    if not (found["bill_no"] and total is not None):
        return None
    # OCR runs words together ("MeeraKulkarni"). A name without a space is not a
    # name this reader can vouch for; it is left blank for the customer to type.
    patient = found["patient"].strip()
    if " " not in patient:
        patient = ""
    return BillFields(found["issuer_id"].upper(), lines[0] if lines else "", found["bill_no"].upper(),
                      date_iso or "", patient, [], total,
                      found["ticket"].upper().replace(" ", ""))
