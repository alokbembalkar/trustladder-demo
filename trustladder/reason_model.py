"""
The one place a language model is allowed to touch the output.

What it does, and the three things it cannot do
-----------------------------------------------
After the rule has decided, this asks a model to turn the evidence into two or three
sentences a held customer or an auditor can read. It is given the verdict, the rule
that fired, what the issuer said, the findings grouped by evidence family, and
nothing else.

It cannot change the verdict: the decision was made before this runs, by `decide.py`,
and nothing here is fed back into it. It cannot invent evidence: `explain.check_back`
rejects any reason that does not open with the sentence belonging to the decided
verdict, and a rejected reason is discarded in favour of the written one. And it cannot
stop the demo: if there is no key, no network, or the call is slow, the template answers
instead and the screen says which one you are reading.

Grounding
---------
The prompt is built from the evidence graph: each finding is listed under its family
(arithmetic, file history, fonts, claim history, issuer registry), with the rule's own
sentence. That is retrieval over the decision's graph and the published policy, which
is the small-scale form of what the architecture calls "GraphRAG over policy". The
writer is told in so many words that it is explaining, not judging.

Why the call is made once
-------------------------
The reason is written when the claim is submitted and stored with it. Screens redraw
from the stored text, so nothing is re-generated while someone is presenting, and the
same claim always reads the same way to the officer, the auditor and the customer.

What is sent, and what a reviewer should ask about it
-----------------------------------------------------
Only the verdict, the rule, the issuer's answer and the finding sentences. No patient
name, no bill, no file. That is enough for a demo on synthetic data, and it is not
enough for production: claim text should not leave the insurer's boundary at all, so a
real deployment runs this against a model inside that boundary. Say that before anyone
asks it.
"""

from __future__ import annotations

from . import llm

SYSTEM = (
    "You write the explanation an insurer attaches to a decision about a hospital bill. "
    "The decision has already been made by a published rule; you are not deciding anything "
    "and you must not argue with it. Write two or three short sentences, plain English, no "
    "jargon, no bullet points, addressed to a careful reader who may be the customer or an "
    "auditor. Say what was decided, and why, using only the evidence given to you. Never "
    "invent a finding. Never mention a rule number. Begin with exactly the sentence you are "
    "given as the opening, word for word."
)


def available() -> bool:
    """Is a model configured and switched on?"""
    return llm.available()


def write(opener: str, verdict: str, rule_text: str, registry_note: str,
          findings: list[str], families: dict[str, list[str]] | None = None
          ) -> tuple[str, str] | None:
    """Ask the model for the reason. Returns (text, model name), or None on any problem.

    Returning None is a normal outcome, not an error path: no key configured is the
    ordinary case for anyone who clones this repository, and the written reason is a
    complete answer on its own.
    """
    if not llm.available():
        return None

    if families:
        # Grounded on the evidence graph: findings under the family they belong to.
        evidence = "\n".join(f"- {fam}: " + " ".join(details) for fam, details in families.items())
    else:
        evidence = "\n".join(f"- {f}" for f in findings)
    evidence = evidence or "- nothing was found in the document itself"
    prompt = (
        f"Opening sentence to use, word for word: {opener}\n\n"
        f"Decision: {verdict}\n"
        f"Why the rule reached it: {rule_text}\n"
        f"What the issuer's registry said: {registry_note or 'the registry was not consulted'}\n"
        f"What was found, by independent line of evidence:\n{evidence}\n\n"
        "Write the explanation now."
    )
    try:
        reply = llm.generate([{"role": "user", "parts": [llm.text_part(prompt)]}], system=SYSTEM)
    except (llm.ModelUnavailable, ValueError):
        # A demo must not depend on a network call. The written reason takes over.
        return None
    text = " ".join(reply.text.split())
    # A reason is two or three sentences. Anything shorter is a cut-off or an empty
    # answer, and the written reason is better than a fragment.
    if len(text) < 60 or text.count(".") < 2:
        return None
    return text, reply.model
