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

## Sign in

One password for every demo login: **TrustLadder@2026**

| User ID | Role | Lands on | Menu |
|---|---|---|---|
| `hospital` | Hospital billing (Sahyog Multispeciality Hospital) | My bills | My bills · Issue a bill |
| `customer` | Customer (Meera Kulkarni) | My claims | My claims · Submit a claim |
| `officer` | Insurer claims officer | Claims inbox | Claims inbox · All claims · Try a sample bill |
| `riskhead` | Insurer risk head (CRO) | Impact dashboard | Impact dashboard · Simulated month |
| `registry` | Registry operator | Network | Network · Integrity check |
| `auditor` | Auditor / regulator (read-only) | Audit trail | Audit trail · How decisions are made |
| `presenter` | Every role, with "View as" and **Reset demo** | Claims inbox | (the chosen role's menu) |

This is a demonstration login: passwords are stored hashed, but there is no rate
limiting or account management.

## Sample data (all fictional, rebuilt by `python -m trustladder.seed`)

* 5 hospitals: Sahyog (Pune), Arogya (Nashik) and Kaveri (Kolhapur) have joined
  the registry; Shanti (Satara) and Niramay (Sangli) have not.
* 15 customers; Meera Kulkarni has a login and three claims (one paid, one waiting
  for a clearer copy, one in review).
* 34 claims across every outcome, each judged by the real ladder: 16 paid on
  proof, 8 known frauds (none paid automatically), officer decisions on many,
  and one honest wrongful hold (a genuine discounted bill held by the rule, then
  released by the officer).
* "Reset demo" (presenter) restores exactly this state from a snapshot.

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
    .venv/bin/python -m pytest -q tests          # TC-01..TC-17 engine, TC-40..TC-54 role-based screens
    .venv/bin/python tools/record_demo.py        # TC-30 in real Chrome + the backup video

The recorder drives the running app in Google Chrome as one story across every role,
checks each verdict and status in the browser, and writes `build/TrustLadder_demo.mp4`
plus stills for the slides. Add a URL and `--check` to run the same checks against the
live site without recording.

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
