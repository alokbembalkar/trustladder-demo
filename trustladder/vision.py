"""
Document intelligence: reading a bill that is a photo or a scan.

Where this sits
---------------
`reader.py` reads a born-digital PDF from its text layer, exactly. This module is
the other door: a JPEG or PNG a customer photographed, or a PDF that is only a
picture. A vision-language model reads it, in whatever language the bill is printed
in, and reports a confidence for every field. The result is put into the same
`BillFields` shape, normalised by the same functions, and compared with the registry
by the same code, so the rest of the ladder never knows which door the bill came in by.

What a reading from a picture is, and is not
--------------------------------------------
A text-layer read is exact. A picture read is a model's best transcription, and a
model can misread one character of a ticket or one digit of a total. The registry
would then answer "Not issued" or "Mismatch" to a bill the hospital did issue, which
is the one mistake this product must never make on its own. Three guards follow.

1. Confidence gate. Any field the lookup needs that the model is not confident of
   stops the reading at "could not read", exactly as a missing field does in a PDF.
2. Corroboration. A second, independent reader (a deep OCR model running locally,
   see `ocr.py`) reads the same picture when it is installed. The two readers must
   agree on the ticket, the bill number and the total before the reading is marked
   corroborated. Without that, the picture can be CLEARED by the registry, because
   a misread can never produce a false match, but it cannot be CONDEMNED by it:
   `decide.py` turns an uncorroborated contradiction into "ask for the original".
3. The customer confirms. The fields are shown back before submission, the customer
   can correct them, and the corrections are counted: that count is the reading
   model's error rate as the risk head sees it.

Privacy, stated plainly
-----------------------
In this demo the vision model is hosted and the bills are synthetic. In the product
the reader runs inside the insurer's boundary; the architecture marks the hosted call
as the demo's shortcut, not the design.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import llm, ocr
from .models import BillFields, LineItem, ReadStatus
from .reader import (canonical_bill_no, canonical_ticket, parse_amount_paise, parse_date_iso,
                     pdf_text, read_bill)

IMAGE_TYPES = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}

# What the lookup needs. The confidence gate applies to exactly these.
LOOKUP_FIELDS = ("issuer_id", "bill_no", "bill_date", "patient", "total")
CONFIDENCE_FLOOR = 0.70

_TICKET_SHAPE = re.compile(r"^[A-Z]{2}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$")


@dataclass
class Reading:
    """How a bill was read, and how far that reading can be trusted.

    `source` is "text layer" (exact), "vision model" (a picture, read by the model)
    or "none" (nothing usable). `corroborated` is the doctrine's switch: True for a
    text layer, and for a picture only when the second reader agreed. The customer's
    confirmation and corrections are filled in by the submission screen.
    """
    status: ReadStatus
    fields: BillFields | None
    note: str
    source: str = "text layer"
    model: str = ""
    confidence: dict = field(default_factory=dict)
    language: str = ""
    second_reader: str = ""           # "deep OCR" when it ran, else ""
    agree: bool | None = None         # did the second reader agree on the lookup fields?
    customer_confirmed: bool = False
    corrections: int = 0
    partial: dict = field(default_factory=dict)
    """What was read when the reading was NOT good enough to use: the form the customer
    completes is pre-filled from this, blanks where the reader could not see."""

    @property
    def from_picture(self) -> bool:
        return self.source == "vision model"

    @property
    def corroborated(self) -> bool:
        """May the registry's contradiction of this reading be treated as proof?"""
        if self.source == "text layer":
            return True
        return bool(self.agree) and self.customer_confirmed

    def report(self) -> dict:
        """What is stored with the claim and shown to the officer and the auditor."""
        return {"source": self.source, "model": self.model, "language": self.language,
                "confidence": self.confidence, "second_reader": self.second_reader,
                "agree": self.agree, "customer_confirmed": self.customer_confirmed,
                "corrections": self.corrections, "corroborated": self.corroborated}


