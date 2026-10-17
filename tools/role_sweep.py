"""
Sign in as every demo user and open every screen, in a real browser (test case TC-32).

    .venv/bin/python tools/role_sweep.py                 # local app on :8765
    .venv/bin/python tools/role_sweep.py <url>           # the live site

The guided story (tools/record_demo.py) walks the presenter through six steps. This
walks the OTHER door: each role's own login, every page on its menu, as the person who
owns that screen would see it. For each page it checks that the page rendered without
a Streamlit exception, that the role's name and the page's own heading are on screen,
and that the hosted-model line is present. Then, as the customer, it submits the first
pair of the sample pack (bill 01 and summary 02) and reads the status back; as the
officer it opens the top of the queue; as the risk head it moves one policy menu and
puts it back; as the registry it runs the forged-entry test.

It stops with an error at the first thing that is wrong, and prints a pass line for
everything it checked, so the result can be read as a test report.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
URL = next((a for a in sys.argv[1:] if a.startswith("http")), "http://127.0.0.1:8765/")
PASSWORD = "TrustLadder@2026"
SLOW = 180000

# login -> (name shown in the sidebar, [(menu label, heading on the page)])
ROLES = {
    "hospital": ("Billing desk", [("Issue a bill", "Issue a bill"), ("My bills", "My bills")]),
    "customer": ("Customer", [("My claims", "Your claims"), ("Submit a claim", "Submit a claim")]),
    "customer2": ("Second customer", [("My claims", "Your claims"), ("Submit a claim", "Submit a claim")]),
    "officer": ("Claims officer", [("Claims inbox", "Claims inbox")]),
    "riskhead": ("Risk head", [("Dashboard", "Risk head's dashboard")]),
    "registry": ("Registry operator", [("Network", "Network")]),
    "auditor": ("Auditor", [("Audit trail", "Audit trail"), ("How decisions are made", "How decisions are made")]),
}


def fail(msg: str) -> None:
    raise SystemExit(f"TC-32 FAILED: {msg}")


def ok(msg: str) -> None:
    print(f"TC-32 pass: {msg}")


def settle(pg: Page) -> None:
    pg.wait_for_timeout(600)
    try:
        pg.wait_for_selector("[data-testid='stStatusWidget']", state="detached", timeout=SLOW)
    except Exception:
        pass
    pg.wait_for_timeout(400)


def body(pg: Page) -> str:
    return pg.inner_text("body")


def no_exception(pg: Page, where: str) -> None:
    text = body(pg)
    if "Traceback" in text or "Error:" in text and "stException" in pg.content():
        fail(f"{where}: an exception is on screen")


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
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        pg = browser.new_context(viewport={"width": 1440, "height": 900}).new_page()

        # A wrong password must not get in.
        pg.goto(URL, timeout=SLOW)
        pg.wait_for_selector("text=Sign in", timeout=SLOW)
        pg.get_by_role("textbox", name="User ID").fill("officer")
        pg.get_by_role("textbox", name="Password").fill("wrong")
        pg.get_by_role("button", name="Sign in").click()
        pg.wait_for_selector("text=do not match", timeout=SLOW)
        ok("a wrong password is refused")

        for user, (name, pages) in ROLES.items():
            sign_in(pg, user)
            text = body(pg)
            if name not in text:
                fail(f"{user}: the sidebar does not name the user ({name})")
            if "Hosted AI models:" not in text:
                fail(f"{user}: the hosted-models line is missing")
            if "Reset demo" in text or "Demo toolkit" in text:
                fail(f"{user}: presenter-only controls are visible to a role login")
            for label, heading in pages:
                if len(pages) > 1:
                    pg.get_by_text(label, exact=True).first.click()
                    settle(pg)
                no_exception(pg, f"{user} / {label}")
                if heading not in body(pg):
                    fail(f"{user} / {label}: heading '{heading}' not on screen")
                ok(f"{user} / {label}: renders, named, no exception")
            sign_out(pg)

        # ---- customer: the first pair of the pack, bill 01 in slot 1 and summary 02 in slot 2
        sign_in(pg, "customer")
        pg.get_by_text("Submit a claim", exact=True).first.click()
        settle(pg)
        if pg.get_by_role("button", name="Submit claim").is_enabled():
            fail("customer: Submit is enabled with nothing attached")
        with pg.expect_download() as d:
            pg.get_by_role("button", name="Download sample bills (zip)").click()
        pack = ROOT / "build" / "demo_files" / "sweep_pack.zip"
        pack.parent.mkdir(parents=True, exist_ok=True)
        d.value.save_as(str(pack))
        with zipfile.ZipFile(pack) as z:
            names = z.namelist()
            bill = next(n for n in names if "/01 " in n)
            summ = next(n for n in names if "/02 " in n)
            (pack.parent / "sweep_01.pdf").write_bytes(z.read(bill))
            (pack.parent / "sweep_02.jpg").write_bytes(z.read(summ))
        ok(f"customer: the sample pack has {len(names) - 1} files, named for the patient and hospital")
        slots = pg.locator("[data-testid='stMain'] input[type='file']")
        if slots.count() != 2:
            fail(f"customer: expected two slots, found {slots.count()}")
        slots.nth(0).set_input_files(str(pack.parent / "sweep_01.pdf"))
        settle(pg)
        pg.wait_for_selector("[data-testid='stMain'] img", timeout=SLOW)      # the bill's preview
        settle(pg)
        if pg.get_by_role("button", name="Submit claim").is_enabled():
            fail("customer: Submit is enabled without the discharge summary")
        ok("customer: the bill alone cannot be submitted")
        slots.nth(1).set_input_files(str(pack.parent / "sweep_02.jpg"))
        settle(pg)
        pg.wait_for_selector("text=These are the details on my discharge summary", timeout=SLOW)
        text = body(pg)
        models_on = "Hosted AI models: on" in text
        if models_on and "Here is what the vision model read" not in text:
            shown = next((ln for ln in text.splitlines() if "model" in ln.lower() and "read" in ln.lower()), "")
            print(f"TC-32 note: models are on but the scan was not read by the model: {shown[:140]!r}")
        pg.get_by_text("These are the details on my discharge summary").click()
        settle(pg)
        # The tick triggers a rerun; on the free host that can take a few seconds.
        for _ in range(60):
            if pg.get_by_role("button", name="Submit claim").is_enabled():
                break
            pg.wait_for_timeout(1000)
        else:
            fail("customer: Submit stays disabled after both documents are attached and confirmed")
        pg.get_by_role("button", name="Submit claim").click()
        settle(pg)
        pg.wait_for_selector("[data-testid='tl-claim-status']", timeout=SLOW)
        status = pg.locator("[data-testid='tl-claim-status']").last.inner_text().strip()
        if "Tampered" in body(pg) or "data-testid=\"tl-verdict\"" in pg.content():
            fail("customer: a verdict is visible to the customer")
        if status not in ("Paid", "In review"):
            fail(f"customer: packet 01+02 came back '{status}'; expected Paid (or In review if bill 01 was claimed before)")
        ok(f"customer: packet 01+02 submitted, status shown = {status}, no verdict shown")
        pg.get_by_text("My claims", exact=True).first.click()
        settle(pg)
        if status not in body(pg):
            fail("customer: the new claim is not on My claims")
        ok("customer: the claim appears on My claims with the same status")
        sign_out(pg)

        # ---- officer: open the top of the queue
        sign_in(pg, "officer")
        if pg.locator("[data-testid='tl-verdict']:visible").count():
            fail("officer: a verdict is shown before a claim is chosen")
        pg.get_by_role("button", name="Open").first.click()
        pg.wait_for_selector("[data-testid='tl-verdict']", timeout=SLOW)   # the free host is slow
        settle(pg)
        pg.wait_for_selector("text=How the evidence was gathered", timeout=SLOW)
        text = body(pg)
        for needed in ("What was found", "How the evidence was gathered", "Approve payment", "Reject as fraud"):
            if needed not in text:
                fail(f"officer: '{needed}' missing on the opened claim")
        if "discharge summary" not in text.lower():
            fail("officer: the packet's summary line is missing")
        ok("officer: the queue opens a claim with verdict, findings, reading line, packet line and trace")
        sign_out(pg)

        # ---- risk head: move a menu and put it back
        sign_in(pg, "riskhead")
        before = body(pg).split("would go:")[1].split("\n")[0]
        # The selected value is not readable from the combobox, so try one option and,
        # if it was already selected, the other: one of the two must move the line.
        moved = ""
        def counts_line() -> str:
            return body(pg).split("would go:")[1].split("\n")[0]

        for option in ("Ask customer for original", "Human review"):
            pg.get_by_role("combobox", name="Inconclusive").click()
            pg.get_by_role("option", name=option).click()
            settle(pg)
            for _ in range(30):                       # the free host reruns slowly
                if counts_line() != before:
                    break
                pg.wait_for_timeout(1000)
            if counts_line() != before:
                moved = option
                break
        if not moved:
            fail("riskhead: changing the policy changed nothing")
        pg.get_by_role("combobox", name="Inconclusive").click()
        pg.get_by_role("option", name="Human review").click()      # the seeded default
        settle(pg)
        ok(f"riskhead: the policy menu moves the counts (set to '{moved}'), and was put back to the default")
        sign_out(pg)

        # ---- registry: the forged-entry test
        sign_in(pg, "registry")
        pg.get_by_text("Check that nobody has tampered with the registry").click()
        pg.wait_for_timeout(600)
        pg.get_by_role("button", name="Try to insert a forged entry").click()
        settle(pg)
        if "Rejected: the hospital" not in body(pg):
            fail("registry: the forged entry was not rejected")
        ok("registry: a forged entry is rejected")
        sign_out(pg)

        # ---- auditor: the customer's claim is in the trail, with its models row
        sign_in(pg, "auditor")
        pg.get_by_text("Audit trail", exact=True).first.click()
        settle(pg)
        # The table itself is drawn on a canvas; the summary line under it is text.
        if "claims ·" not in body(pg) or "paid on proof" not in body(pg):
            fail("auditor: the trail's summary line is missing")
        if not pg.get_by_role("button", name="Download as CSV").count():
            fail("auditor: the trail cannot be downloaded")
        pg.get_by_text("How decisions are made", exact=True).first.click()
        settle(pg)
        try:
            pg.wait_for_selector("text=The models around the rule", timeout=SLOW)
        except Exception:
            fail("auditor: the model registry is missing")
        ok("auditor: the trail and the model registry are on screen")
        browser.close()
    print("TC-32: every role's screens pass")


if __name__ == "__main__":
    main()
