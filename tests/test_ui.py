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
from trustladder.bills import render_altered
from trustladder.reader import read_bill
from trustladder.service import CLAIM_HISTORY, submit_claim
from trustladder.store import OPEN_STATUSES, Store, result_from_json

APP = str(Path(__file__).resolve().parent.parent / "app.py")
SPEC = json.loads((Path(APP).parent / "trustladder" / "architecture.json").read_text())

MENUS = {
    "hospital": ["Issue a bill", "My bills"],
    "customer": ["My claims", "Submit a claim"],
    "officer": ["Claims inbox"],
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
    """Move to a page. Roles with a single screen have no menu radio to move."""
    if len(MENUS[role]) > 1:
        at.radio(key=f"nav_{role}").set_value(page).run()
    return at


def _queue() -> list[dict]:
    """The officer's queue, in the order the inbox lists it: worst first, newest first."""
    rows = _store().claims(statuses=OPEN_STATUSES)
    order = {"On hold": 0, "In review": 1, "Waiting for hospital": 2, "Waiting for customer": 3}
    rows.sort(key=lambda c: c["submitted_at"], reverse=True)
    rows.sort(key=lambda c: order.get(c["status"], 9))
    return rows


def _open_claim(at: AppTest, claim_id: str) -> AppTest:
    """The inbox is a work queue: press Open on that claim's row."""
    at.button(key=f"open_{claim_id}").click().run()
    return at


def _open_top(at: AppTest) -> str:
    """Open the claim at the top of the officer's queue and return its id."""
    cid = _queue()[0]["claim_id"]
    _open_claim(at, cid)
    return cid


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
    homes = {"hospital": "Issue a bill", "customer": "Your claims", "officer": "Claims inbox",
             "riskhead": "Dashboard", "registry": "Network", "auditor": "Audit trail"}
    for role, items in MENUS.items():
        at = _login(role)
        assert not at.exception, role
        nav = [r for r in at.radio if r.key == f"nav_{role}"]
        # a role with a single screen has no menu at all: nothing to get lost in
        assert (nav[0].options if nav else [homes[role]]) == items, role
        # the heading may name the role too ("Risk head's dashboard"), so compare loosely
        assert homes[role].lower() in _texts(at).lower(), role
        assert 'class="tl-role"' in _texts(at)


def test_tc42_role_isolation():
    """TC-42. Steps: sign in as the customer, then as the hospital.
    Expected: the customer sees their own claims and nobody else's, and has no officer
    or risk pages; the hospital's screens never show a claim or who checked a bill."""
    store = _store()
    mine = store.claims(customer_id=store.user("customer")["customer_id"])
    at = _login("customer")
    shown = _texts(at)
    assert shown.count("claim CLM-") == len(mine), "every claim of theirs, and only theirs"
    others = [c["claim_id"] for c in store.claims()
              if c["customer_id"] != store.user("customer")["customer_id"]]
    assert not [cid for cid in others if cid in shown], "another customer's claim is visible"
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
    """TC-44. Steps: the hospital issues a bill on screen; the customer submits that
    exact PDF; the officer and the auditor then look.
    Expected: the claim is Paid on proof, carries the bill number read from the
    uploaded PDF, appears in the customer's claims, and both the issue and the
    verdict are in the audit trail."""
    at = _login("hospital")
    at.button(key="h_issue").click().run()
    assert not at.exception
    ok = next(s.value for s in at.success if "Ticket printed on the bill" in s.value)
    bill_no = re.search(r"Issued (\S+)", ok).group(1)

    store = _store()
    pdf = store.bill_pdf(bill_no)                       # exactly the file the customer downloads
    claim_id = submit_claim(store, Verifier(RegistryStore(DATA_DIR)), "CUST-001", pdf,
                            f"{bill_no.replace('/', '_')}.pdf")
    claim = store.claim(claim_id)
    assert claim["verdict"] == "Authentic" and claim["status"] == "Paid"
    assert claim["bill_no"] == bill_no, "the uploaded claim must link to the bill it came from"

    at = _login("customer")
    assert claim_id in _texts(at)
    at = _login("auditor")
    at.text_input(key="aud_q").input(claim_id).run()
    events = at.dataframe[0].value["Event"].tolist()
    assert "Claim submitted" in events and "Authentic (R1)" in events
    assert any(e["event"] == "Bill issued and published" for e in store.audit())


def test_tc45_customer_sends_clearer_copy():
    """TC-45. Steps: the customer opens My claims; for the claim waiting on them, picks
    the Arogya bill from their documents and presses Send clearer copy.
    Expected: that claim is re-judged Authentic and shows Paid; the trail logs
    'Clearer copy re-submitted'."""
    # She starts with no claims, so the demo creates the situation: a blurred photo of a
    # real bill comes back "could not read", which is a request for a clearer copy and
    # never an accusation.
    store, verifier = _store(), Verifier(RegistryStore(DATA_DIR))
    me = store.user("customer")["customer_id"]
    photo = (DATA_DIR / "showcase" / "05_photo_of_bill_1.pdf").read_bytes()
    cid = submit_claim(store, verifier, me, photo, "photo_of_bill.pdf")
    assert _store().claim(cid)["status"] == "Waiting for customer", \
        "an unreadable copy is sent back to the customer, not accused"
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
    assert _queue()[0]["status"] == "On hold", "the worst claim is at the top of the queue"
    cid = _open_top(at)
    at.button(key=f"dec_reject_{cid}").click().run()
    assert _store().claim(cid)["status"] == "Rejected"
    assert _store().measures()["rejected"] == before + 1
    at = _login("riskhead")
    frauds = _store().measures()["sample_frauds"]
    assert f'<div class="v">{before + 1} of {frauds}</div>' in _texts(at), \
        "the risk head's rejected count must move with the officer's decision"
    assert "known frauds rejected after review" in _texts(at)
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
    pdf = (DATA_DIR / "showcase" / "04_genuine_hospital_not_joined.pdf").read_bytes()
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
    Expected: a month with claims in every outcome, no known fraud paid automatically,
    exactly one wrongful hold, 15 customers and 7 working logins; the demo's own patient
    starts with NO claims so her page is empty until she uploads; every joined hospital's
    ledger matches what the registry covers."""
    s = _store()
    m = s.measures()
    assert m["claims"] == 31 and m["auto_paid"] == 15 and m["wrongful_holds"] == 1
    assert m["claims"] == m["auto_paid"] + m["to_person"], "every claim is accounted for"
    assert m["sample_frauds"] == 8 and m["sample_frauds_paid"] == 0, "no known fraud paid on its own"
    assert len(s.customers()) == 15
    assert not s.claims(customer_id=s.user("customer")["customer_id"]), \
        "the demo's patient must start with no claim history"
    assert s.documents_of_customer(s.user("customer")["customer_id"]), \
        "she still holds bills, or she would have nothing to claim on"
    for uid in ("hospital", "customer", "officer", "riskhead", "registry", "auditor", "presenter"):
        assert s.authenticate(uid, DEMO_PASSWORD), uid
    reg = RegistryStore(DATA_DIR)
    c = sqlite3.connect(db_path(DATA_DIR))
    for iid in reg.load_directory():
        covered = sum(b["count"] for b in reg.load_file(iid)["batches"])
        ledger = c.execute("SELECT COUNT(*) FROM bills WHERE issuer_id=? AND published=1", (iid,)).fetchone()[0]
        assert covered == ledger, iid


def test_tc50_presenter_switches_roles_and_resets():
    """TC-50. Steps: sign in as presenter; issue a bill on step 1; press Reset demo.
    Expected: step 1 is the hospital's screen; the bill is really created; Reset puts
    the world back to exactly the seeded claims and returns to step 1."""
    at = _login("presenter")
    assert at.radio(key="nav_hospital").options == MENUS["hospital"]      # step 1 = hospital
    seeded = _store().measures()["claims"]
    before = len(_store().bills_of_issuer("IN-HOSP-SAHYOG-PUN-0101"))
    at.button(key="h_issue").click().run()
    assert len(_store().bills_of_issuer("IN-HOSP-SAHYOG-PUN-0101")) == before + 1
    at.button(key="reset").click().run()
    assert _store().measures()["claims"] == seeded
    assert len(_store().bills_of_issuer("IN-HOSP-SAHYOG-PUN-0101")) == before
    assert at.session_state["step"] == 0


@pytest.mark.parametrize("index", range(6))
def test_tc51_sample_bills_show_expected_verdicts(index):
    """TC-51 (x6). Steps: as the customer, submit prepared bill N on the one upload
    screen (the route the presenter uses now that the officer has no sample tab).
    Expected: the claim records the manifest's expected verdict."""
    manifest = json.loads((DATA_DIR / "showcase" / "manifest.json").read_text())
    m = manifest[index]
    store, verifier = _store(), Verifier(RegistryStore(DATA_DIR))
    claim = store.claim(submit_claim(store, verifier, "CUST-002",
                                     (DATA_DIR / "showcase" / m["file"]).read_bytes(), m["file"]))
    assert claim["verdict"] == m["expected"], m["file"]


def test_tc52_auditor_sees_rule_and_architecture():
    """TC-52. Steps: auditor → How decisions are made.
    Expected: rules R0-R6 listed; the architecture diagram draws every component
    in architecture.json, solid when built and dashed when target."""
    at = _go(_login("auditor"), "auditor", "How decisions are made")
    texts = _texts(at)
    for rid in [f"R{i}" for i in range(7)]:
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
    """TC-53. Steps: officer → Claims inbox (it opens the tampered claim) → switch on
    'Show technical details'.
    Expected: stage cards in the order Detect, Decide, Verify, Human review, each
    with its 'AI inside' line; '4 independent lines against'; an evidence graph
    naming R2 and Tampered; the reason labelled as a template (GraphRAG = target)."""
    at = _login("officer")
    cid = _open_top(at)
    assert _store().claim(cid)["verdict"] == "Tampered"
    at.toggle(key="details").set_value(True).run()
    titles = [re.search(r'tl-stage-title">(.+?)<', m.value).group(1)
              for m in at.markdown if 'tl-stage-title">' in m.value]
    assert [t.split(" · ")[1].split(" (")[0] for t in titles] == ["Detect", "Decide", "Verify", "Human review"]
    for stage in SPEC["stages"].values():
        assert stage["ai_inside"] in _texts(at)
    assert "4 independent lines against" in _texts(at)
    dot = at.get("graphviz_chart")[0].proto.spec
    assert "R2" in dot and "Tampered" in dot
    assert any("GraphRAG" in c.value and "template" in c.value for c in at.caption)


def test_tc54_a_repeat_of_a_proven_bill_is_evidence_not_a_refusal():
    """TC-54. Steps: submit the same genuine, registry-confirmed bill PDF twice for
    the same policy.
    Expected: the second submission is accepted and checked (there is one upload
    screen and it never refuses a file); the repeat is recorded as a 'claim history'
    finding naming the first claim; and because the issuer still confirms the bill it
    is STILL Authentic — a repeat on its own never convicts an honest customer."""
    store = _store()
    verifier = Verifier(RegistryStore(DATA_DIR))
    pdf = (DATA_DIR / "showcase" / "01_genuine_bill.pdf").read_bytes()
    bill_no = read_bill(pdf)[1].bill_no
    before = store.measures()["claims"]
    first = store.claim(submit_claim(store, verifier, "CUST-002", pdf, "bill.pdf"))
    second = store.claim(submit_claim(store, verifier, "CUST-002", pdf, "bill_again.pdf"))
    assert store.measures()["claims"] == before + 2, "both submissions are recorded"
    assert first["bill_no"] == second["bill_no"] == bill_no

    r = result_from_json(json.loads(second["result_json"]))
    dup = [f for f in r.findings if f.family == CLAIM_HISTORY]
    assert len(dup) == 1, "the repeat must be reported once, as its own family"
    assert bill_no in dup[0].detail and first["claim_id"] in dup[0].detail
    assert second["verdict"] == "Authentic", "proof still clears: a repeat alone is not a conviction"
    assert not [f for f in result_from_json(json.loads(first["result_json"])).findings
                if f.family == CLAIM_HISTORY], "the first submission has no history against it"

    # The verdict is about the document; the money is a separate question.
    assert first["status"] == "Paid"
    assert second["initial_action"] == "Human review" and second["status"] == "In review", \
        "nobody is paid twice for one bill without a person looking"

    # The reason must say where the evidence came from, and must not call a repeat
    # claim a screening finding or invent a re-saved file.
    assert "The insurer's own records add:" in r.reason
    assert "Screening also found" not in r.reason
    assert "re-saved" not in r.reason
    assert r.reason.startswith("This bill can be paid.")


def test_tc62_a_repeat_can_never_be_paid_automatically_under_any_policy():
    """TC-62. Steps: set the risk head's policy to the most permissive it allows
    (Authentic → Pay), then submit the same proven bill twice.
    Expected: the first is paid straight through and the second is not, whatever the
    policy says, because a second claim on one bill always reaches a person."""
    store = _store()
    verifier = Verifier(RegistryStore(DATA_DIR))
    store.set_policy("Authentic", "Pay", "risk head")      # the most permissive policy allowed
    assert store.policy()["Authentic"] == "Pay"
    pdf = (DATA_DIR / "showcase" / "01_genuine_bill.pdf").read_bytes()
    a = store.claim(submit_claim(store, verifier, "CUST-002", pdf, "a.pdf"))
    b = store.claim(submit_claim(store, verifier, "CUST-002", pdf, "b.pdf"))
    assert a["status"] == "Paid"
    assert b["status"] != "Paid", "a repeat is never paid automatically"


def test_tc61_a_repeat_of_an_edited_bill_adds_an_independent_line():
    """TC-61. Steps: the customer submits a genuine bill, then submits the SAME bill
    with the total raised (what the guided story now does on one screen).
    Expected: the second claim is Tampered and On hold; its evidence carries the
    issuer registry, the document's own findings AND the claim-history repeat, so the
    officer sees one more independent line than the document alone would give."""
    store = _store()
    verifier = Verifier(RegistryStore(DATA_DIR))
    pdf = (DATA_DIR / "showcase" / "01_genuine_bill.pdf").read_bytes()
    bill = read_bill(pdf)[1]
    genuine = store.claim(submit_claim(store, verifier, "CUST-002", pdf, "bill.pdf"))
    edited_pdf = render_altered(bill, bill.total_paise + 5000000, joined=bool(bill.ticket))
    edited = store.claim(submit_claim(store, verifier, "CUST-002", edited_pdf, "bill_edited.pdf"))

    assert genuine["verdict"] == "Authentic" and genuine["status"] == "Paid"
    assert edited["verdict"] == "Tampered" and edited["status"] == "On hold"
    assert edited["bill_no"] == genuine["bill_no"], "one bill, two claims, one screen"
    r = result_from_json(json.loads(edited["result_json"]))
    families = {f.family for f in r.findings}
    assert CLAIM_HISTORY in families, "the insurer's own history must reach the rule"
    assert families - {CLAIM_HISTORY}, "the document's own findings are still there"
    # The registry counts as one line, and each finding family as another.
    assert edited["independent_against"] == len(families) + 1

    # And the officer must be able to SEE it, not just have it stored.
    at = _login("officer")
    assert _queue()[0]["claim_id"] == edited["claim_id"], "the newest tampered claim tops the queue"
    _open_claim(at, edited["claim_id"])
    at.toggle(key="details").set_value(True).run()
    texts = _texts(at)
    assert CLAIM_HISTORY in texts, "the claim-history family must be named on screen"
    assert "was already claimed on this policy" in texts
    assert "The insurer's own records add:" in texts


def test_tc55_simple_by_default():
    """TC-55. Steps: officer → Claims inbox (it opens the tampered claim) with the
    details switch OFF (the default).
    Expected: the verdict, a one-sentence reason and the four-step strip are shown;
    no rule numbers, evidence graph or 'AI inside' lines appear until the switch is on."""
    at = _login("officer")
    _open_top(at)
    texts = _texts(at)
    assert _verdict(at) == "Tampered"
    assert 'class="tl-strip"' in texts and "Hospital's record differs" in texts
    assert "Rule R2" not in texts and "AI inside" not in texts
    assert not at.get("graphviz_chart")
    at.toggle(key="details").set_value(True).run()
    assert at.get("graphviz_chart") and "Rule R2" in _texts(at)


def test_tc56_guided_steps_for_the_presenter():
    """TC-56. Steps: sign in as presenter; press Next through every step.
    Expected: each step shows a 'Do:' and a 'Say:' line and switches to the right
    LOGIN; there is ONE customer step, because both uploads happen on one screen with
    one button; Back is disabled on the first step and Next on the last; no
    exceptions."""
    expected = ["hospital", "customer", "officer", "riskhead", "registry", "auditor"]
    at = _login("presenter")
    assert at.button(key="step_back").disabled
    for i, login in enumerate(expected):
        texts = _texts(at)
        assert "<b>Do:</b>" in texts and "Say:" in texts, i
        shown = _store().user(login)["name"]
        assert f"**{shown}**" in texts, (i, login)
        assert not at.exception, i
        if i < len(expected) - 1:
            at.button(key="step_next").click().run()
    assert at.button(key="step_next").disabled


def test_tc57_the_demo_story_follows_one_bill():
    """TC-57. Steps: the hospital issues a bill; the customer uploads it; the SAME
    customer then uploads the edited copy of that same bill on the same screen.
    Expected: both claims carry the same hospital and bill number; the genuine one is
    Authentic and Paid; the edited one is Tampered and On hold, and is the claim the
    officer's inbox opens."""
    at = _login("hospital")
    at.button(key="h_issue").click().run()
    bill_no = re.search(r"Issued (\S+)", next(s.value for s in at.success)).group(1)
    store, verifier = _store(), Verifier(RegistryStore(DATA_DIR))
    pdf = store.bill_pdf(bill_no)
    bill = read_bill(pdf)[1]
    paid = store.claim(submit_claim(store, verifier, "CUST-001", pdf, "bill.pdf"))
    tampered_pdf = render_altered(bill, bill.total_paise + 5000000, joined=True)
    forged = store.claim(submit_claim(store, verifier, "CUST-001", tampered_pdf, "bill_tampered.pdf"))
    assert paid["verdict"] == "Authentic" and paid["status"] == "Paid"
    assert forged["verdict"] == "Tampered" and forged["status"] == "On hold"
    assert forged["bill_no"] == paid["bill_no"] == bill_no, "one bill, two claims"
    assert forged["issuer_name"] == paid["issuer_name"]
    assert forged["customer_id"] == paid["customer_id"], \
        "one customer, one upload screen: the same person sends the genuine bill and the edited one"
    at = _login("officer")
    # worst first, newest first: the claim just submitted is at the top of the queue
    assert _queue()[0]["claim_id"] == forged["claim_id"]
    _open_claim(at, forged["claim_id"])
    assert f"bill {bill_no}" in _texts(at)


def test_tc58_there_is_exactly_one_place_to_upload_a_bill():
    """TC-58. Steps: open the customer's Submit a claim screen; then read every other
    role's screens looking for a second uploader.
    Expected: the customer's only route is an upload (no document list), that screen
    offers the six sample bills as a zip, Submit is disabled until a file is chosen,
    and NO other role has an uploader — the officer's separate 'Check any bill' tab is
    gone, so a genuine bill and a tampered one arrive by the same door."""
    import zipfile
    at = _go(_login("customer"), "customer", "Submit a claim")
    assert at.get("file_uploader"), "the customer must be able to upload"
    assert not [r for r in at.radio if r.key in ("cs_pick", "cs_source")], "no document list any more"
    assert at.button(key="cs_submit").disabled, "Submit waits for a file"
    assert any(b.label.startswith("Download 6 sample bills") for b in at.get("download_button"))

    assert MENUS["officer"] == ["Claims inbox"], "the officer reviews claims; it is not an upload desk"
    for role in ("officer", "riskhead", "registry", "auditor"):
        for page in MENUS[role]:
            other = _go(_login(role), role, page)
            assert not other.get("file_uploader"), f"{role} / {page} must not offer a second uploader"

    names = zipfile.ZipFile(DATA_DIR / "TrustLadder_sample_bills.zip").namelist()
    assert len([n for n in names if n.endswith(".pdf")]) == 6
    assert any(n.endswith("README.txt") for n in names)


def test_tc59_separate_logins_and_no_person_hardcoded_in_the_screens():
    """TC-59. Steps: sign in as each of the eight logins and read who the screen says
    you are.
    Expected: eight working logins including two separate customers, and the SIGNED-IN
    IDENTITY is always a role, never a person. Claim data is a different matter: a claim
    must name the party who made it, or an officer could not do their job, so a person's
    name on a claim is correct and only the chrome is checked here."""
    store = _store()
    for uid in ("hospital", "customer", "customer2", "officer", "riskhead", "registry", "auditor",
                "presenter"):
        assert store.authenticate(uid, DEMO_PASSWORD), uid
    assert store.user("customer")["customer_id"] != store.user("customer2")["customer_id"]
    people = {c["name"] for c in store.customers()}
    for uid, shown in (("customer", "Customer"), ("customer2", "Second customer"),
                       ("officer", "Claims officer"), ("hospital", "Billing desk")):
        at = _login(uid)
        assert f"**{shown}**" in _texts(at), uid
        who = next(m.value for m in at.sidebar.markdown if 'class="tl-role"' in m.value)
        assert not [n for n in people if n in who], \
            f"{uid}: the signed-in identity names a person: {who!r}"


def test_tc60_the_forgery_is_made_by_the_forger_not_the_hospital():
    """TC-60. Steps: check the hospital's screen for any way to produce a tampered
    bill; then use the presenter-only Demo toolkit on a genuine bill.
    Expected: the hospital offers only the genuine bill, and says nothing about a
    tampered copy; the toolkit is presenter-only and turns a genuine bill into one
    that the ladder judges Tampered, with the same bill number."""
    at = _login("hospital")
    at.button(key="h_issue").click().run()
    labels = [b.label for b in at.get("download_button")]
    assert labels == ["Download the bill (PDF)"], labels
    assert "tampered" not in _texts(at).lower()
    assert not [e for e in at.get("expander") if "toolkit" in (e.label or "").lower()]

    at = _login("presenter")
    assert any("toolkit" in (e.label or "").lower() for e in at.get("expander")), \
        "the forger's editor belongs to the presenter, not to any role"
    assert not at.get("file_uploader"), \
        "the toolkit must not ask for an upload: one upload box in the demo, and it is the claim form"

    # what the toolkit produces, judged by the real ladder
    store, verifier = _store(), Verifier(RegistryStore(DATA_DIR))
    bill_no = store.bills_of_issuer("IN-HOSP-SAHYOG-PUN-0101")[0]["bill_no"]
    genuine = store.bill_pdf(bill_no)
    fields = read_bill(genuine)[1]
    edited = render_altered(fields, fields.total_paise + 5000000, joined=bool(fields.ticket))
    claim = store.claim(submit_claim(store, verifier, "CUST-002", edited, "bill_edited.pdf"))
    assert claim["verdict"] == "Tampered" and claim["bill_no"] == bill_no


def test_tc63_only_one_upload_box_on_the_screen_that_takes_the_forged_copy():
    """TC-63. Steps: sign in as presenter and walk to step 2, the one customer step,
    where both the genuine bill and the forged copy are submitted.
    Expected: exactly ONE file-upload box is on screen, the claim form's. The demo
    toolkit in the sidebar offers the bill it already holds, so the presenter is
    never asked to upload the same bill in two places."""
    at = _login("presenter")
    at.button(key="step_next").click().run()          # step 1 -> 2, the one customer step
    assert at.session_state["step"] == 1, "step 2 of the guided story"
    assert not at.exception
    uploaders = at.get("file_uploader")
    assert len(uploaders) == 1, f"expected one upload box on the customer step, found {len(uploaders)}"
    assert uploaders[0].key == "cs_upload", "the one upload box is the customer's claim form"
    assert any("toolkit" in (e.label or "").lower() for e in at.get("expander")), \
        "the toolkit is still there; it just does not ask for a file"


def test_tc64_every_bill_in_the_demo_is_for_one_patient():
    """TC-64. Steps: read the patient off each of the six prepared bills; then issue a
    bill on the hospital screen without changing anything and read its patient too.
    Expected: one patient throughout. The bill the live story issues is in the name of
    the customer who then claims it, and that is the same person the prepared bills
    name, so no screen ever shows one person claiming another person's bill."""
    from trustladder.reader import read_bill
    names = set()
    for f in sorted((DATA_DIR / "showcase").glob("*.pdf")):
        fields = read_bill(f.read_bytes())[1]
        if fields:                                   # bill 5 is a blurred photo, by design
            names.add(fields.patient)
    assert len(names) == 1, f"the prepared bills name more than one patient: {names}"
    prepared = names.pop()

    at = _login("hospital")
    at.button(key="h_issue").click().run()
    assert not at.exception
    bill_no = re.search(r"Issued (\S+)", next(s.value for s in at.success)).group(1)
    store = _store()
    issued = read_bill(store.bill_pdf(bill_no))[1]
    assert issued.patient == prepared, \
        f"the live story issues a bill for {issued.patient!r} but the samples are {prepared!r}"

    claimant = store.customer(store.user("customer")["customer_id"])["name"]
    assert claimant == prepared, \
        f"{claimant!r} would be claiming a bill made out to {prepared!r}"


def test_tc65_the_toolkit_edits_the_bill_that_was_just_issued():
    """TC-65. Steps: sign in as presenter (the sidebar toolkit is drawn straight away,
    before any bill exists); press Next to step 1 and issue a bill; walk on to step 3.
    Expected: the toolkit is offering THAT bill, not a seeded one from another
    hospital. This is the failure the browser walkthrough caught: the box was drawn
    once at step 1 and kept its first choice for the rest of the story."""
    at = _login("presenter")
    at.button(key="h_issue").click().run()
    bill_no = re.search(r"Issued (\S+)", next(s.value for s in at.success)).group(1)
    for _ in range(2):                                  # step 1 -> 2 -> 3
        at.button(key="step_next").click().run()
    assert at.session_state["step"] == 2
    picked = [b.value for b in at.selectbox if str(b.key or "").startswith("tool_bill")]
    assert picked, "the toolkit must offer a bill to edit"
    assert bill_no in picked[0], \
        f"toolkit is editing {picked[0]!r}, but the story just issued {bill_no}"


def test_tc66_the_audit_trail_names_actors_by_one_convention():
    """TC-66. Steps: read the 'Who' column of the seeded audit trail; then issue a bill
    and submit a claim live and read the new rows.
    Expected: ONE convention throughout. A claim is attributed to the party who made it,
    by name. Staff actions are attributed to the desk or role that did them, and no
    person is invented for a login that is a role. Live rows and seeded rows read the
    same, so nobody can tell which came from the sample data."""
    store = _store()
    claims = {c["claim_id"]: c["customer_name"] for c in store.claims()}
    audit = store.audit()

    for e in audit:
        if e["event"] == "Claim submitted" and e["claim_id"] in claims:
            assert e["actor"] == claims[e["claim_id"]], \
                f"claim {e['claim_id']} logged against {e['actor']!r}, not the claimant"
    decisions = [e for e in audit if e["role"] == "officer"]
    assert decisions, "the sample month must contain officer decisions"
    assert {e["actor"] for e in decisions} == {"Claims officer"}, \
        f"officer rows name more than one actor: {sorted({e['actor'] for e in decisions})}"
    assert all(e["actor"].endswith(" billing") for e in audit if e["role"] == "hospital"), \
        "a bill must be logged against the issuing hospital's billing desk"

    # now the live path, through the screens
    at = _login("hospital")
    at.button(key="h_issue").click().run()
    bill_no = re.search(r"Issued (\S+)", next(s.value for s in at.success)).group(1)
    store = _store()
    cid = store.user("customer")["customer_id"]
    claim_id = submit_claim(store, Verifier(RegistryStore(DATA_DIR)), cid,
                            store.bill_pdf(bill_no), "bill.pdf")
    fresh = _store().audit()
    issued = next(e for e in fresh if e["role"] == "hospital" and bill_no in e["detail"])
    claimed = next(e for e in fresh if e["claim_id"] == claim_id and e["event"] == "Claim submitted")
    assert issued["actor"].endswith(" billing"), issued["actor"]
    assert claimed["actor"] == store.customer(cid)["name"], \
        f"the live claim is logged against {claimed['actor']!r}, not the customer"


VERDICT_WORDS = ("Authentic", "Suspicious", "Tampered", "Inconclusive")


def test_tc67_the_customer_is_never_shown_the_verdict():
    """TC-67. Steps: the customer submits a genuine bill and then the same bill with the
    total raised; look at the Submit a claim screen after each, and at My claims.
    Expected: the customer sees the STATUS of their claim and what happens next, and
    never the verdict, the findings or the four-stage ladder. Telling someone who just
    uploaded a forged bill that it came back 'Tampered', with the checks that caught it,
    would coach a fraudster. The officer, who acts on it, still sees all of it."""
    store, verifier = _store(), Verifier(RegistryStore(DATA_DIR))
    cid = store.user("customer")["customer_id"]
    pdf = (DATA_DIR / "showcase" / "01_genuine_bill.pdf").read_bytes()
    bill = read_bill(pdf)[1]
    paid = submit_claim(store, verifier, cid, pdf, "bill.pdf")
    edited = submit_claim(store, verifier, cid,
                          render_altered(bill, bill.total_paise + 5000000, joined=bool(bill.ticket)),
                          "bill_edited.pdf")
    assert _store().claim(edited)["verdict"] == "Tampered", "the forged copy must be caught"

    for claim_id in (paid, edited):
        at = _login("customer")
        at.session_state["cs_last"] = claim_id          # as if they had just uploaded it
        at = _go(at, "customer", "Submit a claim")
        at.run()
        texts = _texts(at)
        status = _store().claim(claim_id)["status"]
        assert f'data-testid="tl-claim-status">{status}<' in texts, f"{claim_id}: status not shown"
        for word in VERDICT_WORDS:
            assert f'data-testid="tl-verdict">{word}<' not in texts, \
                f"{claim_id}: the customer is being shown the verdict {word}"
        assert "What was found" not in texts, f"{claim_id}: findings shown to the customer"
        assert 'class="tl-strip"' not in texts, f"{claim_id}: the stage ladder shown to the customer"

    # My claims: the same rule, one card per claim
    at = _login("customer")
    texts = _texts(at)
    assert "What was found" not in texts and 'class="tl-strip"' not in texts
    assert "on hold while it is investigated" in texts.lower(), \
        "the customer should be told where the claim stands, in their own words"

    # the officer, who must act on it, still sees everything
    at = _open_claim(_login("officer"), edited)
    otexts = _texts(at)
    assert 'data-testid="tl-verdict">Tampered<' in otexts, "the officer must see the verdict"
    assert "What was found" in otexts, "the officer must see what was found"


def test_tc68_the_officer_lands_on_a_queue_not_on_a_verdict():
    """TC-68. Steps: sign in as the officer; read the landing screen; open the top claim;
    press Back to the queue.
    Expected: the landing screen is a work queue listing every pending claim with an Open
    button, and NO verdict is on screen until one is chosen. Opening a claim shows the
    verdict and what was found. Back returns to the queue."""
    queue = _queue()
    assert queue, "the sample world must leave claims waiting"
    at = _login("officer")

    texts = _texts(at)
    assert 'data-testid="tl-verdict"' not in texts, \
        "the officer must choose a claim before any verdict is shown"
    assert "Waiting for a decision" in texts
    for c in queue:                                   # every pending claim is listed and openable
        assert c["claim_id"] in texts, c["claim_id"]
        assert at.button(key=f"open_{c['claim_id']}"), c["claim_id"]

    # A real queue holds many people's bills at many hospitals. One name repeated down
    # the list reads like one person's file, not an insurer's inbox.
    assert len({c["customer_name"] for c in queue}) > 1, "the queue must show different patients"
    assert len({c["issuer_name"] for c in queue}) > 1, "the queue must show different hospitals"

    cid = _open_top(at)
    assert _verdict(at) == _store().claim(cid)["verdict"], "the verdict appears once a claim is chosen"
    assert "What was found" in _texts(at)

    at.button(key="inbox_back").click().run()
    back = _texts(at)
    assert 'data-testid="tl-verdict"' not in back and "Waiting for a decision" in back, \
        "Back must return to the queue"


def test_tc69_the_risk_head_screen_says_the_role_the_job_and_the_consequence():
    """TC-69. Steps: sign in as the risk head; read the screen; change one policy menu.
    Expected: the screen names the role and contrasts it with the claims officer's job,
    numbers the three things on the page, and shows what the current setting would do to
    this month's claims. Changing a menu moves those counts, so the control has a visible
    consequence instead of being four menus with no effect."""
    at = _login("riskhead")
    texts = _texts(at)
    assert "Chief Risk Officer" in texts, "the role must be named"
    assert "one claim at a time" in texts, "it must contrast with the claims officer's job"
    for heading in ("1. Where this month stands", "2. Your decision", "3. What changes"):
        assert heading in texts, heading
    assert "would go:" in texts, "the consequence of the current setting must be on screen"

    import re
    def counts(t):
        m = re.search(r"would go:</b> (\d+) paid automatically · (\d+) to a person · "
                      r"(\d+) held and investigated · (\d+) sent back", t)
        assert m, "the routing line is missing or has changed shape"
        return tuple(int(g) for g in m.groups())

    before = counts(texts)
    at.selectbox(key="pol_Tampered").set_value("Human review").run()
    after = counts(_texts(at))
    assert after != before, "changing the policy must change what the screen says would happen"
    assert after[2] == 0, "nothing is held once Tampered goes to review"
    assert after[1] > before[1], "those claims move to a person instead"

    # and the guarantee still holds whatever is chosen
    assert "no setting can pay a bill the hospital has not confirmed" in _texts(at)
    assert "Pay" not in at.selectbox(key="pol_Tampered").options


def test_tc70_the_officer_can_see_what_they_decided():
    """TC-70. Steps: the officer opens the top claim and presses Reject as fraud.
    Expected: they are returned to the queue with the decision confirmed in words, the
    claim is gone from the queue, and it is listed under 'Decided recently' with the
    machine verdict beside the person's decision. A decision that simply made the claim
    vanish gave the officer no way to confirm what had been recorded."""
    at = _login("officer")
    cid = _open_top(at)
    before = _store().claim(cid)
    at.button(key=f"dec_reject_{cid}").click().run()
    assert not at.exception

    texts = _texts(at)
    assert "Waiting for a decision" in texts, "the officer is returned to the queue"
    assert "Rejected and referred to investigation" in texts, "the decision is confirmed in words"
    assert cid in texts and before["verdict"] in texts, \
        "the confirmation names the claim and the machine verdict"
    assert cid not in [c["claim_id"] for c in _queue()], "it has left the queue"

    after = _store().claim(cid)
    assert after["status"] == "Rejected"
    assert after["officer_decision"] == "Rejected and referred to investigation"
    assert cid in [c["claim_id"] for c in _store().decided()], "it is in the decided list"
    assert any((e.label or "").startswith("Decided recently") for e in at.get("expander")), \
        "the officer can go back and look at what they decided"

    # machine verdict and human decision are kept side by side, never collapsed into one
    row = next(c for c in _store().decided() if c["claim_id"] == cid)
    assert row["verdict"] == before["verdict"] and row["officer_decision"] != row["verdict"]
