"""
Record the presenter's OWN demo path as a video, signed in as each role (test case TC-39).

    .venv/bin/python tools/record_roles.py "<folder with the four sets>" [url]

This is the backup for the day: the exact sequence rehearsed for the talk, on the Mac with
the hosted models on. It signs in as each role through the ordinary login (no presenter
story, no step bar): the hospital issues a bill and a summary; the customer submits the
four Desktop sets and then the two photographs; the officer opens the photographed forgery,
reads the evidence, the agent's account and the techniques used, and rejects it; the risk
head moves the policy; the registry rejects a forged line; the auditor reads the trail
and the model registry. Captions say what the viewer is looking at.

Every status and verdict is asserted; the recorder stops rather than record a wrong result.

Output: build/TrustLadder_demo_mac.mp4
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg
from playwright.sync_api import Page, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from record_demo import BUILD, PASSWORD, READ, SIZE, SLOW, caption, scroll, settle, top  # noqa: E402

URL = next((a for a in sys.argv[1:] if a.startswith("http")), "http://127.0.0.1:8765/")
SETS = Path(next(a for a in sys.argv[1:] if not a.startswith("http")))
LONG = 7000


def fail(msg: str) -> None:
    raise SystemExit(f"TC-39 FAILED: {msg}")


def ok(msg: str) -> None:
    print(f"TC-39 pass: {msg}")


def sign_in(pg: Page, user: str) -> None:
    pg.goto(URL, timeout=SLOW)
    pg.wait_for_selector("text=Sign in", timeout=SLOW)
    pg.get_by_role("textbox", name="User ID").fill(user)
    pg.get_by_role("textbox", name="Password").fill(PASSWORD)
    pg.get_by_role("button", name="Sign in").click()
    pg.wait_for_selector("text=Sign out", timeout=SLOW)
    settle(pg)


def sign_out(pg: Page) -> None:
    pg.evaluate("() => { const el = document.getElementById('tl-caption'); if (el) el.remove(); }")
    top(pg)
    pg.get_by_role("button", name="Sign out").click()
    pg.wait_for_selector("text=Sign in", timeout=SLOW)


def submit_pair(pg: Page, bill: Path, summary: Path, picture: bool) -> str:
    """Attach the two files, confirm readings when they are pictures, submit; return the status."""
    pg.wait_for_selector("[data-testid='stMain'] input[type='file']", state="attached", timeout=SLOW)
    slots = pg.locator("[data-testid='stMain'] input[type='file']")
    slots.nth(0).set_input_files(str(bill))
    settle(pg)
    if picture:
        pg.wait_for_selector("text=These are the details on my bill", timeout=SLOW)
        settle(pg)
        if "Here is what the vision model read" not in pg.inner_text("body"):
            fail("the bill photo was not read by the vision model (models off or quota)")
        caption(pg, "A phone photo of the Marathi bill. The vision model read every field, in Marathi, "
                    "with a confidence each; the customer checks and confirms.", LONG)
        pg.get_by_text("These are the details on my bill").click()
        settle(pg)
    else:
        pg.wait_for_selector("[data-testid='stMain'] figure.tl-doc img", timeout=SLOW)
    slots.nth(1).set_input_files(str(summary))
    settle(pg)
    if picture:
        pg.wait_for_selector("text=These are the details on my discharge summary", timeout=SLOW)
        settle(pg)
        caption(pg, "The scanned discharge summary, read the same way. Two documents, two readings, "
                    "both confirmed before anything is compared.", LONG)
        pg.get_by_text("These are the details on my discharge summary").click()
        settle(pg)
    for _ in range(120):
        if pg.get_by_role("button", name="Submit claim").last.is_enabled():
            break
        pg.wait_for_timeout(1000)
    else:
        fail("Submit stayed disabled")
    pg.get_by_role("button", name="Submit claim").last.click()
    settle(pg)
    pg.wait_for_selector("[data-testid='tl-claim-status']", timeout=SLOW)
    if pg.locator("[data-testid='tl-verdict']:visible").count():
        fail("the customer is being shown a verdict")
    return pg.locator("[data-testid='tl-claim-status']").last.inner_text().strip()


def main() -> None:
    folders = sorted(p for p in SETS.iterdir() if p.is_dir() and p.name[0] in "1234")
    if len(folders) != 4:
        fail(f"expected four set folders under {SETS}")
    pdfs = {f.name[0]: sorted(f.glob("*.pdf")) for f in folders}
    photos = sorted(folders[3].glob("*.jpg"))
    if len(photos) != 2:
        fail("folder 4 should hold the two pictures (09 and 10)")

    raw_dir = BUILD / "raw_video_mac"
    shutil.rmtree(raw_dir, ignore_errors=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        ctx = browser.new_context(viewport=SIZE, record_video_dir=str(raw_dir), record_video_size=SIZE)
        pg = ctx.new_page()

        # 0. A clean world
        sign_in(pg, "presenter")
        pg.get_by_role("button", name="Reset demo").click()
        settle(pg)
        if "Hosted AI models: on" not in pg.inner_text("body"):
            fail("the hosted models are not on; start the app with the key")
        sign_out(pg)

        # 1. Hospital
        sign_in(pg, "hospital")
        caption(pg, "TrustLadder. One patient, Meera Kulkarni, and the people who handle her claim. "
                    "First the hospital: at discharge it issues the bill and the discharge summary.", LONG)
        pg.get_by_role("button", name="Issue bill and discharge summary, and publish").click()
        settle(pg)
        pg.wait_for_selector("text=with discharge summary", timeout=SLOW)
        caption(pg, "One press issues both. A random ticket on each document; two scrambled codes per document "
                    "go to the registry. No name, no amount, no diagnosis leaves the hospital.", LONG)
        scroll(pg, 900)
        caption(pg, "Techniques used at issue: a random ticket, scrypt hash commitment, an Ed25519 signature, "
                    "privacy by design. No AI on this screen, by design.", LONG)
        ok("hospital issued both documents")
        sign_out(pg)

        # 2. Customer: the four sets, then the pictures
        sign_in(pg, "customer")
        pg.get_by_text("Submit a claim", exact=True).first.click()
        settle(pg)
        caption(pg, "The customer. One screen, two required slots: the bill and the discharge summary. "
                    "Set 1: the English bill and its summary, as the hospital issued them.", LONG)
        expected = {"1": "Approved", "2": "Approved", "3": "On hold", "4": "On hold"}
        lines = {
            "1": "Both read exactly from the text layer; the hospital's record confirms both; the bill is dated "
                 "inside the stay. Approved at once, no officer involved. The customer sees only the status.",
            "2": "Set 2: the hospital's own bilingual Marathi print. Read as exactly as English, with no model: "
                 "multilingual is not an AI problem when the document is born digital. Approved.",
            "3": "Set 3: the English bill with its total raised in a PDF editor. The hospital's record disagrees. "
                 "The customer is told only On hold. Telling a forger what was caught would coach them.",
            "4": "Set 4: the Marathi bill, total raised. The same answer: On hold. The verdict waits for the officer.",
        }
        for key in "1234":
            bill, summary = pdfs[key]
            status = submit_pair(pg, bill, summary, picture=False)
            if status != expected[key]:
                fail(f"set {key}: customer sees {status}, expected {expected[key]}")
            ok(f"set {key}: {status}")
            scroll(pg, 1400)
            caption(pg, lines[key], LONG)
            top(pg)
            if key != "4":
                caption(pg, f"Next set.", 1200)
        caption(pg, "Now the pictures: the forged Marathi bill photographed on a phone, and its summary as a "
                    "scan. This is where the AI earns its place: no code can be computed from a photo "
                    "without reading it first.", LONG)
        status = submit_pair(pg, photos[0], photos[1], picture=True)
        if status != "On hold":
            fail(f"photos: customer sees {status}, expected On hold")
        ok("photos: On hold")
        scroll(pg, 1400)
        caption(pg, "How your documents were read: the vision model, the language it saw, the lowest confidence, "
                    "a second reader agreeing, the customer's confirmation. Never the verdict.", LONG)
        sign_out(pg)

        # 3. Officer
        sign_in(pg, "officer")
        caption(pg, "The claims officer. A work queue, not an answer: only what could not be proven is here. "
                    "The two approved claims never reached a person.", LONG)
        pg.get_by_role("button", name="Open").first.click()
        pg.wait_for_selector("[data-testid='tl-verdict']", timeout=SLOW)
        settle(pg)
        verdict = pg.locator("[data-testid='tl-verdict']").first.inner_text()
        if "Tampered" not in verdict:
            fail(f"officer: verdict '{verdict.strip()[:40]}', expected Tampered")
        body = pg.inner_text("body")
        if "Meera Kulkarni" not in body or "SMH/2026/004877" not in body:
            fail("officer: the opened claim is not the photographed Marathi bill")
        ok("officer: the photographed forgery is Tampered")
        caption(pg, "Tampered. The hospital issued this ticket, but not with these details. Two readers and the "
                    "customer agreed on what the picture says; the hospital's record disagrees. That is proof.", LONG)
        scroll(pg, 700)
        caption(pg, "What was found: the registry's contradiction, the file saved in layers by a different program, "
                    "the screening model's score, and the same bill claimed before.", LONG)
        # the agent's account may still be being written in the background: give it a moment
        for _ in range(40):
            if "chose the order of these checks" in pg.inner_text("body"):
                break
            pg.wait_for_timeout(3000)
            pg.reload(); pg.wait_for_selector("text=Sign out", timeout=SLOW); settle(pg)
            if not pg.locator("[data-testid='tl-verdict']").count():
                pg.get_by_role("button", name="Open").first.click()
                pg.wait_for_selector("[data-testid='tl-verdict']", timeout=SLOW); settle(pg)
        pg.get_by_text("How the evidence was gathered").scroll_into_view_if_needed()
        pg.wait_for_timeout(500)
        caption(pg, "How the evidence was gathered: the agent chose the order of the checks and gave its reason for "
                    "each. It gathers and narrates; it never decides. Any check it skips, the guard runs.", LONG)
        pg.get_by_text("Techniques used on this claim").scroll_into_view_if_needed()
        pg.wait_for_timeout(500)
        caption(pg, "Techniques used on this claim: every AI concept, the model behind it, what it did here, and the "
                    "threshold it applied. Greyed rows did not apply, and say why.", LONG)
        scroll(pg, 500)
        caption(pg, "Confidence gate 0.70, corroboration by a second reader, screening at 0.60, the rule R2, "
                    "the registry's exact codes, the reason writer's check-back.", LONG)
        top(pg)
        pg.get_by_role("button", name="Reject as fraud").click()
        settle(pg)
        pg.wait_for_selector("text=Decided recently", timeout=SLOW)
        caption(pg, "The officer rejects. The machine's verdict and the person's decision are kept side by side; "
                    "where they differ, the person's decision stands and the disagreement is the record.", LONG)
        ok("officer rejected the claim")
        sign_out(pg)

        # 4. Risk head
        sign_in(pg, "riskhead")
        caption(pg, "The risk head. Fraud stopped and honest customers wrongly held, side by side. A system that "
                    "only reports catches is not safe to deploy.", LONG)
        scroll(pg, 600)
        pg.get_by_role("combobox", name="Inconclusive").click()
        pg.get_by_role("option", name="Ask customer for original").click()
        settle(pg)
        caption(pg, "The policy is the risk head's: move one menu and the routing line moves. The one thing no "
                    "setting can do is approve a bill the hospital has not confirmed.", LONG)
        pg.get_by_role("combobox", name="Inconclusive").click()
        pg.get_by_role("option", name="Human review").click()
        settle(pg)
        ok("risk head moved the policy and put it back")
        sign_out(pg)

        # 5. Registry
        sign_in(pg, "registry")
        caption(pg, "The registry. Codes and signatures only. Patient data held: none.", LONG)
        pg.get_by_text("Check that nobody has tampered with the registry").click()
        pg.wait_for_timeout(800)
        pg.get_by_role("button", name="Try to insert a forged entry").click()
        settle(pg)
        if "Rejected: the hospital" not in pg.inner_text("body"):
            fail("registry: the forged entry was not rejected")
        scroll(pg, 500)
        caption(pg, "A forged line is rejected: the hospital signed its file, and the registry host cannot "
                    "alter or forge an entry.", LONG)
        ok("registry rejected a forged entry")
        sign_out(pg)

        # 6. Auditor
        sign_in(pg, "auditor")
        pg.get_by_text("Audit trail", exact=True).first.click()
        settle(pg)
        caption(pg, "The auditor. Every verdict and every human decision, with the model that read, gathered "
                    "and wrote for each claim.", LONG)
        pg.get_by_text("How decisions are made", exact=True).first.click()
        settle(pg)
        pg.wait_for_selector("text=The models around the rule", timeout=SLOW)
        caption(pg, "How decisions are made: the rule, printed, and the model registry: five models, none of "
                    "which decides, each with its version and its limit.", LONG)
        pg.get_by_text("The models around the rule").scroll_into_view_if_needed()
        pg.wait_for_timeout(500)
        caption(pg, "TrustLadder. Only proof clears. Unproven goes to a person. Wrongful holds are counted, "
                    "not hidden.", LONG)
        ok("auditor: trail and model registry")
        video = pg.video.path()
        ctx.close()
        browser.close()

    mp4 = BUILD / "TrustLadder_demo_mac.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(video),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-movflags", "+faststart",
                    str(mp4)], check=True)
    shutil.rmtree(raw_dir, ignore_errors=True)
    print(f"TC-39: recorded {mp4} ({mp4.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
