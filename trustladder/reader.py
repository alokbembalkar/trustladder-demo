"""
Reading the fields off a bill, and the single standard form they are compared in.

Two rules from the proposal are enforced here:

1. One standard form. Every value is normalised before it is hashed, so
   "Rs 1,86,400.00", "186400" and "1,86,400" all become the same amount, and
   "03-Mar-2026" becomes "2026-03-03". A formatting difference can never look
   like a changed value.

2. "Could not read" is never "Mismatch". If any field needed for the lookup
   cannot be read with confidence, the reader says so and the document goes to
   its own queue. It is never allowed to fall through to a registry comparison
   that would report a genuine bill as not matching.

This demo reads the PDF's text layer, which is exact for born-digital bills.
Reading scans and photos needs an OCR / document-understanding model
(LayoutLM / Donut families in the proposal); that model would plug in here and
report a confidence per field. Without a text layer this demo reports
"could not read", which is the honest behaviour for an unreadable document.
"""

from __future__ import annotations

import datetime as _dt
import io
import re
import unicodedata

from pypdf import PdfReader

from .models import BillFields, LineItem, ReadStatus

# --------------------------------------------------------------------------
# Normalisation: the one standard form
# --------------------------------------------------------------------------


def canonical_ticket(ticket: str) -> str:
    """Upper-case, with every space and dash removed: 'sh-7k2p 9qx4' -> 'SH7K2P9QX4'."""
    return re.sub(r"[\s\-]", "", ticket or "").upper()


def canonical_name(name: str) -> str:
    """Unicode-normalised, lower-case, single-spaced, no punctuation."""
    name = unicodedata.normalize("NFKC", name or "")
    name = re.sub(r"[^\w\s]", " ", name)
    return " ".join(name.lower().split())


def canonical_bill_no(bill_no: str) -> str:
    return re.sub(r"\s", "", bill_no or "").upper()


def parse_amount_paise(text: str) -> int | None:
    """'Rs 1,86,400.00' -> 18640000. Returns None if it is not an amount."""
    m = re.search(r"(\d[\d,]*)(?:\.(\d{1,2}))?", text or "")
    if not m:
        return None
    rupees = int(m.group(1).replace(",", ""))
    paise = int((m.group(2) or "0").ljust(2, "0"))
    return rupees * 100 + paise


def parse_date_iso(text: str) -> str | None:
    """Accept the common Indian bill date styles; return YYYY-MM-DD or None."""
    text = (text or "").strip()
    for fmt in ("%d-%b-%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d %b %Y", "%d.%m.%Y"):
        try:
            return _dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def canonical_record(bill: BillFields) -> str:
    """The exact string whose code the issuer publishes as the 'value'.

    ticket | bill number | date | patient | total in paise, each in standard form.
    The line items are deliberately not included: the total is the field that
    matters for payment, and fewer fields means fewer chances to misread.
    """
    return "|".join([
        canonical_ticket(bill.ticket),
        canonical_bill_no(bill.bill_no),
        bill.bill_date,
        canonical_name(bill.patient),
        str(int(bill.total_paise)),
    ])


# --------------------------------------------------------------------------
# Reading a PDF
# --------------------------------------------------------------------------

_FIELD_PATTERNS = {
    "issuer_id": re.compile(r"Issuer ID:\s*(\S+)"),
    "bill_no": re.compile(r"Bill No:\s*(\S+)"),
    "bill_date": re.compile(r"Bill Date:\s*([0-9A-Za-z\-/ .]+?)\s*$", re.M),
    "patient": re.compile(r"Patient:\s*(.+?)\s*$", re.M),
    "ticket": re.compile(r"Ticket:\s*([A-Z0-9\-]+)"),
    "total": re.compile(r"Grand Total:\s*(?:Rs\.?\s*)?([\d,]+(?:\.\d{1,2})?)"),
}
# An amount on its own ("36,000.00") or at the end of a description line.
_AMOUNT_ONLY = re.compile(r"^\d[\d,]*\.\d{2}$")
_DESC_AND_AMOUNT = re.compile(r"^(?P<desc>[A-Za-z][A-Za-z ()&,\-]*?)[ \t]+(?P<amt>\d[\d,]*\.\d{2})$")


def _read_items(text: str) -> list[LineItem]:
    """Read the charges table: the lines between the table header and the total.

    Text extraction may put a description and its amount on one line or on two
    consecutive lines; both layouts are accepted.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    try:
        start = next(i for i, ln in enumerate(lines) if ln.startswith("Amount (Rs)")) + 1
        end = next(i for i, ln in enumerate(lines) if ln.startswith("Grand Total"))
    except StopIteration:
        return []
    items: list[LineItem] = []
    pending_desc = ""
    for ln in lines[start:end]:
        m = _DESC_AND_AMOUNT.match(ln)
        if m:
            items.append(LineItem(m.group("desc").strip(), parse_amount_paise(m.group("amt"))))
            pending_desc = ""
        elif _AMOUNT_ONLY.match(ln) and pending_desc:
            items.append(LineItem(pending_desc, parse_amount_paise(ln)))
            pending_desc = ""
        else:
            pending_desc = ln
    return items


def pdf_text(pdf_bytes: bytes) -> str:
    """All text on all pages, or '' if the document has no text layer."""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        return ""


def read_bill(pdf_bytes: bytes) -> tuple[ReadStatus, BillFields | None, str]:
    """Read a bill's fields.

    Returns (status, fields, note). `fields` is None when status is
    COULD_NOT_READ. The note says in plain words what could or could not be read.
    """
    text = pdf_text(pdf_bytes)
    if len(text.strip()) < 20:
        return (ReadStatus.COULD_NOT_READ, None,
                "The document has no readable text (it looks like a photo or scan). "
                "It was not compared with the registry, because a misread must never "
                "be reported as a mismatch.")

    found = {k: (p.search(text).group(1).strip() if p.search(text) else "")
             for k, p in _FIELD_PATTERNS.items()}
    missing = [k for k in ("issuer_id", "bill_no", "bill_date", "patient", "total") if not found[k]]
    date_iso = parse_date_iso(found["bill_date"]) if found["bill_date"] else None
    total = parse_amount_paise(found["total"]) if found["total"] else None
    if date_iso is None and "bill_date" not in missing:
        missing.append("bill_date")
    if total is None and "total" not in missing:
        missing.append("total")
    if missing:
        labels = {"issuer_id": "issuer", "bill_no": "bill number", "bill_date": "date",
                  "patient": "patient name", "total": "total amount"}
        return (ReadStatus.COULD_NOT_READ, None,
                "Could not read with confidence: " + ", ".join(labels[m] for m in missing) + ".")

    # The issuer's name is the first line of the bill.
    issuer_name = text.strip().splitlines()[0].strip()

    items = _read_items(text)

    fields = BillFields(
        issuer_id=found["issuer_id"],
        issuer_name=issuer_name,
        bill_no=found["bill_no"],
        bill_date=date_iso,
        patient=found["patient"],
        items=items,
        total_paise=total,
        ticket=found["ticket"],
    )
    return ReadStatus.OK, fields, "All fields needed for the lookup were read."
