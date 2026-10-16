"""
The learned part of Stage 1, Detect: a small trained model that screens a bill.

Why this exists, and what it is honestly for
--------------------------------------------
The deterministic checks in `detect.py` look for named things: charges that do not
add up, a file saved in layers, a total in the wrong typeface. Each one is a rule
somebody wrote. This module is the other half: a model that was not told what to
look for, trained on a corpus of bills this repository generates, which learns for
itself how an edited document differs from one that came off a billing system.

It never decides anything. It produces a probability, that probability becomes one
more finding for the officer to read, and the published rule in `decide.py` treats
it exactly as it treats any other screening signal: as something that can corroborate
but can never clear.

The blind spot is the point
---------------------------
A bill typed from a blank page is not an edited document. It has correct arithmetic,
one revision, consistent fonts and ordinary metadata, because nothing was ever
altered. There is no signal in the file for any model to find, and `tools/train_screener.py`
measures exactly that: this model scores near chance on fabricated bills.

That is not a defect to be trained away. It is the reason the rule never lets a
screening model clear a document, and the reason the issuer registry exists at all.
Report the number; it is the strongest evidence for the design.

Why the model ships as coefficients
-----------------------------------
Training uses scikit-learn, offline, on a development machine. What ships is this
module plus `screener.json`: the feature names, the coefficients, the intercept and
the scaling, in plain text that anyone can read and apply by hand. Three consequences.
The deployed app needs no machine-learning dependency at all, so it stays small enough
for a free instance. The model is as inspectable as the rule is, which is the same
promise the rest of the product makes. And an auditor can reproduce any score with a
calculator.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .models import BillFields, Finding

MODEL_PATH = Path(__file__).with_name("screener.json")

# The family the model's finding belongs to. It is deliberately NOT a new independent
# line of evidence whenever the deterministic checks have already fired: a model
# reading the same document as those checks is a second opinion on one cause, not a
# second cause. See `screen_with_model` in detect.py for how that is enforced.
MODEL_FAMILY = "screening model"

# Above this, the model is saying "this looks like an edited document". Chosen on the
# training run's precision/recall curve and recorded in screener.json, not guessed.
DEFAULT_THRESHOLD = 0.60


def features(pdf_bytes: bytes, fields: BillFields | None) -> dict[str, float]:
    """Turn a bill into the numbers the model sees.

    Every one of these is cheap, local and explainable to a claims officer. There is
    no pixel model and no network call: the point of this feature set is that an
    auditor can see exactly what the model was given.
    """
    text = pdf_bytes[:400_000]
    revisions = text.count(b"%%EOF")
    creator = _meta(text, b"/Creator")
    producer = _meta(text, b"/Producer")
    created, modified = _meta(text, b"/CreationDate"), _meta(text, b"/ModDate")

    items_total = sum(i.amount_paise for i in fields.items) if fields and fields.items else 0
    stated = fields.total_paise if fields else 0
    gap = abs(stated - items_total)

    fonts = len(set(re.findall(rb"/BaseFont\s*/([A-Za-z0-9+,\-]+)", text)))

    return {
        # how many times the file was written. One is normal; more means layers.
        "revisions": float(revisions),
        # a bill written by a different program from the one that created it
        "creator_differs": 1.0 if (creator and producer and creator != producer) else 0.0,
        # a file modified after it was created
        "modified_later": 1.0 if (created and modified and modified > created) else 0.0,
        # the charges do not add up to the total, absolute and relative
        "total_gap": float(gap),
        "total_gap_ratio": float(gap) / float(stated) if stated else 0.0,
        # how many typefaces are embedded. An edit often brings a new one.
        "fonts": float(fonts),
        # size, which moves when a file is re-saved or re-encoded
        "size_kb": len(pdf_bytes) / 1024.0,
        # a joined issuer prints a ticket; its absence is itself informative
        "has_ticket": 1.0 if (fields and fields.ticket) else 0.0,
    }


def _meta(blob: bytes, key: bytes) -> str:
    m = re.search(re.escape(key) + rb"\s*\(([^)]*)\)", blob)
    return m.group(1).decode("latin-1", "replace") if m else ""


class Screener:
    """The trained model, applied with arithmetic anyone can check."""

    def __init__(self, spec: dict):
        self.names: list[str] = spec["features"]
        self.coef: list[float] = spec["coefficients"]
        self.intercept: float = spec["intercept"]
        self.mean: list[float] = spec["mean"]
        self.scale: list[float] = spec["scale"]
        self.threshold: float = spec.get("threshold", DEFAULT_THRESHOLD)
        self.trained_on: dict = spec.get("trained_on", {})
        self.measured: dict = spec.get("measured", {})

    def probability(self, feats: dict[str, float]) -> float:
        """Logistic regression, written out longhand so it can be audited."""
        z = self.intercept
        for name, w, mu, sd in zip(self.names, self.coef, self.mean, self.scale):
            x = (feats.get(name, 0.0) - mu) / (sd or 1.0)
            z += w * x
        return 1.0 / (1.0 + _exp(-z))

    def finding(self, pdf_bytes: bytes, fields: BillFields | None) -> Finding | None:
        """The model's opinion, as a sentence, or nothing if it is not concerned."""
        p = self.probability(features(pdf_bytes, fields))
        if p < self.threshold:
            return None
        return Finding(
            family=MODEL_FAMILY,
            check="learned_screener",
            detail=(f"A screening model trained on {self.trained_on.get('bills', 'synthetic')} "
                    f"generated bills puts this document at {p:.0%} likely to have been edited "
                    f"after it was issued."),
        )


def _exp(x: float) -> float:
    import math
    return math.exp(max(-60.0, min(60.0, x)))


_CACHE: Screener | None | bool = False


def load() -> Screener | None:
    """The trained model, or None if this build was shipped without one."""
    global _CACHE
    if _CACHE is False:
        try:
            _CACHE = Screener(json.loads(MODEL_PATH.read_text()))
        except (OSError, ValueError, KeyError):
            _CACHE = None
    return _CACHE
