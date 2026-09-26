"""
Records the backup video of the demo as one story across every role, and checks
the real browser surface while doing it (test case TC-30).

    .venv/bin/python tools/record_demo.py                 # local app on :8765
    .venv/bin/python tools/record_demo.py <url> --check   # browser checks only, no video

The story (signed in as the presenter, switching roles with "View as"):
  0  sign in
  1  Hospital      issues a bill; the genuine PDF is downloaded
  2  Customer      uploads that PDF: paid on proof
  3  2nd customer  edits the bill in the demo toolkit (the forger's editor) and
                   uploads the edited copy of the SAME bill: Tampered
  4  Officer       opens it (same bill number) and rejects it
  5  Officer       a bill made from nothing: Suspicious
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
READ = 0 if CHECK_ONLY else 5200          # ms each caption stays on screen
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
    """Wait until Streamlit has STARTED and then FINISHED re-running the page.

    On a slow server the 'running' indicator can take a moment to appear, so
    waiting only for its absence would return too early.
    """
    try:
        pg.wait_for_selector("[data-testid='stStatusWidget']", state="attached", timeout=3000)
    except Exception:
        pass                                   # the rerun was already over
    pg.wait_for_function("() => !document.querySelector('[data-testid=\"stStatusWidget\"]')", timeout=SLOW)
    pg.wait_for_timeout(600)


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

        # 0. Sign in as the presenter; always start from the same clean story.
        caption(pg, "TrustLadder: one hospital bill, seen by every party. The presenter follows seven "
                    "guided steps; each shows what to click and what to say.", 5200)
        pg.get_by_role("textbox", name="User ID").fill("presenter")
        pg.get_by_role("textbox", name="Password").fill(PASSWORD)
        pg.get_by_role("button", name="Sign in").click()
        pg.wait_for_selector("text=Sign out", timeout=SLOW)
        settle(pg)
        pg.get_by_role("button", name="Reset demo").click()
        settle(pg)
        clear = lambda: pg.evaluate("() => { const el = document.getElementById('tl-caption'); if (el) el.remove(); }")
        clear()

        def next_step():
            top(pg)
            pg.get_by_role("button", name="Next ▸").click()
            settle(pg)

        # 1. The hospital issues a bill and hands over the two files the demo needs
        expect_text(pg, "Issue bill and publish", "step 1: hospital")
        pg.wait_for_timeout(READ)
        pg.get_by_role("button", name="Issue bill and publish").click()
        settle(pg)
        expect_text(pg, "Ticket printed on the bill", "bill issued and published")
        with pg.expect_download() as d1:
            pg.get_by_role("button", name="Download the bill (PDF)").click()
        genuine = Path(BUILD / "demo_files" / d1.value.suggested_filename)
        genuine.parent.mkdir(parents=True, exist_ok=True)
        d1.value.save_as(str(genuine))
        print(f"TC-30 pass: downloaded the genuine bill {genuine.name}")
        pg.wait_for_timeout(READ)
        still(pg, "hospital_issue")

        # 2. The customer uploads that bill
        next_step()
        expect_text(pg, "Your bill (PDF)", "step 2: customer upload screen")
        # scope to the main area: the presenter's sidebar toolkit also has an upload box
        pg.locator("[data-testid='stMain'] input[type='file']").first.set_input_files(str(genuine))
        settle(pg)
        pg.get_by_role("button", name="Submit claim").click()
        settle(pg)
        expect_verdict(pg, "Authentic", "step 2: the uploaded bill")
        expect_text(pg, "Paid", "step 2: paid on proof")
        pg.wait_for_timeout(READ)
        still(pg, "customer_upload")

        # 3. The forger edits that bill in the demo toolkit, then a second customer
        #    uploads the edited copy. (A hospital never produces a tampered bill.)
        next_step()
        expect_text(pg, "Second customer", "step 3: a different customer")
        pg.get_by_text("Demo toolkit: the forger's PDF editor").click()
        pg.wait_for_timeout(600)
        pg.locator("[data-testid='stSidebar'] input[type='file']").first.set_input_files(str(genuine))
        settle(pg)
        with pg.expect_download() as d2:
            pg.get_by_role("button", name="Download the edited copy").click()
        tampered = genuine.parent / d2.value.suggested_filename
        d2.value.save_as(str(tampered))
        print(f"TC-30 pass: the forger's toolkit produced {tampered.name}")
        pg.locator("[data-testid='stMain'] input[type='file']").first.set_input_files(str(tampered))
        settle(pg)
        pg.get_by_role("button", name="Submit claim").click()
        settle(pg)
        expect_verdict(pg, "Tampered", "step 3: the forged copy of the same bill")
        pg.wait_for_timeout(READ)
        still(pg, "customer_tampered")

        # 4. The officer opens it and rejects it
        next_step()
        expect_verdict(pg, "Tampered", "step 4: top of the officer's inbox")
        expect_text(pg, "bill SMH/", "step 4: the same bill number")
        pg.wait_for_timeout(READ)
        still(pg, "officer_claim")
        pg.get_by_role("button", name="Reject as fraud").first.click()
        settle(pg)

        # 5. A bill made from nothing
        next_step()
        pg.get_by_role("combobox", name="A prepared bill").click()
        pg.get_by_role("option").nth(2).click()
        settle(pg)
        pg.get_by_role("button", name="Check this bill").click()
        settle(pg)
        expect_verdict(pg, "Suspicious", "step 5: made-from-nothing bill")
        pg.wait_for_timeout(READ)
        still(pg, "case3_suspicious")

        # 6. Risk head
        next_step()
        expect_text(pg, "honest customers wrongly held", "step 6: risk dashboard")
        pg.wait_for_timeout(READ)
        still(pg, "risk_dashboard")

        # 7. Registry
        next_step()
        expect_text(pg, "patient records held", "step 7: registry network")
        pg.wait_for_timeout(READ // 2)
        pg.get_by_text("Check that nobody has tampered with the registry").click()
        pg.wait_for_timeout(600)
        pg.get_by_role("button", name="Try to insert a forged entry").click()
        settle(pg)
        expect_text(pg, "Rejected: the hospital", "forged registry line rejected")
        pg.wait_for_timeout(READ)
        still(pg, "registry_network")

        # 8. Auditor
        next_step()
        # The table is drawn on a canvas; the summary line under it is plain text.
        expect_text(pg, "5 rejected", "step 8: officer's rejection in the audit summary (4 seeded + 1)")
        pg.wait_for_timeout(READ)
        still(pg, "audit_trail")
        menu(pg, "How decisions are made")
        pg.get_by_text("The AI architecture around the rule").click()
        pg.wait_for_timeout(1500)
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
