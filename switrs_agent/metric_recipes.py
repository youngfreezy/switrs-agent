"""Reviewed descriptive analyses with fixed populations and denominators.

The question dispatcher uses full matches; extra requested filters never disappear.
These queries and renderers are trusted code, not LLM-produced SQL or conclusions.
"""
from decimal import Decimal
import re


QUESTIONS = {
    'what percentage of collisions involved a parked vehicle': 'parked',
    'how many collisions involved at least one driver who had been drinking': 'drinking',
    'at what hour of the day do the most fatal crashes happen': 'fatal_hour',
    'what share of at-fault drivers were using a cellphone': 'cellphone',
    'what are recorded victim death shares by lap/shoulder belt use': 'belts',
    'among passenger-car drivers and passengers recorded as victims in police-reported crashes, what '
    'share died when a lap/shoulder belt was recorded as used versus not used? count a belt as used or '
    'not used if either equipment field says so, and leave out conflicting or unknown entries': 'belts',
    'how has covid-19 changed collisions, if at all': 'covid',
}


# Whole-question equivalents only: no added populations, dates, or causal claims.
RECIPE_PARAPHRASES = (
    (r'what (?:share|percentage|percent) of at[- ]fault drivers(?: with known cellphone status)? '
     r'(?:were using|used) a (?:cellphone|cell phone)', 'cellphone'),
    (r'how many (?:collisions|crashes) involved at least one '
     r'(?:driver who had been drinking|drinking driver)', 'drinking'),
    (r'(?:which|what) hour (?:had|has) the most fatal (?:collisions|crashes)', 'fatal_hour'),
)


def recipe_for(question: str) -> str | None:
    if exact := QUESTIONS.get(question):
        return exact
    return next((recipe for pattern, recipe in RECIPE_PARAPHRASES
                 if re.fullmatch(pattern, question)), None)


def share(b, template, metric, q, numerator, denominator, decimals=2):
    if b.count(q, denominator) == 0:
        b.parts.append(f'The {metric.replace("_", " ")} share is unavailable: no eligible records form its denominator.')
        return
    b.fact(template, metric, q, numerator, denominator=denominator, decimals=decimals)


def change(b, q, newer, older, label):
    a, baseline = b.count(q, newer), b.count(q, older)
    if baseline == 0:
        b.parts.append(f'{label}: percentage change is unavailable because the baseline count is zero.')
        return
    c = dict(op='percent_change', metric='reported_collision_change', population=label,
             cells=[dict(query_id=q['query_id'], row=0, column=col) for col in (newer, older)])
    value = Decimal(100) * (a - baseline) / baseline
    direction = 'increased' if value > 0 else 'decreased' if value < 0 else 'changed'
    text = f'{label}: reported collisions {direction} by {abs(value):.2f}%.'
    b.parts.append(text)
    b.claims.append({**c, 'text': text, 'computed': float(value)})


def parked(b):
    q = b.query("""SELECT COUNT(*) AS total,
      COUNT(*) FILTER (WHERE motor_vehicle_involved_with='parked motor vehicle') AS primary_n,
      COUNT(*) FILTER (WHERE EXISTS (SELECT 1 FROM parties p WHERE p.case_id=c.case_id
        AND p.party_type='parked vehicle')) AS party_n,
      COUNT(*) FILTER (WHERE EXISTS (SELECT 1 FROM parties p WHERE p.case_id=c.case_id
        AND p.movement_preceding_collision='parked')) AS movement_n
      FROM collisions c""", 'parked_collision_shares')
    for column, definition in (
        ('primary_n', 'coded as primarily involving a parked motor vehicle'),
        ('party_n', 'with a party recorded as a parked vehicle'),
        ('movement_n', 'with a party whose movement before the crash was recorded as parked'),
    ):
        share(b, '{value} of reported collisions were ' + definition + '.', 'parked_collision_share', q, column, 'total')
    b.parts.append('These are different definitions. The denominator is all reported collisions; '
                   'missing category values are not evidence that no parked vehicle was involved.')
    return b.finish(interpretation='Report primary-involvement, party-type, and pre-crash-movement definitions separately.')


