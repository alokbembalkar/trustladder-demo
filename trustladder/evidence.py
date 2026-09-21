"""
The evidence graph behind every verdict (the "neuro-symbolic core" of stage 2).

Findings from the screening checks and the issuer registry become NODES. Nodes
are grouped by FAMILY: findings in the same family can share one cause (one edit
can break the arithmetic AND change the font), so they are not independent.
Each family is one independent line of evidence.

The rule (decide.py) needs "two independent findings" to convict. The graph makes
that visible: count the families that point AGAINST the document. It is built
from exactly the same inputs the rule uses, and a test checks the two agree.

Rendered with Graphviz DOT (drawn in the browser, no system install needed).
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import LadderResult, RegistryAnswer

# Registry answers that count as a line of evidence against the document.
_REGISTRY_AGAINST = {RegistryAnswer.MISMATCH, RegistryAnswer.NO_RECORD}


@dataclass
class EvidenceGraph:
    families: dict[str, list[str]]     # family -> finding details (the nodes)
    against: list[str]                 # families pointing against the document
    proof: bool                        # the issuer confirmed the document
    independent_against: int           # the number the rule's "two findings" test uses


def build(result: LadderResult) -> EvidenceGraph:
    """Group the result's evidence into independent families."""
    families: dict[str, list[str]] = {}
    for f in result.findings:
        families.setdefault(f.family, []).append(f.detail)
    if result.registry_answer is not RegistryAnswer.NOT_ASKED:
        families["issuer registry"] = [result.registry_note]
    against = sorted(fam for fam in families if fam != "issuer registry")
    if result.registry_answer in _REGISTRY_AGAINST:
        against.append("issuer registry")
    return EvidenceGraph(
        families=families,
        against=against,
        proof=result.registry_answer is RegistryAnswer.VERIFIED,
        independent_against=len(against),
    )


def _short(text: str, n: int = 70) -> str:
    text = text.replace('"', "'")
    return text if len(text) <= n else text[: n - 1] + "…"


def to_dot(result: LadderResult, graph: EvidenceGraph) -> str:
    """Graphviz DOT: document -> evidence families -> rule -> verdict."""
    colour = {"Authentic": "#15803D", "Suspicious": "#B45309",
              "Tampered": "#B91C1C", "Inconclusive": "#475569"}[result.verdict.value]
    lines = [
        "digraph G {",
        '  rankdir=LR; bgcolor="transparent"; nodesep=0.25; ranksep=0.45;',
        '  node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10, '
        'color="#CBD5E1", fillcolor="white"];',
        '  edge [color="#94A3B8"];',
        '  doc [label="Document", fillcolor="#F1F5F9"];',
        f'  rule [label="Policy-as-code rule\\n{result.rule_applied.split(":")[0]}", '
        'fillcolor="#0F766E", fontcolor="white", color="#0F766E"];',
        f'  verdict [label="{result.verdict.value}", fillcolor="{colour}", fontcolor="white", '
        f'color="{colour}"];',
    ]
    if not graph.families:
        lines.append('  none [label="No usable evidence\\n(could not read)", fillcolor="#F1F5F9"];')
        lines += ["  doc -> none;", "  none -> rule;"]
    for i, (fam, details) in enumerate(graph.families.items()):
        if fam == "issuer registry":
            tone = "#15803D" if graph.proof else ("#B91C1C" if fam in graph.against else "#475569")
            role = "proof" if graph.proof else ("against" if fam in graph.against else "no answer")
        else:
            tone, role = "#B45309", "against"
        lines.append(f'  subgraph cluster_{i} {{ label="{fam} ({role})"; fontname="Helvetica"; '
                     f'fontsize=10; color="{tone}"; style="rounded";')
        for j, d in enumerate(details):
            lines.append(f'    n{i}_{j} [label="{_short(d)}"];')
        lines.append("  }")
        for j in range(len(details)):
            lines.append(f"  doc -> n{i}_{j};")
            lines.append(f"  n{i}_{j} -> rule;")
    lines += ["  rule -> verdict;", "}"]
    return "\n".join(lines)
