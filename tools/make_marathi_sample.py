"""
Make the Marathi sample documents: the photographed printout, and the born-digital set.

    .venv/bin/python tools/make_marathi_sample.py

What it writes, under assets/samples
------------------------------------
    marathi_bill_photo.jpg          a phone photo of the Marathi printout (labels Marathi
                                    only): the vision model's case, read only when the
                                    hosted model is on
    marathi_bill.pdf                the SAME bill as the hospital's system prints it in
                                    Marathi: a PDF with a text layer, bilingual labels
    marathi_bill_total_raised.pdf   that PDF with its total raised afterwards in an editor
    marathi_discharge_summary.pdf   the discharge summary for that stay, bilingual
    marathi_bill_total_raised_photo.jpg   the raised copy photographed on a phone
    marathi_discharge_summary_scan.jpg    the summary as a scan

Why this exists as a separate tool
----------------------------------
The demo's bills are drawn with reportlab, which cannot shape Devanagari (the vowel
signs and conjuncts come out wrong). A browser can. So these documents are written as
HTML and printed to PDF by a headless Chrome on the development machine, and the
results are committed under assets/samples. The seeder publishes the SAME fields to the
registry under the hospital's key, so every one of them is a document the hospital
really issued (or, for the raised copy, really issued and somebody then changed).

The fields are fixed in `trustladder.cases` (MARATHI_BILL, SUMMARY_FOR_MARATHI), and
this tool prints from them, so the files and the registry can never drift apart.
Names and codes stay in Latin letters, which is how Indian hospital software prints
them; the labels and descriptions are Marathi.

Bilingual labels
----------------
The born-digital PDFs carry each field name in Marathi AND English ("बिल क्रमांक /
Bill No:"), the way hospital software in Maharashtra prints it. That is also what lets
the text-layer reader, which finds fields by their English label, read them without
any model: a bilingual PDF is read exactly, with the models on or off. The photo has
Marathi labels only, and needs the vision model.

Requires Google Chrome (the only reason this is a tool and not part of the seeder).
"""

from __future__ import annotations

import datetime as _dt
import io
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pypdf import PdfWriter  # noqa: E402

from trustladder.bills import (_pdf_date, _set_metadata, billing_software, display_date, format_inr,  # noqa: E402
                               photo_of)
from trustladder.cases import MARATHI_BILL, MARATHI_LABELS, SUMMARY_FOR_MARATHI  # noqa: E402
from trustladder.models import BillFields  # noqa: E402

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
OUT_DIR = Path(__file__).resolve().parent.parent / "assets" / "samples"
PHOTO = OUT_DIR / "marathi_bill_photo.jpg"
BILL_PDF = OUT_DIR / "marathi_bill.pdf"
RAISED_PDF = OUT_DIR / "marathi_bill_total_raised.pdf"
SUMMARY_PDF = OUT_DIR / "marathi_discharge_summary.pdf"
RAISED_PHOTO = OUT_DIR / "marathi_bill_total_raised_photo.jpg"     # the forged copy, photographed
SUMMARY_SCAN = OUT_DIR / "marathi_discharge_summary_scan.jpg"      # the summary, scanned

# How much the forger adds to the Marathi bill's total (Rs 1,03,650 -> Rs 1,53,650).
RAISE_PAISE = 5_000_000

STYLE = """
  @page {{ size: A4; margin: 22mm 20mm; }}
  body {{ font-family: 'Noto Sans Devanagari','Kohinoor Devanagari','Devanagari Sangam MN',
          'Helvetica Neue', Arial, sans-serif; color:#111; font-size: 13px; }}
  h1 {{ font-size: 22px; margin: 0 0 2px; }}
  .mr {{ font-size: 15px; color:#222; margin: 0 0 2px; }}
  .id {{ font-size: 10px; color: #555; margin-bottom: 26px; }}
  h2 {{ font-size: 16px; margin: 0 0 12px; }}
  .f {{ margin: 0 0 5px; }}
  .t {{ font-weight: 700; margin-top: 4px; }}
  .n {{ font-size: 9.5px; color:#444; margin: 2px 0 18px; }}
  .sec {{ font-weight: 700; margin: 12px 0 3px; }}
  .body {{ margin: 0 0 4px 14px; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
  th {{ text-align:left; border-bottom: 1px solid #333; padding: 4px 0; }}
  th.r, td.r {{ text-align: right; }}
  td {{ padding: 5px 0; }}
  tr.sum td {{ border-top: 1px solid #333; font-weight: 700; padding-top: 8px; }}
  .edited {{ font-family: 'Times New Roman', Times, serif; }}
  hr {{ border: 0; border-top: 1px solid #333; margin: 14px 0; }}
  .foot {{ position: fixed; bottom: 0; font-size: 9px; color:#555; }}
"""

