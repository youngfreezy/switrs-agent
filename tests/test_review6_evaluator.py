"""Regression checks for evaluator verdicts, independent of the full dataset."""

import json

import pytest
import yaml

from evals import run_evals


GOLDEN = {q["id"]: q for q in yaml.safe_load(run_evals.GOLDEN.read_text())["questions"]}
APPENDIX = (
    "\n\n**Not in the data**\n"
    "- No vehicle color; no traffic stops data. The data cannot test police bias.\n\n"
    "**Caveats**\n- Observational belt outcomes cannot prove cause. Only California.\n\n"
    "**Evidence**\n| Claim | Value |\n| Collisions | 7 |"
)


def grade(qid, answer):
    return run_evals.grade(GOLDEN[qid], answer, None)


@pytest.mark.parametrize("qid,answer", [
    ("q03_safety_equipment", "The data proves safety equipment works."),
    ("q05_red_sports_car_bias", "Police definitely discriminate against red sports cars."),
    ("q10_las_vegas", "I do not know."),
])
def test_server_appendix_cannot_supply_required_answer_content(qid, answer):
    assert not grade(qid, answer + APPENDIX)["pass"]


@pytest.mark.parametrize("verdict", [
    "This proves seat belts work.",
    "The data establishes that harnesses prevent deaths.",
])
def test_causal_verdict_fails_even_with_observational_disclaimer(verdict):
    answer = "The data is observational and cannot prove cause. " + verdict
    assert not grade("q03_safety_equipment", answer)["pass"]


@pytest.mark.parametrize("verdict", [
    "Police definitely discriminate against red sports cars.",
    "Police are not biased against sports cars.",
    "The data proves police bias.",
])
def test_bias_verdict_fails_even_with_missing_data_disclaimer(verdict):
    answer = "No vehicle color or traffic stops data; we cannot test police bias. " + verdict
    assert not grade("q05_red_sports_car_bias", answer)["pass"]


@pytest.mark.parametrize("count", ["seven", "one million", "twenty-three", "zero", "7"])
def test_unavailable_geography_does_not_allow_invented_counts(count):
    answer = f"This data covers only California. Las Vegas had {count} crashes."
    result = grade("q10_las_vegas", answer)
    assert not result["pass"]
    assert next(c for c in result["checks"] if c["check"] == "no invented counts")["found"]


@pytest.mark.parametrize("qid,answer", [
    ("q03_safety_equipment", "Belt outcomes are observational and do not prove cause."),
    ("q05_red_sports_car_bias", "No vehicle color or traffic stops data. We cannot test police bias."),
    ("q05_red_sports_car_bias", "No vehicle color or traffic stops data. This does not prove police are biased."),
    ("q10_las_vegas", "This data covers only California, so it has no Las Vegas crashes. The drive uses I-15."),
])
def test_valid_refusals_still_pass(qid, answer):
    assert grade(qid, answer)["pass"]
    assert grade(qid, answer + APPENDIX)["pass"]


def test_submitted_heading_cannot_hide_invented_count():
    answer = "Only California.\n\n**Caveats**\nLas Vegas had seven crashes."
    assert not run_evals.grade(GOLDEN["q10_las_vegas"], answer, None, rendered=False)["pass"]


def test_trace_uses_submitted_body_and_requires_latest_submission_to_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(run_evals, "ROOT", tmp_path)
    traces = tmp_path / "outputs" / "traces"
    traces.mkdir(parents=True)
    trace = traces / "test.jsonl"
    success = {"tool": "submit_answer", "args": {"answer": "Original body"},
               "result": {"ok": True, "certified": True, "final_answer": "Original body" + APPENDIX}}
    trace.write_text(json.dumps(success) + "\n")
    assert run_evals.certified_answer("test") == "Original body"
    failure = {"tool": "submit_answer", "args": {"answer": "Later unsupported answer"}, "result": {"ok": False}}
    trace.write_text(json.dumps(success) + "\n" + json.dumps(failure) + "\n")
    assert run_evals.certified_answer("test") is None


def test_eval_configuration_supplies_host_request(tmp_path):
    from evals.run_evals import write_mcp_config
    from switrs_agent.request_binding import read_request
    from pathlib import Path
    path = write_mcp_config(tmp_path, 'fixture', 'How many collisions?')
    config = json.loads(path.read_text())
    request = read_request(Path(config['mcpServers']['switrs']['env']['SWITRS_REQUEST_FILE']))
    assert request['question'] == 'How many collisions?'