def drinking(b):
    q = b.query("""SELECT COUNT(*) AS n FROM collisions c WHERE EXISTS (
      SELECT 1 FROM parties p WHERE p.case_id=c.case_id AND p.party_type='driver'
      AND p.party_sobriety LIKE 'had been drinking%')""", 'collisions_with_drinking_driver')
    b.fact('There were {value} reported collisions with at least one driver recorded as having been drinking.',
           'collisions_with_drinking_driver', q)
    return b.finish(interpretation='Count each collision once using recorded driver sobriety, not officer-assigned DUI cause.')


def fatal_hour(b):
    if blocked := fatal_classification(b):
        return blocked
    # DuckDB accepts TIME '24:00:00'; it is not an hour in a 0..23 ranking.
    invalid = b.query("SELECT COUNT(*) AS n FROM collisions WHERE collision_severity='fatal' "
                      "AND HOUR(collision_time) NOT BETWEEN 0 AND 23", 'invalid_collision_hours')
    if b.count(invalid, 'n'):
        from .certification import CertificationError
        raise CertificationError('Invalid recorded collision hours prevent a certified hourly ranking.')
    q = b.query("SELECT HOUR(collision_time) AS hour, COUNT(*) AS n FROM collisions "
                "WHERE collision_severity='fatal' AND collision_time IS NOT NULL GROUP BY 1 ORDER BY n DESC, hour",
                'fatal_collision_hour')
    if not q['rows']:
        b.parts.append('No fatal collisions with recorded times matched; an hourly ranking is unavailable.')
        return b.finish('no_matching_records')
    maximum = q['rows'][0][1]
    for i, (_, n) in enumerate(q['rows']):
        if n != maximum:
            break
        b.fact('A leading hour was {value} on the local-time 24-hour clock.', 'hour_of_day', q, 'hour', row=i)
        b.fact('That hour had {value} reported collisions coded fatal.', 'fatal_collision_count', q, row=i)
    b.parts.append('Fatal collisions here means records coded fatal. All tied leading hours are shown. '
                   'Records with missing collision times are excluded.')
    return b.finish(interpretation='Rank records coded fatal by recorded local hour, excluding unknown severity and missing times.')


def cellphone(b):
    q = b.query("""SELECT COUNT(*) FILTER (WHERE cellphone_in_use=1) AS used,
      COUNT(*) FILTER (WHERE cellphone_in_use IN (0,1)) AS known,
      COUNT(*) FILTER (WHERE cellphone_in_use IS NULL OR cellphone_in_use NOT IN (0,1)) AS unknown
      FROM parties WHERE party_type='driver' AND at_fault=1""", 'cellphone_share')
    share(b, '{value} of at-fault drivers with known cellphone status were recorded as using a cellphone.',
          'cellphone', q, 'used', 'known')
    b.fact('{value} at-fault drivers had missing or unknown cellphone status and were excluded from that denominator.',
           'unknown_cellphone_status', q, 'unknown')
    return b.finish(interpretation='At-fault driver parties; denominator includes only recorded binary cellphone values.')


def belts(b):
    for status, opposite in (('used', 'not used'), ('not used', 'used')):
        q = b.query(f"""SELECT COUNT(*) FILTER (WHERE v.victim_degree_of_injury='killed') AS killed,
          COUNT(*) AS total FROM victims v JOIN parties p
          ON p.case_id=v.case_id AND p.party_number=v.party_number
          WHERE p.statewide_vehicle_type='passenger car' AND v.victim_role IN ('driver','passenger')
          AND 'lap/shoulder harness {status}' IN (v.victim_safety_equipment_1,v.victim_safety_equipment_2)
          AND 'lap/shoulder harness {opposite}' NOT IN
            (COALESCE(v.victim_safety_equipment_1,''),COALESCE(v.victim_safety_equipment_2,''))""",
                    'recorded_belt_death_share')
        share(b, '{value} of recorded passenger-car occupant victims were recorded as killed with a lap/shoulder belt recorded as '
              + status + '.', 'belt_' + status.replace(' ', '_'), q, 'killed', 'total', decimals=3)
    b.parts.append('Both equipment fields are used; conflicting or unknown belt entries are excluded. '
                   'The denominator includes recorded driver and passenger victims, including recorded no-injury victims. '
                   'Missing or unknown injury outcomes remain in this recorded-victim denominator; '
                   'not being recorded as killed is not proof of survival. '
                   'These observational associations do not prove causal effects or measure risk for all vehicle occupants.')
    return b.finish(interpretation='Recorded passenger-car driver/passenger victims; mutually exclusive belt status from either equipment field.')


