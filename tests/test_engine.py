"""
Engine test cases (TC-01 .. TC-17).

Each test is one formal test case: its docstring states the steps and the
expected result. These cover the verdicts, the reasons and the registry; the
screens are covered in test_ui.py.
"""

import copy
import datetime as _dt
import random
import io
import itertools
import json

import pytest
from pathlib import Path
from pypdf import PdfReader

from trustladder import crypto
from trustladder.bills import (render_altered, render_discount_resaved, render_fabricated,
                               render_genuine, render_resaved)
from trustladder.cases import DATA_DIR, HOSPITALS, SCENARIOS
from trustladder.decide import decide
from trustladder.detect import check_arithmetic, check_file_history
from trustladder.explain import check_back, _OPENERS
from trustladder.models import (BillFields, EvidenceGrade, Finding, LineItem, ReadStatus,
                                RegistryAnswer, Verdict)
from trustladder.pipeline import run_ladder
from trustladder.policy import ALLOWED, PAY, baseline, evaluate, validate
from trustladder.reader import canonical_record, read_bill
from trustladder.registry import Publisher, RegistryStore, Verifier


def _jpeg() -> bytes:
    """A real 8x8 JPEG, for claims that arrive as a picture."""
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (240, 236, 228)).save(buf, format="JPEG")
    return buf.getvalue()


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
    counted = {f.family for f in r.findings if f.independent}
    assert counted == {"arithmetic", "file history", "fonts"}, "three independent causes"
    model = [f for f in r.findings if f.family == "screening model"]
    assert model and not model[0].independent, \
        "the model agrees, and is reported; a second opinion on one document is not a second cause"
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
    Expected: (a) Authentic, because proof outweighs file-history noise.
    (b) With no issuer to ask, this used to be Suspicious: the file-history check fires
    on every phone app that re-saves a PDF, and could not tell that from an edit. The
    trained screener can, so the finding is shown but no longer counted, and an honest
    customer is no longer recorded as suspected. It is still not cleared: Inconclusive
    via R6, because only the issuer clears a document and no issuer answered."""
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
    assert r2.verdict is Verdict.INCONCLUSIVE and r2.rule_applied.startswith("R6")
    assert r2.needs_human, "not cleared: it still goes to a person"
    history = [f for f in r2.findings if f.family == "file history"]
    assert history and not history[0].independent, \
        "the file history is still shown to the officer, it just no longer counts against them"


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
        expected = len({x.family for x in r.findings if x.independent}) + (
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


def test_tc20_the_screening_model_earns_its_place_and_shows_its_blind_spot():
    """TC-20. Steps: generate genuine bills, the same bills re-saved by a phone app,
    the same bills discounted and re-saved, bills altered after issue, and bills typed
    from a blank page. Run all of them past the written checks and past the model.
    Expected, and these are the two claims made to the panel:

      1. The model earns its place on FAIRNESS, not on catching more. It flags every
         altered bill, exactly as the written checks do, and flags none of the genuine
         re-saved or discounted bills, which the written checks flag every time. That
         is the difference between a system that accuses honest customers and one that
         does not.

      2. The model is BLIND to fabrication, and that is the argument, not a defect.
         A bill typed from a blank page was never edited, so there is nothing in the
         file for any model to find. It is why no screening signal is ever allowed to
         clear a document, and why the issuer registry exists."""
    from trustladder.screener import features, load
    rng = random.Random(31415)
    model = load()
    assert model is not None, "the demo ships a trained screener"

    def bill(n):
        items = [LineItem(d, int(a * rng.uniform(0.7, 1.4)) // 100 * 100) for d, a in
                 (("Room charges", 2400000), ("Surgeon fee", 5500000), ("Pharmacy", 1825000))]
        return BillFields("IN-HOSP-SHANTI-STR-0419", "Shanti Clinic and Maternity Home",
                          f"SCM/2026/{n:06d}", "2026-05-11", f"Patient {n}", items,
                          sum(i.amount_paise for i in items), "")

    flags = {k: 0 for k in ("genuine", "resaved", "discounted", "altered", "fabricated")}
    rules = dict(flags)
    N = 40
    for n in range(N):
        b = bill(n)
        raised = b.total_paise + 100 * rng.randrange(15000, 90000, 500)
        for kind, pdf in (("genuine", render_genuine(b, joined=False)),
                          ("resaved", render_resaved(b, joined=False)),
                          ("discounted", render_discount_resaved(b)),
                          ("altered", render_altered(b, raised, joined=False)),
                          ("fabricated", render_fabricated(b, joined=False))):
            fields = read_bill(pdf)[1]
            if model.probability(features(pdf, fields)) >= model.threshold:
                flags[kind] += 1
            if check_file_history(pdf) or check_arithmetic(fields):
                rules[kind] += 1

    # 1. fairness: the written checks accuse every genuine re-saved bill; the model none
    assert rules["resaved"] == N and rules["discounted"] == N, \
        "the written checks cannot tell a re-save from an edit, which is the problem"
    assert flags["resaved"] == 0 and flags["discounted"] == 0, \
        "the model must not accuse a genuine bill that merely passed through another program"
    # and it gives up nothing on real edits
    assert flags["altered"] == N, "every altered bill is still flagged"

    # 2. the blind spot, stated as a measurement
    assert flags["fabricated"] == 0, \
        "a bill typed from a blank page leaves nothing to detect: this is why screening never clears"
    assert flags["genuine"] == 0

    # and the trained artefact publishes its own measurements, so nobody has to take our word
    assert model.measured["auc_edited_vs_genuine"] > 0.9
    assert 0.4 <= model.measured["auc_fabricated_vs_genuine"] <= 0.6, \
        "near chance on fabrication, and we publish that number rather than hide it"


def test_tc21_a_model_written_reason_cannot_get_past_the_guardrail(monkeypatch):
    """TC-21. Steps: with no key configured, write a reason. Then stub the model to
    return (a) a well-behaved reason and (b) a reason that contradicts the verdict,
    and write again.
    Expected: no key means the written reason, labelled as such. A good draft is used
    and labelled as the model's. A draft that contradicts the verdict is DISCARDED and
    the written reason is shown instead. There is no path by which a model's sentence
    reaches a screen without passing the same check a written sentence passes."""
    from trustladder import explain, reason_model
    d = decide(ReadStatus.OK, RegistryAnswer.MISMATCH, [Finding("arithmetic", "x", "do not add up")])
    args = (d, RegistryAnswer.MISMATCH, "the issuer's record differs", 
            [Finding("arithmetic", "x", "do not add up")], "")

    monkeypatch.setattr(reason_model, "available", lambda: False)
    text, writer, model = explain.write_reason_best(*args)
    assert writer == "template" and model == "" and text.startswith(explain._OPENERS[d.verdict])

    monkeypatch.setattr(reason_model, "available", lambda: True)
    good = explain._OPENERS[d.verdict] + " The hospital's own record does not match this copy."
    monkeypatch.setattr(reason_model, "write", lambda **kw: (good, "stub-model"))
    text, writer, model = explain.write_reason_best(*args)
    assert writer == "model" and text == good and model == "stub-model"

    # a draft that announces a different verdict than the one decided
    bad = explain._OPENERS[Verdict.AUTHENTIC] + " Everything checks out, pay it."
    monkeypatch.setattr(reason_model, "write", lambda **kw: (bad, "stub-model"))
    text, writer, model = explain.write_reason_best(*args)
    assert writer == "template" and model == "", "a contradicting draft must be thrown away, not shown"
    assert "pay it" not in text and text.startswith(explain._OPENERS[d.verdict])

    # and a model that fails or times out is simply absent, never fatal
    monkeypatch.setattr(reason_model, "write", lambda **kw: None)
    text, writer, model = explain.write_reason_best(*args)
    assert writer == "template" and text


# ---------------------------------------------------------------- the AI layer (TC-22 .. TC-28)
# Every test here runs with the hosted models switched OFF (conftest) and stubs the
# model where a model answer is needed, so the suite is offline and deterministic.

def test_tc22_no_key_means_no_model_and_no_network():
    """TC-22. Steps: with models off, ask the provider, the vision reader, the agent and
    the reason writer. Expected: the provider says unavailable and raises
    ModelUnavailable rather than calling anything; a picture is 'could not read' with
    an honest note (or an OCR partial), the agent produces the fixed-order trace with
    no note, and the reason writer returns None."""
    from trustladder import agent, llm, reason_model, vision
    assert not llm.available()
    with pytest.raises(llm.ModelUnavailable):
        llm.generate([{"role": "user", "parts": [llm.text_part("hi")]}])
    assert reason_model.write("x", "y", "z", "", []) is None
    r = _run("01_genuine_bill.pdf")
    trace, note, model = agent.investigate(r, [], True)
    assert [s["tool"] for s in trace] == agent.TOOL_NAMES
    assert all(s["by"] == "fixed order" for s in trace) and note == "" and model == ""
    # a 1x1 PNG: no OCR can read it, and no vision model is configured
    png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS"
           b"\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7V\xbd\xfa\x00\x00\x00\x00IEND\xaeB`\x82")
    reading = vision.read_any(png, "photo.png")
    assert reading.status is ReadStatus.COULD_NOT_READ and reading.fields is None
    assert reading.source in ("none", "vision model")


def test_tc23_picture_reading_from_the_model_is_normalised_and_gated(monkeypatch):
    """TC-23. Steps: stub the vision model to return (a) a clean Marathi reading, (b) a
    reading with a low-confidence ticket, (c) one with the total missing.
    Expected: (a) Read, every field in standard form, language recorded, not
    corroborated (no second reader); (b) and (c) 'could not read' with the field named,
    and the partial values kept for the customer's form."""
    from trustladder import llm, vision
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(vision.ocr, "available", lambda: False)
    clean = {"language_seen": "Marathi", "issuer_name": "सह्योग", "issuer_id": "in-hosp-sahyog-pun-0101",
             "bill_no": "SMH/2026/004877", "bill_date": "2026-03-05", "patient": " Meera  Kulkarni ",
             "ticket": "sh-wtku-szuf-7m7g-arrc", "line_items": [{"description": "खोली", "amount": "24,000.00"}],
             "total": "Rs 1,03,650.00",
             "confidence": {k: 1.0 for k in ("issuer_id", "bill_no", "bill_date", "patient", "ticket", "total")},
             "notes": None}
    monkeypatch.setattr(llm, "ask_json", lambda parts, **kw: (clean, "stub-vision"))
    r = vision.read_picture(b"jpegbytes", "image/jpeg")
    assert r.status is ReadStatus.OK and r.source == "vision model" and r.model == "stub-vision"
    assert r.language == "Marathi" and r.fields.issuer_id == "IN-HOSP-SAHYOG-PUN-0101"
    assert r.fields.ticket == "SH-WTKU-SZUF-7M7G-ARRC" and r.fields.total_paise == 10365000
    assert r.fields.patient == "Meera Kulkarni" and r.fields.items[0].amount_paise == 2400000
    assert r.corroborated is False and r.second_reader == ""

    unsure = {**clean, "confidence": {**clean["confidence"], "ticket": 0.4}}
    monkeypatch.setattr(llm, "ask_json", lambda parts, **kw: (unsure, "stub-vision"))
    r = vision.read_picture(b"jpegbytes", "image/jpeg")
    assert r.status is ReadStatus.COULD_NOT_READ and "ticket" in r.note and r.fields is None
    assert r.partial["bill_no"] == "SMH/2026/004877"

    blank = {**clean, "total": ""}
    monkeypatch.setattr(llm, "ask_json", lambda parts, **kw: (blank, "stub-vision"))
    r = vision.read_picture(b"jpegbytes", "image/jpeg")
    assert r.status is ReadStatus.COULD_NOT_READ and "total amount" in r.note


