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
from .models import KIND_BILL, KIND_SUMMARY, BillFields, DischargeFields, LineItem, ReadStatus
from .reader import (canonical_bill_no, canonical_ticket, is_summary_text, parse_amount_paise,
                     parse_date_iso, pdf_text, read_bill, read_summary)

IMAGE_TYPES = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}

# What the lookup needs. The confidence gate applies to exactly these.
LOOKUP_FIELDS = ("issuer_id", "bill_no", "bill_date", "patient", "total")
SUMMARY_LOOKUP_FIELDS = ("issuer_id", "summary_no", "patient", "admitted", "discharged")
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
    kind: str = KIND_BILL
    """Which document this is: a bill or a discharge summary. Decided by the text layer
    for a PDF and by the vision model for a picture."""

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
        text = pdf_text(data)
        if is_summary_text(text):
            status, fields, note = read_summary(data)
            return Reading(status, fields, note, source="text layer", kind=KIND_SUMMARY)
        status, fields, note = read_bill(data)
        if status is ReadStatus.OK or len(text.strip()) >= 20:
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
    "You are reading a photograph or scan of a hospital document for an insurance claim: either a "
    "hospital BILL or a DISCHARGE SUMMARY. The document may be printed in any language; read it as "
    "printed and do not translate names. Return ONLY a JSON object with these keys:\n"
    "  document_type: \"bill\" or \"discharge summary\"\n"
    "  language_seen: the language of the printed text, in English (e.g. English, Marathi, Hindi)\n"
    "  issuer_name: the hospital's name exactly as printed\n"
    "  issuer_id: the issuer ID code printed on the document (letters, digits and hyphens)\n"
    "  patient: the patient's name exactly as printed\n"
    "  ticket: the TrustLadder ticket printed on the document, character by character, or \"\" if none\n"
    "  For a bill: bill_no (exactly as printed), bill_date (YYYY-MM-DD), line_items (a list of "
    "{description, amount} with amount as a number), total (the grand total as a number)\n"
    "  For a discharge summary: summary_no (the summary or document number), uhid, admitted "
    "(date of admission, YYYY-MM-DD), discharged (date of discharge, YYYY-MM-DD), diagnosis, "
    "procedure (the operation performed, or the word none if the summary says no operation was "
    "performed, or \"\" if that line is not visible), consultant\n"
    "  confidence: an object with a number from 0 to 1 for each of issuer_id, patient, ticket and, "
    "for a bill, bill_no, bill_date, total; for a summary, summary_no, admitted, discharged: 1 means "
    "every character is clearly legible\n"
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
        if isinstance(fields, DischargeFields):
            partial = {"issuer_id": fields.issuer_id, "summary_no": fields.summary_no, "patient": fields.patient,
                       "admitted": fields.admitted, "discharged": fields.discharged, "ticket": fields.ticket,
                       "uhid": fields.uhid, "diagnosis": "", "procedure": "", "consultant": "",
                       "issuer_name": fields.issuer_name}
            blanks = [k for k in SUMMARY_LOOKUP_FIELDS if not partial.get(k)]
            if not blanks:
                return Reading(ReadStatus.OK, fields, "Read by the local deep OCR model; " + why + ".",
                               source="vision model", model=ocr.MODEL_NAME, kind=KIND_SUMMARY)
            return Reading(ReadStatus.COULD_NOT_READ, None,
                           f"The local OCR model read the picture ({why}) but could not read every "
                           "field. Please complete the details before submitting.",
                           source="vision model", model=ocr.MODEL_NAME, partial=partial, kind=KIND_SUMMARY)
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
    """Normalise the model's JSON into BillFields or DischargeFields, with the confidence gate."""
    conf = {k: _num(v) for k, v in (raw.get("confidence") or {}).items()}
    language = str(raw.get("language_seen") or "").strip()
    if "summary" in str(raw.get("document_type") or "").lower():
        return _to_summary_reading(raw, model, conf, language)

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


def _to_summary_reading(raw: dict, model: str, conf: dict, language: str) -> Reading:
    """The discharge-summary half of `_to_reading`."""
    issuer_id = str(raw.get("issuer_id") or "").strip().upper()
    summary_no = canonical_bill_no(str(raw.get("summary_no") or ""))
    patient = " ".join(str(raw.get("patient") or "").split())
    admitted = parse_date_iso(str(raw.get("admitted") or "")) or _iso_or_none(raw.get("admitted"))
    discharged = parse_date_iso(str(raw.get("discharged") or "")) or _iso_or_none(raw.get("discharged"))
    ticket = canonical_ticket_display(str(raw.get("ticket") or ""))
    values = {"issuer_id": issuer_id, "summary_no": summary_no, "patient": patient,
              "admitted": admitted, "discharged": discharged}
    missing = [k for k, v in values.items() if not v]
    unsure = [k for k in SUMMARY_LOOKUP_FIELDS if k not in missing and conf.get(k, 1.0) < CONFIDENCE_FLOOR]
    if ticket and (conf.get("ticket", 1.0) < CONFIDENCE_FLOOR or not _TICKET_SHAPE.match(ticket)):
        unsure.append("ticket")
    extras = {"uhid": str(raw.get("uhid") or "").strip(), "diagnosis": " ".join(str(raw.get("diagnosis") or "").split()),
              "procedure": " ".join(str(raw.get("procedure") or "").split()),
              "consultant": " ".join(str(raw.get("consultant") or "").split()),
              "issuer_name": " ".join(str(raw.get("issuer_name") or "").split())}
    if missing or unsure:
        labels = {"issuer_id": "issuer", "summary_no": "summary number", "patient": "patient name",
                  "admitted": "admission date", "discharged": "discharge date", "ticket": "ticket"}
        parts = []
        if missing:
            parts.append("could not find " + ", ".join(labels[m] for m in missing))
        if unsure:
            parts.append("not confident of " + ", ".join(labels[u] for u in unsure))
        return Reading(ReadStatus.COULD_NOT_READ, None,
                       "The vision model read the picture but " + "; ".join(parts) + ". A reading "
                       "that is not certain is never compared with the registry: please check and "
                       "complete the details.",
                       source="vision model", model=model, confidence=conf, language=language,
                       partial={**values, "ticket": ticket, **extras}, kind=KIND_SUMMARY)
    fields = DischargeFields(issuer_id, extras["issuer_name"], summary_no, extras["uhid"], patient,
                             admitted, discharged, extras["diagnosis"], extras["procedure"],
                             extras["consultant"], ticket)
    return Reading(ReadStatus.OK, fields,
                   f"Read from a picture by the vision model ({language or 'language not stated'}). "
                   "Every field needed for the lookup was read with confidence.",
                   source="vision model", model=model, confidence=conf, language=language,
                   kind=KIND_SUMMARY)


