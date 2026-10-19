"""Bounded question compiler and server-owned answer rendering.

Only whole-question matches select metrics. Model SQL, interpretations, and prose
are never inputs to this compiler. Unknown forms receive a clarification, not a
best-guess filter. Adding a supported form requires independent fixture tests.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from . import answerability, semantic
from .metric_recipes import QUESTIONS, RECIPES
from .coverage import bounds as coverage_bounds

VERSION = 'certified-metrics-v1'


class CertificationError(ValueError):
    pass


@dataclass(frozen=True)
class Request:
    metric: str
    county: str | None = None
    year: int | None = None
    fatal: bool = False
    motorcycle: bool = False


def normalize(question: str) -> str:
    return ' '.join(question.lower().split()).rstrip('?.')


def counties() -> set[str]:
    _, catalog = semantic.load()
    return set(catalog['collisions']['columns']['county_location']['values'])


def parse(question: str) -> Request | None:
    text = normalize(question)
    if text in ('which county has the most collisions', 'which county has the most crashes'):
        return Request('top_county')
    match = re.fullmatch(
        r'how many (?P<metric>(?:reported |fatal )?(?:collisions|crashes)|people were killed|fatalities|deaths)'
        r'(?: (?:happened|occurred|were recorded))?'
        r'(?P<motorcycle> involved a motorcycle)?'
        r'(?: in (?P<county>[a-z ]+?) county| in (?P<state>california))?'
        r'(?: in (?P<year>\d{4}))?', text)
    if not match:
        return None
    county = match['county']
    if county and county not in counties():
        return None
    metric = match['metric']
    deaths = metric in ('people were killed', 'fatalities', 'deaths')
    if deaths and match['motorcycle']:
        return None  # Deaths in motorcycle crashes != motorcyclists killed.
    return Request('fatalities' if deaths else 'collision_count', county,
                   int(match['year']) if match['year'] else None,
                   metric.startswith('fatal '), bool(match['motorcycle']))


class Builder:
    def __init__(self, toolkit, turn_id: str):
        self.kit, self.turn_id = toolkit, turn_id
        self.claims: list[dict] = []
        self.parts: list[str] = []

    def query(self, sql: str, metric: str) -> dict:
        q = self.kit.run_sql(sql, f'Approved metric: {metric}',
                             f'Server-compiled {VERSION}; scope comes from the bound original question.')
        if 'error' in q:
            raise CertificationError(q['error'])
        if q['truncated']:
            raise CertificationError('The metric result was truncated; no answer can be certified.')
        return q

    def fact(self, template: str, metric: str, q: dict, column='n', row=0,
             denominator: str | None = None, decimals=2) -> str:
        cells = [dict(query_id=q['query_id'], row=row, column=column)]
        if denominator:
            cells.append(dict(query_id=q['query_id'], row=row, column=denominator))
        claim = dict(op='percent_of' if denominator else 'value', cells=cells,
                     metric=metric, population=self.kit.turns[self.turn_id]['question'])
        # Counts must never round through binary floating point, including in the
        # evidence object. In particular, integers above 2**53 must remain exact.
        value = self.count(q, column, row)
        if denominator:
            total = self.count(q, denominator, row)
            if not total:
                raise CertificationError('A percentage needs a nonzero denominator.')
            if value > total:
                raise CertificationError('The numerator exceeds its eligible population.')
            exact = Decimal(value) * 100 / Decimal(total)
            shown = f'{exact:.{decimals}f}%'
            value = float(exact)
        else:
            shown = f'{value:,}'
        text = template.format(value=shown)
        self.claims.append({**claim, 'text': text, 'computed': value})
        self.parts.append(text)
        return text

    def count(self, q: dict, column: str, row=0) -> int:
        value = q['rows'][row][q['columns'].index(column)]
        if type(value) is int and value >= 0:
            return value
        if (type(value) is float and math.isfinite(value) and value.is_integer()
                and 0 <= value <= 2**53):
            return int(value)
        raise CertificationError('A count must be a finite nonnegative integer with exact precision.')

    def finish(self, status='answered', interpretation=None) -> dict:
        return dict(answer='\n\n'.join(self.parts), claims=self.claims, status=status,
                    interpretation=interpretation, metric_version=VERSION)


def refusal(question: str, dataset: dict) -> dict:
    hits = answerability.classify(question, dataset)
    gaps = list(dict.fromkeys(h.gap for h in hits if h.gap))
    if any(h.kind == 'causal' for h in hits):
        gaps.append('These observational crash records cannot prove causal effects or test police bias. '
                    'Recorded seat-belt outcomes can describe associations only.')
    if gaps:
        text = 'This question cannot be answered as stated from these records. ' + ' '.join(gaps)
        status = 'unsupported'
    else:
        text = ('I cannot certify this question with the supported metric definitions. '
                'Please specify a reported collision count, fatality count, or county ranking, '
                'with an optional county and calendar year. Extra conditions and ambiguous '
                'follow-ups require an explicitly supported question.')
        status = 'needs_clarification'
    return dict(answer=text, claims=[], status=status, interpretation=None, metric_version=VERSION)


def period_check(toolkit, first: date, last: date) -> dict | None:
    """A requested whole period must fit the reconciled snapshot, not prompt text."""
    try:
        start, end = coverage_bounds(toolkit.dataset.get('coverage'))
    except ValueError as exc:
        raise CertificationError(str(exc)) from exc
    if first < start or last > end:
        return dict(answer=f'The requested whole period is not available. This snapshot covers {start} to {end}; '
                           'a partial-period count cannot stand in for the requested full period.',
                    claims=[], status='unsupported', interpretation=None, metric_version=VERSION)
    return None


def prepare(toolkit, turn_id: str) -> dict:
    question = toolkit.turns[turn_id]['question']
    recipe = QUESTIONS.get(normalize(question))
    if recipe:
        if recipe == 'covid':
            unavailable = period_check(toolkit, date(2019, 1, 1), date(2020, 12, 31))
            if unavailable:
                return unavailable
        return RECIPES[recipe](Builder(toolkit, turn_id))
    request = parse(question)
    # Unsupported modifiers cannot be silently dropped, including unavailable dates.
    if request is None or answerability.classify(question, toolkit.dataset):
        return refusal(question, toolkit.dataset)
    if request.year:
        unavailable = period_check(toolkit, date(request.year, 1, 1), date(request.year, 12, 31))
        if unavailable:
            return unavailable
    b = Builder(toolkit, turn_id)
    if request.metric == 'top_county':
        q = b.query('SELECT county_location, COUNT(*) AS n FROM collisions '
                    'WHERE county_location IS NOT NULL GROUP BY 1 ORDER BY n DESC, county_location', 'top_county')
        if not q['rows']:
            b.parts.append('No collisions with a recorded county matched; a county ranking is unavailable.')
            return b.finish('no_matching_records')
        maximum = q['rows'][0][1]
        winners = [(i, row) for i, row in enumerate(q['rows']) if row[1] == maximum]
        for i, (county, _) in winners:
            if county not in counties():
                raise CertificationError('The result contains an unrecognized county label.')
            template = county.title() + ' had {value} reported collisions'
            template += ', tied for the most among recorded counties.' if len(winners) > 1 else ', the most among recorded counties.'
            b.fact(template, 'collision_count_by_county', q, row=i)
        b.parts.append('This compares raw counts among records with a known county, not per-capita risk.')
        return b.finish(interpretation='Rank known counties by reported collision count; include ties.')
    filters = []
    scope = ' across the available snapshot'
    if request.county:
        # County is an exact member of the checked-in catalog, never arbitrary model text.
        filters.append(f"county_location = '{request.county}'")
        scope = f' in {request.county.title()} County'
    if request.year:
        filters.append(f'YEAR(collision_date) = {request.year}')
        scope += f' in {request.year}'
    if request.fatal:
        filters.append("collision_severity = 'fatal'")
    if request.motorcycle:
        filters.append('motorcycle_collision = 1')
    where = ' WHERE ' + ' AND '.join(filters) if filters else ''
    if request.metric == 'fatalities':
        q = b.query('SELECT COUNT(*) AS records, COUNT(killed_victims) AS known, '
                    'SUM(killed_victims) AS n, COUNT(*) FILTER (WHERE killed_victims < 0 '
                    'OR NOT ISFINITE(killed_victims) OR killed_victims <> FLOOR(killed_victims)) AS invalid '
                    'FROM collisions' + where, request.metric)
        records, known, _, invalid = q['rows'][0]
        if records == 0:
            b.fact('There were {value} matching collision records' + scope + '.', 'collision_count', q, 'records')
            return b.finish('no_matching_records', request.__dict__)
        if known != records:
            raise CertificationError('Missing fatality counts prevent a complete total for this population.')
        if invalid:
            raise CertificationError('Invalid individual fatality counts prevent a certified total.')
        b.fact('There were {value} recorded fatalities' + scope + '.', request.metric, q)
    else:
        q = b.query('SELECT COUNT(*) AS n FROM collisions' + where, request.metric)
        label = 'reported fatal collisions' if request.fatal else 'reported collisions'
        if request.motorcycle:
            label += ' involving a motorcycle'
        b.fact('There were {value} ' + label + scope + '.', request.metric, q)
    empty = request.metric == 'collision_count' and b.count(q, 'n') == 0
    return b.finish('no_matching_records' if empty else 'answered', request.__dict__)
