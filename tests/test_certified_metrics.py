"""Known-answer positive controls for the documented descriptive analyses."""
import pytest

from test_review_regressions import kit
from test_certification import publish


@pytest.fixture
def rich_kit(kit):
    kit.dataset['coverage'] = {'min_collision_date': '2001-01-01', 'max_collision_date': '2021-06-03'}
    kit.db.connection.execute("""
      ALTER TABLE collisions ADD COLUMN collision_date DATE;
      ALTER TABLE collisions ADD COLUMN collision_time TIME;
      ALTER TABLE collisions ADD COLUMN collision_severity VARCHAR;
      ALTER TABLE collisions ADD COLUMN motorcycle_collision BIGINT;
      ALTER TABLE collisions ADD COLUMN motor_vehicle_involved_with VARCHAR;
      UPDATE collisions SET collision_date=CASE WHEN case_id='A' THEN DATE '2019-04-01' ELSE DATE '2020-04-01' END,
        collision_time=TIME '18:00:00', collision_severity=CASE WHEN case_id='C' THEN 'property damage only' ELSE 'fatal' END,
        motorcycle_collision=CASE WHEN case_id='A' THEN 1 ELSE 0 END,
        motor_vehicle_involved_with=CASE WHEN case_id='C' THEN 'parked motor vehicle' ELSE 'other motor vehicle' END;
      ALTER TABLE parties ADD COLUMN party_type VARCHAR;
      ALTER TABLE parties ADD COLUMN movement_preceding_collision VARCHAR;
      ALTER TABLE parties ADD COLUMN party_sobriety VARCHAR;
      ALTER TABLE parties ADD COLUMN statewide_vehicle_type VARCHAR;
      ALTER TABLE parties ADD COLUMN at_fault BIGINT;
      ALTER TABLE parties ADD COLUMN cellphone_in_use BIGINT;
      UPDATE parties SET party_type=CASE WHEN party_number=2 THEN 'parked vehicle' ELSE 'driver' END,
        movement_preceding_collision=CASE WHEN party_number=2 THEN 'parked' ELSE 'proceeding straight' END,
        party_sobriety=CASE WHEN case_id='A' THEN 'had been drinking, under influence' ELSE 'had not been drinking' END,
        statewide_vehicle_type='passenger car', at_fault=1,
        cellphone_in_use=CASE WHEN case_id='A' THEN 1 WHEN case_id='B' THEN 0 ELSE NULL END;
      DELETE FROM victims;
      INSERT INTO victims VALUES ('A',1,'killed'), ('A',1,'killed'), ('A',1,'severe injury'), ('B',1,'killed'), ('C',1,'pain');
      ALTER TABLE victims ADD COLUMN victim_role VARCHAR;
      ALTER TABLE victims ADD COLUMN victim_safety_equipment_1 VARCHAR;
      ALTER TABLE victims ADD COLUMN victim_safety_equipment_2 VARCHAR;
      UPDATE victims SET victim_role='passenger',
        victim_safety_equipment_1=CASE WHEN case_id='A' THEN 'lap/shoulder harness used' ELSE 'lap/shoulder harness not used' END;
    """)
    kit.schema_cols = kit.db.table_columns()
    return kit


@pytest.mark.parametrize('question,values', [
    ('How many collisions happened in 2019?', [1]),
    ('What percentage of collisions involved a parked vehicle?', [100/3, 100/3, 100/3]),
    ('How many collisions involved at least one driver who had been drinking?', [1]),
    ('How many fatal collisions involved a motorcycle in 2019?', [1]),
    ('At what hour of the day do the most fatal crashes happen?', [18, 2]),
    ('What share of at-fault drivers were using a cellphone?', [50, 1]),
    ('What are recorded victim death shares by lap/shoulder belt use?', [200/3, 50]),
])
def test_documented_metrics_have_computed_positive_answers(rich_kit, question, values):
    _, p, r = publish(rich_kit, question)
    assert p['status'] == 'answered'
    assert [c['computed'] for c in p['claims']] == pytest.approx(values)
    assert r['certified']


def test_covid_direction_is_derived_not_assumed(rich_kit):
    _, p, _ = publish(rich_kit, 'How has COVID-19 changed collisions, if at all?')
    assert p['status'] == 'answered'
    assert [c['computed'] for c in p['claims']] == pytest.approx([1,2,100,100,100,50])
    assert 'increased' in p['answer']
    assert 'fatal share decreased' in p['answer'].lower()
    assert 'cannot establish' in p['answer']


def test_no_denominator_is_not_zero_percent(rich_kit):
    rich_kit.db.connection.execute('UPDATE parties SET cellphone_in_use=NULL')
    _, p, _ = publish(rich_kit, 'What share of at-fault drivers were using a cellphone?')
    assert '0.00%' not in p['answer']
    assert 'unavailable' in p['answer'].lower()


def test_parked_membership_counts_collisions_once_and_ignores_orphans(rich_kit):
    rich_kit.db.connection.execute("""
      INSERT INTO parties (case_id, party_number, party_type, movement_preceding_collision)
      VALUES ('A', 9, 'parked vehicle', NULL), ('A', 10, 'parked vehicle', 'parked'),
             ('B', 9, NULL, 'parked'), ('orphan', 1, 'parked vehicle', 'parked'),
             (NULL, 1, 'parked vehicle', 'parked');
    """)
    _, p, result = publish(rich_kit, 'What percentage of collisions involved a parked vehicle?')
    assert [c['computed'] for c in p['claims']] == pytest.approx([100/3, 100/3, 200/3])
    assert result['certified']


def test_equipment_conflicts_are_excluded_and_second_field_is_used(rich_kit):
    rich_kit.db.connection.execute("""
      UPDATE victims SET victim_safety_equipment_2=victim_safety_equipment_1,
        victim_safety_equipment_1=NULL;
      UPDATE victims SET victim_safety_equipment_1='lap/shoulder harness not used'
        WHERE case_id='A' AND victim_degree_of_injury='severe injury';
    """)
    _, p, _ = publish(rich_kit, 'What are recorded victim death shares by lap/shoulder belt use?')
    assert [c['computed'] for c in p['claims']] == [100,50]
    assert 'conflicting' in p['answer']


def test_extra_predicate_on_known_example_requires_clarification(rich_kit):
    _, p, _ = publish(rich_kit, 'What share of at-fault drivers were using a cellphone in Orange County?')
    assert p['status'] == 'needs_clarification'


def test_invalid_zero_year_cannot_disappear_from_scope(rich_kit):
    _, p, _ = publish(rich_kit, 'How many collisions happened in 0000?', status='partially_available')
    assert p['status'] == 'unsupported'
    assert p['claims'] == []
    assert not p['claims']


def test_coverage_footer_uses_this_snapshot(rich_kit):
    rich_kit.dataset['coverage'] = {'min_collision_date': '2019-04-01', 'max_collision_date': '2020-04-01'}
    _, _, result = publish(rich_kit)
    assert '2019-04-01' in result['final_answer']
    assert '2001-01-01' not in result['final_answer']


def test_zero_fatalities_with_existing_records_is_an_answer(rich_kit):
    rich_kit.db.connection.execute('UPDATE collisions SET killed_victims=0')
    _, p, _ = publish(rich_kit, 'How many people were killed?')
    assert p['status'] == 'answered'
    assert p['claims'][0]['computed'] == 0
