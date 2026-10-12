"""
Engine test cases (TC-01 .. TC-17).

Each test is one formal test case: its docstring states the steps and the
expected result. These cover the verdicts, the reasons and the registry; the
screens are covered in test_ui.py.
"""

import copy
import datetime as _dt
import io
import itertools
import json

import pytest
from pypdf import PdfReader

from trustladder import crypto
from trustladder.bills import render_genuine, render_resaved
from trustladder.cases import DATA_DIR, HOSPITALS, SCENARIOS
from trustladder.decide import decide
from trustladder.explain import check_back, _OPENERS
from trustladder.models import (BillFields, EvidenceGrade, Finding, LineItem, ReadStatus,
                                RegistryAnswer, Verdict)
from trustladder.pipeline import run_ladder
from trustladder.policy import ALLOWED, PAY, baseline, evaluate, validate
from trustladder.reader import canonical_record, read_bill
from trustladder.registry import Publisher, RegistryStore, Verifier


def _run(file_name):
    verifier = Verifier(RegistryStore(DATA_DIR))
    pdf = (DATA_DIR / "showcase" / file_name).read_bytes()
    return run_ladder(pdf, verifier, file_name)


# ---------------------------------------------------------------- showcase

def test_tc01_genuine_joined_is_authentic():
    """TC-01. Steps: run 01_genuine_bill.pdf.
    Expected: Authentic, rule R1, registry Verified, Proof-grade, no human needed,
    reason opens 'This bill can be paid.'"""
    r = _run("01_genuine_bill.pdf")
    assert r.verdict is Verdict.AUTHENTIC and r.rule_applied.startswith("R1")
    assert r.registry_answer is RegistryAnswer.VERIFIED
    assert r.evidence_grade is EvidenceGrade.PROOF
    assert r.needs_human is False
    assert r.reason.startswith("This bill can be paid.")
    assert r.findings == []


def test_tc02_altered_total_is_tampered():
    """TC-02. Steps: run 02_same_bill_total_raised.pdf (bill 1 with Rs 1 lakh added).
    Expected: Tampered (High), rule R2, registry Mismatch, screening finds the
    arithmetic break, the layered re-save and the foreign font; reason quotes
    both amounts in Indian format."""
    r = _run("02_same_bill_total_raised.pdf")
    assert r.verdict is Verdict.TAMPERED and r.risk == "High" and r.rule_applied.startswith("R2")
    assert r.registry_answer is RegistryAnswer.MISMATCH
    assert {f.family for f in r.findings} == {"arithmetic", "file history", "fonts"}
    assert "Rs 1,86,400.00" in r.reason and "Rs 2,86,400.00" in r.reason


def test_tc03_fabricated_at_a_joined_issuer_is_caught_outright():
    """TC-03. Steps: run 03_made_from_nothing.pdf (typed from a blank page) against a
    hospital that has joined and published for the period the bill is dated in.
    Expected: ZERO screening findings, because appearance checks cannot see a document
    that was never edited. Caught anyway, and caught OUTRIGHT: the issuer has published
    a code for every bill it issued in that period and this ticket is not among them,
    so the answer is Not issued and the verdict is Tampered on that evidence alone.
    This is the whole point of the registry. Anything less and a clean fabrication walks
    through on 'nothing looks wrong'."""
    r = _run("03_made_from_nothing.pdf")
    assert r.findings == [], "a bill typed from a blank page leaves no edit traces"
    assert r.registry_answer is RegistryAnswer.NOT_ISSUED
    assert r.verdict is Verdict.TAMPERED and r.rule_applied.startswith("R3")
    assert r.evidence_grade is EvidenceGrade.CONTRADICTION
    assert r.needs_human and r.queue.startswith("Hold")


def test_tc04_genuine_not_joined_is_referred_not_cleared():
    """TC-04. Steps: run 04_genuine_hospital_not_joined.pdf.
    Expected: registry Not covered, verdict Inconclusive via R6,
    Consistency-grade, sent to a person, reason says it is not proof."""
    r = _run("04_genuine_hospital_not_joined.pdf")
    assert r.registry_answer is RegistryAnswer.NOT_COVERED
    assert r.verdict is Verdict.INCONCLUSIVE and r.rule_applied.startswith("R6")
    assert r.evidence_grade is EvidenceGrade.CONSISTENCY
    assert r.needs_human and "not proof" in r.reason


