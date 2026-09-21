"""
Synthetic hospital bills, rendered as PDFs.

Every hospital, patient and amount here is invented. The generator produces the
kinds of document the demo needs:

    render_genuine()     a bill exactly as the hospital's billing system issues it
    render_altered()     a genuine bill whose total was changed afterwards in a
                         PDF editor (the classic "edited" forgery)
    render_fabricated()  a bill typed from a blank page by a forger: clean fonts,
                         correct arithmetic, clean file history, and a ticket the
                         hospital never issued (the "made from nothing" forgery)
    render_scan()        a genuine bill that reached the insurer as a poor photo /
                         scan with no text layer (the reading-risk case)

All amounts are whole paise (integers) so every check is exact.
"""

from __future__ import annotations

import datetime as _dt
import io
import random

from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from .models import BillFields, LineItem

# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------


def format_inr(paise: int) -> str:
    """Format paise the Indian way: 18640000 -> '1,86,400.00'."""
    rupees, p = divmod(int(paise), 100)
    s = str(rupees)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups) + "," + tail
    return f"{s}.{p:02d}"


def display_date(iso: str) -> str:
    """'2026-03-03' -> '03-Mar-2026', the style printed on the bill."""
    return _dt.date.fromisoformat(iso).strftime("%d-%b-%Y")


def _pdf_date(when: _dt.datetime) -> str:
    """PDF metadata date format, e.g. D:20260303101500+05'30'."""
    return when.strftime("D:%Y%m%d%H%M%S+05'30'")


# --------------------------------------------------------------------------
# Drawing
# --------------------------------------------------------------------------

BODY_FONT = "Helvetica"
BOLD_FONT = "Helvetica-Bold"


def _draw_bill(c: canvas.Canvas, bill: BillFields, *, total_font: str = BOLD_FONT,
               joined: bool = True) -> None:
    """Draw one bill onto a reportlab canvas.

    `total_font` lets the altered-bill generator reproduce what a PDF editor
    typically does: the replaced amount comes out in a different font from the
    rest of the bill.
    """
    width, height = A4
    left = 20 * mm
    right = width - 20 * mm
    y = height - 25 * mm

    c.setFont(BOLD_FONT, 16)
    c.drawString(left, y, bill.issuer_name)
    y -= 6 * mm
    c.setFont(BODY_FONT, 9)
    c.drawString(left, y, f"Issuer ID: {bill.issuer_id}")
    y -= 12 * mm

    c.setFont(BOLD_FONT, 13)
    c.drawString(left, y, "FINAL IN-PATIENT BILL")
    y -= 9 * mm

    c.setFont(BODY_FONT, 10)
    for label, value in (
        ("Bill No", bill.bill_no),
        ("Bill Date", display_date(bill.bill_date)),
        ("Patient", bill.patient),
    ):
        c.drawString(left, y, f"{label}: {value}")
        y -= 6 * mm

    if joined and bill.ticket:
        # The one addition TrustLadder makes to the document: the random ticket.
        c.setFont(BOLD_FONT, 10)
        c.drawString(left, y, f"Ticket: {bill.ticket}")
        c.setFont(BODY_FONT, 7.5)
        c.drawString(left, y - 4 * mm, "This bill can be confirmed with the issuer through the TrustLadder registry.")
        y -= 10 * mm

    y -= 4 * mm
    c.setFont(BOLD_FONT, 10)
    c.drawString(left, y, "Description")
    c.drawRightString(right, y, "Amount (Rs)")
    y -= 2.5 * mm
    c.line(left, y, right, y)
    y -= 6 * mm

    c.setFont(BODY_FONT, 10)
    for item in bill.items:
        c.drawString(left, y, item.description)
        c.drawRightString(right, y, format_inr(item.amount_paise))
        y -= 6 * mm

    y -= 1 * mm
    c.line(left, y, right, y)
    y -= 7 * mm
    c.setFont(total_font, 11)
    c.drawString(left, y, "Grand Total:")
    c.drawRightString(right, y, f"Rs {format_inr(bill.total_paise)}")

    c.setFont(BODY_FONT, 7.5)
    c.drawString(left, 15 * mm, "Synthetic document generated for the IIM-V Capstone demo (Group 7). "
                                "Not a real bill.")