# --------------------------------------------------------------------------
# Entry point: any file the customer may upload
# --------------------------------------------------------------------------


def mime_of(file_name: str) -> str | None:
    """The image type by extension, or None for anything that is not an image."""
    return IMAGE_TYPES.get(file_name.rsplit(".", 1)[-1].lower()) if "." in file_name else None


def read_any(data: bytes, file_name: str) -> Reading:
    """Read a PDF from its text layer when it has one; otherwise read it as a picture."""
    mime = mime_of(file_name)
    if mime is None:
        status, fields, note = read_bill(data)
        if status is ReadStatus.OK or len(pdf_text(data).strip()) >= 20:
            # A real text layer, read exactly (or honestly incomplete).
            return Reading(status, fields, note, source="text layer")
        # A PDF that is only a picture: render the page and read it as one.
        png = first_page_png(data)
        if png is None:
            return Reading(ReadStatus.COULD_NOT_READ, None,
                           "The file could not be opened as a PDF or an image.", source="none")
        return read_picture(png, "image/png")
    return read_picture(data, mime)


def first_page_png(pdf_bytes: bytes, scale: float = 1.6) -> bytes | None:
    """The first page of a PDF as a PNG, for the vision model."""
    import io
    try:
        import pypdfium2 as pdfium
        page = pdfium.PdfDocument(pdf_bytes)[0]
        img = page.render(scale=scale).to_pil()
    except Exception:
        return None
    out = io.BytesIO()
    img.convert("RGB").save(out, format="PNG")
    return out.getvalue()


# --------------------------------------------------------------------------
# The vision model
# --------------------------------------------------------------------------

_PROMPT = (
    "You are reading a photograph or scan of a hospital bill for an insurance claim. The bill "
    "may be printed in any language; read it as printed and do not translate names. Return ONLY "
    "a JSON object with these keys:\n"
    "  language_seen: the language of the printed text, in English (e.g. English, Marathi, Hindi)\n"
    "  issuer_name: the hospital's name exactly as printed\n"
    "  issuer_id: the issuer ID code printed on the bill (letters, digits and hyphens)\n"
    "  bill_no: the bill number exactly as printed\n"
    "  bill_date: the bill date as YYYY-MM-DD\n"
    "  patient: the patient's name exactly as printed\n"
    "  ticket: the TrustLadder ticket printed on the bill, character by character, or \"\" if none\n"
    "  line_items: a list of {description, amount} for each charge, amount as a number\n"
    "  total: the grand total as a number\n"
    "  confidence: an object with a number from 0 to 1 for each of issuer_id, bill_no, "
    "bill_date, patient, ticket, total: 1 means every character is clearly legible\n"
    "  notes: anything a claims officer should know about the picture, or null\n"
    "Transcribe codes exactly. If a value is unreadable, use \"\" and a low confidence."
)
_SYSTEM = ("You are a careful document reader. You transcribe; you never guess a code or a number "
           "you cannot see clearly, and you report low confidence instead.")


def read_picture(image: bytes, mime: str) -> Reading:
    """Read one picture with the vision model, then let the second reader check it."""
    if not llm.available():
        return _without_model(image)

    try:
        raw, model = llm.ask_json([llm.text_part(_PROMPT), llm.image_part(image, mime)],
                                  system=_SYSTEM, max_tokens=1200)
    except (llm.ModelUnavailable, ValueError):
        return _without_model(image, unavailable=True)

    reading = _to_reading(raw, model)
    if reading.status is ReadStatus.OK:
        _second_opinion(reading, image)
    return reading


