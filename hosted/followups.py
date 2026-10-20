"""Resolve one explicit scope replacement; never use model prose or prior numbers."""
import re
from dataclasses import replace

from switrs_agent.certification import counties, normalize, parse


def resolve(question: str, previous_question: str) -> str:
    match = re.fullmatch(
        r'(?:and|what about|how about) (?:in |during )?'
        r'(?P<scope>[0-9]{4}|[a-z ]+ county|california)(?: instead)?',
        normalize(question))
    prior = parse(previous_question)
    if not match or prior is None:
        return question
    scope = match['scope']
    if scope.isdigit():
        prior = replace(prior, year=int(scope))
    else:
        county = None if scope == 'california' else scope.removesuffix(' county')
        if (county and county not in counties()) or prior.metric == 'top_county':
            return question
        prior = replace(prior, county=county)
    if prior.metric == 'top_county':
        stem = 'Which county had the most collisions'
    elif prior.metric == 'fatalities':
        stem = 'How many fatalities'
    else:
        stem = 'How many ' + ('fatal ' if prior.fatal else '')
        stem += ('motorcycle ' if prior.motorcycle else '') + 'collisions'
    place = f'{prior.county.title()} County' if prior.county else 'California'
    # Keep even zero as an explicit year so the calendar guard can reject it.
    year = f' in {prior.year:04d}' if prior.year is not None else ''
    return f'{stem} in {place}{year}?'
