"""
Builds the complete demo world with realistic sample data.

    python -m trustladder.seed            # (re)build everything under ./data

What it creates (all fictional, deterministic):

  * the six "sample bills" and the simulated month (from cases.py)
  * 5 hospitals: Sahyog, Arogya and Kaveri have JOINED the registry;
    Shanti and Niramay have not
  * 15 customers, including Meera Kulkarni, who has a login
  * each joined hospital's ledger of issued bills (published to the registry)
  * about 35 claims covering every outcome, submitted through the REAL ladder
  * a history of officer decisions on many of them, including one honest
    wrongful hold (a genuine bill held by the rule, then released by a person)
  * seven demo logins, one per role plus a presenter, all with the same password

Every claim goes through service.submit_claim, i.e. the same code as a live
click, so the sample verdicts are real verdicts, not typed-in labels.
"""

from __future__ import annotations

import random
import shutil
from pathlib import Path

from .bills import (photo_of, random_bill, render_altered, render_discount_resaved, render_fabricated,
                    render_genuine, render_resaved, render_scan)
from .cases import DATA_DIR, HOSPITALS, MARATHI_BILL, ROOT, build_showcase, build_simulation
from .models import BillFields
from .reader import read_bill
from .registry import RegistryStore, Verifier
from .service import enrol_hospital, issue_bill, submit_claim
from .store import Store

DEMO_PASSWORD = "TrustLadder@2026"

INSURER = "Sahaya Health Insurance (fictional)"
REGISTRY_BODY = "Health Document Registry (fictional industry body)"

# Logins are generic on purpose: the screens name a ROLE, never a person, so the
# demo works with whatever bill is uploaded on the day.
USERS = [
    # user_id,    name (shown on screen),   role,        org,                           customer_id
    ("hospital", "Billing desk", "hospital", "Sahyog Multispeciality Hospital, Pune", ""),
    ("customer", "Customer", "customer", "Policyholder · " + INSURER, "CUST-001"),
    ("customer2", "Second customer", "customer", "Policyholder · " + INSURER, "CUST-002"),
    ("officer", "Claims officer", "officer", INSURER, ""),
    ("riskhead", "Risk head", "riskhead", INSURER, ""),
    ("registry", "Registry operator", "registry", REGISTRY_BODY, ""),
    ("auditor", "Auditor", "auditor", "Internal audit / regulator (read-only)", ""),
    ("presenter", "Presenter", "presenter", "Demo presenter: every role", ""),
]

CUSTOMERS = [
    ("CUST-001", "Meera Kulkarni", "Pune"), ("CUST-002", "Arjun Iyer", "Pune"),
    ("CUST-003", "Kavya Deshpande", "Nashik"), ("CUST-004", "Rohan Patil", "Kolhapur"),
    ("CUST-005", "Sneha Reddy", "Pune"), ("CUST-006", "Vikram Nair", "Satara"),
    ("CUST-007", "Ananya Chatterjee", "Nashik"), ("CUST-008", "Farhan Qureshi", "Sangli"),
    ("CUST-009", "Lakshmi Menon", "Pune"), ("CUST-010", "Sameer Joshi", "Kolhapur"),
    ("CUST-011", "Divya Bose", "Satara"), ("CUST-012", "Imran Gupta", "Sangli"),
    ("CUST-013", "Pooja Pillai", "Pune"), ("CUST-014", "Nikhil Sharma", "Nashik"),
    ("CUST-015", "Asha Kulkarni", "Satara"),
]

JOINED = ["IN-HOSP-SAHYOG-PUN-0101", "IN-HOSP-AROGYA-NSK-0233", "IN-HOSP-KAVERI-KOP-0577"]
NOT_JOINED = ["IN-HOSP-SHANTI-STR-0419", "IN-HOSP-NIRAMAY-SGL-0612"]


def db_path(data_dir: Path = DATA_DIR) -> Path:
    return Path(data_dir) / "trustladder.db"


def _bill_for(rng, issuer_id, customer, serial, day) -> BillFields:
    name, _, _, bp = HOSPITALS[issuer_id]
    b = random_bill(rng, issuer_id, name, bp, serial, f"2026-03-{day:02d}")
    b.patient = customer[1]
    return b


