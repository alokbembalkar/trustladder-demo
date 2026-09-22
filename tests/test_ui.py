"""
Screen test cases for the role-based app (TC-40 .. TC-53), run against the real
app script with Streamlit's headless AppTest. Every test starts from the seeded
sample world (restored from its snapshot), so tests never depend on each other.

Real-browser checks of the same surfaces are in tools/record_demo.py (TC-30,
local) and the live-site check (TC-31).
"""

import json
import re
import sqlite3
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from trustladder.cases import DATA_DIR
from trustladder.registry import RegistryStore, Verifier
from trustladder.seed import DEMO_PASSWORD, db_path, restore_snapshot
from trustladder.service import submit_claim
from trustladder.store import Store

APP = str(Path(__file__).resolve().parent.parent / "app.py")
SPEC = json.loads((Path(APP).parent / "trustladder" / "architecture.json").read_text())

MENUS = {
    "hospital": ["Issue a bill", "My bills"],
    "customer": ["My claims", "Submit a claim"],
    "officer": ["Claims inbox", "Try a sample bill"],
    "riskhead": ["Dashboard"],
    "registry": ["Network"],
    "auditor": ["Audit trail", "How decisions are made"],
}


@pytest.fixture(autouse=True)
def fresh_world():
    """Every screen test starts from the seeded sample world."""
    restore_snapshot(DATA_DIR)
    st.cache_resource.clear()
    st.cache_data.clear()
    yield


def _login(user_id: str, password: str = DEMO_PASSWORD) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.text_input(key="login_user").input(user_id)
    at.text_input(key="login_pw").input(password)
    next(b for b in at.button if b.label == "Sign in").click().run()
    return at


def _go(at: AppTest, role: str, page: str) -> AppTest:
    at.radio(key=f"nav_{role}").set_value(page).run()
    return at


def _texts(at: AppTest) -> str:
    return " ".join(m.value for m in at.markdown) + " " + " ".join(c.value for c in at.caption)


def _verdict(at: AppTest) -> str:
    for m in at.markdown:
        hit = re.search(r'data-testid="tl-verdict">([A-Za-z]+)<', m.value)
        if hit:
            return hit.group(1)
    return ""


def _store() -> Store:
    return Store(db_path(DATA_DIR))


# ---------------------------------------------------------------- login

def test_tc40_login_page_and_wrong_password():
    """TC-40. Steps: open the app; sign in with a wrong password; then an unknown user.
    Expected: login page shows the logo and a Sign in form, no menu; both bad
    attempts show 'do not match' and stay on the login page."""
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception
    assert any("TrustLadder logo" in m.value for m in at.markdown)
    assert not [r for r in at.radio if (r.key or "").startswith("nav_")]
    for uid, pw in (("officer", "wrong-password"), ("nobody", DEMO_PASSWORD)):
        at = _login(uid, pw)
        assert any("do not match" in e.value for e in at.error)
        assert "user" not in at.session_state


def test_tc41_each_role_lands_on_its_own_home_with_its_own_menu():
    """TC-41. Steps: sign in as each of the six role users.
    Expected: no exception; the menu holds exactly that role's items; the first
    page is the role's home; the sidebar shows the role badge."""
    homes = {"hospital": "Issue a bill", "customer": "Hello, Meera", "officer": "Claims inbox",
             "riskhead": "Dashboard", "registry": "Network", "auditor": "Audit trail"}
    for role, items in MENUS.items():
        at = _login(role)
        assert not at.exception, role
        nav = [r for r in at.radio if r.key == f"nav_{role}"]
        # a role with a single screen has no menu at all: nothing to get lost in
        assert (nav[0].options if nav else [homes[role]]) == items, role
        assert homes[role] in _texts(at), role
        assert 'class="tl-role"' in _texts(at)


def test_tc42_role_isolation():
    """TC-42. Steps: sign in as the customer, then as the hospital.
    Expected: the customer sees only Meera's own claims (3) and no officer or
    risk pages; the hospital's screens never show a claim or who checked a bill."""
    at = _login("customer")
    assert _texts(at).count("claim CLM-") == 3
    for other in ("Claims inbox", "Impact dashboard", "Audit trail", "Network"):
        assert other not in at.radio(key="nav_customer").options
    at = _go(_login("hospital"), "hospital", "My bills")
    assert "CLM-" not in _texts(at)
    assert "never see who checked" in _texts(at)