def _second_opinion(reading: Reading, image: bytes) -> None:
    """Let the local deep OCR read the same picture and say whether the two agree."""
    if not ocr.available():
        return
    if reading.kind == KIND_SUMMARY:
        other = ocr.read_fields(image)
        reading.second_reader = "deep OCR"
        if not isinstance(other, DischargeFields):
            reading.agree = None
            return
        mine = reading.fields
        reading.agree = (canonical_ticket(mine.ticket) == canonical_ticket(other.ticket)
                         and canonical_bill_no(mine.summary_no) == canonical_bill_no(other.summary_no)
                         and mine.admitted == other.admitted and mine.discharged == other.discharged)
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
    if reading.kind == KIND_SUMMARY:
        if f is not None:
            return {"issuer_id": f.issuer_id, "summary_no": f.summary_no, "patient": f.patient,
                    "admitted": f.admitted, "discharged": f.discharged, "ticket": f.ticket,
                    "uhid": f.uhid, "diagnosis": f.diagnosis, "procedure": f.procedure,
                    "consultant": f.consultant, "issuer_name": f.issuer_name}
        p = reading.partial or {}
        return {k: p.get(k) or "" for k in ("issuer_id", "summary_no", "patient", "admitted", "discharged",
                                             "ticket", "uhid", "diagnosis", "procedure", "consultant",
                                             "issuer_name")}
    if f is not None:
        return {"issuer_id": f.issuer_id, "bill_no": f.bill_no, "bill_date": f.bill_date or "",
                "patient": f.patient, "total": f.total_paise, "ticket": f.ticket,
                "issuer_name": f.issuer_name}
    p = reading.partial or {}
    return {k: p.get(k) or "" for k in ("issuer_id", "bill_no", "bill_date", "patient", "ticket",
                                         "issuer_name")} | {"total": p.get("total") or 0}


def apply_summary_corrections(reading: Reading, corrected: dict) -> Reading:
    """`apply_corrections` for a discharge summary: the dates and the identity are the lookup."""
    before = form_values(reading)
    after = dict(before)
    for k in ("issuer_id", "summary_no", "patient", "admitted", "discharged", "ticket", "uhid",
              "diagnosis", "procedure", "consultant"):
        if k in corrected:
            v = str(corrected[k])
            if k in ("admitted", "discharged"):
                v = parse_date_iso(v) or _iso_or_none(v) or ""
            elif k == "issuer_id":
                v = v.strip().upper()
            elif k == "summary_no":
                v = canonical_bill_no(v)
            elif k == "ticket":
                v = canonical_ticket_display(v)
            else:
                v = " ".join(v.split())
            after[k] = v
    changed = sum(before[k] != after[k] for k in ("issuer_id", "summary_no", "patient", "admitted",
                                                   "discharged", "ticket"))
    complete = all(after[k] for k in SUMMARY_LOOKUP_FIELDS)
    fields = DischargeFields(after["issuer_id"], after["issuer_name"], after["summary_no"], after["uhid"],
                             after["patient"], after["admitted"], after["discharged"], after["diagnosis"],
                             after["procedure"], after["consultant"], after["ticket"]) if complete else None
    out = Reading(**{**reading.__dict__, "fields": fields})
    out.customer_confirmed = True
    out.corrections = changed
    if complete:
        out.status = ReadStatus.OK
        if reading.status is not ReadStatus.OK:
            out.note = ("Read from a picture, completed and confirmed by the customer "
                        f"({changed} field{'s' if changed != 1 else ''} typed or corrected).")
    else:
        out.status = ReadStatus.COULD_NOT_READ
        out.note = "The details are still incomplete, so the summary was not compared with the registry."
    if changed:
        out.agree = False if out.second_reader else None
    return out


def apply_corrections(reading: Reading, corrected: dict) -> Reading:
    """The customer's confirmed values replace the model's, and the changes are counted.

    Dispatches to `apply_summary_corrections` for a discharge summary.

    `corrected` holds the same keys as the form: issuer_id, bill_no, bill_date, patient,
    total (rupees as text), ticket. A changed value is a correction; the count is what
    the risk head sees as the reader's error rate. A reading that was incomplete becomes
    usable once every lookup field is filled in.
    """
    if reading.kind == KIND_SUMMARY:
        return apply_summary_corrections(reading, corrected)
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