# The pack people download: what the demo's patient has on her phone. The PDFs are the
# bills hospitals sent her; the two JPEGs are photographs she took of printed bills.
# "Expected" is what happens the FIRST time each file is claimed on her policy.
SAMPLE_PACK = [
    ("01_genuine_bill.pdf", "Bill 1: genuine, from a hospital that has joined", "Authentic"),
    ("02_same_bill_total_raised.pdf", "Bill 1 with its total raised afterwards (same bill number)", "Tampered"),
    ("03_made_from_nothing.pdf", "Same patient and hospital, but a bill the hospital never issued", "Tampered"),
    ("04_genuine_hospital_not_joined.pdf", "Genuine, from a hospital that has not joined", "Inconclusive"),
    ("05_photo_of_arogya_bill.jpg",
     "A phone PHOTO of her genuine Arogya bill. Read by the vision model; the hospital confirms it.",
     "Authentic"),
    ("06_made_from_nothing_not_joined.pdf", "A fake from a hospital that has not joined", "Inconclusive"),
    ("07_genuine_discounted_and_resaved.pdf",
     "GENUINE: bill 4 with a hospital discount, re-saved by a phone app. Looks edited, is not.",
     "Suspicious"),
    ("08_marathi_bill_photo.jpg",
     "A phone PHOTO of a genuine Sahyog bill printed in Marathi. The vision model reads it in "
     "Marathi; the hospital confirms it.",
     "Authentic"),
]


def build_sample_pack(data_dir: Path = DATA_DIR) -> Path:
    """A folder and a zip of sample bills, named in plain words, for people to upload."""
    import zipfile
    out = Path(data_dir) / "sample_bills"
    out.mkdir(parents=True, exist_ok=True)
    readme = ["TrustLadder sample bills (synthetic: fictional hospitals, one patient, invented amounts).",
              "This is what the demo's patient, Meera Kulkarni, has on her phone: PDFs her hospitals",
              "sent her, and two photographs she took of printed bills (05 and 08). File 2 is file 1",
              "edited; file 7 is file 4 after a hospital discount and a phone app re-save.",
              "Upload any of these in the portal at Customer -> Submit a claim: that one screen takes a",
              "PDF or a photo, genuine or tampered, and checks every one the same way. A photo is read by",
              "a vision model and the details are shown to you to confirm before you submit.",
              "The expected result is for the FIRST claim of each bill; a bill claimed twice goes to a",
              "person, never paid twice automatically.",
              ""]
    for name, what, expected in SAMPLE_PACK:
        (out / name).write_bytes((Path(data_dir) / "showcase" / name).read_bytes())
        readme.append(f"{name}\n    {what}\n    Expected: {expected}\n")
    (out / "README.txt").write_text("\n".join(readme))
    zip_path = Path(data_dir) / "TrustLadder_sample_bills.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(out.iterdir()):
            z.write(f, f"TrustLadder_sample_bills/{f.name}")
    return zip_path


