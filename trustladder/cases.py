"""
Builds the demo world.

    python -m trustladder.cases          # (re)build everything under ./data

1. The showcase: three fictional hospitals (two have joined the registry, one has
   not) and six bills, each chosen to show one principle of the rule.

2. The simulated month: a batch of synthetic claims run through the REAL ladder
   at three levels of registry coverage (how many hospitals have joined). The
   results are saved as JSON so the app can apply different institutional
   policies instantly. The batch mix (fraud share, scans, re-saved genuine
   files) is a stated assumption of this demo, not a statistic.

Everything is synthetic and deterministic (fixed random seeds).
"""

from __future__ import annotations

import json
import os
import random
import shutil
from pathlib import Path

from .bills import (random_bill, render_altered, render_discount_resaved, render_fabricated,
                    render_genuine, render_resaved, render_scan)
from .models import BillFields, LineItem
from .pipeline import run_ladder
from .registry import Publisher, RegistryStore, Verifier

ROOT = Path(__file__).resolve().parent.parent
# The data folder can be redirected (the test suite builds a private copy).
DATA_DIR = Path(os.environ.get("TRUSTLADDER_DATA", ROOT / "data"))

# --------------------------------------------------------------------------
# The fictional hospitals
# --------------------------------------------------------------------------

HOSPITALS = {
    "IN-HOSP-SAHYOG-PUN-0101": ("Sahyog Multispeciality Hospital", "Pune", "SH", "SMH"),
    "IN-HOSP-AROGYA-NSK-0233": ("Arogya Nursing Home", "Nashik", "AR", "ANH"),
    "IN-HOSP-SHANTI-STR-0419": ("Shanti Clinic and Maternity Home", "Satara", "SC", "SCM"),
    "IN-HOSP-KAVERI-KOP-0577": ("Kaveri Hospital", "Kolhapur", "KV", "KVH"),
    "IN-HOSP-NIRAMAY-SGL-0612": ("Niramay Surgical Centre", "Sangli", "NR", "NSC"),
}
# In the showcase only the first two have joined.
SHOWCASE_JOINED = ["IN-HOSP-SAHYOG-PUN-0101", "IN-HOSP-AROGYA-NSK-0233"]

# The seven prepared bills are ONE coherent set: the same patient throughout, and every
# file is explicitly related to another, so a viewer can see what changed and why.
SHOWCASE = [
    # (file, title, what it shows, expected verdict)
    ("01_genuine_bill.pdf", "Genuine bill (hospital has joined)",
     "The bill as the hospital issued it. Proof clears: the hospital confirms every field.", "Authentic"),
    ("02_same_bill_total_raised.pdf", "The SAME bill, total raised afterwards",
     "Bill 1 with its total edited. Same bill number, so the hospital's record contradicts it, and the "
     "file itself shows the edit.", "Tampered"),
    ("03_made_from_nothing.pdf", "Made from nothing (hospital has joined)",
     "Same patient and hospital, but a bill the hospital never issued. It passes every appearance "
     "check, and it is still caught outright: the hospital has published a code for every bill it "
     "issued in this period, and this ticket is not among them.", "Tampered"),
    ("04_genuine_hospital_not_joined.pdf", "Genuine bill from a hospital that has not joined",
     "The same patient at a hospital outside the registry. Nothing can be proven, so it is referred, "
     "never cleared.", "Inconclusive"),
    ("05_photo_of_bill_1.pdf", "A blurred photo of bill 1",
     "The same genuine bill, sent as a phone photo. Unreadable is not wrong: it is never called a "
     "mismatch.", "Inconclusive"),
    ("06_made_from_nothing_not_joined.pdf", "Made from nothing, hospital not joined",
     "The honest gap: with no issuer to ask, a clean fake cannot be told from a genuine bill. It is "
     "still never cleared.", "Inconclusive"),
    ("07_genuine_discounted_and_resaved.pdf", "GENUINE, discounted, then re-saved by a phone app",
     "Bill 4 with a discount the hospital gave, in a file the patient's phone app re-saved. Nothing "
     "was tampered with, but it carries both signatures an edit leaves: the total no longer matches "
     "the printed charges, and the file was written twice. Edit-detection tools call this tampered "
     "and hold an honest customer. The trained screening model recognises an ordinary re-save, so "
     "the file history is shown and not counted, and it goes to a person instead of being held.",
     "Suspicious"),
]

# The patient on the prepared bills. The seeder passes the demo policyholder's name so
# that the sample bills, the ledger and the seeded claims all agree.
SHOWCASE_PATIENT = "Meera Kulkarni"

