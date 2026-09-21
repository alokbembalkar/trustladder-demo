"""
The shared demo database: one SQLite file every role reads and writes.

Why a database: the roles are different people looking at the same claims. A
bill the hospital issues must appear in the customer's documents; a claim the
customer submits must appear in the officer's inbox; the officer's decision must
move the risk head's numbers and appear in the auditor's trail. Per-browser
memory cannot do that, so the state lives here.

Tables
    users      demo logins (password stored as a salted scrypt hash, never in clear)
    customers  policyholders
    bills      each hospital's OWN ledger of bills it issued (private to that
               hospital; the registry only ever sees two codes per bill)
    claims     what customers submitted to the insurer, with TrustLadder's verdict
               and whatever a person later decided
    audit      every event: machine verdicts, human decisions, policy changes,
               enrolments; append-only
    policy     the risk head's action for each verdict

Everything is synthetic. The file lives under the demo data folder and is rebuilt
by `python -m trustladder.seed` (and by "Reset demo").
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import json
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .models import LadderResult
from .policy import ALLOWED, DEFAULT_POLICY, HOLD, PAY, REQUEST, REVIEW, validate

# --------------------------------------------------------------------------
# Roles
# --------------------------------------------------------------------------

ROLES = {
    "hospital": "Hospital billing",
    "customer": "Customer",
    "officer": "Insurer claims officer",
    "riskhead": "Insurer risk head",
    "registry": "Registry operator",
    "auditor": "Auditor / regulator",
    "presenter": "Presenter (all roles)",
}

# What each action means for the claim, in the words the CUSTOMER sees.
STATUS_FOR_ACTION = {
    PAY: "Paid",
    REVIEW: "In review",
    HOLD: "On hold",
    REQUEST: "Waiting for customer",
}
OFFICER_DECISIONS = {
    "release": ("Released for payment", "Paid after review"),
    "reject": ("Rejected and referred to investigation", "Rejected"),
    "ask_customer": ("Asked the customer for a clearer copy", "Waiting for customer"),
    "ask_issuer": ("Asked the hospital to confirm", "Waiting for hospital"),
}
OPEN_STATUSES = ("In review", "On hold", "Waiting for customer", "Waiting for hospital")

CUSTOMER_TEXT = {
    "Paid": "Paid. The hospital confirmed your bill, so it was paid straight away.",
    "Paid after review": "Paid. A claims officer reviewed your bill and released the payment.",
    "In review": "A claims officer is looking at your bill. You do not need to do anything yet.",
    "On hold": "Your claim is on hold while it is investigated. You will be contacted if anything is needed.",
    "Waiting for customer": "We need a clearer copy of your bill. Please upload the original PDF from the hospital.",
    "Waiting for hospital": "We have asked the hospital to confirm your bill. No action is needed from you.",
    "Rejected": "Your claim was not accepted. You can ask for the reasons and appeal.",
}


# --------------------------------------------------------------------------
# Passwords
# --------------------------------------------------------------------------

def _hash_password(password: str, salt_hex: str) -> str:
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=2 ** 12, r=8, p=1,
                          dklen=32).hex()


def now() -> str:
    """Indian Standard Time, whatever time zone the server runs in (Render runs UTC)."""
    return _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d %H:%M")


class Store:
    """All reads and writes of the shared demo state."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(_SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------ users

    def add_user(self, user_id: str, name: str, role: str, org: str, password: str,
                 customer_id: str = "", issuer_id: str = "") -> None:
        salt = secrets.token_hex(16)
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO users VALUES (?,?,?,?,?,?,?,?)",
                      (user_id, name, role, org, salt, _hash_password(password, salt),
                       customer_id, issuer_id))

    def authenticate(self, user_id: str, password: str) -> dict | None:
        """Return the user record if the id and password match, else None."""
        with self._conn() as c:
            row = c.execute("SELECT * FROM users WHERE user_id = ?",
                            ((user_id or "").strip().lower(),)).fetchone()
        if row is None:
            _hash_password(password or "", "00" * 16)          # same work either way
            return None
        if not hmac.compare_digest(_hash_password(password or "", row["pw_salt"]), row["pw_hash"]):
            return None
        return {k: row[k] for k in ("user_id", "name", "role", "org", "customer_id", "issuer_id")}

    def user(self, user_id: str) -> dict | None:
        """A user record without the password (used by the presenter's 'view as')."""
        with self._conn() as c:
            row = c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return {k: row[k] for k in ("user_id", "name", "role", "org", "customer_id", "issuer_id")} if row else None

    # ------------------------------------------------------------------ customers & bills

    def add_customer(self, customer_id: str, name: str, city: str) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO customers VALUES (?,?,?)", (customer_id, name, city))

    def customers(self) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM customers ORDER BY name")]

    def customer(self, customer_id: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM customers WHERE customer_id = ?", (customer_id,)).fetchone()
        return dict(r) if r else None

    def add_bill(self, bill_no: str, issuer_id: str, issuer_name: str, customer_id: str,
                 patient: str, bill_date: str, total_paise: int, ticket: str, published: bool,
                 pdf: bytes, issued_at: str | None = None) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO bills VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (bill_no, issuer_id, issuer_name, customer_id, patient, bill_date,
                       int(total_paise), ticket, int(published), pdf, issued_at or now()))

    def bills_of_issuer(self, issuer_id: str) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT bill_no, patient, bill_date, total_paise, ticket, published, issued_at "
                "FROM bills WHERE issuer_id = ? ORDER BY issued_at DESC, bill_no DESC", (issuer_id,))]

    def documents_of_customer(self, customer_id: str) -> list[dict]:
        """Bills hospitals handed to this customer (their 'documents')."""
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT bill_no, issuer_name, bill_date, total_paise, issued_at FROM bills "
                "WHERE customer_id = ? ORDER BY issued_at DESC", (customer_id,))]

    def bill_pdf(self, bill_no: str) -> bytes:
        with self._conn() as c:
            return c.execute("SELECT pdf FROM bills WHERE bill_no = ?", (bill_no,)).fetchone()["pdf"]

    # ------------------------------------------------------------------ policy

    def policy(self) -> dict:
        with self._conn() as c:
            rows = {r["verdict"]: r["action"] for r in c.execute("SELECT * FROM policy")}
        return {**DEFAULT_POLICY, **rows}

    def set_policy(self, verdict: str, action: str, actor: str) -> None:
        new = {**self.policy(), verdict: action}
        validate(new)                         # refuses 'Pay' for anything but Authentic
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO policy VALUES (?,?)", (verdict, action))
        self.log(actor, "riskhead", f"Policy: {verdict} → {action}", "", "")

    # ------------------------------------------------------------------ claims

    def next_claim_id(self) -> str:
        with self._conn() as c:
            n = c.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
        return f"CLM-2026-{1001 + n:06d}"

    def record_claim(self, customer_id: str, customer_name: str, file_name: str, pdf: bytes,
                     result: LadderResult, independent_against: int, bill_no: str = "",
                     truth: str = "", submitted_at: str | None = None, actor: str = "") -> str:
        """Store a submitted claim with TrustLadder's verdict; route it by the policy."""
        action = self.policy()[result.verdict.value]
        status = STATUS_FOR_ACTION[action]
        if result.rule_applied.startswith("R0") and action == REVIEW:
            # an unreadable bill is the customer's to fix, not an officer's to judge
            status = "Waiting for customer"
        claim_id = self.next_claim_id()
        issuer = self._issuer_for(result, bill_no)
        when = submitted_at or now()
        with self._conn() as c:
            c.execute(
                "INSERT INTO claims (claim_id, customer_id, customer_name, issuer_name, bill_no, "
                "submitted_at, file_name, pdf, verdict, risk, rule, registry, evidence_grade, reason, "
                "independent_against, initial_action, status, truth, result_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (claim_id, customer_id, customer_name, issuer, bill_no, when, file_name, pdf,
                 result.verdict.value, result.risk, result.rule_applied, result.registry_answer.value,
                 result.evidence_grade.value, result.reason, independent_against, action, status,
                 truth, json.dumps(_result_json(result))))
        self.log(actor or customer_name, "customer", "Claim submitted", claim_id, file_name, when)
        self.log("TrustLadder", "system", f"{result.verdict.value} ({result.rule_applied.split(':')[0]})",
                 claim_id, f"{action} → {status}", when)
        return claim_id

    def _issuer_for(self, result: LadderResult, bill_no: str) -> str:
        """The hospital's name: read from the bill, or from the linked bill in the ledger."""
        if result.fields:
            return result.fields.issuer_name
        if bill_no:
            with self._conn() as c:
                r = c.execute("SELECT issuer_name FROM bills WHERE bill_no = ?", (bill_no,)).fetchone()
            if r:
                return r["issuer_name"]
        return "Not readable"

    def resubmit(self, claim_id: str, file_name: str, pdf: bytes, result: LadderResult,
                 independent_against: int, actor: str) -> None:
        """The customer sends a clearer copy for the same claim; it is judged afresh."""
        action = self.policy()[result.verdict.value]
        status = STATUS_FOR_ACTION[action]
        if result.rule_applied.startswith("R0") and action == REVIEW:
            status = "Waiting for customer"
        old = self.claim(claim_id)
        issuer = self._issuer_for(result, old["bill_no"])
        with self._conn() as c:
            c.execute("UPDATE claims SET file_name=?, pdf=?, issuer_name=?, verdict=?, risk=?, rule=?, "
                      "registry=?, evidence_grade=?, reason=?, independent_against=?, initial_action=?, "
                      "status=?, officer_decision='', decided_by='', decided_at='', result_json=? "
                      "WHERE claim_id=?",
                      (file_name, pdf, issuer, result.verdict.value, result.risk, result.rule_applied,
                       result.registry_answer.value, result.evidence_grade.value, result.reason,
                       independent_against, action, status, json.dumps(_result_json(result)), claim_id))
        self.log(actor, "customer", "Clearer copy re-submitted", claim_id, file_name)
        self.log("TrustLadder", "system", f"{result.verdict.value} ({result.rule_applied.split(':')[0]})",
                 claim_id, f"{action} → {status}")

    def claims(self, customer_id: str | None = None, statuses: tuple | None = None) -> list[dict]:
        q, args = "SELECT * FROM claims WHERE 1=1", []
        if customer_id:
            q += " AND customer_id = ?"
            args.append(customer_id)
        if statuses:
            q += f" AND status IN ({','.join('?' * len(statuses))})"
            args += list(statuses)
        q += " ORDER BY submitted_at DESC, claim_id DESC"
        with self._conn() as c:
            return [dict(r) for r in c.execute(q, args)]

    def claim(self, claim_id: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM claims WHERE claim_id = ?", (claim_id,)).fetchone()
        return dict(r) if r else None

    def decide(self, claim_id: str, decision: str, officer: str, when: str | None = None) -> None:
        """A person's decision on an open claim. Logged beside the machine's verdict."""
        label, status = OFFICER_DECISIONS[decision]
        when = when or now()
        with self._conn() as c:
            c.execute("UPDATE claims SET status = ?, officer_decision = ?, decided_by = ?, "
                      "decided_at = ? WHERE claim_id = ?", (status, label, officer, when, claim_id))
        machine = self.claim(claim_id)["verdict"]
        self.log(officer, "officer", label, claim_id, f"machine verdict was {machine}", when)

    # ------------------------------------------------------------------ audit

    def log(self, actor: str, role: str, event: str, claim_id: str, detail: str,
            when: str | None = None) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO audit (ts, actor, role, event, claim_id, detail) VALUES (?,?,?,?,?,?)",
                      (when or now(), actor, role, event, claim_id, detail))

    def audit(self, limit: int = 500) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT ts, actor, role, event, claim_id, detail FROM audit ORDER BY ts DESC, id DESC LIMIT ?",
                (limit,))]

    # ------------------------------------------------------------------ measures

    def measures(self) -> dict:
        """Both directions, from what actually happened to the claims.

        A wrongful hold is measured the way an institution could measure it:
        a claim the system HELD that a person later RELEASED as genuine.
        """
        rows = self.claims()
        n = len(rows)
        auto_paid = sum(r["status"] == "Paid" for r in rows)
        to_person = sum(r["initial_action"] != PAY for r in rows)
        held = [r for r in rows if r["initial_action"] == HOLD]
        wrongful = sum(r["officer_decision"] == OFFICER_DECISIONS["release"][0] for r in held)
        released_after_review = sum(r["status"] == "Paid after review" for r in rows)
        rejected = sum(r["status"] == "Rejected" for r in rows)
        open_now = sum(r["status"] in OPEN_STATUSES for r in rows)
        # Sample-data only: ground truth is known for the seeded claims.
        labelled = [r for r in rows if r["truth"]]
        frauds = [r for r in labelled if r["truth"] == "fraud"]
        return {
            "claims": n, "auto_paid": auto_paid,
            "straight_through_pct": round(100 * auto_paid / n) if n else 0,
            "to_person": to_person, "held": len(held), "wrongful_holds": wrongful,
            "released_after_review": released_after_review, "rejected": rejected,
            "open": open_now,
            "officer_hours": round(to_person * 15 / 60, 1),
            "sample_frauds": len(frauds),
            "sample_frauds_paid": sum(r["initial_action"] == PAY for r in frauds),
        }


