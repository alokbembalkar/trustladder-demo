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

from . import screener
from .bills import format_inr
from .models import BillFields, Finding

# --------------------------------------------------------------------------
# Individual checks
# --------------------------------------------------------------------------


def check_arithmetic(fields: BillFields | None) -> list[Finding]:
    """Do the line items add up to the Grand Total?"""
    if fields is None or not fields.items or not fields.total_paise:
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


def screen(pdf_bytes: bytes, fields: BillFields | None, container: bool = True) -> list[Finding]:
    """Run every screening check, then let the trained model weigh in.

    `container` is False for a plain picture (JPEG, PNG). The file-history and font
    checks, and the trained model's file features, read the structure of a PDF; a
    picture has none, so only the arithmetic of what was read is screened.

    The written checks look for named things. The model, trained on bills this
    repository generates, was not told what to look for and learns the difference
    between a document that was edited and one that merely passed through another
    program. It never decides anything; what it does is settle the one signal the
    written checks cannot settle on their own.

    That signal is **file history**. Every phone app that compresses or merges a PDF
    leaves the same trace an editor leaves: a second revision, a different producer,
    a later modification date. The written check cannot tell those apart, so it fires
    on both, and that is how a genuine customer with a re-saved bill ends up accused.
    Measured on the generated corpus, the written checks flag 100% of genuine
    re-saved and discounted bills; the model flags 0% of them, while still flagging
    every bill that really was edited.

    So when the model says this file was not edited, the file-history finding is still
    shown to the officer but no longer counts as an independent cause. Note what that
    can and cannot do. It can turn a conviction into a referral, so a person looks
    instead of the claim being held. It can never clear anything: only the issuer
    clears a document, and the model has no say in that.
    """
    if not container:
        return check_arithmetic(fields)
    found = check_arithmetic(fields) + check_file_history(pdf_bytes) + check_fonts(pdf_bytes)
    model = screener.load()
    if model is None:
        return found

    probability = model.probability(screener.features(pdf_bytes, fields))
    looks_edited = probability >= model.threshold

    if looks_edited:
        note = model.finding(pdf_bytes, fields)
        # A second opinion on the same document is not a second cause, so it is
        # reported and not counted.
        if note is not None:
            found.append(Finding(note.family, note.check, note.detail, independent=False))
        return found

    history = [f for f in found if f.family == "file history"]
    if history:
        found = [f if f.family != "file history"
                 else Finding(f.family, f.check, f.detail, independent=False) for f in found]
        found.append(Finding(
            screener.MODEL_FAMILY, "learned_screener_clears_history",
            f"The screening model puts this at only {probability:.0%} likely to have been edited. "
            "The file history above is consistent with an ordinary re-save, so it is shown but not "
            "counted as evidence of wrongdoing.",
            independent=False))
    return found


def families(findings: list[Finding]) -> set[str]:
    """The distinct families among the findings: the count the rule uses."""
    return {f.family for f in findings if f.independent}