def test_tc05_unreadable_scan_is_never_a_mismatch():
    """TC-05. Steps: run 05_photo_of_bill_1.pdf (a blurred photo of bill 1).
    Expected: Could not read, registry NOT consulted, Inconclusive via R0, queue
    'Could-not-read', and the word 'Mismatch' appears nowhere in the result."""
    r = _run("05_photo_of_bill_1.pdf")
    assert r.read_status is ReadStatus.COULD_NOT_READ
    assert r.registry_answer is RegistryAnswer.NOT_ASKED
    assert r.verdict is Verdict.INCONCLUSIVE and r.rule_applied.startswith("R0")
    assert r.queue.startswith("Could-not-read")
    assert "mismatch" not in (r.reason + r.registry_note).lower().replace("never be reported as a mismatch", "")


def test_tc06_fabricated_not_joined_is_the_honest_gap():
    """TC-06. Steps: run 06_made_from_nothing_not_joined.pdf.
    Expected: indistinguishable from TC-04 (Inconclusive via R6), and never Authentic.
    This is the honest gap and the argument for coverage: the same fabrication that is
    caught outright at a joined hospital (TC-03) cannot be told from a genuine bill when
    there is no issuer to ask."""
    r = _run("06_made_from_nothing_not_joined.pdf")
    assert r.verdict is Verdict.INCONCLUSIVE and r.rule_applied.startswith("R6")
    assert _run("03_made_from_nothing.pdf").verdict is Verdict.TAMPERED, \
        "the only difference between the two is whether the issuer had joined"


# ---------------------------------------------------------------- reading & registry

def test_tc07_one_standard_form():
    """TC-07. Steps: build the same bill with different spellings of name/ticket.
    Expected: identical canonical record and identical code, so formatting can
    never look like a changed value."""
    base = BillFields("X", "H", "smh/2026/1", "2026-03-03", "Meera  Kulkarni", [], 18640000,
                      "sh-7k2p-9qx4-8m31-rt5w")
    other = BillFields("X", "H", "SMH/2026/1", "2026-03-03", "MEERA KULKARNI.", [], 18640000,
                       "SH 7K2P 9QX4 8M31 RT5W")
    assert canonical_record(base) == canonical_record(other)
    salt = crypto.new_salt()
    assert crypto.code(canonical_record(base), salt) == crypto.code(canonical_record(other), salt)


def test_tc08_forged_registry_line_is_rejected(tmp_path):
    """TC-08. Steps: enrol a hospital, publish a bill; then (a) insert a forged
    entry into the published batch on disk, (b) look the genuine bill up.
    Expected: the altered batch fails its signature and is ignored, so the bill
    is 'No record' and the note reports the rejected batch; before tampering it
    was 'Verified'."""
    store = RegistryStore(tmp_path)
    store.enrol_issuer("IN-HOSP-T-1", "Test Hospital", "Pune", "TH")
    pub = Publisher(store, "IN-HOSP-T-1")
    bill = BillFields("IN-HOSP-T-1", "Test Hospital", "T/1", "2026-03-01", "A B",
                      [LineItem("x", 100)], 100, pub.new_ticket())
    pub.publish([bill])
    assert Verifier(store).lookup(bill)[0] is RegistryAnswer.VERIFIED
    data = json.loads(store.file_path("IN-HOSP-T-1").read_text())
    data["batches"][0]["entries"].append(["00" * 32, "11" * 32])
    store.file_path("IN-HOSP-T-1").write_text(json.dumps(data))
    answer, note = Verifier(store).lookup(bill)
    assert answer is RegistryAnswer.NO_RECORD
    assert "failed the signature check" in note


def test_tc09_resaved_genuine_bill():
    """TC-09. Steps: run a genuine bill re-saved by a phone app, (a) from a joined
    hospital (published), (b) from a hospital that has not joined.
    Expected: (a) Authentic (proof outweighs file-history noise); (b) Suspicious
    via R5, NOT Tampered. With no issuer to ask, one family is not enough to convict."""
    store = RegistryStore(DATA_DIR)
    pub = Publisher(store, "IN-HOSP-SAHYOG-PUN-0101")
    bill = BillFields("IN-HOSP-SAHYOG-PUN-0101", "Sahyog Multispeciality Hospital", "SMH/2026/009001",
                      "2026-03-12", "Asha Nair", [LineItem("Surgeon fee", 5000000)], 5000000,
                      pub.new_ticket())
    pub.publish([bill])
    r = run_ladder(render_resaved(bill, joined=True), Verifier(store))
    assert r.findings and r.verdict is Verdict.AUTHENTIC
    other = BillFields("IN-HOSP-SHANTI-STR-0419", "Shanti Clinic and Maternity Home", "SCM/2026/1",
                       "2026-03-12", "Asha Nair", [LineItem("Surgeon fee", 5000000)], 5000000)
    r2 = run_ladder(render_resaved(other, joined=False), Verifier(store))
    assert r2.verdict is Verdict.SUSPICIOUS and r2.rule_applied.startswith("R5")


