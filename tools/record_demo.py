"""
Records the backup video of the demo as one story across every role, and checks
the real browser surface while doing it (test case TC-30).

    .venv/bin/python tools/record_demo.py                 # local app on :8765
    .venv/bin/python tools/record_demo.py <url> --check   # browser checks only, no video

The story (signed in as the presenter, who steps through the six screens):
  0  sign in
  1  Hospital      issues a bill; the genuine PDF is downloaded
  2  Customer      uploads that PDF on the one upload screen: Paid. The customer is
                   shown the STATUS of their claim, never the verdict or the findings;
                   then a PHOTO of a bill printed in Marathi: the vision model reads
                   it, the customer confirms what it read, and it is Paid on the
                   hospital's proof (or, with the hosted models off, sent back for
                   the original: the screen says which reader ran); then, on that
                   SAME screen, the bill is edited in the demo toolkit (the forger's
                   editor, which needs no upload of its own) and the edited copy is
                   uploaded: Tampered
  3  Officer       a QUEUE of pending claims; opens the top one (same bill number),
                   sees WHAT was found, how the bill was read, how the evidence was
                   gathered (the agent's trace), and rejects it
  4  Risk head     impact dashboard; a separate what-if as hospitals join
  5  Registry      the network; a forged line is rejected
  6  Auditor       the trail of everything above; the rule and the AI architecture

There is exactly one place a bill is uploaded, and it takes a genuine bill or a
tampered one; the officer has no separate sample-bill tab.

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


def clear_uploads(pg: Page) -> None:
    """Remove every file from the customer's packet box, so the next upload is a new packet."""
    for _ in range(4):
        x = pg.locator("[data-testid='stMain'] [data-testid='stFileChipDeleteBtn']").first
        if not x.count():
            break
        x.click()
        pg.wait_for_timeout(400)
    settle(pg)


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