def _without_model(image: bytes, unavailable: bool = False) -> Reading:
    """No vision model: the deep OCR reads alone if installed, else 'could not read'.

    The OCR alone reads codes and amounts well and names badly, so what it read is
    offered as a part-filled form for the customer to complete, never used as is.
    """
    why = ("the reading model did not answer" if unavailable else "no reading model is configured")
    if ocr.available():
        fields = ocr.read_fields(image)
        if fields is not None:
            partial = {"issuer_id": fields.issuer_id, "bill_no": fields.bill_no,
                       "bill_date": fields.bill_date, "patient": fields.patient,
                       "total": fields.total_paise, "ticket": fields.ticket,
                       "issuer_name": fields.issuer_name}
            blanks = [k for k in LOOKUP_FIELDS if not partial.get(k)]
            if not blanks:
                return Reading(ReadStatus.OK, fields, "Read by the local deep OCR model; " + why + ".",
                               source="vision model", model=ocr.MODEL_NAME)
            return Reading(ReadStatus.COULD_NOT_READ, None,
                           f"The local OCR model read the picture ({why}) but could not read every "
                           "field. Please complete the details before submitting.",
                           source="vision model", model=ocr.MODEL_NAME, partial=partial)
    return Reading(ReadStatus.COULD_NOT_READ, None,
                   f"This is a picture of a bill and {why}, so it was not read. Upload the PDF "
                   "the hospital gave you.", source="none")


def _to_reading(raw: dict, model: str) -> Reading:
    """Normalise the model's JSON into BillFields, applying the confidence gate."""
    conf = {k: _num(v) for k, v in (raw.get("confidence") or {}).items()}
    language = str(raw.get("language_seen") or "").strip()

    issuer_id = str(raw.get("issuer_id") or "").strip().upper()
    bill_no = canonical_bill_no(str(raw.get("bill_no") or ""))
    date_iso = parse_date_iso(str(raw.get("bill_date") or "")) or _iso_or_none(raw.get("bill_date"))
    patient = " ".join(str(raw.get("patient") or "").split())
    total = parse_amount_paise(str(raw.get("total") or ""))
    ticket = canonical_ticket_display(str(raw.get("ticket") or ""))
    items = []
    for it in raw.get("line_items") or []:
        if isinstance(it, dict):
            amt = parse_amount_paise(str(it.get("amount") or ""))
            if amt is not None and str(it.get("description") or "").strip():
                items.append(LineItem(" ".join(str(it["description"]).split()), amt))

    values = {"issuer_id": issuer_id, "bill_no": bill_no, "bill_date": date_iso,
              "patient": patient, "total": total}
    missing = [k for k, v in values.items() if not v]
    unsure = [k for k in LOOKUP_FIELDS if k not in missing and conf.get(k, 1.0) < CONFIDENCE_FLOOR]
    if ticket and (conf.get("ticket", 1.0) < CONFIDENCE_FLOOR or not _TICKET_SHAPE.match(ticket)):
        unsure.append("ticket")
    if missing or unsure:
        labels = {"issuer_id": "issuer", "bill_no": "bill number", "bill_date": "date",
                  "patient": "patient name", "total": "total amount", "ticket": "ticket"}
        parts = []
        if missing:
            parts.append("could not find " + ", ".join(labels[m] for m in missing))
        if unsure:
            parts.append("not confident of " + ", ".join(labels[u] for u in unsure))
        partial = {**values, "ticket": ticket, "issuer_name": str(raw.get("issuer_name") or "")}
        return Reading(ReadStatus.COULD_NOT_READ, None,
                       "The vision model read the picture but " + "; ".join(parts) + ". A reading "
                       "that is not certain is never compared with the registry: please check and "
                       "complete the details.",
                       source="vision model", model=model, confidence=conf, language=language,
                       partial=partial)

    fields = BillFields(issuer_id=issuer_id, issuer_name=" ".join(str(raw.get("issuer_name") or "").split()),
                        bill_no=bill_no, bill_date=date_iso, patient=patient, items=items,
                        total_paise=total, ticket=ticket)
    return Reading(ReadStatus.OK, fields,
                   f"Read from a picture by the vision model ({language or 'language not stated'}). "
                   "Every field needed for the lookup was read with confidence.",
                   source="vision model", model=model, confidence=conf, language=language)