def test_tc43_sign_out():
    """TC-43. Steps: sign in as the auditor, press Sign out.
    Expected: back on the login page, session cleared."""
    at = _login("auditor")
    at.button(key="logout").click().run()
    assert "user" not in at.session_state
    assert any(b.label == "Sign in" for b in at.button)


# ---------------------------------------------------------------- the cross-role story

def test_tc44_hospital_to_customer_to_officer_to_auditor():
    """TC-44. Steps: the hospital issues a bill to Meera; Meera submits it as a
    claim; the officer opens All claims; the auditor searches the trail.
    Expected: the bill appears in Meera's documents; the claim is Paid on proof
    (Authentic); the officer's list shows it; the auditor sees the issue and the
    verdict events."""
    at = _login("hospital")
    patient = at.selectbox(key="h_patient")
    patient.select_index(patient.options.index("Meera Kulkarni (Pune)")).run()
    at.button(key="h_issue").click().run()
    assert not at.exception
    ok = next(s.value for s in at.success if "Ticket printed on the bill" in s.value)
    bill_no = re.search(r"Issued (\S+)", ok).group(1)

    at = _go(_login("customer"), "customer", "Submit a claim")
    option = next(o for o in at.radio(key="cs_pick").options if o.startswith(bill_no))
    at.radio(key="cs_pick").set_value(option).run()
    at.button(key="cs_submit").click().run()
    claim = _store().claim(at.session_state["cs_last"])
    assert claim["verdict"] == "Authentic" and claim["status"] == "Paid"
    assert "Paid" in _texts(at)

    assert claim["claim_id"] in [c["claim_id"] for c in _store().claims()]

    at = _login("auditor")
    at.text_input(key="aud_q").input(claim["claim_id"]).run()
    events = at.dataframe[0].value["Event"].tolist()
    assert "Claim submitted" in events and "Authentic (R1)" in events
    assert any(e["event"] == "Bill issued and published" for e in _store().audit())


def test_tc45_customer_sends_clearer_copy():
    """TC-45. Steps: Meera opens My claims; for the claim waiting on her, picks
    the Arogya bill from her documents and presses Send clearer copy.
    Expected: that claim is re-judged Authentic and shows Paid; the trail logs
    'Clearer copy re-submitted'."""
    waiting = _store().claims(customer_id="CUST-001", statuses=("Waiting for customer",))
    assert len(waiting) == 1
    cid = waiting[0]["claim_id"]
    at = _login("customer")
    sel = at.selectbox(key=f"cc_pick_{cid}")
    sel.set_value(next(o for o in sel.options if "Arogya" in o)).run()
    at.button(key=f"cc_send_{cid}").click().run()
    after = _store().claim(cid)
    assert after["verdict"] == "Authentic" and after["status"] == "Paid"
    assert any(e["event"] == "Clearer copy re-submitted" for e in _store().audit())


def test_tc46_officer_decision_moves_the_numbers():
    """TC-46. Steps: the officer opens the first claim in the inbox (on hold) and
    presses 'Reject as fraud'.
    Expected: the claim becomes Rejected; the risk head's 'stopped as fraud' count rises
    by one; the audit trail records the decision next to the machine verdict."""
    before = _store().measures()["rejected"]
    at = _login("officer")
    first = at.selectbox(key="inbox_pick").value
    cid = first.split(" · ")[-1]
    assert "On hold" in first
    at.button(key=f"dec_reject_{cid}").click().run()
    assert _store().claim(cid)["status"] == "Rejected"
    assert _store().measures()["rejected"] == before + 1
    at = _login("riskhead")
    assert f'<div class="v">{before + 1}</div><div class="l">stopped as fraud after review' in _texts(at)
    ev = [e for e in _store().audit() if e["claim_id"] == cid]
    assert any(e["event"] == "Rejected and referred to investigation" and "machine verdict was" in e["detail"]
               for e in ev)


def test_tc47_risk_policy_change_routes_new_claims():
    """TC-47. Steps: the risk head sets Inconclusive → 'Ask customer for original';
    a genuine bill from a hospital that has not joined is then submitted.
    Expected: 'Pay' is never offered for non-Authentic verdicts; the policy
    change is logged; the new claim is Inconclusive and 'Waiting for customer'."""
    at = _login("riskhead")
    for v in ("Suspicious", "Tampered", "Inconclusive"):
        assert "Pay" not in at.selectbox(key=f"pol_{v}").options
    at.selectbox(key="pol_Inconclusive").set_value("Ask customer for original").run()
    assert _store().policy()["Inconclusive"] == "Ask customer for original"
    assert any(e["event"].startswith("Policy: Inconclusive") for e in _store().audit())
    pdf = (DATA_DIR / "showcase" / "04_genuine_shanti_not_joined.pdf").read_bytes()
    cid = submit_claim(_store(), Verifier(RegistryStore(DATA_DIR)), "CUST-001", pdf, "shanti.pdf")
    c = _store().claim(cid)
    assert c["verdict"] == "Inconclusive" and c["status"] == "Waiting for customer"


