"""Quick visual check: screenshot every tab of the running demo (uses installed Chrome)."""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "shots"); OUT.mkdir(parents=True, exist_ok=True)
URL = "http://127.0.0.1:8765/"

with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    pg = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
    pg.goto(URL); pg.wait_for_selector("text=Run it up the ladder", timeout=60000)
    pg.screenshot(path=OUT / "00_landing.png", full_page=True)
    for i in range(6):
        pg.get_by_role("combobox", name="Pick one of the six demo bills").click()
        pg.get_by_role("option").nth(i).click()
        pg.wait_for_timeout(600)
        pg.get_by_role("button", name="Run it up the ladder").click()
        pg.wait_for_selector("[data-testid='tl-verdict']", timeout=30000)
        pg.wait_for_timeout(1200)
        pg.screenshot(path=OUT / f"01_case{i+1}.png", full_page=True)
    for n, tab in enumerate(["2 · Human review", "3 · Hospital (issuer)", "4 · Registry", "5 · Policy & impact"]):
        pg.get_by_role("tab", name=tab).click(); pg.wait_for_timeout(1200)
        if tab.startswith("3"):
            pg.get_by_role("button", name="Issue bill and publish").click(); pg.wait_for_timeout(2500)
            pg.get_by_role("button", name="Alter the total and check it").click(); pg.wait_for_timeout(2500)
        if tab.startswith("4"):
            pg.get_by_role("button", name="Insert a forged entry").click(); pg.wait_for_timeout(1500)
        pg.screenshot(path=OUT / f"0{n+2}_{tab[0]}.png", full_page=True)
    b.close()
print("ok", sorted(x.name for x in OUT.iterdir()))