def _second_opinion(reading: Reading, image: bytes) -> None:
    """Let the local deep OCR read the same picture and say whether the two agree."""
    if not ocr.available():
        return
    other = ocr.read_fields(image)
    reading.second_reader = "deep OCR"
    if other is None:
        reading.agree = None          # it could not read the layout; no opinion either way
        return
    mine = reading.fields
    reading.agree = (canonical_ticket(mine.ticket) == canonical_ticket(other.ticket)
                     and canonical_bill_no(mine.bill_no) == canonical_bill_no(other.bill_no)
                     and mine.total_paise == other.total_paise)


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def canonical_ticket_display(ticket: str) -> str:
    """Upper-case with single hyphens, the way the bill prints it."""
    t = re.sub(r"[\s]", "", ticket or "").upper().replace("—", "-").replace("–", "-")
    return t


def _num(v) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


def _iso_or_none(v) -> str | None:
    s = str(v or "").strip()
    return s if re.match(r"^\d{4}-\d{2}-\d{2}$", s) else None


def form_values(reading: Reading) -> dict:
    """What the confirmation form shows: the fields read, or the partial read, or blanks."""
    f = reading.fields
    if f is not None:
        return {"issuer_id": f.issuer_id, "bill_no": f.bill_no, "bill_date": f.bill_date or "",
                "patient": f.patient, "total": f.total_paise, "ticket": f.ticket,
                "issuer_name": f.issuer_name}
    p = reading.partial or {}
    return {k: p.get(k) or "" for k in ("issuer_id", "bill_no", "bill_date", "patient", "ticket",
                                         "issuer_name")} | {"total": p.get("total") or 0}


def apply_corrections(reading: Reading, corrected: dict) -> Reading:
    """The customer's confirmed values replace the model's, and the changes are counted.

    `corrected` holds the same keys as the form: issuer_id, bill_no, bill_date, patient,
    total (rupees as text), ticket. A changed value is a correction; the count is what
    the risk head sees as the reader's error rate. A reading that was incomplete becomes
    usable once every lookup field is filled in.
    """
    before = form_values(reading)
    after = {
        "issuer_id": str(corrected.get("issuer_id", before["issuer_id"])).strip().upper(),
        "bill_no": canonical_bill_no(str(corrected.get("bill_no", before["bill_no"]))),
        "bill_date": (parse_date_iso(str(corrected.get("bill_date", before["bill_date"])))
                      or _iso_or_none(corrected.get("bill_date")) or before["bill_date"]),
        "patient": " ".join(str(corrected.get("patient", before["patient"])).split()),
        "total": (parse_amount_paise(str(corrected["total"])) if corrected.get("total") not in (None, "")
                  else before["total"]) or before["total"],
        "ticket": canonical_ticket_display(str(corrected.get("ticket", before["ticket"]))),
    }
    changed = sum(before[k] != after[k] for k in after)
    items = reading.fields.items if reading.fields else []
    fields = BillFields(after["issuer_id"], before["issuer_name"], after["bill_no"], after["bill_date"],
                        after["patient"], items, after["total"] or 0, after["ticket"])
    complete = all([fields.issuer_id, fields.bill_no, fields.bill_date, fields.patient, fields.total_paise])
    out = Reading(**{**reading.__dict__, "fields": fields if complete else None})
    out.customer_confirmed = True
    out.corrections = changed
    if complete:
        out.status = ReadStatus.OK
        if reading.status is not ReadStatus.OK:
            out.note = ("Read from a picture, completed and confirmed by the customer "
                        f"({changed} field{'s' if changed != 1 else ''} typed or corrected).")
    else:
        out.status = ReadStatus.COULD_NOT_READ
        out.note = "The details are still incomplete, so the bill was not compared with the registry."
    if changed:
        # A corrected reading is the customer's statement, not the model's, and the
        # second reader never saw the corrected values: it no longer corroborates.
        out.agree = False if out.second_reader else None
    return out
