# TrustLadder demo (IIM-V Capstone, Group 7)

A small working demonstration of the group's proposal, *TrustLadder: From Document
Detection to Digital Trust*. A hospital bill goes up four stages,
**Detect → Decide → Verify → Human review**, and comes back with one of four
verdicts (Authentic, Suspicious, Tampered, Inconclusive), the grade of the evidence
behind it, and a reason in plain words.

Everything is synthetic: fictional hospitals and patients, generated bills, a
simulated issuer registry. It runs offline on a laptop and connects to nothing.

## Run it

    ./run_demo.sh                      # opens on http://127.0.0.1:8765

Online: the same app is deployed from this repository on Render using
`render.yaml` (free plan, Python 3.12; the synthetic data is generated at build
time). A free Render service sleeps when idle, so the first visit can take
about a minute to wake it.

The script uses its own isolated Python environment (`.venv`, Python 3.12 via
`uv`). It shares nothing with any other project on the machine and uses port 8765.

## What is on screen

| Tab | Shows |
|---|---|
| 1 Check a bill | Six prepared bills (or upload a PDF) run through the ladder |
| 2 Human review | Every non-Authentic verdict, the officer's decision, the audit trail |
| 3 Hospital | Issue a bill: random ticket printed, two codes published and signed |
| 4 Registry | What the registry holds (codes only) and a forged-entry attempt being rejected |
| 5 Policy & impact | A simulated month at three coverage levels, the institution's policy, and a comparison with an edit-detection tool |

## The six demo bills

| # | Bill | Verdict | Principle |
|---|---|---|---|
| 1 | Genuine, hospital has joined | Authentic | Only proof clears |
| 2 | Total raised by Rs 1 lakh after issue | Tampered | Two independent findings convict |
| 3 | Made from nothing, hospital has joined | Suspicious | Appearance checks find nothing; the issuer says "no record" |
| 4 | Genuine, hospital not joined | Inconclusive | No proof, so referred, never cleared |
| 5 | Genuine, arrived as a blurred photo | Inconclusive | "Could not read" is never "mismatch" |
| 6 | Made from nothing, hospital not joined | Inconclusive | The honest gap, still never cleared |

## Code map (`trustladder/`)

`crypto.py` codes and signatures · `registry.py` publisher / registry / verifier ·
`bills.py` synthetic PDFs · `reader.py` field reading and the one standard form ·
`detect.py` screening checks · `decide.py` the published rule (R0-R8) ·
`explain.py` the plain-language reason · `pipeline.py` one document up the ladder ·
`policy.py` the institution's policy and both-direction outcomes · `cases.py`
builds the demo world.

## Tests

    uv pip install --python .venv/bin/python -r requirements-dev.txt
    .venv/bin/python -m pytest -q tests          # TC-01..TC-16 engine, TC-20..TC-27 screens
    .venv/bin/python tools/record_demo.py        # TC-30 in real Chrome + the backup video

The recorder drives the running app in Google Chrome, checks every verdict the
browser shows, and writes `build/TrustLadder_demo.mp4` plus stills for the slides.

## Design notes and honest limits

* The per-issuer salt is published in the directory: the verifier must recompute
  the code from its copy of the bill, so the salt cannot be secret. Privacy comes
  from the random 16-character ticket printed only on the bill.
* Fields are read from the PDF's text layer. Scans need an OCR / document-
  understanding model, which would plug into `reader.py`; without one, a scan is
  reported as "could not read", the safe behaviour.
* The reason is assembled from fixed sentences. In the design one LLM writes it
  after the rule has decided, fed only the findings, and is checked back
  (`explain.check_back`).
* The simulated month's mix (about 10% fraud, 8% photos, 8% re-saved genuine
  files) and the officer-time cost (15 min at Rs 300/hour) are assumptions, not
  statistics.