def result_from_json(d: dict) -> LadderResult:
    """Rebuild a stored verdict so the screens can redraw it (fields are not kept)."""
    from .models import EvidenceGrade, Finding, ReadStatus, RegistryAnswer, Verdict
    return LadderResult(
        file_name=d["file_name"], read_status=ReadStatus(d["read_status"]), fields=None,
        read_note=d["read_note"],
        findings=[Finding(f["family"], f["check"], f["detail"]) for f in d["findings"]],
        registry_answer=RegistryAnswer(d["registry_answer"]), registry_note=d["registry_note"],
        verdict=Verdict(d["verdict"]), risk=d["risk"], evidence_grade=EvidenceGrade(d["evidence_grade"]),
        rule_applied=d["rule_applied"], reason=d["reason"], needs_human=d["needs_human"],
        queue=d["queue"], rule_version=d["rule_version"])


def _result_json(r: LadderResult) -> dict:
    """Everything needed to redraw the verdict screen later, without the PDF engine."""
    return {
        "file_name": r.file_name,
        "read_status": r.read_status.value,
        "read_note": r.read_note,
        "findings": [{"family": f.family, "check": f.check, "detail": f.detail} for f in r.findings],
        "registry_answer": r.registry_answer.value,
        "registry_note": r.registry_note,
        "verdict": r.verdict.value,
        "risk": r.risk,
        "evidence_grade": r.evidence_grade.value,
        "rule_applied": r.rule_applied,
        "reason": r.reason,
        "needs_human": r.needs_human,
        "queue": r.queue,
        "rule_version": r.rule_version,
    }