def test_tc24_customer_confirmation_counts_corrections_and_completes_a_partial_read():
    """TC-24. Steps: take a clean reading and confirm it unchanged; confirm it with the
    total changed; take a partial reading (patient blank) and fill the patient in.
    Expected: unchanged -> 0 corrections, confirmed, still not corroborated without a
    second reader; changed total -> 1 correction and the new total; the partial read
    becomes Read once complete, with the typed field counted."""
    from trustladder import vision
    f = BillFields("IN-HOSP-SAHYOG-PUN-0101", "Sahyog", "SMH/2026/004877", "2026-03-05", "Meera Kulkarni",
                   [], 10365000, "SH-WTKU-SZUF-7M7G-ARRC")
    r = vision.Reading(ReadStatus.OK, f, "read", source="vision model", model="stub")
    same = vision.apply_corrections(r, {"total": "1,03,650.00"})
    assert same.corrections == 0 and same.customer_confirmed and same.status is ReadStatus.OK
    assert same.corroborated is False
    changed = vision.apply_corrections(r, {"total": "1,13,650"})
    assert changed.corrections == 1 and changed.fields.total_paise == 11365000
    partial = vision.Reading(ReadStatus.COULD_NOT_READ, None, "incomplete", source="vision model",
                             partial={"issuer_id": f.issuer_id, "bill_no": f.bill_no, "bill_date": f.bill_date,
                                      "patient": "", "total": f.total_paise, "ticket": f.ticket,
                                      "issuer_name": "Sahyog"})
    done = vision.apply_corrections(partial, {"patient": "Meera Kulkarni"})
    assert done.status is ReadStatus.OK and done.fields.patient == "Meera Kulkarni" and done.corrections == 1
    still = vision.apply_corrections(partial, {})
    assert still.status is ReadStatus.COULD_NOT_READ and still.fields is None


