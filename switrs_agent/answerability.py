"""Classify a question before any SQL runs.

The server reads the question itself, so a model cannot avoid a caveat by avoiding a column.
Each detection names a concept the data lacks, or a kind of claim the data can only describe.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

STATUSES = ("answerable", "ambiguous", "descriptive_only", "partially_available", "unavailable")


@dataclass(frozen=True)
class Rule:
    id: str
    pattern: re.Pattern
    kind: str            # missing | causal | exposure | out_of_scope
    gap: str             # sentence rendered under "Not in the data" (empty for non-gap rules)
    caveat: str | None   # caveat id from schema.yaml the answer must carry


def _r(p: str) -> re.Pattern:
    return re.compile(p, re.IGNORECASE)


RULES = [
    Rule("vehicle_color", _r(r"\bcolou?rs?\b|\b(red|blue|black|silver|green|yellow|orange|grey|gray)\b(?=.{0,30}\b(cars?|vehicles?|trucks?|sports|suvs?|sedans?)\b)"),
         "missing", "The data does not record vehicle color.", None),
    Rule("vehicle_model", _r(r"\bmodels?\b(?!\s+year)|\btrim\b|\b(mustang|corvette|camry|civic|accord|tesla model|911|f-150)\b|\bsports?\s+(cars?|vehicles?|models?)\b|\bflash(y|ier)\b|\bexotic\b"),
         "missing", "The data records vehicle make and model year only, not model or body style (so not 'sports car').", None),
    Rule("stops_tickets", _r(r"\b(traffic stops?|stopped by|pulled over|tickets?|citations?|cited)\b"),
         "missing", "The data holds only police-reported crashes. It has no traffic stops or tickets outside crashes.", None),
    Rule("police_bias", _r(r"\b(bias|biased|prejudice|profil\w*|discriminat\w*|target(s|ed|ing)?)\b"),
         "causal", "Testing police bias needs stop and exposure data the file does not have. An at-fault comparison here is descriptive only.", "observational"),
    Rule("causal", _r(r"\b(causes?|caused|prove|disprove|effective(ness)?|does .{0,20}work|do .{0,20}work|actually work|impact of|effect of|because of|reduce[sd]?)\b"),
         "causal", "", "observational"),
    Rule("exposure", _r(r"\b(safest|most dangerous|riskiest|per capita|per mile|miles driven|rate per|likelihood|chance of|more likely)\b"),
         "exposure", "The data has no traffic volume, miles driven or population, so it cannot give risk per driver, mile or resident.", "per_capita"),
    Rule("outside_california", _r(r"\b(nevada|las vegas|reno|arizona|phoenix|oregon|portland|texas|new york|washington state|seattle|mexico)\b"),
         "out_of_scope", "The data covers California only.", None),
    Rule("after_coverage", _r(r"\b(20(2[2-9]|3\d))\b|\b(this year|last year|right now|currently|today|recent months)\b"),
         "out_of_scope", "The data ends on 2021-06-03.", "coverage_window"),
]


def classify(question: str) -> list[Rule]:
    return [r for r in RULES if r.pattern.search(question)]


def check_status(question: str, status: str) -> dict:
    """Return detections, the gaps and caveats they require, and an error if the declared status
    contradicts them."""
    status = (status or "").strip().lower()
    hits = classify(question)
    out = {
        "detected": [h.id for h in hits],
        "gaps": [h.gap for h in hits if h.gap],
        "required_caveats": sorted({h.caveat for h in hits if h.caveat}),
        "error": None,
    }
    if status not in STATUSES:
        out["error"] = f"answerability must be one of {', '.join(STATUSES)}."
    elif status == "answerable" and hits:
        kinds = {h.kind for h in hits}
        need = ("unavailable or partially_available" if kinds & {"missing", "out_of_scope"}
                else "descriptive_only" if kinds & {"causal"} else "descriptive_only or partially_available")
        out["error"] = (f"The question involves {', '.join(out['detected'])}. Declare {need}, "
                        "answer what the data supports, and state the gap.")
    return out
