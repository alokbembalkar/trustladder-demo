"""
Records the backup video of the demo as one story across every role, and checks
the real browser surface while doing it (test case TC-30).

    .venv/bin/python tools/record_demo.py                 # local app on :8765
    .venv/bin/python tools/record_demo.py <url> --check   # browser checks only, no video

The story (signed in as the presenter, switching roles with "View as"):
  0  sign in
  1  Hospital      issues a bill to Meera: ticket printed, two signed codes published
  2  Customer      Meera claims it: paid on proof; then sends a clearer copy for a
                   claim that was waiting on her: paid
  3  Officer       opens the claim on hold: Tampered, evidence graph; rejects it;
                   tries the "made from nothing" sample bill: Suspicious
  4  Risk head     impact dashboard; simulated month as hospitals join
  5  Registry      the network; a forged line is rejected
  6  Auditor       the trail of everything above; the rule and the AI architecture

Every verdict and status is ASSERTED in the browser; the recorder stops with an
error rather than make a video of a wrong result.

Output: build/TrustLadder_demo.mp4 and build/stills/*.png
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg
from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
STILLS = BUILD / "stills"
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
URL = ARGS[0] if ARGS else "http://127.0.0.1:8765/"
CHECK_ONLY = "--check" in sys.argv
SIZE = {"width": 1440, "height": 900}
PASSWORD = "TrustLadder@2026"
READ = 0 if CHECK_ONLY else 4200          # ms each caption stays on screen
SLOW = 180000                             # generous waits: the free server is slow


def caption(pg: Page, text: str, hold: int | None = None) -> None:
    if CHECK_ONLY:
        return
    pg.evaluate("""(t) => {
        let el = document.getElementById('tl-caption');
        if (!el) {
            el = document.createElement('div'); el.id = 'tl-caption';
            el.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:999999;' +
              'background:rgba(27,42,65,0.95);color:#fff;font:600 20px/1.45 "Source Sans Pro",' +
              'Helvetica,Arial,sans-serif;padding:16px 40px 18px 40px;text-align:center;';
            document.body.appendChild(el);
        }
        el.textContent = t;
    }""", text)
    pg.wait_for_timeout(READ if hold is None else hold)


def still(pg: Page, name: str) -> None:
    if CHECK_ONLY:
        return
    STILLS.mkdir(parents=True, exist_ok=True)
    pg.evaluate("() => { const el = document.getElementById('tl-caption'); if (el) el.remove(); }")
    pg.screenshot(path=str(STILLS / f"{name}.png"))


def settle(pg: Page) -> None:
    """Wait until Streamlit has finished re-running the page."""
    pg.wait_for_timeout(700)
    pg.wait_for_function("() => !document.querySelector('[data-testid=\"stStatusWidget\"]')", timeout=SLOW)
    pg.wait_for_timeout(500)


def top(pg: Page) -> None:
    pg.mouse.move(900, 450)
    pg.mouse.wheel(0, -8000)
    pg.wait_for_timeout(400)


def scroll(pg: Page, dy: int) -> None:
    pg.mouse.move(900, 450)
    pg.mouse.wheel(0, dy)
    pg.wait_for_timeout(700)


def view_as(pg: Page, role_label: str) -> None:
    top(pg)
    pg.get_by_role("combobox", name="View as").click()
    pg.get_by_role("option", name=role_label, exact=True).click()
    settle(pg)


def menu(pg: Page, item: str) -> None:
    top(pg)
    pg.locator("[data-testid='stSidebar']").get_by_text(item, exact=True).click()
    settle(pg)


def expect_text(pg: Page, text: str, what: str) -> None:
    try:
        pg.get_by_text(text).first.wait_for(timeout=SLOW)
    except Exception:
        raise SystemExit(f"TC-30 FAILED: {what}: '{text}' not shown")
    print(f"TC-30 pass: {what}")


def expect_verdict(pg: Page, expected: str, what: str) -> None:
    loc = pg.locator("[data-testid='tl-verdict']:visible").last
    loc.wait_for(timeout=SLOW)
    shown = loc.inner_text().strip()
    if shown != expected:
        raise SystemExit(f"TC-30 FAILED: {what}: expected {expected}, browser shows {shown}")
    print(f"TC-30 pass: {what} = {expected}")


def main() -> None:
    BUILD.mkdir(exist_ok=True)
    raw_dir = BUILD / "raw_video"
    shutil.rmtree(raw_dir, ignore_errors=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        opts = {"viewport": SIZE}
        if not CHECK_ONLY:
            opts |= {"record_video_dir": str(raw_dir), "record_video_size": SIZE}
        ctx = browser.new_context(**opts)
        pg = ctx.new_page()
        pg.goto(URL, timeout=SLOW)
        pg.wait_for_selector("text=Sign in", timeout=SLOW)
        still(pg, "login")

        # 0. Sign in
        caption(pg, "TrustLadder: the same claim seen by every party. We sign in as the presenter, "
                    "who can view the system as each role.", 5200)
        pg.get_by_role("textbox", name="User ID").fill("presenter")
        pg.get_by_role("textbox", name="Password").fill(PASSWORD)
        pg.get_by_role("button", name="Sign in").click()
        pg.wait_for_selector("text=Sign out", timeout=SLOW)
        settle(pg)
        if not CHECK_ONLY:
            pg.get_by_role("button", name="Reset demo").click()       # always the same story
            settle(pg)
        expect_text(pg, "Claims inbox", "presenter signed in")

        # 1. Hospital issues a bill
        view_as(pg, "Hospital billing")
        expect_text(pg, "patient records shared", "hospital home")
        caption(pg, "1 · The hospital's billing desk. Every bill it issues carries a random ticket, and "
                    "two signed codes go to the registry. No patient data leaves.")
        still(pg, "hospital_bills")
        menu(pg, "Issue a bill")
        pg.get_by_role("combobox", name="Patient").click()
        pg.get_by_role("option", name="Meera Kulkarni (Pune)").click()
        settle(pg)
        pg.get_by_role("button", name="Issue bill and publish").click()
        settle(pg)
        expect_text(pg, "Ticket printed on the bill", "bill issued and published")
        scroll(pg, 420)
        caption(pg, "The bill goes to Meera. The registry receives only two scrambled codes, signed "
                    "with the hospital's key.", 5000)
        still(pg, "hospital_issue")

        # 2. Customer claims it
        view_as(pg, "Customer")
        caption(pg, "2 · Meera, the customer. She sees her claims in plain words, and what, if "
                    "anything, she needs to do.")
        still(pg, "customer_claims")
        menu(pg, "Submit a claim")
        pg.get_by_role("button", name="Submit claim").click()
        settle(pg)
        expect_text(pg, "Paid. The hospital confirmed your bill", "new claim paid on proof")
        caption(pg, "She submits the new bill. The hospital's codes confirm it, so it is paid at once, "
                    "with no officer involved.", 4600)
        menu(pg, "My claims")
        scroll(pg, 250)
        caption(pg, "An earlier claim is waiting on her: she had sent a blurred photo. It was never "
                    "called a mismatch; she is simply asked for a clearer copy.", 5000)
        pg.get_by_role("combobox", name="Send a clearer copy from your documents").click()
        pg.get_by_role("option").filter(has_text="Arogya").first.click()
        settle(pg)
        pg.get_by_role("button", name="Send clearer copy").click()
        settle(pg)
        top(pg)
        expect_text(pg, "need something from you", "customer page after resubmission")
        if pg.get_by_text("We need a clearer copy of your bill").count():
            raise SystemExit("TC-30 FAILED: clearer copy did not clear the waiting claim")
        print("TC-30 pass: clearer copy resolved the waiting claim")
        caption(pg, "The original PDF is confirmed by the hospital and paid.", 3200)

        # 3. Officer
        view_as(pg, "Insurer claims officer")
        caption(pg, "3 · The claims officer sees only what could not be cleared on proof, worst first.")
        expect_verdict(pg, "Tampered", "first inbox claim")
        scroll(pg, 330)
        caption(pg, "A bill whose total was raised after it was issued. The hospital's record and the "
                    "file itself disagree with it.", 4800)
        still(pg, "officer_claim")
        pg.locator("[data-testid='stGraphVizChart']:visible").first.scroll_into_view_if_needed()
        pg.wait_for_timeout(900)
        caption(pg, "The evidence graph: four independent lines against the bill. The published rule R2 "
                    "turns them into the verdict. The officer decides.", 5200)
        still(pg, "evidence_graph")
        pg.get_by_role("button", name="Fraud: reject and refer").first.click()
        settle(pg)
        menu(pg, "Try a sample bill")
        pg.get_by_role("combobox", name="Sample bill").click()
        pg.get_by_role("option").nth(2).click()
        settle(pg)
        pg.get_by_role("button", name="Run it up the ladder").click()
        settle(pg)
        expect_verdict(pg, "Suspicious", "made-from-nothing sample bill")
        scroll(pg, 330)
        caption(pg, "A bill made from nothing passes every appearance check. Only the hospital can say "
                    "it never issued the ticket, so a person looks before anything is paid.", 5600)
        still(pg, "case3_suspicious")

        # 4. Risk head
        view_as(pg, "Insurer risk head")
        expect_text(pg, "wrongful holds", "risk dashboard")
        caption(pg, "4 · The risk head sees both directions: fraud stopped, and genuine customers held "
                    "up. Wrongful holds are counted, not hidden.", 5200)
        caption(pg, "She owns the policy. 'Pay' is offered only for Authentic: no setting can pay a "
                    "document the issuer has not confirmed.", 5000)
        still(pg, "risk_dashboard")
        menu(pg, "Simulated month")
        for label, line in (("0 of 5 hospitals joined", "On day one, with no hospital joined, every bill "
                             "goes to a person, as today. No fraud is paid."),
                            ("4 of 5 hospitals joined", "As hospitals join, straight-through payment "
                             "rises and review work falls. Frauds paid stays at zero.")):
            pg.get_by_text(label, exact=True).click()
            settle(pg)
            caption(pg, line, 4600)
        still(pg, "policy_4of5")

        # 5. Registry
        view_as(pg, "Registry operator")
        expect_text(pg, "patient records held", "registry network")
        caption(pg, "5 · The neutral registry: who has joined, and what it holds. Signed codes only: "
                    "a complete theft reveals nothing.")
        still(pg, "registry_network")
        menu(pg, "Integrity check")
        pg.get_by_role("button", name="Insert a forged entry").click()
        settle(pg)
        expect_text(pg, "Rejected: the hospital", "forged registry line rejected")
        caption(pg, "Try to slip in a fake line: the hospital's signature breaks and every verifier "
                    "ignores it.")

        # 6. Auditor
        view_as(pg, "Auditor / regulator")
        # The table is drawn on a canvas; the summary line under it is plain text.
        expect_text(pg, "5 rejected", "officer's rejection counted in the audit summary (4 seeded + 1)")
        caption(pg, "6 · The auditor reads everything and changes nothing: every verdict, every human "
                    "decision, every policy change.", 5000)
        still(pg, "audit_trail")
        menu(pg, "How decisions are made")
        caption(pg, "The rule is a published table, not a score. Around it, the AI architecture: solid "
                    "boxes run today, dashed boxes are the target design.", 5200)
        scroll(pg, 520)
        still(pg, "architecture")
        caption(pg, "TrustLadder. Only proof clears. Unproven goes to a person. Wrongful holds are "
                    "counted, not hidden.", 5200)
        video = pg.video.path() if not CHECK_ONLY else None
        ctx.close()
        browser.close()

    if CHECK_ONLY:
        print("TC-30: all browser checks passed")
        return
    mp4 = BUILD / "TrustLadder_demo.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(video),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-movflags", "+faststart",
                    str(mp4)], check=True)
    shutil.rmtree(raw_dir, ignore_errors=True)
    print(f"video: {mp4}  ({mp4.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