def test_tc48_registry_enrol_and_integrity():
    """TC-48. Steps: the registry operator enrols Shanti Clinic; opens Integrity
    check; inserts a forged entry.
    Expected: '4 of 5' hospitals joined; 'Patient data held' is None everywhere;
    every signature Valid; the forged entry is rejected."""
    at = _login("registry")
    at.selectbox(key="enrol_pick").set_value("IN-HOSP-SHANTI-STR-0419").run()
    at.button(key="enrol").click().run()
    assert "4 of 5" in _texts(at)
    assert set(at.dataframe[0].value["Patient data held"]) == {"None"}
    assert set(at.dataframe[1].value["Signature"]) == {"Valid"}
    at.button(key="forge").click().run()
    assert any(s.value.startswith("Rejected: the hospital's signature") for s in at.success)


# ---------------------------------------------------------------- sample data, presenter, carried-over checks

def test_tc49_sample_data_is_complete_and_consistent():
    """TC-49. Steps: read the seeded world.
    Expected: 34 claims, 16 paid on proof, 1 wrongful hold, 8 known frauds with
    0 paid automatically, 15 customers, 7 working logins; every joined hospital's
    ledger matches what the registry covers."""
    s = _store()
    m = s.measures()
    assert (m["claims"], m["auto_paid"], m["wrongful_holds"]) == (34, 16, 1)
    assert m["sample_frauds"] == 8 and m["sample_frauds_paid"] == 0
    assert len(s.customers()) == 15
    for uid in ("hospital", "customer", "officer", "riskhead", "registry", "auditor", "presenter"):
        assert s.authenticate(uid, DEMO_PASSWORD), uid
    reg = RegistryStore(DATA_DIR)
    c = sqlite3.connect(db_path(DATA_DIR))
    for iid in reg.load_directory():
        covered = sum(b["count"] for b in reg.load_file(iid)["batches"])
        ledger = c.execute("SELECT COUNT(*) FROM bills WHERE issuer_id=? AND published=1", (iid,)).fetchone()[0]
        assert covered == ledger, iid


def test_tc50_presenter_switches_roles_and_resets():
    """TC-50. Steps: sign in as presenter; issue a bill on step 1; Next; submit the
    claim on step 2; press Reset demo.
    Expected: the guided steps switch role and screen; after Reset the world is
    back to 34 claims."""
    at = _login("presenter")
    assert at.radio(key="nav_hospital").options == MENUS["hospital"]      # step 1 = hospital
    patient = at.selectbox(key="h_patient")
    patient.select_index(patient.options.index("Meera Kulkarni (Pune)")).run()
    at.button(key="h_issue").click().run()
    at.button(key="step_next").click().run()                             # step 2 = customer
    assert at.radio(key="nav_customer").value == "Submit a claim"
    at.button(key="cs_submit").click().run()          # newest document = the bill just issued
    assert _store().measures()["claims"] == 35
    at.button(key="reset").click().run()
    assert _store().measures()["claims"] == 34


@pytest.mark.parametrize("index", range(6))
def test_tc51_sample_bills_show_expected_verdicts(index):
    """TC-51 (x6). Steps: officer → Try a sample bill → pick bill N → Run.
    Expected: the verdict banner shows the manifest's expected verdict."""
    manifest = json.loads((DATA_DIR / "showcase" / "manifest.json").read_text())
    at = _go(_login("officer"), "officer", "Try a sample bill")
    sel = at.selectbox(key="showcase_pick")
    sel.set_value(sel.options[index]).run()
    at.button(key="run").click().run()
    assert not at.exception
    assert _verdict(at) == manifest[index]["expected"]