def covid(b):
    if blocked := fatal_classification(b, " WHERE collision_date BETWEEN DATE '2019-01-01' AND DATE '2020-12-31'"):
        return blocked
    q = b.query("""SELECT
      COUNT(*) FILTER (WHERE YEAR(collision_date)=2019) AS n2019,
      COUNT(*) FILTER (WHERE YEAR(collision_date)=2020) AS n2020,
      COUNT(*) FILTER (WHERE collision_date BETWEEN DATE '2019-04-01' AND DATE '2019-04-30') AS april2019,
      COUNT(*) FILTER (WHERE collision_date BETWEEN DATE '2020-04-01' AND DATE '2020-04-30') AS april2020,
      COUNT(*) FILTER (WHERE YEAR(collision_date)=2019 AND collision_severity='fatal') AS fatal2019,
      COUNT(*) FILTER (WHERE YEAR(collision_date)=2020 AND collision_severity='fatal') AS fatal2020
      FROM collisions""", 'covid_period_comparison')
    b.fact('In 2019 there were {value} reported collisions.', 'collision_count_2019', q, 'n2019')
    b.fact('In 2020 there were {value} reported collisions.', 'collision_count_2020', q, 'n2020')
    change(b, q, 'n2020', 'n2019', 'Calendar-year 2020 versus 2019')
    change(b, q, 'april2020', 'april2019', 'April 2020 versus April 2019')
    share(b, 'In 2019 records coded fatal were {value} of reported collisions.', 'fatal_collision_2019', q, 'fatal2019', 'n2019')
    share(b, 'In 2020 records coded fatal were {value} of reported collisions.', 'fatal_collision_2020', q, 'fatal2020', 'n2020')
    n19, n20, _, _, f19, f20 = q['rows'][0]
    if n19 and n20:
        comparison = f20 * n19 - f19 * n20
        direction = 'increased' if comparison > 0 else 'decreased' if comparison < 0 else 'was unchanged'
        b.parts.append(f'The fatal share {direction} between those calendar years.')
    b.parts.append('This is a descriptive period comparison. It cannot establish how much COVID-19 caused the changes; '
                   'the records do not provide traffic-exposure denominators. Fatal collisions here means records coded fatal.')
    return b.finish(interpretation='Compare calendar 2019 and 2020 and their April counts; report each calendar year\'s fatal-collision share.')


RECIPES = {name: globals()[name] for name in set(QUESTIONS.values())}


def fatal_classification(b, where=''):
    """Unknown classifications cannot become zero; observed fatal codes can be reported with exclusions.

    A coded-fatal count is still reproducible when a known code disagrees with a
    death count, but must not be presented as a resolved real-world fatal count.
    """
    q = b.query("""SELECT COUNT(*) FILTER (WHERE collision_severity IS NULL OR
      collision_severity NOT IN ('fatal','severe injury','other injury','pain','property damage only')) AS unknown,
      COUNT(*) FILTER (WHERE collision_severity IN
        ('fatal','severe injury','other injury','pain','property damage only') AND killed_victims IS NOT NULL AND
        ((collision_severity='fatal') <> (killed_victims > 0))) AS conflicts
      , COUNT(*) FILTER (WHERE collision_severity='fatal') AS coded_fatal
      FROM collisions""" + where, 'fatal_classification_quality')
    if b.count(q, 'unknown'):
        b.fact('Severity is missing or unrecognized for {value} records in the requested population.',
               'unknown_fatal_classification', q, 'unknown')
        if not b.count(q, 'coded_fatal'):
            b.parts.append('The requested fatal-collision measure is unavailable. This is not evidence of zero fatal collisions.')
            return b.finish('data_quality_blocked')
        b.parts.append('The fatal-collision results below cover only records coded fatal. Unknown severity is excluded '
                       'from that classification; this is not a complete count of fatal crashes.')
    if b.count(q, 'conflicts'):
        b.fact('{value} records have conflicting severity and fatality counts.',
               'conflicting_fatal_classification', q, 'conflicts')
        b.parts.append('The fatal-collision results below count records coded fatal; they do not resolve these conflicts.')
    return None
