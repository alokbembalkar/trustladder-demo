"""
Records the backup video of the demo, and checks the real browser surface while
doing it (test case TC-30).

    .venv/bin/python tools/record_demo.py            # app must be running on :8765

What it does:
  * drives the running app in the installed Google Chrome (no browser download),
  * shows a caption bar at the bottom of the screen for each step, so the video
    explains itself without a voice-over,
  * ASSERTS every verdict shown in the browser against the expected one, and
    stops with an error if any differs (a video of a wrong result is never made),
  * saves a still of each key moment for the slides (build/stills/),
  * converts the recording to MP4 (plays in PowerPoint, Keynote and QuickTime).

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
# Default: the local demo. Pass a URL to run the same browser checks against a
# deployed copy, e.g. python tools/record_demo.py https://trustladder-demo.onrender.com/
URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765/"
SIZE = {"width": 1440, "height": 900}

# How long each caption stays on screen (ms). Tuned for reading, not speed.
READ = 4200


def caption(pg: Page, text: str, hold: int = READ) -> None:
    """Show a caption bar at the bottom of the page for `hold` ms."""
    pg.evaluate("""(t) => {
        let el = document.getElementById('tl-caption');
        if (!el) {
            el = document.createElement('div');
            el.id = 'tl-caption';
            el.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:999999;' +
              'background:rgba(27,42,65,0.94);color:#fff;font:600 20px/1.45 "Source Sans Pro",' +
              'Helvetica,Arial,sans-serif;padding:16px 40px 18px 40px;text-align:center;';
            document.body.appendChild(el);
        }
        el.textContent = t;
    }""", text)
    pg.wait_for_timeout(hold)


def clear_caption(pg: Page) -> None:
    pg.evaluate("() => { const el = document.getElementById('tl-caption'); if (el) el.remove(); }")


def scroll_main(pg: Page, dy: int) -> None:
    """Scroll the app's main area (Streamlit scrolls an inner container)."""
    pg.mouse.move(900, 450)
    pg.mouse.wheel(0, dy)
    pg.wait_for_timeout(700)


def scroll_top(pg: Page) -> None:
    pg.mouse.move(900, 450)
    pg.mouse.wheel(0, -6000)
    pg.wait_for_timeout(500)


def tab(pg: Page, name: str) -> None:
    scroll_top(pg)
    pg.get_by_role("tab", name=name).click()
    pg.wait_for_timeout(1200)


def still(pg: Page, name: str) -> None:
    STILLS.mkdir(parents=True, exist_ok=True)
    clear_caption(pg)
    pg.screenshot(path=str(STILLS / f"{name}.png"))


def expect_verdict(pg: Page, expected: str) -> None:
    """TC-30: the verdict the browser shows must be the expected one."""
    loc = pg.locator("[data-testid='tl-verdict']:visible").last
    loc.wait_for(timeout=30000)
    shown = loc.inner_text().strip()
    if shown != expected:
        raise SystemExit(f"TC-30 FAILED: expected {expected}, browser shows {shown}")
    print(f"TC-30 pass: {expected}")


def run_case(pg: Page, index: int, expected: str, lines: list[str], still_name: str) -> None:
    scroll_top(pg)
    pg.get_by_role("combobox", name="Pick one of the six demo bills").click()
    pg.get_by_role("option").nth(index).click()
    pg.wait_for_timeout(900)
    caption(pg, lines[0])
    pg.get_by_role("button", name="Run it up the ladder").click()
    expect_verdict(pg, expected)
    pg.wait_for_timeout(600)
    scroll_main(pg, 430)
    still(pg, still_name)
    for line in lines[1:]:
        caption(pg, line)
    scroll_main(pg, 380)
    pg.wait_for_timeout(1800)