def expect_claim_status(pg: Page, expected: str, what: str) -> None:
    """What the CUSTOMER sees: the status of their claim, never a verdict."""
    loc = pg.locator("[data-testid='tl-claim-status']:visible").last
    loc.wait_for(timeout=SLOW)
    shown = loc.inner_text().strip()
    if shown != expected:
        raise SystemExit(f"TC-30 FAILED: {what}: expected {expected}, browser shows {shown}")
    if pg.locator("[data-testid='tl-verdict']:visible").count():
        raise SystemExit(f"TC-30 FAILED: {what}: the customer is being shown a verdict")
    for banned in ("What was found", "Rule R"):
        if banned in pg.inner_text("body"):
            raise SystemExit(f"TC-30 FAILED: {what}: '{banned}' is on the customer's screen")
    print(f"TC-30 pass: {what} = {expected} (and no verdict shown to the customer)")


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
        caption(pg, "TrustLadder: one hospital bill, seen by every party. The presenter follows six "
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
        expect_text(pg, "Issue bill and discharge summary, and publish", "step 1: hospital")
        pg.wait_for_timeout(READ)
        pg.get_by_role("button", name="Issue bill and discharge summary, and publish").click()
        settle(pg)
        expect_text(pg, "with discharge summary", "bill and discharge summary issued and published")
        with pg.expect_download() as d1:
            pg.get_by_role("button", name="Download the bill (PDF)").click()
        genuine = Path(BUILD / "demo_files" / d1.value.suggested_filename)
        genuine.parent.mkdir(parents=True, exist_ok=True)
        d1.value.save_as(str(genuine))
        print(f"TC-30 pass: downloaded the genuine bill {genuine.name}")
        with pg.expect_download() as d1b:
            pg.get_by_role("button", name="Download the discharge summary (PDF)").click()
        summary = genuine.parent / d1b.value.suggested_filename
        d1b.value.save_as(str(summary))
        print(f"TC-30 pass: downloaded the discharge summary {summary.name}")
        pg.wait_for_timeout(READ)
        still(pg, "hospital_issue")

        # 2. The customer uploads that bill
        next_step()
        expect_text(pg, "Your claim documents", "step 2: customer upload screen takes the claim packet")
        # scope to the main area: the presenter's sidebar toolkit also has an upload box.
        # The first packet is the bill AND the discharge summary, attached together.
        pg.locator("[data-testid='stMain'] input[type='file']").first.set_input_files([str(genuine), str(summary)])
        settle(pg)
        expect_text(pg, "Discharge summary", "step 2: the packet recognised the discharge summary")
        # The bill must be on screen, and ABOVE the Submit button, before anything is sent.
        if "Check this is the right bill" not in pg.inner_text("body"):
            raise SystemExit("TC-30 FAILED: the customer cannot see the bill before submitting")
        order = pg.evaluate("""() => {
            const main = document.querySelector("[data-testid='stMain']");
            const img = main.querySelector("img");
            const btns = [...main.querySelectorAll("button")];
            const submit = btns.find(b => b.innerText.trim().startsWith("Submit claim"));
            if (!img || !submit) return "missing";
            return (img.compareDocumentPosition(submit) & Node.DOCUMENT_POSITION_FOLLOWING)
                ? "preview first" : "button first";
        }""")
        if order != "preview first":
            raise SystemExit(f"TC-30 FAILED: the Submit button comes before the bill ({order})")
        print("TC-30 pass: step 2: the customer sees the bill above the Submit button")
        pg.get_by_role("button", name="Submit claim").click()
        settle(pg)
        expect_claim_status(pg, "Paid", "step 2: the customer's own packet (bill + summary), paid on proof")
        clear_uploads(pg)          # a new packet starts clean
        pg.wait_for_timeout(READ)
        still(pg, "customer_upload")

        # Second upload, same screen: a PHOTO of a bill printed in Marathi. The vision
        # model reads it, the customer checks the reading and confirms, and the hospital's
        # record confirms the photo. With the hosted models off (no key on this server)
        # the honest path is shown instead: the reading form says no model answered and
        # the claim is sent back for the original.
        models_on = "Hosted AI models: on" in pg.inner_text("body")
        with pg.expect_download() as d3:
            pg.get_by_role("button", name="Download sample bills (zip)").click()
        import zipfile as _zip
        pack = Path(BUILD / "demo_files" / d3.value.suggested_filename)
        d3.value.save_as(str(pack))
        with _zip.ZipFile(pack) as z:
            member = next(n for n in z.namelist() if n.endswith("08_marathi_bill_photo.jpg"))
            photo = pack.parent / "08_marathi_bill_photo.jpg"
            photo.write_bytes(z.read(member))
        pg.locator("[data-testid='stMain'] input[type='file']").first.set_input_files(str(photo))
        settle(pg)
        pg.wait_for_selector("text=These are the details on my bill", timeout=SLOW)
        body = pg.inner_text("body")
        if models_on and "Here is what the vision model read from your photo" not in body:
            # Say what the screen DID show before failing: usually the hosted model's
            # quota or capacity, which is a deployment fact the presenter must know.
            shown = next((ln for ln in body.splitlines() if "read" in ln.lower() and "model" in ln.lower()), "")
            print(f"TC-30 note: the reading form said: {shown[:160]!r}")
        if models_on:
            expect_text(pg, "Here is what the vision model read from your photo",
                        "step 2: the vision model read the photo")
            expect_text(pg, "Marathi", "step 2: the language of the printed bill is named")
            expect_text(pg, "SMH/2026/004877", "step 2: the bill number was read from the photo")
        else:
            expect_text(pg, "no reading model is configured",
                        "step 2: with models off, the screen says no model read the photo")
        if pg.get_by_role("button", name="Submit claim").is_enabled():
            raise SystemExit("TC-30 FAILED: step 2: a photo could be submitted before it was confirmed")
        print("TC-30 pass: step 2: a photo cannot be submitted until the customer confirms the reading")
        pg.get_by_text("These are the details on my bill").click()
        settle(pg)
        still(pg, "customer_photo_reading")
        pg.get_by_role("button", name="Submit claim").click()
        settle(pg)
        if models_on:
            expect_claim_status(pg, "Paid", "step 2: a phone photo of a Marathi bill, paid on proof")
            print("TC-30 pass: step 2: the photo was read by the vision model and paid on the hospital's proof")
        else:
            expect_claim_status(pg, "Waiting for customer", "step 2: unread photo sent back for the original")
            print("TC-30 pass: step 2: with models off, the photo is sent back for the original, never judged")
        pg.wait_for_timeout(READ)
        still(pg, "customer_photo")

        # Still step 2, same screen, same button: the forger edits that bill in the demo
        # toolkit and the SAME customer uploads the edited copy here. No step change,
        # because there is one customer step and one place to upload a bill.
        if pg.get_by_text("Second customer").count():
            raise SystemExit("TC-30 FAIL: the story must stay with one customer account")
        pg.get_by_text("Demo toolkit: the forger's PDF editor").click()
        pg.wait_for_timeout(900)
        # The toolkit starts from the bill it already holds, so the presenter is never
        # asked for the same file twice. Exactly one upload box may be on screen here.
        n_up = pg.locator("input[type='file']").count()
        if n_up != 1:
            raise SystemExit(f"TC-30 FAIL: expected one upload box on screen, found {n_up}")
        print("TC-30 pass: step 2: exactly one upload box on screen (the claim form)")
        with pg.expect_download() as d2:
            pg.get_by_role("button", name="Download the edited copy").click()
        tampered = genuine.parent / d2.value.suggested_filename
        d2.value.save_as(str(tampered))
        print(f"TC-30 pass: the forger's toolkit produced {tampered.name}")
        clear_uploads(pg)
        pg.locator("[data-testid='stMain'] input[type='file']").first.set_input_files(str(tampered))
        settle(pg)
        pg.get_by_role("button", name="Submit claim").click()
        settle(pg)
        expect_claim_status(pg, "On hold", "step 2: the edited copy, same screen, same customer")
        pg.wait_for_timeout(READ)
        still(pg, "customer_tampered")


        # 3. The officer works a queue: nothing is open until a claim is chosen.
        next_step()
        expect_text(pg, "Waiting for a decision", "step 3: the inbox is a queue of pending claims")
        if pg.locator("[data-testid='tl-verdict']:visible").count():
            raise SystemExit("TC-30 FAILED: step 3: a verdict is shown before a claim is chosen")
        print("TC-30 pass: step 3: no verdict until the officer picks a claim")
        pg.wait_for_timeout(READ)
        still(pg, "officer_queue")
        pg.get_by_role("button", name="Open").first.click()
        settle(pg)
        expect_verdict(pg, "Tampered", "step 3: the claim the officer opened")
        expect_text(pg, "bill SMH/", "step 3: the same bill number")
        expect_text(pg, "What was found", "step 3: the officer can see WHY, without any switch")
        expect_text(pg, "Read from the PDF's text layer", "step 3: how the bill was read is stated")
        expect_text(pg, "How the evidence was gathered", "step 3: the agent's trace is on screen")
        expect_text(pg, "discharge summary", "step 3: the packet's second document is accounted for")
        expect_text(pg, "Asked the issuer's registry", "step 3: the trace names the checks in words")
        if "Reason written by the language model" not in pg.inner_text("body") and \
           "Reason assembled from fixed sentences" not in pg.inner_text("body"):
            raise SystemExit("TC-30 FAILED: step 3: the reason does not say who wrote it")
        print("TC-30 pass: step 3: the reason says whether a model or the template wrote it")
        pg.wait_for_timeout(READ)
        still(pg, "officer_claim")
        scroll(pg, 700)
        still(pg, "officer_evidence_trace")
        top(pg)
        pg.get_by_role("button", name="Reject as fraud").first.click()
        settle(pg)
        # the officer must be able to SEE what they just did
        expect_text(pg, "Rejected and referred to investigation",
                    "step 3: the decision is confirmed back on the queue")
        if "Decided recently" not in pg.inner_text("body"):
            raise SystemExit("TC-30 FAILED: step 3: no way to look back at decided claims")
        print("TC-30 pass: step 3: decided claims can be looked at again")
        pg.wait_for_timeout(READ)
        still(pg, "officer_decided")

        # 4. Risk head
        next_step()
        expect_text(pg, "Chief Risk Officer", "step 4: the screen names the role")
        expect_text(pg, "wrongly held", "step 4: both sides of the error are shown")
        # the one control on this screen must visibly change something
        before = pg.inner_text("body").split("would go:")[1].split("\n")[0]
        pg.get_by_role("combobox", name="Inconclusive").click()
        pg.get_by_role("option", name="Ask customer for original").click()
        settle(pg)
        after = pg.inner_text("body").split("would go:")[1].split("\n")[0]
        if after == before:
            raise SystemExit("TC-30 FAILED: step 4: changing the policy changed nothing on screen")
        print("TC-30 pass: step 4: changing the policy moves the counts on screen")
        pg.wait_for_timeout(READ)
        still(pg, "risk_dashboard")

        # 5. Registry
        next_step()
        expect_text(pg, "patient records held", "step 5: registry network")
        pg.wait_for_timeout(READ // 2)
        pg.get_by_text("Check that nobody has tampered with the registry").click()
        pg.wait_for_timeout(600)
        pg.get_by_role("button", name="Try to insert a forged entry").click()
        settle(pg)
        expect_text(pg, "Rejected: the hospital", "forged registry line rejected")
        pg.wait_for_timeout(READ)
        still(pg, "registry_network")

        # 6. Auditor
        next_step()
        # The table is drawn on a canvas; the summary line under it is plain text.
        expect_text(pg, "5 rejected", "step 6: officer's rejection in the audit summary (4 seeded + 1)")
        pg.wait_for_timeout(READ)
        still(pg, "audit_trail")
        menu(pg, "How decisions are made")
        expect_text(pg, "The models around the rule", "step 6: the model registry is published")
        expect_text(pg, "Vision-language reader", "step 6: the registry names the reading model")
        still(pg, "auditor_models")
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