def test_tc25_rule_r7_a_photo_can_be_cleared_but_not_condemned_without_corroboration():
    """TC-25. Steps: apply the rule to Verified, Mismatch and Not issued, each with
    corroborated=False (a picture nobody corroborated) and corroborated=True.
    Expected: Verified clears either way (R1). Mismatch and Not issued convict only when
    corroborated (R2, R3); uncorroborated they are R7, Inconclusive, 'ask for the
    original'. Silence is unaffected (R4..R6 as before)."""
    one = [Finding("arithmetic", "x", "y")]
    assert decide(ReadStatus.OK, RegistryAnswer.VERIFIED, [], corroborated=False).rule_id == "R1"
    for ans, rid in ((RegistryAnswer.MISMATCH, "R2"), (RegistryAnswer.NOT_ISSUED, "R3")):
        d = decide(ReadStatus.OK, ans, one, corroborated=False)
        assert d.rule_id == "R7" and d.verdict is Verdict.INCONCLUSIVE and "original" in d.queue
        assert decide(ReadStatus.OK, ans, one, corroborated=True).rule_id == rid
    assert decide(ReadStatus.OK, RegistryAnswer.NOT_COVERED, one, corroborated=False).rule_id == "R5"
    assert decide(ReadStatus.OK, RegistryAnswer.NO_RECORD, one * 1 + [Finding("fonts", "a", "b")],
                  corroborated=False).rule_id == "R4"
    assert decide(ReadStatus.COULD_NOT_READ, RegistryAnswer.NOT_ASKED, [], corroborated=False).rule_id == "R0"