# The Marathi bill: a printout whose labels are in Marathi, photographed by the patient.
# Its fields are fixed here because two things are made from them and must agree: the
# picture (tools/make_marathi_sample.py, rendered by a browser because reportlab cannot
# shape Devanagari) and the registry entry the seeder publishes under the hospital's
# key. Names and codes are in Latin letters, as Indian hospital software prints them.
MARATHI_BILL = BillFields(
    "IN-HOSP-SAHYOG-PUN-0101", "Sahyog Multispeciality Hospital", "SMH/2026/004877", "2026-03-05",
    SHOWCASE_PATIENT,
    [LineItem("Room charges (semi-private)", 2400000), LineItem("Surgeon fee", 5500000),
     LineItem("Pharmacy and consumables", 1825000), LineItem("Laboratory investigations", 640000)],
    10365000, "SH-WTKU-SZUF-7M7G-ARRC")
# The discharge summaries in the sample pack. One belongs to bill 1 and is confirmed by the
# hospital. The other is the mismatched-dates case: a genuine-looking summary from the
# hospital that has not joined, whose stay does not contain the date of bill 4. Nothing
# inside either document is wrong on its own; only the packet shows it.
from .models import DischargeFields  # noqa: E402

SUMMARY_FOR_BILL_1 = DischargeFields(
    "IN-HOSP-SAHYOG-PUN-0101", "Sahyog Multispeciality Hospital", "SMH/DS/2026/001184", "SMH-UH-204117",
    SHOWCASE_PATIENT, "2026-02-27", "2026-03-03", "Acute calculous cholecystitis",
    "Laparoscopic cholecystectomy under general anaesthesia on 28-Feb-2026", "Dr. A. Deshmukh, MS")
# The rest of the pack's summaries. A claim needs both documents, so every bill in the
# pack has the summary that belongs with it: the stay contains the bill's date.
SUMMARY_FOR_MARATHI = DischargeFields(
    "IN-HOSP-SAHYOG-PUN-0101", "Sahyog Multispeciality Hospital", "SMH/DS/2026/001209", "SMH-UH-204331",
    SHOWCASE_PATIENT, "2026-03-02", "2026-03-05", "Acute pyelonephritis", "none", "Dr. S. Phadke, MD")
SUMMARY_FAKE_SAHYOG = DischargeFields(          # typed by the forger to go with the fake bill
    "IN-HOSP-SAHYOG-PUN-0101", "Sahyog Multispeciality Hospital", "SMH/DS/2026/001260", "SMH-UH-204502",
    SHOWCASE_PATIENT, "2026-03-08", "2026-03-11", "Acute appendicitis",
    "Laparoscopic appendicectomy under general anaesthesia on 09-Mar-2026", "Dr. A. Deshmukh, MS")
SUMMARY_FOR_SHANTI = DischargeFields(           # matches bill 04 of the showcase (dated 7 March)
    "IN-HOSP-SHANTI-STR-0419", "Shanti Clinic and Maternity Home", "SCM/DS/2026/000398", "SCM-UH-7702",
    SHOWCASE_PATIENT, "2026-03-05", "2026-03-07", "Lower-segment caesarean section, elective",
    "Lower-segment caesarean section under spinal anaesthesia on 05-Mar-2026", "Dr. P. Jagtap, MD")
SUMMARY_FOR_AROGYA = DischargeFields(           # matches the Arogya bill Meera holds (dated 8 March)
    "IN-HOSP-AROGYA-NSK-0233", "Arogya Nursing Home", "ANH/DS/2026/000871", "ANH-UH-5519",
    SHOWCASE_PATIENT, "2026-03-06", "2026-03-08", "Dengue fever with thrombocytopenia", "none", "Dr. N. Pawar, MD")
SUMMARY_FAKE_SHANTI = DischargeFields(          # the forger's summary for the fake Shanti bill (9 March)
    "IN-HOSP-SHANTI-STR-0419", "Shanti Clinic and Maternity Home", "SCM/DS/2026/000420", "SCM-UH-7740",
    SHOWCASE_PATIENT, "2026-03-08", "2026-03-09", "Incomplete abortion",
    "Dilatation and evacuation under short general anaesthesia on 08-Mar-2026", "Dr. P. Jagtap, MD")
SUMMARY_DATES_MISMATCH = DischargeFields(
    "IN-HOSP-SHANTI-STR-0419", "Shanti Clinic and Maternity Home", "SCM/DS/2026/000412", "SCM-UH-7731",
    SHOWCASE_PATIENT, "2026-03-12", "2026-03-15", "Acute gastroenteritis with dehydration",
    "none", "Dr. P. Jagtap, MD")

