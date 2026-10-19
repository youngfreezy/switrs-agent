"""Evidence-bound claims.

A claim points at result cells and names an operation on them. The server recomputes the value,
checks the number in the claim text against it (precision, sign and direction words), checks the
named entity against the cited rows, and requires every number in the answer prose to sit inside
some claim. This shows that each number is the stated arithmetic on the stated cells. It does not
show that a query measures what the question asks; that is what the query plans are for.
"""

from __future__ import annotations

import math
import re

from .grounding import NUM, extract_numbers

OPS = ("value", "ratio", "percent_of", "percent_change", "difference", "sum")
ARITY = {"value": 1, "ratio": 2, "percent_of": 2, "percent_change": 2, "difference": 2}
UP = re.compile(r"\b(increas\w*|rose|rise|rising|up|higher|more|grew|growth|jump\w*|climb\w*|gain\w*)\b", re.I)
DOWN = re.compile(r"\b(decreas\w*|fell|fall|falling|down|lower|fewer|less|drop\w*|declin\w*|shrank|reduc\w*)\b", re.I)
ROAD = re.compile(r"\b(I|US|SR|CA|Route|Highway|Hwy|Interstate)[- ]?\d+\b", re.I)
QUERY_ID = re.compile(r"\b[0-9a-f]{4}-q\d+\b|\bq\d+\b", re.I)
DATE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b|\b\d{1,2}:\d{2}(?::\d{2})?\b")
COUNT_NOUN = re.compile(r"^\s*(crashes|collisions|people|persons|deaths|drivers|victims|parties|cases|fatalities|injuries|motorists)\b", re.I)


class ClaimError(ValueError):
    pass


def _validate_claim(claim) -> list[str]:
    if not isinstance(claim, dict):
        return ["Each claim must be an object."]
    errors = []
    for field in ("text", "metric", "population"):
        if not isinstance(claim.get(field), str) or not claim[field].strip():
            errors.append(f"Claim '{field}' must be a nonempty string.")
    if claim.get("op", "value") not in OPS:
        errors.append(f"Claim op must be one of {OPS}.")
    cells = claim.get("cells")
    if not isinstance(cells, list):
        errors.append("Claim cells must be a list of cell references.")
    else:
        for cell in cells:
            if (not isinstance(cell, dict)
                    or not isinstance(cell.get("query_id"), str)
                    or not isinstance(cell.get("column"), str)
                    or type(cell.get("row")) is not int):
                errors.append("Each cell needs string query_id and column fields and an integer row.")
    entity = claim.get("entity")
    if entity is not None and not (
            isinstance(entity, str) and entity.strip()
            or isinstance(entity, list) and entity and all(isinstance(e, str) and e.strip() for e in entity)):
        errors.append("Entity must be a nonempty string or a list of nonempty strings.")
    return errors


def _cell(queries: dict, turn_id: str, ref: dict):
    qid = ref.get("query_id")
    q = queries.get(qid)
    if not q:
        raise ClaimError(f"unknown query_id {qid!r}")
    if q.get("turn_id") != turn_id:
        raise ClaimError(f"{qid} belongs to another question; rerun it in this turn")
    cols = q["columns"]
    col = ref.get("column")
    if col not in cols:
        raise ClaimError(f"{qid} has no column {col!r}; columns are {cols}")
    ci = cols.index(col)
    provenance = q.get("data_derived")
    if not isinstance(provenance, list) or len(provenance) != len(cols):
        raise ClaimError(f"{qid} has missing or mismatched output provenance; rerun with explicit columns")
    if cols.count(col) != 1:
        raise ClaimError(f"{qid} has duplicate column {col!r}; rerun with unique column aliases")
    if provenance[ci] is not True:
        raise ClaimError(f"{qid}.{col} is a literal, not a value computed from the tables")
    row = ref.get("row")
    if not isinstance(row, int) or not 0 <= row < len(q["rows"]):
        raise ClaimError(f"{qid} row {row!r} is out of range (0..{len(q['rows']) - 1})")
    v = q["rows"][row][ci]
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        try:
            v = float(v)
        except (TypeError, ValueError):
            raise ClaimError(f"{qid} row {row} column {col!r} is not numeric ({v!r})") from None
    if not math.isfinite(float(v)):
        raise ClaimError(f"{qid}.{col} is not a finite number")
    return float(v), q["rows"][row], q