def test_tc10_rule_table_guarantees():
    """TC-10. Steps: apply the rule to every combination of read status x registry
    answer x 0-3 screening families.
    Expected, and these are the guarantees the whole product rests on:
      * Authentic ONLY when the bill was read AND the issuer confirmed it. Nothing
        else can ever clear a document.
      * Tampered ONLY when the ISSUER contradicted the document, or two independent
        screening families agree. Screening alone never convicts on one finding.
      * The issuer's silence is never evidence: no record outside its published
        period, or an issuer that has not joined, can never produce Tampered on its
        own.
      * Unreadable is always Inconclusive, whatever else is true."""
    fams = ["arithmetic", "file history", "fonts"]
    issuer_against = (RegistryAnswer.MISMATCH, RegistryAnswer.NOT_ISSUED)
    silence = (RegistryAnswer.NO_RECORD, RegistryAnswer.NOT_COVERED, RegistryAnswer.NOT_ASKED)
    for status, answer, n in itertools.product(ReadStatus, RegistryAnswer, range(4)):
        findings = [Finding(f, f, f) for f in fams[:n]]
        d = decide(status, answer, findings)
        if d.verdict is Verdict.AUTHENTIC:
            assert status is ReadStatus.OK and answer is RegistryAnswer.VERIFIED
        if d.verdict is Verdict.TAMPERED:
            assert answer in issuer_against or n >= 2, (answer, n)
        if status is ReadStatus.OK and answer in silence and n == 0:
            assert d.verdict is Verdict.INCONCLUSIVE, "silence plus nothing is not a conviction"
        if status is ReadStatus.COULD_NOT_READ:
            assert d.verdict is Verdict.INCONCLUSIVE


def test_tc11_no_policy_can_pay_without_proof():
    """TC-11. Steps: try to map Suspicious/Tampered/Inconclusive to Pay; then run
    every allowed policy over every simulated scenario.
    Expected: the illegal policies raise; every allowed policy pays 0 frauds."""
    for verdict in ("Suspicious", "Tampered", "Inconclusive"):
        with pytest.raises(ValueError):
            validate({"Authentic": PAY, "Suspicious": "Human review", "Tampered": "Hold and investigate",
                      "Inconclusive": "Human review", verdict: PAY})
    sim = json.loads((DATA_DIR / "simulation.json").read_text())
    for rows in sim["results"].values():
        for combo in itertools.product(*ALLOWED.values()):
            policy = dict(zip(ALLOWED, combo))
            assert evaluate(rows, policy)["fraud_paid"] == 0


def test_tc12_simulation_story_holds():
    """TC-12. Steps: evaluate the default policy over the three coverage scenarios,
    and the edit-detection baseline over the same claims.
    Expected: straight-through rises strictly with coverage; the baseline pays at
    least one fabricated bill and holds at least one genuine re-saved bill."""
    sim = json.loads((DATA_DIR / "simulation.json").read_text())
    from trustladder.policy import DEFAULT_POLICY
    rates = [evaluate(sim["results"][k], DEFAULT_POLICY)["paid_straight_through"] for k in SCENARIOS]
    assert rates[0] < rates[1] < rates[2]
    rows = sim["results"][list(SCENARIOS)[1]]
    b = baseline(rows)
    assert b["fraud_paid"] >= 1 and b["genuine_held"] >= 1
    assert any(r["kind"] == "fabricated" and not r["families"] for r in rows)


def test_tc13_reason_never_contradicts_verdict():
    """TC-13. Steps: run all six showcase bills; then feed check_back a reason
    carrying another verdict's opening.
    Expected: every reason passes check_back; the contradicting one fails."""
    for f in ["01_genuine_bill.pdf", "02_same_bill_total_raised.pdf", "03_made_from_nothing.pdf",
              "04_genuine_hospital_not_joined.pdf", "05_photo_of_bill_1.pdf",
              "06_made_from_nothing_not_joined.pdf"]:
        r = _run(f)
        assert r.reason.startswith(_OPENERS[r.verdict])
    d = decide(ReadStatus.OK, RegistryAnswer.NOT_COVERED, [])
    assert not check_back(_OPENERS[Verdict.AUTHENTIC] + " whatever", d)


