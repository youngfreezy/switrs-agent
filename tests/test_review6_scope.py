"""Regression coverage for unavailable dates, geography, and vehicle attributes."""

import pytest

from switrs_agent.answerability import check_status


@pytest.mark.parametrize("question", [
    "How many collisions happened in December 2021?",
    "How many collisions happened on June 4, 2021?",
    "How many collisions happened on 4 June 2021?",
    "How many collisions happened on 2021-06-04?",
    "How many collisions happened on 06/04/2021?",
    "How many collisions happened in June 2021?",
    "How many collisions happened in 2000?",
    "How many collisions happened in 1999–2002?",
    "How many collisions happened in 2040?",
    "How many collisions happened in Florida?",
    "How many collisions happened in Wisconsin?",
    "How many collisions happened in Washington?",
    "How many collisions involved white cars?",
    "How many collisions involved brown trucks?",
])
def test_unavailable_scope_cannot_be_declared_answerable(question):
    result = check_status(question, "answerable")
    assert result["error"], result
    assert result["gaps"], result


@pytest.mark.parametrize("question", [
    "How many collisions happened in California in 2020?",
    "How many collisions happened in Orange County involving cars?",
    "How many collisions happened on June 3, 2021?",
    "How many collisions happened on 3 June 2021?",
    "How many collisions happened on 2021-06-03?",
    "How many collisions happened on 06/03/2021?",
    "How many collisions happened between 2001-01-01 and 2021-06-03?",
    "How many collisions involved vehicles with model year 2000?",
    "How many collisions involved 2000 model year vehicles?",
    "Which counties recorded more than 5000 collisions?",
    "Which counties recorded at least 2000 deaths?",
])
def test_supported_scope_remains_answerable(question):
    result = check_status(question, "answerable")
    assert result["error"] is None, result


def test_partial_period_requires_coverage_caveat():
    result = check_status("How many collisions happened in December 2021?", "partially_available")
    assert result["error"] is None
    assert "coverage_window" in result["required_caveats"]