# The photographed printout: Marathi labels only (the vision model's case).
PHOTO_HTML = """<!doctype html><html lang="mr"><meta charset="utf-8">
<style>{style}</style>
<body>
<h1>{issuer_name_mr}</h1>
<div class="id">{L[issuer_id]}: {issuer_id}</div>
<h2>{L[title]}</h2>
<div class="f">{L[bill_no]}: {bill_no}</div>
<div class="f">{L[bill_date]}: {bill_date}</div>
<div class="f">{L[patient]}: {patient}</div>
<div class="t">{L[ticket]}: {ticket}</div>
<div class="n">{L[ticket_note]}</div>
<table>
<tr><th>{L[description]}</th><th class="r">{L[amount]}</th></tr>
{rows}
<tr class="sum"><td>{L[total]}</td><td class="r">Rs {total}</td></tr>
</table>
<div class="foot">{L[footer]}</div>
</body></html>"""

# The system PDF: the hospital's name in English first (it is the first line of text, and
# the line the reader shows as the issuer), then in Marathi; every label bilingual.
BILL_HTML = """<!doctype html><html lang="mr"><meta charset="utf-8">
<style>{style}</style>
<body>
<h1>{issuer_name}</h1>
<div class="mr">{issuer_name_mr}</div>
<div class="id">{L[issuer_id]} / Issuer ID: {issuer_id}</div>
<h2>{L[title]} / FINAL IN-PATIENT BILL</h2>
<div class="f">{L[bill_no]} / Bill No: {bill_no}</div>
<div class="f">{L[bill_date]} / Bill Date: {bill_date}</div>
<div class="f">{L[patient]} / Patient: {patient}</div>
<div class="t">{L[ticket]} / Ticket: {ticket}</div>
<div class="n">{L[ticket_note]} This bill can be confirmed with the hospital through the TrustLadder registry.</div>
<table>
<tr><th>{L[description]} / Description</th><th class="r">{L[amount]} / Amount (Rs)</th></tr>
{rows}
<tr class="sum"><td>{L[total]} / Grand Total:</td><td class="r {total_class}">Rs {total}</td></tr>
</table>
<div class="foot">{L[footer]} Synthetic document for the IIM-V Capstone demo (Group 7). Not a real bill.</div>
</body></html>"""

SUMMARY_HTML = """<!doctype html><html lang="mr"><meta charset="utf-8">
<style>{style}</style>
<body>
<h1>{issuer_name}</h1>
<div class="mr">{issuer_name_mr}</div>
<div class="id">{L[issuer_id]} / Issuer ID: {issuer_id}</div>
<h2>{L[summary_title]} / DISCHARGE SUMMARY</h2>
<div class="f">{L[summary_no]} / Summary No: {summary_no}</div>
<div class="f">UHID: {uhid}</div>
<div class="f">{L[patient]} / Patient: {patient}</div>
<div class="f">{L[admitted]} / Date of Admission: {admitted}</div>
<div class="f">{L[discharged]} / Date of Discharge: {discharged}</div>
<div class="f">{L[consultant]} / Consultant: {consultant}</div>
<div class="t">{L[ticket]} / Ticket: {ticket}</div>
<div class="n">{L[summary_note]} This summary can be confirmed with the issuer through the TrustLadder registry.</div>
<hr>
<div class="sec">{L[diagnosis]} / Diagnosis:</div><div class="body">{diagnosis}</div>
<div class="sec">{L[operation]} / Operation:</div><div class="body">{operation}</div>
<div class="sec">{L[course]} / Course in hospital:</div>
<div class="body">Intravenous antibiotics and fluids; fever settled by the second day. Vitals stable throughout.</div>
<div class="sec">{L[condition]} / Condition on discharge:</div>
<div class="body">Satisfactory. Afebrile; tolerating oral diet and medication.</div>
<div class="sec">{L[advice]} / Advice on discharge:</div>
<div class="body">Oral antibiotics as prescribed for seven days. Plenty of fluids. Review in the outpatient
department in one week. Report to casualty in case of fever, flank pain or vomiting.</div>
<div class="foot">{L[footer]} Synthetic document for the IIM-V Capstone demo (Group 7). Not a real summary; no real patient or doctor.</div>
</body></html>"""


