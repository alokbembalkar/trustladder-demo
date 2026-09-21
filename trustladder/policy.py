"""
The institution's policy layer, and the cost of being wrong in both directions.

The rule (decide.py) says WHAT the evidence supports. The policy says what the
institution DOES about each verdict. The proposal gives this choice to the
institution (its Chief Risk Officer), not to the software, with one structural
limit that no policy can override:

    Only an Authentic verdict can be paid automatically.

So the trade-off the institution sets is between holding customers (safe but
harsh on the genuine ones) and sending them to a person (kinder but costs
officer time), never between catching fraud and paying it.

`evaluate` applies a policy to a simulated month and reports both sides:
fraud stopped AND genuine customers held or delayed. `baseline` applies the
common alternative, an edit-detection tool that clears anything with no
findings, to the same claims.
"""

from __future__ import annotations

PAY = "Pay"
REVIEW = "Human review"
HOLD = "Hold and investigate"
REQUEST = "Ask customer for original"

# Which actions each verdict may be mapped to. "Pay" appears only for Authentic:
# that is the guarantee, enforced here and not left to configuration.
ALLOWED = {
    "Authentic": [PAY, REVIEW],
    "Suspicious": [REVIEW, HOLD],
    "Tampered": [HOLD, REVIEW],
    "Inconclusive": [REVIEW, REQUEST],
}
DEFAULT_POLICY = {"Authentic": PAY, "Suspicious": REVIEW, "Tampered": HOLD, "Inconclusive": REVIEW}

# Working assumptions (the group's, not statistics): officer time per case
# looked at by a person, and the fully loaded cost of that time.
MINUTES_PER_REVIEW = 15
COST_PER_HOUR_RS = 300


def validate(policy: dict) -> None:
    """Refuse any policy that would pay a document without proof."""
    for verdict, action in policy.items():
        if action not in ALLOWED[verdict]:
            raise ValueError(f"'{action}' is not allowed for {verdict}: only proof clears.")


def _tally(rows: list[dict], action_of) -> dict:
    """Count outcomes in both directions for any verdict->action mapping."""
    out = {"claims": len(rows), "paid_straight_through": 0, "to_person": 0, "held": 0,
           "asked_for_original": 0, "fraud_total": 0, "fraud_paid": 0, "fraud_stopped": 0,
           "genuine_total": 0, "genuine_held": 0, "genuine_delayed": 0}
    for r in rows:
        action = action_of(r)
        out["paid_straight_through"] += action == PAY
        out["to_person"] += action == REVIEW
        out["held"] += action == HOLD
        out["asked_for_original"] += action == REQUEST
        if r["is_fraud"]:
            out["fraud_total"] += 1
            out["fraud_paid"] += action == PAY
            out["fraud_stopped"] += action != PAY
        else:
            out["genuine_total"] += 1
            out["genuine_held"] += action == HOLD
            out["genuine_delayed"] += action in (REVIEW, REQUEST)
    touched = out["to_person"] + out["held"]
    out["officer_hours"] = round(touched * MINUTES_PER_REVIEW / 60, 1)
    out["officer_cost_rs"] = int(round(touched * MINUTES_PER_REVIEW / 60 * COST_PER_HOUR_RS))
    out["straight_through_pct"] = round(100 * out["paid_straight_through"] / max(1, len(rows)))
    return out


def evaluate(rows: list[dict], policy: dict) -> dict:
    """TrustLadder: apply the institution's policy to the rule's verdicts."""
    validate(policy)
    return _tally(rows, lambda r: policy[r["verdict"]])


def baseline(rows: list[dict]) -> dict:
    """An edit-detection tool: pay if nothing looks wrong, hold if anything does."""
    return _tally(rows, lambda r: PAY if not r["families"] else HOLD)