MARATHI_LABELS = {
    "issuer_name": "सह्योग मल्टीस्पेशालिटी हॉस्पिटल",
    "issuer_id": "जारीकर्ता आयडी", "title": "अंतिम आंतररुग्ण बिल", "bill_no": "बिल क्रमांक",
    "bill_date": "बिल दिनांक", "patient": "रुग्ण", "ticket": "तिकीट",
    "ticket_note": "हे बिल TrustLadder नोंदणीद्वारे रुग्णालयाकडून पडताळता येते.",
    "description": "विवरण", "amount": "रक्कम (रु)", "total": "एकूण",
    "items": ["खोली शुल्क (सेमी-प्रायव्हेट)", "शल्यचिकित्सक शुल्क", "औषधे आणि उपभोग्य वस्तू",
              "प्रयोगशाळा तपासण्या"],
    "footer": "IIM-V कॅपस्टोन प्रात्यक्षिकासाठी तयार केलेला कृत्रिम दस्तऐवज (गट 7). हे खरे बिल नाही.",
}


def _headline_bill(ticket: str, patient: str) -> BillFields:
    """The bill used throughout the set: Rs 1,86,400 at Sahyog, Pune."""
    items = [
        LineItem("Room charges (semi-private)", 3600000),
        LineItem("Surgeon fee", 7200000),
        LineItem("Anaesthesia", 1450000),
        LineItem("Operation theatre", 2800000),
        LineItem("Pharmacy and consumables", 2640000),
        LineItem("Laboratory investigations", 950000),
    ]
    return BillFields("IN-HOSP-SAHYOG-PUN-0101", "Sahyog Multispeciality Hospital",
                      "SMH/2026/004812", "2026-03-03", patient, items,
                      sum(i.amount_paise for i in items), ticket)


# The showcase hospitals joined before the bills in the set were issued. The date
# matters: an issuer's coverage window starts when it joins, and a missing ticket
# only means "we did not issue this" for bills dated inside that window.
SHOWCASE_JOINED_ON = "2026-02-20"


def _enrol(store: RegistryStore, ids: list[str], when: str = SHOWCASE_JOINED_ON) -> None:
    for issuer_id in ids:
        name, city, prefix, _ = HOSPITALS[issuer_id]
        store.enrol_issuer(issuer_id, name, city, prefix, when)


def build_showcase(data_dir: Path = DATA_DIR, patient: str = SHOWCASE_PATIENT) -> Path:
    """Create the showcase registry and the seven prepared bills. Returns the folder.

    All seven are for the SAME patient. Files 2 and 5 are file 1 edited and photographed;
    file 7 is file 4 discounted and re-saved. The set explains itself.
    """
    store = RegistryStore(data_dir)
    _enrol(store, SHOWCASE_JOINED)
    sahyog = Publisher(store, "IN-HOSP-SAHYOG-PUN-0101")
    rng = random.Random(2026)
    out = data_dir / "showcase"
    out.mkdir(parents=True, exist_ok=True)

    # 1. The bill as issued, published by the hospital, plus that day's other bills.
    bill = _headline_bill(sahyog.new_ticket(), patient)
    background = [random_bill(rng, bill.issuer_id, bill.issuer_name, "SMH", 4800 + i, "2026-03-03")
                  for i in range(11)]
    for b in background:
        b.ticket = sahyog.new_ticket()
    sahyog.publish([bill] + background)
    (out / SHOWCASE[0][0]).write_bytes(render_genuine(bill, joined=True))

    # 2. The same bill, with its total raised afterwards in a PDF editor.
    (out / SHOWCASE[1][0]).write_bytes(render_altered(bill, bill.total_paise + 10_000_000, joined=True))

    # 3. A bill typed from a blank page: same patient and hospital, never issued.
    fake = BillFields(**{**bill.__dict__, "bill_no": "SMH/2026/004907", "bill_date": "2026-03-11",
                         "ticket": sahyog.new_ticket()})       # right format, never published
    (out / SHOWCASE[2][0]).write_bytes(render_fabricated(fake, joined=True))

    # 4. The same patient, at a hospital that has not joined the registry.
    name, _, _, bp = HOSPITALS["IN-HOSP-SHANTI-STR-0419"]
    shanti = random_bill(rng, "IN-HOSP-SHANTI-STR-0419", name, bp, 1187, "2026-03-07")
    shanti.patient = patient
    (out / SHOWCASE[3][0]).write_bytes(render_genuine(shanti, joined=False))

    # 5. A blurred phone photo of bill 1.
    (out / SHOWCASE[4][0]).write_bytes(render_scan(bill, joined=True, seed=3))

    # 6. A fake in the name of the hospital that has not joined.
    fake2 = random_bill(rng, "IN-HOSP-SHANTI-STR-0419", name, bp, 1203, "2026-03-09")
    fake2.patient = patient
    (out / SHOWCASE[5][0]).write_bytes(render_fabricated(fake2, joined=False))

    # 7. Bill 4 again: genuine, discounted by the hospital, then re-saved by a phone app.
    #    The case the written checks get wrong and the trained model gets right.
    (out / SHOWCASE[6][0]).write_bytes(render_discount_resaved(shanti))

    # Every bill the showcase PUBLISHED, so the hospital's ledger and the registry agree.
    (out / "published_bills.json").write_text(json.dumps(
        [{**b.__dict__, "items": [i.__dict__ for i in b.items]} for b in [bill] + background], indent=2))
    (out / "manifest.json").write_text(json.dumps(
        [{"file": f, "title": t, "shows": s, "expected": e} for f, t, s, e in SHOWCASE], indent=2))
    return out