def _render(bill: BillFields, *, total_font: str, joined: bool) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    _draw_bill(c, bill, total_font=total_font, joined=joined)
    c.showPage()
    c.save()
    return buf.getvalue()


def _set_metadata(pdf_bytes: bytes, *, creator: str, producer: str,
                  created: _dt.datetime, modified: _dt.datetime) -> bytes:
    """Rewrite the document information dictionary in one clean save."""
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(pdf_bytes)))
    writer.add_metadata({
        "/Creator": creator,
        "/Producer": producer,
        "/CreationDate": _pdf_date(created),
        "/ModDate": _pdf_date(modified),
        "/Title": "Final In-Patient Bill",
    })
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def billing_software(issuer_name: str) -> str:
    """The (fictional) billing system each hospital's PDFs come from."""
    return f"{issuer_name.split()[0]} HIS 4.2"


# --------------------------------------------------------------------------
# The four document kinds
# --------------------------------------------------------------------------


def render_genuine(bill: BillFields, *, joined: bool,
                   issued_at: _dt.datetime | None = None) -> bytes:
    """A bill exactly as the hospital's billing system produces it."""
    issued_at = issued_at or _dt.datetime.fromisoformat(bill.bill_date + "T11:20:00")
    raw = _render(bill, total_font=BOLD_FONT, joined=joined)
    software = billing_software(bill.issuer_name)
    return _set_metadata(raw, creator=software, producer=software,
                         created=issued_at, modified=issued_at)


def render_altered(original: BillFields, new_total_paise: int, *, joined: bool,
                   issued_at: _dt.datetime | None = None) -> bytes:
    """A genuine bill whose Grand Total was changed afterwards in a PDF editor.

    What the edit leaves behind, as it typically does in practice:
      * the line items no longer add up to the new total,
      * the new amount is set in a different font,
      * the file was re-saved by a different program, days later, as an
        incremental update appended to the original file.
    """
    issued_at = issued_at or _dt.datetime.fromisoformat(original.bill_date + "T11:20:00")
    edited = BillFields(**{**original.__dict__, "total_paise": new_total_paise})
    raw = _render(edited, total_font="Times-Bold", joined=joined)
    software = billing_software(original.issuer_name)
    base = _set_metadata(raw, creator=software, producer=software,
                         created=issued_at, modified=issued_at)
    # The editor's save: appended as a second revision, not a clean rewrite.
    writer = PdfWriter(io.BytesIO(base), incremental=True)
    writer.add_metadata({
        "/Producer": "QuickEdit PDF 3.1",
        "/ModDate": _pdf_date(issued_at + _dt.timedelta(days=9, hours=3)),
    })
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def render_resaved(bill: BillFields, *, joined: bool) -> bytes:
    """A GENUINE bill that the customer re-saved through another program
    (e.g. a phone app that merges or compresses PDFs). Nothing on the page
    changes, but the file history now looks edited. Edit-detection tools tend to
    flag these, which is how genuine customers get held up.
    """
    issued_at = _dt.datetime.fromisoformat(bill.bill_date + "T11:20:00")
    base = render_genuine(bill, joined=joined, issued_at=issued_at)
    writer = PdfWriter(io.BytesIO(base), incremental=True)
    writer.add_metadata({
        "/Producer": "PhoneDocs Compressor 2.0",
        "/ModDate": _pdf_date(issued_at + _dt.timedelta(days=4)),
    })
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def render_fabricated(bill: BillFields, *, joined: bool) -> bytes:
    """A bill typed from a blank page.

    The forger copies the hospital's look and uses the billing system's name in
    the file's metadata, gets the arithmetic right, and prints a ticket in the
    right format. Every appearance check passes. Only the issuer can say it
    never issued this bill.
    """
    return render_genuine(bill, joined=joined)


