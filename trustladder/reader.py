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

This demo reads the PDF's text layer, which is exact for born-digital bills (two
extractors, see `pdf_text`, so a browser-printed bilingual bill reads as exactly as a
billing system's English one).
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

from .models import BillFields, DischargeFields, LineItem, ReadStatus

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


def canonical_record(doc) -> str:
    """The exact string whose code the issuer publishes as the 'value'.

    For a bill: ticket | bill number | date | patient | total in paise, each in standard
    form. The line items are deliberately not included: the total is the field that
    matters for payment, and fewer fields means fewer chances to misread.

    For a discharge summary: ticket | summary number | discharge date | patient |
    admission date. The clinical text is not committed to: the dates and the identity
    are what a claim is checked against, and they are the fields a reader gets right.
    """
    if isinstance(doc, DischargeFields):
        return "|".join([
            canonical_ticket(doc.ticket),
            canonical_bill_no(doc.summary_no),
            doc.discharged,
            canonical_name(doc.patient),
            doc.admitted,
        ])
    return "|".join([
        canonical_ticket(doc.ticket),
        canonical_bill_no(doc.bill_no),
        doc.bill_date,
        canonical_name(doc.patient),
        str(int(doc.total_paise)),
    ])


# --------------------------------------------------------------------------
# Reading a discharge summary PDF
# --------------------------------------------------------------------------

_SUMMARY_PATTERNS = {
    "issuer_id": re.compile(r"Issuer ID:\s*(\S+)"),
    "summary_no": re.compile(r"Summary No:\s*(\S+)"),
    "uhid": re.compile(r"UHID:\s*(\S+)"),
    "patient": re.compile(r"Patient:\s*(.+?)\s*$", re.M),
    "admitted": re.compile(r"Date of Admission:\s*([0-9A-Za-z\-/ .]+?)\s*$", re.M),
    "discharged": re.compile(r"Date of Discharge:\s*([0-9A-Za-z\-/ .]+?)\s*$", re.M),
    "diagnosis": re.compile(r"Diagnosis:\s*(.+?)\s*$", re.M),
    "procedure": re.compile(r"Operation:\s*(.+?)\s*$", re.M),
    "consultant": re.compile(r"Consultant:\s*(.+?)\s*$", re.M),
    "ticket": re.compile(r"Ticket:\s*([A-Z0-9\-]+)"),
}


def is_summary_text(text: str) -> bool:
    """Does this text layer belong to a discharge summary rather than a bill?"""
    return "DISCHARGE SUMMARY" in text.upper() and "Summary No:" in text


def read_summary(pdf_bytes: bytes) -> tuple[ReadStatus, "DischargeFields | None", str]:
    """Read a discharge summary's fields from its text layer, like `read_bill`."""
    text = pdf_text(pdf_bytes)
    if len(text.strip()) < 20:
        return (ReadStatus.COULD_NOT_READ, None,
                "The document has no readable text (it looks like a photo or scan).")
    found = {k: (p.search(text).group(1).strip() if p.search(text) else "")
             for k, p in _SUMMARY_PATTERNS.items()}
    admitted = parse_date_iso(found["admitted"]) if found["admitted"] else None
    discharged = parse_date_iso(found["discharged"]) if found["discharged"] else None
    missing = [k for k in ("issuer_id", "summary_no", "patient") if not found[k]]
    if admitted is None:
        missing.append("admission date")
    if discharged is None:
        missing.append("discharge date")
    if missing:
        return (ReadStatus.COULD_NOT_READ, None,
                "Could not read with confidence: " + ", ".join(missing) + ".")
    # "none" is a statement (no operation took place); "" means the line was not read.
    # The packet check charges a surgeon's fee against a stated "none", never against silence.
    procedure = found["procedure"]
    if procedure.lower() in ("none", "nil", "no operation", "-"):
        procedure = "none"
    fields = DischargeFields(
        issuer_id=found["issuer_id"], issuer_name=text.strip().splitlines()[0].strip(),
        summary_no=found["summary_no"], uhid=found["uhid"], patient=found["patient"],
        admitted=admitted, discharged=discharged, diagnosis=found["diagnosis"],
        procedure=procedure, consultant=found["consultant"], ticket=found["ticket"])
    return ReadStatus.OK, fields, "All fields needed for the lookup were read."


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


# The proof that an extraction carried the field labels through intact. Bills and
# discharge summaries both print their number with this label.
_LABELLED = re.compile(r"(Bill No|Summary No):\s*\S")


def _pypdf_text(pdf_bytes: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        return ""


def _pdfium_text(pdf_bytes: bytes) -> str:
    """The same text layer as Chromium's PDF engine reads it (pypdfium2)."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return ""
    try:
        doc = pdfium.PdfDocument(pdf_bytes)
        parts = []
        for page in doc:
            textpage = page.get_textpage()
            parts.append(textpage.get_text_bounded())
            textpage.close()
            page.close()
        doc.close()
        return "\n".join(parts).replace("\r\n", "\n")
    except Exception:
        return ""


def pdf_text(pdf_bytes: bytes) -> str:
    """All text on all pages, or '' if the document has no text layer.

    Two extractors read the text layer: pypdf first, then Chromium's PDF engine. A PDF
    printed by a browser (a hospital's bilingual Marathi bill, for instance) positions
    every word separately and embeds subsetted fonts, and pypdf then returns "Bill  No :"
    with stray spaces and unmapped glyphs; pdfium reads the same file cleanly. The text
    taken is the first in which the field labels came through intact, so a document the
    first extractor garbles is still read exactly, and a document with no text layer at
    all (a scan) is still '', which sends it to the picture readers.
    """
    first = _pypdf_text(pdf_bytes)
    if _LABELLED.search(first):
        return first
    second = _pdfium_text(pdf_bytes)
    if _LABELLED.search(second):
        return second
    return first or second


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