def compute(claim: dict, queries: dict, turn_id: str) -> tuple[float, list]:
    op = claim.get("op", "value")
    if op not in OPS:
        raise ClaimError(f"op must be one of {OPS}")
    cells = claim.get("cells") or []
    need = ARITY.get(op)
    if (need and len(cells) != need) or (op == "sum" and len(cells) < 2):
        raise ClaimError(f"op {op} takes {need or 'two or more'} cells, got {len(cells)}")
    vals, rows = [], []
    for ref in cells:
        v, row, _ = _cell(queries, turn_id, ref)
        vals.append(v)
        rows.append(row)
    if op == "value":
        return vals[0], rows
    if op == "sum":
        return sum(vals), rows
    a, b = vals
    if op == "difference":
        return a - b, rows
    if b == 0:
        raise ClaimError("division by zero")
    return {"ratio": a / b, "percent_of": 100 * a / b, "percent_change": 100 * (a - b) / b}[op], rows


def _masked_numbers(text: str) -> str:
    # Preserve offsets for coverage checks. Only explicitly contextual calendar years are
    # exempt; a number from the question is never evidence for a factual assertion.
    for pattern in (ROAD, QUERY_ID, DATE):
        text = pattern.sub(lambda m: " " * len(m.group(0)), text)
    chars = list(text)
    for match in NUM.finditer(text):
        raw = match.group(1)
        if (raw.isdigit() and 1990 <= int(raw) <= 2030 and not match.group(2)
                and not COUNT_NOUN.match(text[match.end():])
                and re.search(r"\b(in|during|since|before|after|from|to|between|and|year)\s+$",
                              text[:match.start()], re.I)):
            chars[match.start():match.end()] = " " * (match.end() - match.start())
    return "".join(chars)


def _numbers(text: str):
    return extract_numbers(_masked_numbers(text))