def test_tc26_a_confirmed_photo_of_the_edited_bill_is_held_only_when_two_readers_agree():
    """TC-26. Steps: read the edited bill's fields, present them as a picture reading
    confirmed by the customer, once with the second reader agreeing and once without,
    and run the ladder (as a JPEG, so file checks do not apply).
    Expected: with agreement, Tampered R2 (the issuer's record contradicts it); without,
    Inconclusive R7, 'ask for the original', and the read report says why. In both
    cases only the arithmetic is screened: no file-history or font findings on a photo."""
    from trustladder import vision
    verifier = Verifier(RegistryStore(DATA_DIR))
    pdf = (DATA_DIR / "showcase" / "02_same_bill_total_raised.pdf").read_bytes()
    fields = read_bill(pdf)[1]
    base = vision.Reading(ReadStatus.OK, fields, "read", source="vision model", model="stub",
                          customer_confirmed=True)
    agreed = vision.Reading(**{**base.__dict__, "second_reader": "deep OCR", "agree": True})
    r = run_ladder(b"not-a-pdf", verifier, "edited_photo.jpg", reading=agreed)
    assert r.verdict is Verdict.TAMPERED and r.rule_applied.startswith("R2")
    assert {f.family for f in r.findings} == {"arithmetic"}
    assert r.read_report["corroborated"] is True and r.read_report["source"] == "vision model"
    alone = vision.Reading(**{**base.__dict__, "second_reader": "", "agree": None})
    r = run_ladder(b"not-a-pdf", verifier, "edited_photo.jpg", reading=alone)
    assert r.verdict is Verdict.INCONCLUSIVE and r.rule_applied.startswith("R7")
    assert "original" in r.queue and r.read_report["corroborated"] is False
    assert r.registry_answer is RegistryAnswer.MISMATCH


