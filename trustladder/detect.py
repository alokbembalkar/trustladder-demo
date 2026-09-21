"""
Step 1, Detect: light, open screening checks.

These checks look for traces an edit leaves behind. They SCREEN; they never
decide. In particular, finding nothing proves nothing: a bill typed from a blank
page leaves no edit traces at all, which is exactly why the rule in decide.py
never clears a document on these checks alone.

Each finding belongs to a "family". Checks in the same family can be two
symptoms of one cause, so the rule counts families, not findings.

    arithmetic    the charges do not add up to the total
    file history  the file was re-saved by another program, later, or in layers
    fonts         the amount is set in a different typeface from the rest

Every check is written from scratch for this demo and uses only what is visible
in the file itself.
"""

from __future__ import annotations

import io
import re

from pypdf import PdfReader

from .bills import format_inr
from .models import BillFields, Finding

# --------------------------------------------------------------------------
# Individual checks
# --------------------------------------------------------------------------


def check_arithmetic(fields: BillFields | None) -> list[Finding]:
    """Do the line items add up to the Grand Total?"""
    if fields is None or not fields.items:
        return []
    total_items = sum(i.amount_paise for i in fields.items)
    if total_items != fields.total_paise:
        diff = abs(fields.total_paise - total_items)
        direction = "more" if fields.total_paise > total_items else "less"
        return [Finding(
            "arithmetic", "items_vs_total",
            f"The charges add up to Rs {format_inr(total_items)} but the Grand Total says "
            f"Rs {format_inr(fields.total_paise)}, Rs {format_inr(diff)} {direction} than the charges.",
        )]
    return []


def _pdf_date_key(value: str) -> str:
    """Reduce a PDF date 'D:20260303112000+05'30'' to '20260303112000' for comparing."""
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[:14]


def check_file_history(pdf_bytes: bytes) -> list[Finding]:
    """Was the file changed after it was created, or by a different program?"""
    findings: list[Finding] = []
    # Every save of a PDF ends with the marker %%EOF. More than one means the
    # file was saved again on top of itself (an "incremental update").
    revisions = pdf_bytes.count(b"%%EOF")
    if revisions > 1:
        findings.append(Finding(
            "file history", "incremental_saves",
            f"The file was saved {revisions} times in layers; the later layers change the original.",
        ))
    try:
        meta = PdfReader(io.BytesIO(pdf_bytes)).metadata or {}
    except Exception:
        meta = {}
    creator = str(meta.get("/Creator", "") or "")
    producer = str(meta.get("/Producer", "") or "")
    if creator and producer and creator != producer:
        findings.append(Finding(
            "file history", "creator_producer_differ",
            f"The bill was created by '{creator}' but last written by '{producer}'.",
        ))
    created = _pdf_date_key(meta.get("/CreationDate", ""))
    modified = _pdf_date_key(meta.get("/ModDate", ""))
    if created and modified and modified > created:
        findings.append(Finding(
            "file history", "modified_after_creation",
            "The file was modified after it was created "
            f"(created {created[:4]}-{created[4:6]}-{created[6:8]}, "
            f"modified {modified[:4]}-{modified[4:6]}-{modified[6:8]}).",
        ))
    return findings


def check_fonts(pdf_bytes: bytes) -> list[Finding]:
    """Is the Grand Total set in a typeface the rest of the bill does not use?"""
    runs: list[tuple[str, str]] = []

    def visitor(text, cm, tm, font_dict, font_size):
        if text and text.strip() and font_dict is not None:
            runs.append((text.strip(), str(font_dict.get("/BaseFont", ""))))

    try:
        for page in PdfReader(io.BytesIO(pdf_bytes)).pages:
            page.extract_text(visitor_text=visitor)
    except Exception:
        return []
    if not runs:
        return []
    fonts_used = {f for _, f in runs}
    total_fonts = {f for t, f in runs if "Grand Total" in t or t.startswith("Rs ")}
    odd = total_fonts - {f for t, f in runs if not ("Grand Total" in t or t.startswith("Rs "))}
    if odd and len(fonts_used) > 1:
        names = ", ".join(sorted(f.lstrip("/") for f in odd))
        return [Finding(
            "fonts", "total_in_foreign_font",
            f"The Grand Total is set in a typeface ({names}) used nowhere else on the bill.",
        )]
    return []


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def screen(pdf_bytes: bytes, fields: BillFields | None) -> list[Finding]:
    """Run every screening check and return all findings (possibly none)."""
    return check_arithmetic(fields) + check_file_history(pdf_bytes) + check_fonts(pdf_bytes)


def families(findings: list[Finding]) -> set[str]:
    """The distinct families among the findings: the count the rule uses."""
    return {f.family for f in findings}
