"""
Screen test cases (TC-20 .. TC-26), run against the real app script with
Streamlit's headless AppTest. They assert what a viewer would see: tab labels,
verdict banners, the review queue, the audit trail, the hospital flow, the
registry forgery check and the policy metrics.

Real-browser checks of the same surfaces are in tools/shots.py (TC-30).
"""

import json
import re
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from trustladder.cases import DATA_DIR

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def _app():
    return AppTest.from_file(APP, default_timeout=90).run()


def _verdict_shown(at) -> str:
    for m in at.markdown:
        hit = re.search(r'data-testid="tl-verdict">([A-Za-z]+)<', m.value)
        if hit:
            return hit.group(1)
    return ""


def test_tc20_app_loads_with_five_tabs():
    """TC-20. Steps: open the app.
    Expected: no exception; five tabs with the labels the script uses."""
    at = _app()
    assert not at.exception
    assert [t.label for t in at.tabs] == ["1 · Check a bill", "2 · Human review",
                                          "3 · Hospital (issuer)", "4 · Registry",
                                          "5 · Policy & impact"]


@pytest.mark.parametrize("index", range(6))
def test_tc21_each_demo_bill_shows_its_expected_verdict(index):
    """TC-21 (x6). Steps: pick demo bill N, press 'Run it up the ladder'.
    Expected: the verdict banner shows the manifest's expected verdict, and a
    'Reason.' paragraph is shown."""
    manifest = json.loads((DATA_DIR / "showcase" / "manifest.json").read_text())
    at = _app()
    sel = at.selectbox(key="showcase_pick")
    sel.set_value(sel.options[index]).run()
    at.button(key="run").click().run()
    assert not at.exception
    assert _verdict_shown(at) == manifest[index]["expected"]
    assert any(m.value.startswith("**Reason.**") for m in at.markdown)


def test_tc22_review_queue_and_audit_trail():
    """TC-22. Steps: run demo bill 2 (Tampered) and bill 1 (Authentic); in the
    review tab press 'Fraud: reject and refer' on the queued item.
    Expected: only bill 2 is queued; its status becomes 'Rejected and referred to
    investigation'; the audit trail holds the machine row and the officer row."""
    at = _app()
    sel = at.selectbox(key="showcase_pick")
    sel.set_value(sel.options[1]).run()
    at.button(key="run").click().run()
    sel = at.selectbox(key="showcase_pick")
    sel.set_value(sel.options[0]).run()
    at.button(key="run").click().run()
    queue = at.session_state["queue"]
    assert [q["file"] for q in queue] == ["02_altered_total_sahyog.pdf"]
    at.button(key="rej_0").click().run()
    assert at.session_state["queue"][0]["status"] == "Rejected and referred to investigation"
    actors = [row["actor"] for row in at.session_state["audit"]]
    assert "Claims officer (demo)" in actors and any(a.startswith("TrustLadder") for a in actors)


def test_tc23_hospital_issue_then_check_genuine_and_altered():
    """TC-23. Steps: in the hospital tab press 'Issue bill and publish', then
    'Check the genuine bill', then 'Alter the total and check it'.
    Expected: success message quoting the ticket; genuine -> Authentic;
    altered -> Tampered."""
    at = _app()
    at.button(key="h_issue").click().run()
    assert any("Ticket printed on the bill" in s.value for s in at.success)
    at.button(key="h_check_genuine").click().run()
    assert at.session_state["h_result"][0].verdict.value == "Authentic"
    at.button(key="h_check_altered").click().run()
    assert at.session_state["h_result"][0].verdict.value == "Tampered"
    assert not at.exception


def test_tc24_registry_rejects_forged_entry():
    """TC-24. Steps: in the registry tab press 'Insert a forged entry'.
    Expected: the green 'Rejected: the hospital's signature no longer matches'
    message; the directory table shows 'Patient data held' = None for every row."""
    at = _app()
    at.button(key="forge").click().run()
    assert any(s.value.startswith("Rejected: the hospital's signature") for s in at.success)
    table = next(d.value for d in at.dataframe if "Patient data held" in d.value.columns)
    assert set(table["Patient data held"]) == {"None"}
    assert set(table["Joined"]) == {"Yes", "No"}


def test_tc25_policy_tab_guarantee_and_scenarios():
    """TC-25. Steps: open the policy tab; inspect the Suspicious policy options;
    switch the coverage scenario from '0 of 5' to '4 of 5'.
    Expected: 'Pay' is not offered for Suspicious/Tampered/Inconclusive; 'Frauds
    paid' shows 0; the straight-through figure rises with coverage."""
    at = _app()
    for v in ("Suspicious", "Tampered", "Inconclusive"):
        assert "Pay" not in at.selectbox(key=f"pol_{v}").options
    def metric(label):
        return next(m.value for m in at.metric if m.label == label)
    at.radio(key="scenario").set_value("0 of 5 hospitals joined").run()
    low = int(metric("Paid straight through").rstrip("%"))
    assert metric("Frauds paid") == "0"
    at.radio(key="scenario").set_value("4 of 5 hospitals joined").run()
    high = int(metric("Paid straight through").rstrip("%"))
    assert metric("Frauds paid") == "0"
    assert high > low


def test_tc26_reset_restores_clean_state():
    """TC-26. Steps: run bill 2 (queues it), then press 'Reset demo'.
    Expected: queue and audit trail are empty; the six demo bills still exist
    and bill 1 is still Authentic after the rebuild."""
    at = _app()
    sel = at.selectbox(key="showcase_pick")
    sel.set_value(sel.options[1]).run()
    at.button(key="run").click().run()
    assert at.session_state["queue"]
    at.button(key="reset").click().run()
    assert at.session_state["queue"] == [] and at.session_state["audit"] == []
    at.button(key="run").click().run()
    assert _verdict_shown(at) == "Tampered"  # bill 2 is still selected after the reset
    sel = at.selectbox(key="showcase_pick")
    sel.set_value(sel.options[0]).run()
    at.button(key="run").click().run()
    assert _verdict_shown(at) == "Authentic"


def test_tc27_stage_order_matches_the_proposal():
    """TC-27. Steps: run demo bill 1; read the stage-card titles and the header.
    Expected: stages appear in the proposal's and panel's order:
    Detect, Decide, Verify, Human review (header and cards agree)."""
    at = _app()
    at.button(key="run").click().run()
    titles = [re.search(r'tl-stage-title">(.+?)<', m.value).group(1)
              for m in at.markdown if 'tl-stage-title' in m.value and '<div' in m.value]
    assert [t.split(" · ")[1].split(" (")[0] for t in titles] == ["Detect", "Decide", "Verify",
                                                                  "Human review"]
    assert any("Detect → Decide → Verify → Human review" in m.value for m in at.markdown)
