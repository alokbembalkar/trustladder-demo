"""
Screen test cases (TC-20 .. TC-29), run against the real app script with
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
    Expected: no exception; six tabs with the labels the script uses."""
    at = _app()
    assert not at.exception
    assert [t.label for t in at.tabs] == ["1 · Check a bill", "2 · Human review",
                                          "3 · Hospital (issuer)", "4 · Registry",
                                          "5 · Policy & impact", "6 · AI architecture"]


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


def test_tc28_architecture_tab_matches_the_shared_spec():
    """TC-28. Steps: open the AI architecture tab.
    Expected: every component in architecture.json is drawn, solid when built and
    dashed when target; the tagline in the header is the spec's tagline; the
    built/target counts shown add up to the spec."""
    spec = json.loads((Path(APP).parent / "trustladder" / "architecture.json").read_text())
    at = _app()
    html = next(m.value for m in at.markdown if 'data-testid="tl-architecture"' in m.value)
    comps = [c for layer in spec["layers"] + [spec["foundation"]] for c in layer["components"]]
    for c in comps:
        cls = "built" if c["built"] else "target"
        assert f'<div class="tl-comp {cls}">{c["label"]}</div>' in html, c["label"]
    assert any(spec["tagline"] in m.value for m in at.markdown)
    built = sum(c["built"] for c in comps)
    assert any(m.value == f"**Built in this demo ({built})**" for m in at.markdown)
    assert any(m.value == f"**Target design ({len(comps) - built})**" for m in at.markdown)


def test_tc29_stage_cards_carry_ai_inside_and_evidence_graph():
    """TC-29. Steps: run demo bill 2 (altered total).
    Expected: each stage card shows its 'AI inside (target design)' line from the
    spec; the Decide card shows '4 independent lines against'; an evidence graph
    is drawn containing the rule id R2 and the verdict Tampered; the reason is
    labelled as a template with GraphRAG as the target design."""
    spec = json.loads((Path(APP).parent / "trustladder" / "architecture.json").read_text())
    at = _app()
    sel = at.selectbox(key="showcase_pick")
    sel.set_value(sel.options[1]).run()
    at.button(key="run").click().run()
    for name, stage in spec["stages"].items():
        assert any(stage["ai_inside"] in m.value for m in at.markdown), name
    assert any("4 independent lines against" in m.value for m in at.markdown)
    graphs = at.get("graphviz_chart")
    assert graphs, "no evidence graph drawn"
    dot = graphs[0].proto.spec
    assert "R2" in dot and "Tampered" in dot
    assert any("GraphRAG" in c.value and "template" in c.value for c in at.caption)