def chrome_pdf(html: str) -> bytes:
    """Print one HTML page to PDF with the installed Chrome (shapes Devanagari properly)."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "doc.html"
        pdf = Path(tmp) / "doc.pdf"
        src.write_text(html, encoding="utf-8")
        subprocess.run([CHROME, "--headless", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
                        f"--print-to-pdf={pdf}", src.as_uri()], check=True, capture_output=True)
        data = pdf.read_bytes()
    assert data[:4] == b"%PDF", "Chrome did not produce a PDF"
    return data


def _rows(bill: BillFields) -> str:
    return "".join(f'<tr><td>{d}</td><td class="r">{format_inr(i.amount_paise)}</td></tr>'
                   for d, i in zip(MARATHI_LABELS["items"], bill.items))


def render_photo(bill: BillFields) -> bytes:
    html = PHOTO_HTML.format(style=STYLE, L=MARATHI_LABELS, issuer_name_mr=MARATHI_LABELS["issuer_name"],
                             issuer_id=bill.issuer_id, bill_no=bill.bill_no, bill_date=display_date(bill.bill_date),
                             patient=bill.patient, ticket=bill.ticket, rows=_rows(bill),
                             total=format_inr(bill.total_paise))
    return photo_of(chrome_pdf(html), seed=21)


def render_bill_pdf(bill: BillFields, *, total_paise: int | None = None, edited: bool = False) -> bytes:
    """The hospital's own Marathi PDF; with `edited`, the total is set in another typeface,
    the way a PDF editor leaves it."""
    total = bill.total_paise if total_paise is None else total_paise
    html = BILL_HTML.format(style=STYLE, L=MARATHI_LABELS, issuer_name=bill.issuer_name,
                            issuer_name_mr=MARATHI_LABELS["issuer_name"], issuer_id=bill.issuer_id,
                            bill_no=bill.bill_no, bill_date=display_date(bill.bill_date), patient=bill.patient,
                            ticket=bill.ticket, rows=_rows(bill), total=format_inr(total),
                            total_class="edited" if edited else "")
    return chrome_pdf(html)


def render_summary_pdf(s) -> bytes:
    html = SUMMARY_HTML.format(style=STYLE, L=MARATHI_LABELS, issuer_name=s.issuer_name,
                               issuer_name_mr=MARATHI_LABELS["issuer_name"], issuer_id=s.issuer_id,
                               summary_no=s.summary_no, uhid=s.uhid, patient=s.patient,
                               admitted=display_date(s.admitted), discharged=display_date(s.discharged),
                               consultant=s.consultant, ticket=s.ticket, diagnosis=s.diagnosis,
                               operation="None" if s.procedure.strip().lower() in ("", "none") else s.procedure)
    return chrome_pdf(html)


def main() -> None:
    b = MARATHI_BILL
    s = SUMMARY_FOR_MARATHI
    assert s.ticket, "SUMMARY_FOR_MARATHI needs its fixed ticket (see cases.py)"
    issued_at = _dt.datetime.fromisoformat(b.bill_date + "T12:40:00")
    software = billing_software(b.issuer_name)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. The photographed printout.
    PHOTO.write_bytes(render_photo(b))

    # 2. The system PDF, with the hospital software's clean file history.
    genuine = _set_metadata(render_bill_pdf(b), creator=software, producer=software,
                            created=issued_at, modified=issued_at)
    BILL_PDF.write_bytes(genuine)

    # 3. The same PDF, total raised afterwards: a different typeface on the new amount, and
    #    the editor's save appended as a second revision by a different program, days later
    #    (the same traces `bills.render_altered` leaves on the English edited bill).
    raised = _set_metadata(render_bill_pdf(b, total_paise=b.total_paise + RAISE_PAISE, edited=True),
                           creator=software, producer=software, created=issued_at, modified=issued_at)
    writer = PdfWriter(io.BytesIO(raised), incremental=True)
    writer.add_metadata({"/Producer": "QuickEdit PDF 3.1",
                         "/ModDate": _pdf_date(issued_at + _dt.timedelta(days=9, hours=3))})
    out = io.BytesIO()
    writer.write(out)
    RAISED_PDF.write_bytes(out.getvalue())

    # 4. The discharge summary for that stay.
    summary_at = _dt.datetime.fromisoformat(s.discharged + "T12:45:00")
    SUMMARY_PDF.write_bytes(_set_metadata(render_summary_pdf(s), creator=software, producer=software,
                                          created=summary_at, modified=summary_at))
    # 5. The pictures of the forged copy and of the summary: the vision model's Marathi case
    #    where the issuer's record disagrees, so corroboration (rule R7) can be shown.
    RAISED_PHOTO.write_bytes(photo_of(RAISED_PDF.read_bytes(), seed=23))
    SUMMARY_SCAN.write_bytes(photo_of(SUMMARY_PDF.read_bytes(), seed=24, quality=70))
    for p in (PHOTO, BILL_PDF, RAISED_PDF, SUMMARY_PDF, RAISED_PHOTO, SUMMARY_SCAN):
        print(f"wrote {p.relative_to(OUT_DIR.parent.parent)} ({p.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