def build_world(data_dir: Path = DATA_DIR) -> Store:
    """Wipe and rebuild the whole demo world, sample data included."""
    data_dir = Path(data_dir)
    if data_dir.exists():
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True)

    # 1. The prepared bills and the simulated month. The sample pack is zipped at the
    #    end, once the two photographs below exist.
    build_showcase(data_dir, patient=CUSTOMERS[0][1])   # the prepared bills belong to this policyholder
    build_simulation(data_dir)

    registry = RegistryStore(data_dir)
    verifier = Verifier(registry)
    store = Store(db_path(data_dir))
    for uid, name, role, org, customer_id in USERS:
        store.add_user(uid, name, role, org, DEMO_PASSWORD, customer_id=customer_id,
                       issuer_id="IN-HOSP-SAHYOG-PUN-0101" if role == "hospital" else "")
    for cid, name, city in CUSTOMERS:
        store.add_customer(cid, name, city)
    store.log("Registry desk", "registry", "Hospital enrolled", "", "Sahyog Multispeciality Hospital, Pune",
              "2026-02-20 10:00")
    store.log("Registry desk", "registry", "Hospital enrolled", "", "Arogya Nursing Home, Nashik",
              "2026-02-24 11:30")
    # Kaveri joins through the same path as a live enrolment.
    enrol_hospital(store, registry, verifier, "IN-HOSP-KAVERI-KOP-0577", "Registry desk", "2026-02-28 12:15")

    rng = random.Random(7)
    meera = CUSTOMERS[0]
    others = CUSTOMERS[1:]

    # 1b. The showcase's published bills belong in the hospitals' ledgers too. Most
    #     are patients insured elsewhere (no customer id here); Meera's is added below.
    import json as _json
    from .models import LineItem
    for d in _json.loads((data_dir / "showcase" / "published_bills.json").read_text()):
        if d["patient"] == "Meera Kulkarni":
            continue
        b = BillFields(**{**d, "items": [LineItem(**i) for i in d["items"]]})
        store.add_bill(b.bill_no, b.issuer_id, b.issuer_name, "", b.patient, b.bill_date, b.total_paise,
                       b.ticket, True, render_genuine(b, joined=True), f"{b.bill_date} 12:00")

    # 2. The demo's patient starts with NO CLAIM HISTORY.
    #    She holds bills, so she has something to claim on, but she has never claimed.
    #    Two reasons. Her page is empty until she uploads during the demo, so what appears
    #    there is unmistakably what was just done. And her name shows up exactly once in
    #    the officer's queue: the claim the presenter created a minute earlier.
    #    Every other customer keeps a history, which is what makes the queue and the risk
    #    head's percentages look like a portfolio rather than one person's file.
    showcase = data_dir / "showcase"
    meera_pdf = (showcase / "01_genuine_bill.pdf").read_bytes()
    mf = read_bill(meera_pdf)[1]
    store.add_bill(mf.bill_no, mf.issuer_id, mf.issuer_name, meera[0], mf.patient, mf.bill_date,
                   mf.total_paise, mf.ticket, True, meera_pdf, "2026-03-03 11:20")
    #    a second bill she holds: an Arogya bill, issued and published, never claimed. It is
    #    the "original" she can send if a claim of hers is ever sent back for a clearer copy.
    ab = _bill_for(rng, "IN-HOSP-AROGYA-NSK-0233", meera, 3301, 8)
    arogya_pdf = issue_bill(store, registry, verifier, ab, meera[0], "Arogya billing", "2026-03-08 16:05")
    #    the photograph she took of that Arogya bill's printout: the vision model's English case
    (showcase / "05_photo_of_arogya_bill.jpg").write_bytes(photo_of(arogya_pdf, seed=5))
    #    and a third bill: Sahyog's Marathi printout, which she photographed. The hospital's
    #    own copy is its system PDF (English, as its software prints); the picture is what the
    #    customer holds. Both carry the same fields, so the registry confirms the picture.
    mb = BillFields(**{**MARATHI_BILL.__dict__, "items": list(MARATHI_BILL.items)})
    issue_bill(store, registry, verifier, mb, meera[0], "Sahyog billing", "2026-03-05 12:40")
    marathi_photo = ROOT / "assets" / "samples" / "marathi_bill_photo.jpg"
    if marathi_photo.exists():
        (showcase / "08_marathi_bill_photo.jpg").write_bytes(marathi_photo.read_bytes())
    else:
        # A checkout without the asset (it is made by tools/make_marathi_sample.py on a
        # machine with a browser): fall back to a photo of the English system copy, so the
        # pack is complete and the demo never breaks on a missing file.
        (showcase / "08_marathi_bill_photo.jpg").write_bytes(photo_of(store.bill_pdf(mb.bill_no), seed=8))
    build_sample_pack(data_dir)

    # 3. Everyone else: joined hospitals issue and publish; customers claim.
    serial = 5000
    plan = (
        # (kind, hospital, day, officer decision or None)
        [("genuine", h, d, None) for h, d in [
            ("IN-HOSP-SAHYOG-PUN-0101", 2), ("IN-HOSP-SAHYOG-PUN-0101", 4), ("IN-HOSP-SAHYOG-PUN-0101", 6),
            ("IN-HOSP-SAHYOG-PUN-0101", 10), ("IN-HOSP-SAHYOG-PUN-0101", 14), ("IN-HOSP-SAHYOG-PUN-0101", 17),
            ("IN-HOSP-AROGYA-NSK-0233", 3), ("IN-HOSP-AROGYA-NSK-0233", 7), ("IN-HOSP-AROGYA-NSK-0233", 15),
            ("IN-HOSP-KAVERI-KOP-0577", 5), ("IN-HOSP-KAVERI-KOP-0577", 9), ("IN-HOSP-KAVERI-KOP-0577", 13),
            ("IN-HOSP-KAVERI-KOP-0577", 18)]]
        + [("genuine_resaved", "IN-HOSP-SAHYOG-PUN-0101", 11, None),
           ("genuine_resaved", "IN-HOSP-KAVERI-KOP-0577", 16, None)]
        + [("altered", "IN-HOSP-SAHYOG-PUN-0101", 6, "reject"),
           ("altered", "IN-HOSP-AROGYA-NSK-0233", 12, "reject"),
           ("altered", "IN-HOSP-KAVERI-KOP-0577", 19, None)]
        + [("fabricated", "IN-HOSP-SAHYOG-PUN-0101", 8, "reject"),
           ("fabricated", "IN-HOSP-KAVERI-KOP-0577", 18, None)]
        + [("genuine_scan", "IN-HOSP-KAVERI-KOP-0577", 14, None)]
        + [("genuine", "IN-HOSP-SHANTI-STR-0419", 4, "release"),
           ("genuine", "IN-HOSP-NIRAMAY-SGL-0612", 6, "release"),
           ("genuine", "IN-HOSP-NIRAMAY-SGL-0612", 13, "release"),
           ("genuine", "IN-HOSP-SHANTI-STR-0419", 17, None),
           ("genuine", "IN-HOSP-NIRAMAY-SGL-0612", 19, None)]
        + [("genuine_resaved", "IN-HOSP-NIRAMAY-SGL-0612", 9, "release")]
        + [("discount_resaved", "IN-HOSP-SHANTI-STR-0419", 7, "release")]     # the wrongful hold
        + [("fabricated", "IN-HOSP-SHANTI-STR-0419", 10, "ask_issuer"),
           ("fabricated", "IN-HOSP-NIRAMAY-SGL-0612", 16, None)]
        + [("altered", "IN-HOSP-NIRAMAY-SGL-0612", 12, "reject")]
    )
    for i, (kind, hid, day, decision) in enumerate(plan):
        # A real claims queue holds many people's bills at many hospitals, and the
        # officer's inbox is a LIST, so the variety is the point: it shows a portfolio
        # rather than one person's file. The demo's own claim still reaches the top of
        # that queue on its own, because the queue is worst first and then newest first.
        cust = others[i % len(others)]
        serial += 1
        bill = _bill_for(rng, hid, cust, serial, day)
        joined = hid in JOINED
        issued = f"2026-03-{day:02d} {9 + i % 8:02d}:{(i * 7) % 60:02d}"
        submitted = f"2026-03-{min(day + 1, 20):02d} {10 + i % 9:02d}:{(i * 13) % 60:02d}"
        if joined and kind != "fabricated":
            genuine_pdf = issue_bill(store, registry, verifier, bill, cust[0],
                                     f"{HOSPITALS[hid][0].split()[0]} billing", issued)
        else:
            genuine_pdf = render_genuine(bill, joined=joined)
        if kind == "genuine":
            pdf, name = genuine_pdf, "hospital_bill.pdf"
        elif kind == "genuine_resaved":
            pdf, name = render_resaved(bill, joined=joined), "bill_compressed.pdf"
        elif kind == "genuine_scan":
            pdf, name = render_scan(bill, joined=joined, seed=i), "bill_photo.pdf"
        elif kind == "discount_resaved":
            pdf, name = render_discount_resaved(bill), "bill_scan_app.pdf"
        elif kind == "altered":
            pdf = render_altered(bill, bill.total_paise + 100 * rng.randrange(15000, 90000, 500), joined=joined)
            name = "final_bill.pdf"
        else:   # fabricated: typed from a blank page; a joined hospital's ticket format is copied
            if joined:
                bill.ticket = f"{HOSPITALS[hid][2]}-" + "-".join(
                    "".join(rng.choice("23456789ABCDEFGHJKMNPQRSTUVWXYZ") for _ in range(4)) for _ in range(4))
            pdf, name = render_fabricated(bill, joined=joined), "bill.pdf"
        truth = "fraud" if kind in ("altered", "fabricated") else "genuine"
        claim_id = submit_claim(store, verifier, cust[0], pdf, name, bill.bill_no if joined else "",
                                truth=truth, submitted_at=submitted)
        if decision:
            # The officer login is a ROLE, not a person, so the seeded history must not
            # invent one: the audit trail would then name a person on live rows and a
            # different person on seeded ones for the same job.
            store.decide(claim_id, decision, "Claims officer",
                         when=f"2026-03-{min(day + 2, 21):02d} 15:{(i * 11) % 60:02d}")
    return store


def snapshot_dir(data_dir: Path = DATA_DIR) -> Path:
    """Where the pristine seeded world is kept, so 'Reset demo' is a fast copy."""
    return Path(str(Path(data_dir)) + "_seed")


def build_and_snapshot(data_dir: Path = DATA_DIR) -> Store:
    store = build_world(data_dir)
    snap = snapshot_dir(data_dir)
    shutil.rmtree(snap, ignore_errors=True)
    shutil.copytree(data_dir, snap)
    return store


def restore_snapshot(data_dir: Path = DATA_DIR) -> None:
    """Put the seeded world back exactly as it was built."""
    snap = snapshot_dir(data_dir)
    if not snap.exists():
        build_and_snapshot(data_dir)
        return
    shutil.rmtree(data_dir, ignore_errors=True)
    shutil.copytree(snap, data_dir)


if __name__ == "__main__":
    s = build_and_snapshot()
    m = s.measures()
    print(f"Demo world built under {DATA_DIR}: {m['claims']} claims, "
          f"{m['auto_paid']} paid straight through, {m['open']} open, "
          f"{m['wrongful_holds']} wrongful hold(s). Password for every login: {DEMO_PASSWORD}")
