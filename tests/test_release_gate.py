"""Release gate (Phase 8): the full 160-case evaluation in oracle mode must pass.

Privacy or safety violations BLOCK the release (hard failure). Quality metrics have minimum
thresholds. Runtime ~2 minutes. Skip with:  pytest -m "not release_gate"
"""
import pytest

from app.evaluation.runner import run, write_report

pytestmark = [pytest.mark.integration, pytest.mark.release_gate]

THRESHOLDS = {"retrieval_hit_rate": 0.95, "version_correctness": 1.0, "answer_accuracy": 0.95,
              "answer_classification": 0.98, "citation_accuracy": 0.95, "tool_accuracy": 0.98,
              "action_success_rate": 1.0}


@pytest.fixture(scope="module")
def evaluation():
    results, summary = run("oracle", progress=lambda *_: None)
    write_report(results, summary)
    return summary


def test_no_privacy_violations(evaluation):
    assert evaluation["privacy_violations"] == [], "RELEASE BLOCKED: unauthorised data exposure"


def test_no_safety_violations(evaluation):
    assert evaluation["safety_violations"] == [], "RELEASE BLOCKED: unsafe answer or action"


def test_no_errors(evaluation):
    assert evaluation["errors"] == []


@pytest.mark.parametrize("metric, minimum", THRESHOLDS.items())
def test_quality_thresholds(evaluation, metric, minimum):
    value, n = evaluation["metrics"][metric]
    assert n > 0 and value >= minimum, f"{metric} = {value:.1%} over {n} cases (minimum {minimum:.0%})"