def render_scan(bill: BillFields, *, joined: bool, seed: int = 7) -> bytes:
    """A genuine bill that arrived as a blurred phone photo with no text layer."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    rng = random.Random(seed)
    w, h = 1240, 1754                       # A4 at 150 dpi
    img = Image.new("L", (w, h), 236)
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=30)
        big = ImageFont.load_default(size=44)
    except TypeError:                       # very old Pillow
        font = big = ImageFont.load_default()
    y = 120
    d.text((110, y), bill.issuer_name, fill=40, font=big); y += 70
    d.text((110, y), f"Issuer ID: {bill.issuer_id}", fill=60, font=font); y += 90
    d.text((110, y), "FINAL IN-PATIENT BILL", fill=40, font=big); y += 80
    for line in (f"Bill No: {bill.bill_no}", f"Bill Date: {display_date(bill.bill_date)}",
                 f"Patient: {bill.patient}"):
        d.text((110, y), line, fill=50, font=font); y += 50
    if joined and bill.ticket:
        d.text((110, y), f"Ticket: {bill.ticket}", fill=50, font=font); y += 60
    y += 30
    for item in bill.items:
        d.text((110, y), item.description, fill=55, font=font)
        d.text((880, y), format_inr(item.amount_paise), fill=55, font=font); y += 48
    y += 20
    d.text((110, y), f"Grand Total:   Rs {format_inr(bill.total_paise)}", fill=45, font=big)
    # Phone-photo damage: slight rotation, blur, sensor noise, uneven light.
    img = img.rotate(rng.uniform(-2.5, 2.5), fillcolor=200, expand=False)
    img = img.filter(ImageFilter.GaussianBlur(2.2))
    noise = Image.effect_noise((w, h), 28)
    img = Image.blend(img, noise, 0.18)

    png = io.BytesIO()
    img.convert("RGB").save(png, format="JPEG", quality=45)
    png.seek(0)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    from reportlab.lib.utils import ImageReader
    c.drawImage(ImageReader(png), 0, 0, width=A4[0], height=A4[1])
    c.showPage()
    c.save()
    issued_at = _dt.datetime.fromisoformat(bill.bill_date + "T11:20:00")
    return _set_metadata(buf.getvalue(), creator="Phone Scanner", producer="Phone Scanner",
                         created=issued_at + _dt.timedelta(days=2),
                         modified=issued_at + _dt.timedelta(days=2))


# --------------------------------------------------------------------------
# Synthetic content
# --------------------------------------------------------------------------

FIRST_NAMES = ["Meera", "Arjun", "Kavya", "Rohan", "Sneha", "Vikram", "Ananya", "Farhan",
               "Lakshmi", "Sameer", "Divya", "Imran", "Pooja", "Nikhil", "Asha", "Rahul"]
LAST_NAMES = ["Kulkarni", "Iyer", "Deshpande", "Reddy", "Sharma", "Nair", "Patil",
              "Chatterjee", "Menon", "Qureshi", "Joshi", "Bose", "Gupta", "Pillai"]
CHARGES = [
    ("Room charges (semi-private)", 250000, 900000),
    ("Surgeon fee", 1500000, 6000000),
    ("Anaesthesia", 400000, 1500000),
    ("Operation theatre", 800000, 2500000),
    ("Pharmacy and consumables", 300000, 2200000),
    ("Laboratory investigations", 150000, 900000),
    ("Radiology", 200000, 1200000),
    ("Nursing care", 150000, 600000),
]


def random_bill(rng: random.Random, issuer_id: str, issuer_name: str, bill_prefix: str,
                serial: int, bill_date: str) -> BillFields:
    """Make one plausible bill with 4-6 charges, rounded to whole rupees."""
    picks = rng.sample(CHARGES, rng.randint(4, 6))
    items = [LineItem(desc, rng.randrange(lo, hi, 100)) for desc, lo, hi in picks]
    return BillFields(
        issuer_id=issuer_id,
        issuer_name=issuer_name,
        bill_no=f"{bill_prefix}/2026/{serial:06d}",
        bill_date=bill_date,
        patient=f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}",
        items=items,
        total_paise=sum(i.amount_paise for i in items),
    )