def test_tc52_auditor_sees_rule_and_architecture():
    """TC-52. Steps: auditor → How decisions are made.
    Expected: rules R0-R8 listed; the architecture diagram draws every component
    in architecture.json, solid when built and dashed when target."""
    at = _go(_login("auditor"), "auditor", "How decisions are made")
    texts = _texts(at)
    for rid in [f"R{i}" for i in range(9)]:
        assert f"<b>{rid}</b>" in texts
    html = next(m.value for m in at.markdown if 'data-testid="tl-architecture"' in m.value)
    import base64
    from html import escape
    svg = base64.b64decode(html.split("base64,")[1].split('"')[0]).decode()
    for layer in SPEC["layers"] + [SPEC["foundation"]]:
        for c in layer["components"]:
            cls = "built" if c["built"] else "target"
            assert f'<g class="comp {cls}" data-label="{escape(c["label"])}">' in svg, c["label"]


def test_tc53_verdict_view_has_ai_inside_and_evidence_graph():
    """TC-53. Steps: officer → switch on 'Show technical details' → Try a sample
    bill → bill 2 → Check this bill.
    Expected: stage cards in the order Detect, Decide, Verify, Human review, each
    with its 'AI inside' line; '4 independent lines against'; an evidence graph
    naming R2 and Tampered; the reason labelled as a template (GraphRAG = target)."""
    at = _go(_login("officer"), "officer", "Try a sample bill")
    at.toggle(key="details").set_value(True).run()
    sel = at.selectbox(key="showcase_pick")
    sel.set_value(sel.options[1]).run()
    at.button(key="run").click().run()
    titles = [re.search(r'tl-stage-title">(.+?)<', m.value).group(1)
              for m in at.markdown if 'tl-stage-title">' in m.value]
    assert [t.split(" · ")[1].split(" (")[0] for t in titles] == ["Detect", "Decide", "Verify", "Human review"]
    for stage in SPEC["stages"].values():
        assert stage["ai_inside"] in _texts(at)
    assert "4 independent lines against" in _texts(at)
    dot = at.get("graphviz_chart")[0].proto.spec
    assert "R2" in dot and "Tampered" in dot
    assert any("GraphRAG" in c.value and "template" in c.value for c in at.caption)


def test_tc54_duplicate_claim_is_refused():
    """TC-54. Steps: Meera picks a bill she has already claimed and presses Submit.
    Expected: a warning names the bill as already claimed; no new claim is created."""
    before = _store().measures()["claims"]
    at = _go(_login("customer"), "customer", "Submit a claim")
    option = next(o for o in at.radio(key="cs_pick").options if "already claimed" in o)
    at.radio(key="cs_pick").set_value(option).run()
    at.button(key="cs_submit").click().run()
    assert any("already been claimed" in w.value for w in at.warning)
    assert _store().measures()["claims"] == before


def test_tc55_simple_by_default():
    """TC-55. Steps: officer → Try a sample bill → bill 2 → Check this bill, with the
    details switch OFF (the default).
    Expected: the verdict, a one-sentence reason and the four-step strip are shown;
    no rule numbers, evidence graph or 'AI inside' lines appear until the switch is on."""
    at = _go(_login("officer"), "officer", "Try a sample bill")
    sel = at.selectbox(key="showcase_pick")
    sel.set_value(sel.options[1]).run()
    at.button(key="run").click().run()
    texts = _texts(at)
    assert _verdict(at) == "Tampered"
    assert 'class="tl-strip"' in texts and "Hospital's record differs" in texts
    assert "Rule R2" not in texts and "AI inside" not in texts
    assert not at.get("graphviz_chart")
    at.toggle(key="details").set_value(True).run()
    assert at.get("graphviz_chart") and "Rule R2" in _texts(at)


def test_tc56_guided_steps_for_the_presenter():
    """TC-56. Steps: sign in as presenter; press Next through all seven steps.
    Expected: every step shows a 'Do:' and a 'Say:' line; the role changes as
    planned (hospital, customer, officer, officer, risk head, registry, auditor);
    Back is disabled on step 1 and Next on the last step; no exceptions."""
    expected = ["hospital", "customer", "officer", "officer", "riskhead", "registry", "auditor"]
    at = _login("presenter")
    assert at.button(key="step_back").disabled
    for i, role in enumerate(expected):
        texts = _texts(at)
        assert "<b>Do:</b>" in texts and "Say:" in texts, i
        from trustladder.store import ROLES
        assert f'<span class="tl-role">{ROLES[role]}</span>' in texts, (i, role)
        assert not at.exception, i
        if i < len(expected) - 1:
            at.button(key="step_next").click().run()
    assert at.button(key="step_next").disabled
