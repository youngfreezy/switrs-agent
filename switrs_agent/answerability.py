"""Classify a question before any SQL runs.

The server reads the question itself, so a model cannot avoid a caveat by avoiding a column.
Each detection names a concept the data lacks, or a kind of claim the data can only describe.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, replace
from datetime import date

from .semantic import load
from .coverage import bounds as coverage_bounds, statement as coverage_statement

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
    Rule("vehicle_color", _r(r"\bcolou?rs?\b|\b(red|blue|black|white|brown|beige|gold|purple|pink|silver|green|yellow|orange|grey|gray)\b(?!\s+county)(?=.{0,30}\b(cars?|vehicles?|trucks?|sports|suvs?|sedans?)\b)"),
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
    Rule("outside_california", _r(r"\b(alabama|alaska|arizona|arkansas|colorado|connecticut|delaware|florida|georgia|hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|mississippi|missouri|montana|nebraska|nevada|new hampshire|new jersey|new mexico|new york|north carolina|north dakota|ohio|oklahoma|oregon|pennsylvania|rhode island|south carolina|south dakota|tennessee|texas|utah|vermont|virginia|washington|west virginia|wisconsin|wyoming|district of columbia|las vegas|reno|phoenix|portland|seattle|mexico)\b(?!\s+county)"),
         "out_of_scope", "The data covers California only.", None),
    Rule("after_coverage", _r(r"\b(this year|last year|right now|currently|today|recent months)\b"),
         "out_of_scope", "The data ends on 2021-06-03.", "coverage_window"),
]


# Parse explicit calendar periods before looking for bare years, so an in-range
# June 3, 2021 date is not mistaken for a request for the whole of 2021.
_MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
_MONTHS.update({name.lower(): i for i, name in enumerate(calendar.month_abbr) if name})
_MONTH_PATTERN = "|".join(sorted(_MONTHS, key=len, reverse=True))
_DATE_PATTERNS = [
    _r(r"\b(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})\b"),
    _r(r"\b(?P<month>\d{1,2})/(?P<day>\d{1,2})/(?P<year>\d{4})\b"),
    _r(rf"\b(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<month>{_MONTH_PATTERN})\.?[ ,]+(?P<year>\d{{4}})\b"),
    _r(rf"\b(?P<month>{_MONTH_PATTERN})\.?\s+(?:(?P<day>\d{{1,2}})(?:st|nd|rd|th)?[ ,]+)?(?P<year>\d{{4}})\b"),
    _r(r"\b(?P<year>\d{4})\b"),
]


def _coverage_rule(question: str, dataset: dict | None = None) -> Rule | None:
    if dataset is None:  # Standalone diagnostics retain the default catalog scope.
        schema, _ = load()
        dates = re.findall(r"\d{4}-\d{2}-\d{2}", schema["dataset"]["coverage"])
        start, end = map(date.fromisoformat, dates)
    else:
        try:
            start, end = coverage_bounds(dataset.get('coverage'))
        except ValueError:
            start = end = None  # The compiler rejects invalid metadata before querying a period.
    # Model year is an available vehicle attribute, not a collision date.
    text = re.sub(r"\bmodel\s+years?\s+(?:of\s+)?\d{4}\b|\b\d{4}\s+model[- ]years?\b",
                  " ", question, flags=re.IGNORECASE)
    # Explicit quantity comparisons are not calendar years.
    text = re.sub(r"\b(?:more than|fewer than|less than|at least|at most|over|under)\s+\d{4}"
                  r"(?=\s+(?:collisions?|crashes?|deaths?|fatalities|injuries|vehicles?|cars?|victims?|people)\b)",
                  " ", text, flags=re.IGNORECASE)
    consumed: list[tuple[int, int]] = []
    for pattern in _DATE_PATTERNS:
        for match in pattern.finditer(text):
            if any(match.start() < right and match.end() > left for left, right in consumed):
                continue
            consumed.append(match.span())
            parts = match.groupdict()
            year = int(parts["year"])
            raw_month = parts.get("month")
            month = (_MONTHS[raw_month.lower()] if raw_month and raw_month.isalpha()
                     else int(raw_month) if raw_month else None)
            day = int(parts["day"]) if parts.get("day") else None
            try:
                lower = date(year, month or 1, day or 1)
                upper = date(year, month or 12,
                             day or (calendar.monthrange(year, month)[1] if month else 31))
            except ValueError:
                return Rule("invalid_date", _r(r"(?!)"), "out_of_scope",
                            "The requested calendar date is invalid; clarify the date.", "coverage_window")
            if start is not None and (lower < start or upper > end):
                return Rule("outside_coverage", _r(r"(?!)"), "out_of_scope",
                            f"The data covers {start} to {end}; the requested period is not fully available.",
                            "coverage_window")
    return None


def classify(question: str, dataset: dict | None = None) -> list[Rule]:
    hits = [r for r in RULES if r.pattern.search(question)]
    if dataset is not None:
        hits = [replace(r, gap=coverage_statement(dataset) + ' Specify an explicit calendar period.')
                if r.id == 'after_coverage' else r for r in hits]
    coverage = _coverage_rule(question, dataset)
    if coverage:
        hits.append(coverage)
    return hits


def check_status(question: str, status: str, dataset: dict | None = None) -> dict:
    """Return detections, the gaps and caveats they require, and an error if the declared status
    contradicts them."""
    status = (status or "").strip().lower()
    hits = classify(question, dataset)
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