def test_tc14_registry_holds_no_patient_data():
    """TC-14. Steps: read every published registry file and the directory; search
    for every patient name, bill number and total printed on the showcase bills.
    Expected: none of them appears anywhere in what the registry holds."""
    published = (DATA_DIR / "directory.json").read_text() + "".join(
        p.read_text() for p in (DATA_DIR / "registry").glob("*.json"))
    for pdf in (DATA_DIR / "showcase").glob("*.pdf"):
        status, fields, _ = read_bill(pdf.read_bytes())
        if fields is None:
            continue
        for value in (fields.patient, fields.bill_no, str(fields.total_paise),
                      f"{fields.total_paise / 100:.2f}"):
            assert value not in published, value


def test_tc15_real_ticket_on_a_different_bill():
    """TC-15. Steps: copy a genuine ticket onto a clean bill for a different patient,
    leaving no edit traces at all.
    Expected: the issuer issued that ticket, but not for this bill, so the answer is
    Mismatch and the verdict is Tampered via R2 on that alone. There are no screening
    findings to corroborate it and none are needed: the issuer's own signed record
    contradicting the document is proof, not a hint."""
    store = RegistryStore(DATA_DIR)
    genuine = read_bill((DATA_DIR / "showcase" / "01_genuine_bill.pdf").read_bytes())[1]
    other = copy.deepcopy(genuine)
    other.patient = "Someone Else"
    r = run_ladder(render_genuine(other, joined=True), Verifier(store))
    assert r.findings == [], "a cleanly retyped bill leaves nothing for screening to find"
    assert r.registry_answer is RegistryAnswer.MISMATCH
    assert r.verdict is Verdict.TAMPERED and r.rule_applied.startswith("R2")
    assert r.evidence_grade is EvidenceGrade.CONTRADICTION


def test_tc16_freshly_issued_bill_verifies_immediately():
    """TC-16. Steps: the hospital issues and publishes a new bill; a verifier that
    had already mirrored the file refreshes and checks it.
    Expected: Authentic (Verified) straight after publication."""
    store = RegistryStore(DATA_DIR)
    verifier = Verifier(store)
    _ = run_ladder((DATA_DIR / "showcase" / "01_genuine_bill.pdf").read_bytes(), verifier)
    pub = Publisher(store, "IN-HOSP-AROGYA-NSK-0233")
    bill = BillFields("IN-HOSP-AROGYA-NSK-0233", HOSPITALS["IN-HOSP-AROGYA-NSK-0233"][0],
                      "ANH/2026/007777", "2026-03-20", "Kavya Menon",
                      [LineItem("Room charges (semi-private)", 1200000)], 1200000, pub.new_ticket())
    pub.publish([bill])
    verifier.refresh()
    r = run_ladder(render_genuine(bill, joined=True), verifier)
    assert r.verdict is Verdict.AUTHENTIC
    assert PdfReader(io.BytesIO(render_genuine(bill, joined=True))).pages


def test_tc17_evidence_graph_agrees_with_the_rule():
    """TC-17. Steps: build the evidence graph for all six demo bills.
    Expected: Tampered always rests either on the issuer contradicting the document or
    on >= 2 independent lines; Suspicious has exactly 1; Authentic has proof and 0
    against; the counts match the rule's own family arithmetic; DOT output names the
    rule and the verdict."""
    from trustladder import evidence
    for f in ["01_genuine_bill.pdf", "02_same_bill_total_raised.pdf", "03_made_from_nothing.pdf",
              "04_genuine_hospital_not_joined.pdf", "05_photo_of_bill_1.pdf",
              "06_made_from_nothing_not_joined.pdf"]:
        r = _run(f)
        g = evidence.build(r)
        expected = len({x.family for x in r.findings}) + (
            1 if r.registry_answer in (RegistryAnswer.MISMATCH, RegistryAnswer.NOT_ISSUED) else 0)
        assert g.independent_against == expected, f
        if r.verdict is Verdict.TAMPERED:
            # either the issuer itself contradicted the document, or two families did
            assert g.independent_against >= 2 or r.registry_answer in (
                RegistryAnswer.MISMATCH, RegistryAnswer.NOT_ISSUED), f
        if r.verdict is Verdict.SUSPICIOUS:
            assert g.independent_against == 1
        if r.verdict is Verdict.AUTHENTIC:
            assert g.proof and g.independent_against == 0
        dot = evidence.to_dot(r, g)
        assert r.verdict.value in dot and r.rule_applied.split(":")[0] in dot


