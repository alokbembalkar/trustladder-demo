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

from .bills import (random_bill, render_altered, render_fabricated, render_genuine,
                    render_resaved, render_scan)
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

SHOWCASE = [
    # (file, title, what it shows, expected verdict)
    ("01_genuine_sahyog.pdf", "Genuine bill, hospital has joined",
     "Proof clears: the issuer confirms every field.", "Authentic"),
    ("02_altered_total_sahyog.pdf", "Total raised by Rs 1 lakh after issue",
     "Two independent findings convict: the issuer's record and the file itself.", "Tampered"),
    ("03_fabricated_sahyog.pdf", "Made from nothing, hospital has joined",
     "Looks perfect, passes every appearance check; the issuer never issued it.", "Suspicious"),
    ("04_genuine_shanti_not_joined.pdf", "Genuine bill, small hospital not yet joined",
     "No proof is available, so it is referred, never cleared.", "Inconclusive"),
    ("05_scan_arogya.pdf", "Genuine bill, arrived as a blurred photo",
     "Unreadable is not wrong: 'could not read', never 'mismatch'.", "Inconclusive"),
    ("06_fabricated_shanti_not_joined.pdf", "Made from nothing, hospital not joined",
     "The honest gap: without the issuer, a clean fake cannot be told apart. "
     "It is still never cleared.", "Inconclusive"),
]


def _meera_bill(ticket: str) -> BillFields:
    """The bill used in the proposal's story: Rs 1,86,400 at Sahyog, Pune."""
    items = [
        LineItem("Room charges (semi-private)", 3600000),
        LineItem("Surgeon fee", 7200000),
        LineItem("Anaesthesia", 1450000),
        LineItem("Operation theatre", 2800000),
        LineItem("Pharmacy and consumables", 2640000),
        LineItem("Laboratory investigations", 950000),
    ]
    return BillFields("IN-HOSP-SAHYOG-PUN-0101", "Sahyog Multispeciality Hospital",
                      "SMH/2026/004812", "2026-03-03", "Meera Kulkarni", items,
                      sum(i.amount_paise for i in items), ticket)


def _enrol(store: RegistryStore, ids: list[str]) -> None:
    for issuer_id in ids:
        name, city, prefix, _ = HOSPITALS[issuer_id]
        store.enrol_issuer(issuer_id, name, city, prefix)


def build_showcase(data_dir: Path = DATA_DIR) -> Path:
    """Create the showcase registry and the six showcase bills. Returns the folder."""
    store = RegistryStore(data_dir)
    _enrol(store, SHOWCASE_JOINED)
    sahyog = Publisher(store, "IN-HOSP-SAHYOG-PUN-0101")
    arogya = Publisher(store, "IN-HOSP-AROGYA-NSK-0233")
    rng = random.Random(2026)
    out = data_dir / "showcase"
    out.mkdir(parents=True, exist_ok=True)

    # 1 + 2: Meera's genuine bill, published by the hospital; then an altered copy.
    meera = _meera_bill(sahyog.new_ticket())
    background = [random_bill(rng, meera.issuer_id, meera.issuer_name, "SMH", 4800 + i, "2026-03-03")
                  for i in range(11)]
    for b in background:
        b.ticket = sahyog.new_ticket()
    sahyog.publish([meera] + background)
    (out / SHOWCASE[0][0]).write_bytes(render_genuine(meera, joined=True))
    (out / SHOWCASE[1][0]).write_bytes(
        render_altered(meera, meera.total_paise + 10_000_000, joined=True))

    # 3: a fake typed from a blank page, with a well-formed ticket never issued.
    fake = random_bill(rng, meera.issuer_id, meera.issuer_name, "SMH", 4907, "2026-03-11")
    fake.ticket = sahyog.new_ticket()          # right format, but never published
    (out / SHOWCASE[2][0]).write_bytes(render_fabricated(fake, joined=True))

    # 4: a genuine bill from a hospital that has not joined (no ticket printed).
    name, _, _, bp = HOSPITALS["IN-HOSP-SHANTI-STR-0419"]
    shanti = random_bill(rng, "IN-HOSP-SHANTI-STR-0419", name, bp, 1187, "2026-03-07")
    (out / SHOWCASE[3][0]).write_bytes(render_genuine(shanti, joined=False))

    # 5: a genuine, published Arogya bill that reached the insurer as a bad photo.
    name, _, _, bp = HOSPITALS["IN-HOSP-AROGYA-NSK-0233"]
    scan = random_bill(rng, "IN-HOSP-AROGYA-NSK-0233", name, bp, 2290, "2026-03-05")
    scan.ticket = arogya.new_ticket()
    arogya.publish([scan])
    (out / SHOWCASE[4][0]).write_bytes(render_scan(scan, joined=True))

    # 6: a fake in the name of the non-joined hospital.
    name, _, _, bp = HOSPITALS["IN-HOSP-SHANTI-STR-0419"]
    fake2 = random_bill(rng, "IN-HOSP-SHANTI-STR-0419", name, bp, 1203, "2026-03-09")
    (out / SHOWCASE[5][0]).write_bytes(render_fabricated(fake2, joined=False))

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
