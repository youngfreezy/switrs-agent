"""Run the evaluator as a real CLI against an isolated tiny golden dataset."""

import json
import os
import shutil
import subprocess
import sys

import duckdb
import pytest
import yaml

from evals import run_evals


@pytest.fixture
def evaluator(tmp_path):
    (tmp_path / "evals").mkdir()
    (tmp_path / "data").mkdir()
    shutil.copy(run_evals.__file__, tmp_path / "evals" / "run_evals.py")
    (tmp_path / "switrs_agent").symlink_to(run_evals.ROOT / "switrs_agent", target_is_directory=True)
    db = tmp_path / "data" / "switrs.duckdb"
    with duckdb.connect(str(db)) as con:
        con.execute("CREATE TABLE collisions AS SELECT 1 AS case_id")
    (tmp_path / "data" / "dataset_meta.json").write_text(json.dumps({"dataset_version": "fixture"}))
    (tmp_path / "evals" / "golden.yaml").write_text(yaml.safe_dump({"questions": [{
        "id": "one", "question": "How many collisions?", "checks": [{
            "type": "number", "name": "count", "sql": "SELECT COUNT(*) FROM collisions"
        }]
    }]}))
    (tmp_path / "evals" / "expected_v1.json").write_text(json.dumps({
        "dataset_version": "fixture", "values": {"one": {"count": [1]}}
    }))
    return tmp_path


@pytest.mark.parametrize("answer,exit_code", [("There were 2 collisions.", 1), (None, 1), ("There was 1 collision.", 0)])
def test_evaluator_process_status_matches_report(evaluator, answer, exit_code):
    answers = evaluator / "answers.jsonl"
    answers.write_text(json.dumps({"id": "one", "answer": answer}) + "\n" if answer is not None else "")
    env = dict(os.environ, SWITRS_DB=str(evaluator / "data" / "switrs.duckdb"))
    result = subprocess.run([sys.executable, str(evaluator / "evals" / "run_evals.py"),
                             "--answers", str(answers)], capture_output=True, text=True, env=env)
    assert result.returncode == exit_code, result.stdout + result.stderr
    report = json.loads(next((evaluator / "outputs" / "evals").glob("*/report.json")).read_text())
    assert report[0]["pass"] is (exit_code == 0)
    assert ("FAIL one" if exit_code else "PASS one") in result.stdout


def test_old_numeric_ok_only_trace_is_not_certified(tmp_path, monkeypatch):
    monkeypatch.setattr(run_evals, "ROOT", tmp_path)
    traces = tmp_path / "outputs" / "traces"
    traces.mkdir(parents=True)
    (traces / "legacy.jsonl").write_text(json.dumps({
        "tool": "submit_answer", "args": {"answer": "Unsupported conclusion"},
        "result": {"ok": True, "final_answer": "Unsupported conclusion"}
    }) + "\n")
    assert run_evals.certified_answer("legacy") is None
    assert run_evals.trace_stats("legacy")["certified"] is False


def test_unknown_question_selector_cannot_report_empty_success(evaluator):
    answers = evaluator / "answers.jsonl"
    answers.write_text("")
    env = dict(os.environ, SWITRS_DB=str(evaluator / "data" / "switrs.duckdb"))
    result = subprocess.run([sys.executable, str(evaluator / "evals" / "run_evals.py"),
                             "--answers", str(answers), "--only", "misspelled"],
                            capture_output=True, text=True, env=env)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "Unknown question" in result.stderr