# --------------------------------------------------------------------------
# The simulated month
# --------------------------------------------------------------------------

# Batch mix: a stated ASSUMPTION of the demo, shown on screen as such.
BATCH_SIZE = 150
MIX = {
    "genuine": 0.74,           # born-digital, untouched
    "genuine_resaved": 0.08,   # genuine, but re-saved by a phone app
    "genuine_scan": 0.08,      # genuine, arrived as a photo
    "altered": 0.05,           # edited after issue
    "fabricated": 0.05,        # typed from a blank page
}
# Coverage scenarios: which of the five hospitals have joined.
SCENARIOS = {
    "0 of 5 hospitals joined": [],
    "2 of 5 hospitals joined": list(HOSPITALS)[:2],
    "4 of 5 hospitals joined": list(HOSPITALS)[:4],
}


def _render_kind(kind: str, bill: BillFields, joined: bool, seed: int) -> bytes:
    if kind == "genuine":
        return render_genuine(bill, joined=joined)
    if kind == "genuine_resaved":
        return render_resaved(bill, joined=joined)
    if kind == "genuine_scan":
        return render_scan(bill, joined=joined, seed=seed)
    if kind == "altered":
        return render_altered(bill, bill.total_paise + 100 * random.Random(seed).randrange(8000, 60000, 500),
                              joined=joined)
    return render_fabricated(bill, joined=joined)


def run_scenario(label: str, joined_ids: list[str], data_dir: Path, seed: int = 11) -> list[dict]:
    """Generate one month of claims and run every one through the real ladder."""
    sim_dir = data_dir / "sim" / label.split()[0]
    if sim_dir.exists():
        shutil.rmtree(sim_dir)
    store = RegistryStore(sim_dir)
    _enrol(store, joined_ids)
    publishers = {i: Publisher(store, i) for i in joined_ids}
    rng = random.Random(seed)                 # same claims in every scenario
    kinds = rng.choices(list(MIX), weights=list(MIX.values()), k=BATCH_SIZE)

    docs = []
    to_publish: dict[str, list[BillFields]] = {i: [] for i in joined_ids}
    for n, kind in enumerate(kinds):
        issuer_id = rng.choice(list(HOSPITALS))
        name, _, prefix, bp = HOSPITALS[issuer_id]
        bill = random_bill(rng, issuer_id, name, bp, 10000 + n, f"2026-03-{rng.randint(1, 28):02d}")
        joined = issuer_id in joined_ids
        ticket_rng = random.Random(seed * 1000 + n)
        if joined:
            # The printed ticket (deterministic here so every scenario sees the same claims).
            body = "".join(ticket_rng.choice("23456789ABCDEFGHJKMNPQRSTUVWXYZ") for _ in range(16))
            bill.ticket = f"{prefix}-" + "-".join(body[i:i + 4] for i in range(0, 16, 4))
            if kind != "fabricated":
                to_publish[issuer_id].append(bill)   # the hospital really issued it
        docs.append((kind, bill, joined, n))
    for issuer_id, bills in to_publish.items():
        if bills:
            publishers[issuer_id].publish(bills)

    verifier = Verifier(store)
    rows = []
    for kind, bill, joined, n in docs:
        pdf = _render_kind(kind, bill, joined, seed=n)
        r = run_ladder(pdf, verifier, f"claim_{n:04d}.pdf")
        rows.append({
            "claim": n,
            "kind": kind,
            "is_fraud": kind in ("altered", "fabricated"),
            "issuer_joined": joined,
            "verdict": r.verdict.value,
            "rule": r.rule_applied.split(":")[0],
            "registry": r.registry_answer.value,
            "families": sorted({f.family for f in r.findings}),
        })
    return rows


def build_simulation(data_dir: Path = DATA_DIR) -> Path:
    results = {label: run_scenario(label, ids, data_dir) for label, ids in SCENARIOS.items()}
    path = data_dir / "simulation.json"
    path.write_text(json.dumps({"batch_size": BATCH_SIZE, "mix": MIX, "results": results}, indent=2))
    return path


def build_all(data_dir: Path = DATA_DIR) -> None:
    """Wipe and rebuild the whole demo world."""
    if data_dir.exists():
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True)
    build_showcase(data_dir)
    build_simulation(data_dir)


if __name__ == "__main__":
    build_all()
    print(f"Demo world built under {DATA_DIR}")