_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  user_id TEXT PRIMARY KEY, name TEXT, role TEXT, org TEXT, pw_salt TEXT, pw_hash TEXT,
  customer_id TEXT, issuer_id TEXT);
CREATE TABLE IF NOT EXISTS customers (customer_id TEXT PRIMARY KEY, name TEXT, city TEXT);
CREATE TABLE IF NOT EXISTS bills (
  bill_no TEXT PRIMARY KEY, issuer_id TEXT, issuer_name TEXT, customer_id TEXT, patient TEXT,
  bill_date TEXT, total_paise INTEGER, ticket TEXT, published INTEGER, pdf BLOB, issued_at TEXT);
CREATE TABLE IF NOT EXISTS claims (
  claim_id TEXT PRIMARY KEY, customer_id TEXT, customer_name TEXT, issuer_name TEXT, bill_no TEXT,
  submitted_at TEXT, file_name TEXT, pdf BLOB, verdict TEXT, risk TEXT, rule TEXT, registry TEXT,
  evidence_grade TEXT, reason TEXT, independent_against INTEGER, initial_action TEXT, status TEXT,
  officer_decision TEXT DEFAULT '', decided_by TEXT DEFAULT '', decided_at TEXT DEFAULT '',
  truth TEXT DEFAULT '', result_json TEXT);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, actor TEXT, role TEXT, event TEXT,
  claim_id TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS policy (verdict TEXT PRIMARY KEY, action TEXT);
"""