def check_claim(claim: dict, queries: dict, turn_id: str, sentence: str = "") -> tuple[float | None, list[str]]:
    errs = _validate_claim(claim)
    if errs:
        return None, errs
    text = claim.get("text", "")
    for field in ("metric", "population"):
        if not str(claim.get(field, "")).strip():
            errs.append(f"claim {text[:60]!r}: '{field}' is required (what is measured, over whom)")
    try:
        value, rows = compute(claim, queries, turn_id)
    except ClaimError as e:
        return None, errs + [f"claim {text[:60]!r}: {e}"]
    shown = _numbers(text)
    if len(shown) != 1:
        return value, errs + [f"claim {text[:60]!r}: use one numeric assertion per claim; found {len(shown)}"]
    op = claim.get("op", "value")
    # A numeric-only claim span still asserts the direction in its enclosing clause.
    context = sentence or text
    up, down = bool(UP.search(context)), bool(DOWN.search(context))
    matched = False
    for c in shown:
        if not math.isfinite(value) or not math.isfinite(c.value) or not -300 <= c.decimals <= 300:
            errs.append("Claim values must be finite and use a supported precision.")
            continue
        tol = max(0.5 * 10 ** (-c.decimals), 1e-9) + 1e-9
        if op in ("percent_of", "percent_change") and c.kind != "percent":
            continue
        if op == "ratio" and c.kind == "percent":
            errs.append("A ratio is not a percentage. Use percent_of to display the percentage.")
            continue
        if abs(c.value - value) <= tol:
            matched = True
        elif abs(c.value + value) <= tol and value < 0:
            # positive magnitude shown for a negative change: only valid with a "down" word
            if down and not up:
                matched = True
            else:
                errs.append(f"claim {text[:60]!r}: value is {value:.4g} (negative) but the text reads as an increase or has no direction")
                matched = True
        if matched:
            break
    if not matched:
        errs.append(f"claim {text[:60]!r}: computed {value:.6g} from the cited cells; the text shows {', '.join(c.text for c in shown)}")
    if value > 0 and op in ("percent_change", "difference") and down and not up:
        errs.append(f"claim {text[:60]!r}: value is +{value:.4g} but the text describes a decrease")
    raw_entity = claim.get("entity")
    entities = [str(e).strip().lower() for e in raw_entity] if isinstance(raw_entity, list) else (
        [str(raw_entity).strip().lower()] if raw_entity else [])
    context = (sentence or text).lower().strip().lstrip("*-# ")
    labelled = [[str(v).lower() for v in row if isinstance(v, str) and not re.fullmatch(r"-?[\d.,]+", v)] for row in rows]
    if any(labelled) and not entities:
        first = next(l for l in labelled if l)
        errs.append(f"claim {text[:60]!r}: the cited row is labelled ({first[0]!r}); set 'entity' to that label")
    if entities:
        if len(entities) not in (1, len(rows)):
            errs.append(f"claim {text[:60]!r}: give one entity, or one per cited cell ({len(rows)})")
        for e in entities:
            if e not in context:
                errs.append(f"claim {text[:60]!r}: entity {e!r} does not appear in the claim's sentence")
        for i, labels in enumerate(labelled):
            e = entities[i] if len(entities) == len(rows) else entities[0]
            if labels and e not in labels:
                errs.append(f"claim {text[:60]!r}: entity {e!r} does not match the cited row {labels}")
        # A row label merely mentioned in a comparison is not the subject of the asserted
        # value. Use a constrained entity-led clause instead of pretending to parse all prose.
        first = re.search(re.escape(entities[0]) + r"(?!\w)", context)
        number = NUM.search(_masked_numbers(context))
        prefix = context[first.end():number.start()].strip() if first and number else ""
        predicates = r"(?:(?:had|has|have|recorded|reported|accounted|for|was|were|is|are|a|an|the|total|of|share|rate|count|about|approximately|roughly|changed|by|increased|decreased|fell|rose)\b[\s:]*?)*"
        if not first or first.start() != 0 or not number or number.start() < first.end() or not re.fullmatch(predicates, prefix):
            errs.append(f"claim {text[:60]!r}: start its sentence with the asserted entity followed directly by the value "
                        "or a simple predicate, for example 'Los Angeles had 2 collisions'")
    return value, errs


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def check_answer(answer: str, claims: list[dict], queries: dict, turn_id: str, question: str = "") -> dict:
    if not isinstance(answer, str) or not isinstance(claims, list):
        return {"ok": False, "errors": ["Answer must be a string and claims must be a list."], "claims": []}
    errors: list[str] = []
    checked = []
    norm_answer = _norm(answer)
    spans = []
    for claim in claims:
        malformed = _validate_claim(claim)
        if malformed:
            errors.extend(malformed)
            continue
        text = _norm(claim.get("text", ""))
        start = norm_answer.find(text) if text else -1
        value, errs = check_claim(claim, queries, turn_id, _sentence_around(norm_answer, start, len(text)))
        errors += errs
        if start < 0:
            errors.append(f"claim text {text[:60]!r} does not appear verbatim in the answer")
        else:
            spans.append((start, start + len(text)))
        checked.append({**claim, "computed": value})
    masked = _masked_numbers(norm_answer)
    for m in NUM.finditer(masked):
        if not any(s <= m.start() and m.start() + len(m.group(0).rstrip()) <= e for s, e in spans):
            errors.append(f"number {m.group(1)!r} near {norm_answer[max(0, m.start() - 30):m.end() + 20]!r} is not part of any claim")
    return {"ok": not errors, "errors": errors, "claims": checked}


def _sentence_around(text: str, start: int, length: int) -> str:
    """The assertion clause containing a claim; commas inside numbers are not separators."""
    if start < 0:
        return ""
    boundaries = list(re.finditer(r"[.;?!,]\s+|\n", text))
    left = max((m.end() for m in boundaries if m.end() <= start), default=0)
    right = min((m.start() for m in boundaries if m.start() >= start + length), default=len(text))
    return text[left:right]
