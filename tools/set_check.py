"""
Submit the four demo sets as the customer and read each verdict as the officer, in a
real browser (test case TC-35).

    .venv/bin/python tools/set_check.py <folder with the four set folders> [url]

The folder is the one the presenter picks files from: four sub-folders, each holding a
bill and its discharge summary, numbered in presenting order. For each set the customer
attaches the bill in slot 1 and the summary in slot 2, submits, and the status shown is
compared with what the set should produce; then the officer opens the newest claim and
the verdict on it is compared too. Stops at the first thing that is wrong; prints a pass
line for everything checked.
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

URL = next((a for a in sys.argv[1:] if a.startswith("http")), "http://127.0.0.1:8765/")
SETS = Path(next(a for a in sys.argv[1:] if not a.startswith("http")))
SHOTS = Path(__file__).resolve().parent.parent / "build" / "set_check"   # screenshots go here, never beside the sets
PASSWORD = "TrustLadder@2026"
SLOW = 180000
# folder prefix -> (status the customer sees, verdict the officer sees)
EXPECTED = {"1": ("Approved", "Authentic"), "2": ("Approved", "Authentic"),
            "3": ("On hold", "Tampered"), "4": ("On hold", "Tampered")}


def fail(msg: str) -> None:
    raise SystemExit(f"TC-35 FAILED: {msg}")


def settle(pg: Page) -> None:
    pg.wait_for_timeout(600)
    try:
        pg.wait_for_selector("[data-testid='stStatusWidget']", state="detached", timeout=SLOW)
    except Exception:
        pass
    pg.wait_for_timeout(400)


def sign_in(pg: Page, user: str) -> None:
    pg.goto(URL, timeout=SLOW)
    pg.wait_for_selector("text=Sign in", timeout=SLOW)
    pg.get_by_role("textbox", name="User ID").fill(user)
    pg.get_by_role("textbox", name="Password").fill(PASSWORD)
    pg.get_by_role("button", name="Sign in").click()
    pg.wait_for_selector("text=Sign out", timeout=SLOW)
    settle(pg)


def sign_out(pg: Page) -> None:
    pg.get_by_role("button", name="Sign out").click()
    pg.wait_for_selector("text=Sign in", timeout=SLOW)


def main() -> None:
    folders = sorted(p for p in SETS.iterdir() if p.is_dir() and p.name[0] in EXPECTED)
    if len(folders) != 4:
        fail(f"expected four set folders under {SETS}, found {[p.name for p in folders]}")
    SHOTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        pg = browser.new_context(viewport={"width": 1440, "height": 900}).new_page()
        for folder in folders:
            want_status, want_verdict = EXPECTED[folder.name[0]]
            bill, summary = sorted(folder.glob("*.pdf"))
            sign_in(pg, "customer")
            pg.get_by_text("Submit a claim", exact=True).first.click()
            settle(pg)
            # The free host reruns slowly: wait for the slots rather than count at once.
            pg.wait_for_selector("[data-testid='stMain'] input[type='file']", state="attached", timeout=SLOW)
            slots = pg.locator("[data-testid='stMain'] input[type='file']")
            if slots.count() != 2:
                fail(f"{folder.name}: expected two slots, found {slots.count()}")
            slots.nth(0).set_input_files(str(bill))
            settle(pg)
            pg.wait_for_selector("[data-testid='stMain'] img", timeout=SLOW)
            slots.nth(1).set_input_files(str(summary))
            settle(pg)
            for _ in range(90):
                if pg.get_by_role("button", name="Submit claim").last.is_enabled():
                    break
                pg.wait_for_timeout(1000)
            else:
                fail(f"{folder.name}: Submit stayed disabled with both PDFs attached")
            pg.get_by_role("button", name="Submit claim").last.click()
            settle(pg)
            pg.wait_for_selector("[data-testid='tl-claim-status']", timeout=SLOW)
            status = pg.locator("[data-testid='tl-claim-status']").last.inner_text().strip()
            if "Tampered" in pg.inner_text("body"):
                fail(f"{folder.name}: the customer can see a verdict")
            if status != want_status:
                fail(f"{folder.name}: customer sees '{status}', expected '{want_status}'")
            # Both documents of the packet must be on the receipt, each named.
            receipt = pg.inner_text("body")
            for needed in ("Bill", "Discharge summary", bill.name, summary.name):
                if needed not in receipt:
                    fail(f"{folder.name}: '{needed}' is not on the customer's receipt")
            pg.screenshot(path=str(SHOTS / f"set_{folder.name[0]}_customer.png"), full_page=True)
            print(f"TC-35 pass: {folder.name}: {bill.name[:2]} + {summary.name[:2]} -> customer sees {status}")
            sign_out(pg)

            sign_in(pg, "officer")
            body = pg.inner_text("body")
            if want_status == "Approved":
                # A proven claim is paid without an officer: it must NOT be in the queue.
                if "Meera Kulkarni" in body.split("Decided recently")[0]:
                    fail(f"{folder.name}: an approved claim is sitting in the officer's queue")
                print(f"TC-35 pass: {folder.name}: approved on proof, nothing for the officer")
            else:
                # Newest first is the default order, so the claim just made is at the top.
                pg.get_by_role("button", name="Open").first.click()
                pg.wait_for_selector("[data-testid='tl-verdict']", timeout=SLOW)
                settle(pg)
                verdict = pg.locator("[data-testid='tl-verdict']").first.inner_text()
                body = pg.inner_text("body")
                if want_verdict not in verdict:
                    fail(f"{folder.name}: officer sees verdict '{verdict.strip()[:60]}', expected {want_verdict}")
                if "Meera Kulkarni" not in body:
                    fail(f"{folder.name}: the opened claim is not the patient's")
                if "discharge summary" not in body.lower():
                    fail(f"{folder.name}: the packet line is missing on the officer's screen")
                if folder.name.startswith("4") and "SMH/2026/004877" not in body:
                    fail(f"{folder.name}: the Marathi bill number is not on the officer's screen")
                if "QuickEdit" not in body:
                    fail(f"{folder.name}: the file-history evidence is not on the officer's screen")
                print(f"TC-35 pass: {folder.name}: officer sees {want_verdict}, patient named, packet line and file history present")
                pg.screenshot(path=str(SHOTS / f"set_{folder.name[0]}_officer.png"), full_page=True)
            sign_out(pg)
        browser.close()
    print("TC-35: all four sets pass")


if __name__ == "__main__":
    main()