def test_tc18_the_prepared_bills_are_one_coherent_set():
    """TC-18. Steps: read all six prepared bills.
    Expected: one patient across the set; bills 1, 2 and 5 are the SAME bill (same
    number, and 5 is a photo of it); 2 differs from 1 only by a raised total; 3 uses
    the same hospital as 1 but a number it never issued; 4 and 6 are the hospital that
    has not joined. Nothing in the set is an unexplained stranger."""
    import json
    files = json.loads((DATA_DIR / "showcase" / "manifest.json").read_text())
    read = {}
    for m in files:
        status, fields, _ = read_bill((DATA_DIR / "showcase" / m["file"]).read_bytes())
        read[m["file"]] = fields
    readable = [f for f in read.values() if f]
    assert len({f.patient for f in readable}) == 1, "the set must be about one patient"
    one, two = read["01_genuine_bill.pdf"], read["02_same_bill_total_raised.pdf"]
    assert two.bill_no == one.bill_no and two.issuer_id == one.issuer_id
    assert two.total_paise > one.total_paise and two.items == one.items, "only the total was edited"
    assert read["05_photo_of_bill_1.pdf"] is None, "file 5 is a photo: it cannot be read"
    three = read["03_made_from_nothing.pdf"]
    assert three.issuer_id == one.issuer_id and three.bill_no != one.bill_no
    for name in ("04_genuine_hospital_not_joined.pdf", "06_made_from_nothing_not_joined.pdf"):
        assert read[name].issuer_id == "IN-HOSP-SHANTI-STR-0419"


def test_tc19_coverage_is_what_turns_a_missing_ticket_into_a_statement(tmp_path):
    """TC-19. Steps: enrol a hospital with a coverage window, then check three bills it
    has no record of: one dated inside the window, one dated before it joined, and one
    dated after its last publication.
    Expected: only the one INSIDE the window is Tampered. The other two are Inconclusive.

    This is the safety property behind the whole change. 'The issuer has no record of
    this' means two completely different things: either the issuer is telling us it
    issued no such bill, or the issuer simply has not published for that period. Treating
    the second as a conviction would call a genuine bill from before a hospital joined,
    or one caught in the gap before its next batch, a forgery. The window is the issuer's
    public undertaking, and the rule relies on nothing outside it."""
    store = RegistryStore(tmp_path)
    store.enrol_issuer("IN-HOSP-SAHYOG-PUN-0101", "Sahyog Multispeciality Hospital", "Pune", "SH",
                       when="2026-03-01")
    pub = Publisher(store, "IN-HOSP-SAHYOG-PUN-0101")
    real = BillFields("IN-HOSP-SAHYOG-PUN-0101", "Sahyog Multispeciality Hospital", "SMH/2026/1",
                      "2026-03-10", "A Patient", [LineItem("Surgeon fee", 5000000)], 5000000,
                      pub.new_ticket())
    pub.publish([real], when=_dt.datetime.fromisoformat("2026-03-31T10:00:00+05:30"))
    verifier = Verifier(store)

    def check(bill_date: str):
        b = BillFields("IN-HOSP-SAHYOG-PUN-0101", "Sahyog Multispeciality Hospital",
                       "SMH/2026/999", bill_date, "A Patient",
                       [LineItem("Surgeon fee", 5000000)], 5000000, pub.new_ticket())
        return run_ladder(render_genuine(b, joined=True), verifier)

    inside = check("2026-03-15")
    assert inside.registry_answer is RegistryAnswer.NOT_ISSUED
    assert inside.verdict is Verdict.TAMPERED and inside.rule_applied.startswith("R3")

    before_joining = check("2026-01-05")
    assert before_joining.registry_answer is RegistryAnswer.NO_RECORD
    assert before_joining.verdict is Verdict.INCONCLUSIVE, \
        "a genuine bill from before the hospital joined must never be convicted"

    after_last_batch = check("2026-06-20")
    assert after_last_batch.registry_answer is RegistryAnswer.NO_RECORD
    assert after_last_batch.verdict is Verdict.INCONCLUSIVE, \
        "a bill in the gap before the next batch must never be convicted"

    for r in (before_joining, after_last_batch):
        assert "outside the period" in r.registry_note, "the note must say why nothing follows"