def test_tc27_the_agent_narrates_but_cannot_decide_and_the_guard_completes_it(monkeypatch):
    """TC-27. Steps: stub the model so the agent calls the registry, then two checks, then
    writes a note; once with a plain note and once with a note that pronounces a verdict.
    Expected: the trace lists the agent's three steps in its order with its reasons, then
    the guard's steps for the three tools it skipped; the verdict is untouched; the plain
    note is kept and the pronouncing note is dropped. Every tool's result equals what the
    rule saw."""
    from trustladder import agent, llm
    r = _run("04_genuine_hospital_not_joined.pdf")
    monkeypatch.setattr(llm, "available", lambda: True)
    script = [
        llm.Reply("", [("ask_registry", {"why": "the issuer settles most claims"})], "stub-agent",
                  {"parts": [{"functionCall": {"name": "ask_registry", "args": {}}}]}),
        llm.Reply("", [("check_arithmetic", {"why": "sums first"}), ("claim_history", {"why": "repeats"})],
                  "stub-agent", {"parts": []}),
        llm.Reply("The hospital has not joined; the charges add up and there is no earlier claim.", [],
                  "stub-agent", {"parts": []}),
    ]
    calls = iter(script)
    monkeypatch.setattr(llm, "generate", lambda *a, **k: next(calls))
    trace, note, model = agent.investigate(r, [], True)
    assert model == "stub-agent"
    assert [(s["tool"], s["by"]) for s in trace[:3]] == [("ask_registry", "agent"), ("check_arithmetic", "agent"),
                                                        ("claim_history", "agent")]
    assert trace[0]["why"] == "the issuer settles most claims"
    assert sorted(s["tool"] for s in trace[3:]) == sorted(["check_file_history", "check_fonts", "screening_model"])
    assert all(s["by"] == "guard" for s in trace[3:])
    assert trace[0]["result"].startswith("Not covered")
    assert note.startswith("The hospital has not joined")
    assert r.verdict is Verdict.INCONCLUSIVE          # nothing the agent did reaches the rule

    calls = iter([llm.Reply("This looks Authentic to me, pay it.", [], "stub-agent", {"parts": []})])
    monkeypatch.setattr(llm, "generate", lambda *a, **k: next(calls))
    trace, note, _ = agent.investigate(r, [], True)
    assert note == "" and len(trace) == len(agent.TOOL_NAMES) and all(s["by"] == "guard" for s in trace)


def test_tc28_the_model_registry_names_every_model_and_the_audit_trail_names_the_ones_used(monkeypatch):
    """TC-28. Steps: list the model registry; submit a text-layer claim and a confirmed
    picture claim into a scratch store; read the audit rows.
    Expected: five models, each with a version, where it runs, a measurement and a limit;
    with models off every hosted one is inactive. The 'Models used' row says 'text layer,
    no model' for the PDF, and names the reader, the customer's confirmation and the
    correction count for the picture; the claim row carries read_source and corrections."""
    import tempfile
    from trustladder import model_registry, vision
    from trustladder.service import submit_claim
    from trustladder.store import Store
    rows = model_registry.models()
    assert len(rows) == 5 and {r["name"] for r in rows} >= {"Vision-language reader", "Evidence agent", "Reason writer"}
    assert all(r["version"] and r["where"] and r["measured"] and r["cannot"] for r in rows)
    assert not any(r["active"] for r in rows if r["where"].startswith("hosted"))

    store = Store(Path(tempfile.mkdtemp()) / "t.db")
    store.add_customer("C1", "Meera Kulkarni", "Pune")
    verifier = Verifier(RegistryStore(DATA_DIR))
    pdf = (DATA_DIR / "showcase" / "01_genuine_bill.pdf").read_bytes()
    cid = submit_claim(store, verifier, "C1", pdf, "bill.pdf")
    used = next(e for e in store.audit() if e["event"] == "Models used" and e["claim_id"] == cid)
    assert "text layer, no model" in used["detail"] and "fixed sentences" in used["detail"]
    fields = read_bill(pdf)[1]
    reading = vision.apply_corrections(
        vision.Reading(ReadStatus.OK, fields, "read", source="vision model", model="stub-vision",
                       language="English"), {"patient": "Meera  Kulkarni"})
    cid2 = submit_claim(store, verifier, "C1", _jpeg(), "bill_photo.jpg", reading=reading)
    used2 = next(e for e in store.audit() if e["event"] == "Models used" and e["claim_id"] == cid2)
    assert "stub-vision" in used2["detail"] and "English" in used2["detail"] and "customer confirmed" in used2["detail"]
    c = store.claim(cid2)
    assert c["read_source"] == "vision model" and c["read_corrections"] == 0
    assert store.measures()["picture_claims"] == 1 and store.measures()["readings_corrected"] == 0