def main() -> None:
    BUILD.mkdir(exist_ok=True)
    raw_dir = BUILD / "raw_video"
    shutil.rmtree(raw_dir, ignore_errors=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        ctx = browser.new_context(viewport=SIZE, record_video_dir=str(raw_dir), record_video_size=SIZE)
        pg = ctx.new_page()
        pg.goto(URL)
        pg.wait_for_selector("text=Run it up the ladder", timeout=60000)
        # Start from a clean world so the video is always the same story.
        pg.get_by_role("button", name="Reset demo").click()
        pg.wait_for_timeout(2500)
        pg.wait_for_selector("text=Run it up the ladder", timeout=60000)

        caption(pg, "TrustLadder: a hospital bill is carried up four stages (Detect, Decide, "
                    "Verify, Human review) and comes back with a verdict and a reason.", 5200)
        caption(pg, "Everything here is synthetic: fictional hospitals and patients, running offline "
                    "on a laptop.", 3800)

        # --- The hospital issues a bill
        tab(pg, "3 · Hospital (issuer)")
        caption(pg, "Step 0. The hospital issues a bill. Its billing system prints a random ticket "
                    "on the bill...")
        pg.get_by_role("button", name="Issue bill and publish").click()
        pg.wait_for_selector("text=Ticket printed on the bill", timeout=30000)
        scroll_main(pg, 330)
        caption(pg, "...and publishes two scrambled codes, signed with its own key. No name, no "
                    "amount, no diagnosis ever leaves the hospital.", 5200)
        still(pg, "hospital_publish")
        pg.get_by_role("button", name="Alter the total and check it").click()
        expect_verdict(pg, "Tampered")
        caption(pg, "Change one number on that bill, and the insurer's check immediately says "
                    "Tampered.", 3800)

        # --- The registry
        tab(pg, "4 · Registry")
        caption(pg, "The registry is a neutral host. It holds codes and signatures only, so even "
                    "a complete theft reveals nothing.")
        pg.get_by_role("button", name="Insert a forged entry").click()
        pg.wait_for_selector("text=Rejected: the hospital", timeout=15000)
        caption(pg, "Try to slip a fake line into a hospital's file: the signature breaks, and "
                    "every verifier ignores it. Only the hospital can add lines.")
        still(pg, "registry_forgery")

        # --- The insurer checks bills
        tab(pg, "1 · Check a bill")
        run_case(pg, 0, "Authentic", [
            "Case 1. A genuine bill from a hospital that has joined.",
            "The issuer confirms every field. That is proof-grade evidence, so it is paid "
            "straight through, with no person needed."], "case1_authentic")
        run_case(pg, 1, "Tampered", [
            "Case 2. The same bill with the total raised by Rs 1 lakh after it was issued.",
            "The issuer's record contradicts it, and independent checks agree: the sums, the "
            "file's history, the font. Two independent findings convict: Tampered."],
            "case2_tampered")
        pg.locator("[data-testid='stGraphVizChart']:visible").first.scroll_into_view_if_needed()
        pg.wait_for_timeout(1200)
        caption(pg, "The evidence graph: findings grouped into independent families. Four independent "
                    "lines point against this bill; the policy-as-code rule R2 turns them into the verdict.", 5600)
        still(pg, "evidence_graph")
        run_case(pg, 2, "Suspicious", [
            "Case 3. A bill made from nothing. It looks perfect and passes every "
            "appearance check.",
            "Edit detection finds nothing to see. Only the issuer can say: we never issued "
            "this ticket. One finding is not enough to convict, so a person looks at it."],
            "case3_suspicious")
        run_case(pg, 3, "Inconclusive", [
            "Case 4. A genuine bill from a small hospital that has not joined yet.",
            "No proof is available, and 'nothing looks wrong' is not proof. It is referred "
            "to a person, never cleared automatically."], "case4_inconclusive")
        run_case(pg, 4, "Inconclusive", [
            "Case 5. A genuine bill that arrived as a blurred photo.",
            "It could not be read, so it was never compared. A misread is never allowed to "
            "become a 'mismatch' that holds up a genuine customer."], "case5_unreadable")

        # --- Human review
        tab(pg, "2 · Human review")
        caption(pg, "Stage 4. Everything except Authentic lands with a person, with the evidence "
                    "and the reason attached.")
        pg.get_by_role("button", name="Fraud: reject and refer").first.click()
        pg.wait_for_timeout(1500)
        caption(pg, "The officer decides. Both the machine's verdict and the officer's decision "
                    "go into the audit trail.")
        still(pg, "human_review")

        # --- Policy & impact
        tab(pg, "5 · Policy & impact")
        caption(pg, "The institution sets what happens to each verdict. It can never pay a "
                    "document without proof: 'Pay' is offered only for Authentic.", 5200)
        scroll_main(pg, 120)
        still(pg, "policy_2of5")
        for label, line in (
            ("0 of 5 hospitals joined", "A simulated month of 150 claims. On day one, with no hospital "
                                        "joined, every bill goes to a person, as today. No fraud is paid."),
            ("4 of 5 hospitals joined", "As hospitals join, straight-through payment rises and review "
                                        "work falls. Frauds paid stays at zero."),
        ):
            pg.get_by_text(label, exact=True).click()
            pg.wait_for_timeout(1200)
            caption(pg, line, 5000)
        still(pg, "policy_4of5")
        caption(pg, "An edit-detection tool on the same claims pays every fake made from nothing, "
                    "and holds genuine customers whose files were merely re-saved.", 5600)
        # --- AI architecture
        tab(pg, "6 · AI architecture")
        caption(pg, "The AI architecture, drawn from the same file as the deck. Solid boxes run in this "
                    "demo today; dashed boxes are the target design using current AI models.", 5600)
        scroll_main(pg, 200)
        still(pg, "architecture")
        caption(pg, "Neural models read and gather evidence; a symbolic, published rule decides; people "
                    "own the exceptions.", 4600)
        caption(pg, "TrustLadder. Only proof clears. Unproven goes to a person. Wrongful holds are "
                    "counted, not hidden.", 5200)
        video_path = Path(pg.video.path())
        ctx.close()
        browser.close()

    mp4 = BUILD / "TrustLadder_demo.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(video_path),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-movflags", "+faststart",
                    str(mp4)], check=True)
    shutil.rmtree(raw_dir, ignore_errors=True)
    print(f"video: {mp4}  ({mp4.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    sys.exit(main())
