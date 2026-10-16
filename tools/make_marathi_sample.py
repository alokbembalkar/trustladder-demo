"""
Make the Marathi sample bill: a photographed printout whose labels are in Marathi.

    .venv/bin/python tools/make_marathi_sample.py

Why this exists as a separate tool
----------------------------------
The demo's bills are drawn with reportlab, which cannot shape Devanagari (the vowel
signs and conjuncts come out wrong). A browser can. So this one bill is written as
HTML, printed to PDF by a headless Chrome on the development machine, photographed
by `bills.photo_of`, and the JPEG is committed under assets/samples. The seeder then
publishes the SAME fields to the registry under the hospital's key, so the photo is
a bill the hospital really issued and the vision model's reading can be confirmed.

The fields are fixed in `trustladder.cases.MARATHI_BILL`, and this tool prints from
them, so the picture and the registry can never drift apart. Names and codes stay in
Latin letters, which is how Indian hospital software prints them; the labels and
descriptions are Marathi.

Requires Google Chrome (the only reason this is a tool and not part of the seeder).
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trustladder.bills import display_date, format_inr, photo_of  # noqa: E402
from trustladder.cases import MARATHI_BILL, MARATHI_LABELS  # noqa: E402

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
OUT = Path(__file__).resolve().parent.parent / "assets" / "samples" / "marathi_bill_photo.jpg"

HTML = """<!doctype html><html lang="mr"><meta charset="utf-8">
<style>
  @page {{ size: A4; margin: 22mm 20mm; }}
  body {{ font-family: 'Noto Sans Devanagari','Kohinoor Devanagari','Devanagari Sangam MN',
          'Helvetica Neue', Arial, sans-serif; color:#111; font-size: 13px; }}
  h1 {{ font-size: 22px; margin: 0 0 2px; }}
  .id {{ font-size: 10px; color: #555; margin-bottom: 26px; }}
  h2 {{ font-size: 16px; margin: 0 0 12px; }}
  .f {{ margin: 0 0 5px; }}
  .t {{ font-weight: 700; margin-top: 4px; }}
  .n {{ font-size: 9.5px; color:#444; margin: 2px 0 18px; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
  th {{ text-align:left; border-bottom: 1px solid #333; padding: 4px 0; }}
  th.r, td.r {{ text-align: right; }}
  td {{ padding: 5px 0; }}
  tr.sum td {{ border-top: 1px solid #333; font-weight: 700; padding-top: 8px; }}
  .foot {{ position: fixed; bottom: 0; font-size: 9px; color:#555; }}
</style>
<body>
<h1>{issuer_name}</h1>
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


def main() -> None:
    b = MARATHI_BILL
    rows = "".join(f'<tr><td>{d}</td><td class="r">{format_inr(a)}</td></tr>'
                   for d, a in zip(MARATHI_LABELS["items"], [i.amount_paise for i in b.items]))
    html = HTML.format(L=MARATHI_LABELS, issuer_name=MARATHI_LABELS["issuer_name"], issuer_id=b.issuer_id,
                       bill_no=b.bill_no, bill_date=display_date(b.bill_date), patient=b.patient,
                       ticket=b.ticket, rows=rows, total=format_inr(b.total_paise))
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "bill.html"
        pdf = Path(tmp) / "bill.pdf"
        src.write_text(html, encoding="utf-8")
        subprocess.run([CHROME, "--headless", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
                        f"--print-to-pdf={pdf}", src.as_uri()], check=True, capture_output=True)
        assert pdf.read_bytes()[:4] == b"%PDF", "Chrome did not produce a PDF"
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_bytes(photo_of(pdf.read_bytes(), seed=21))
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
