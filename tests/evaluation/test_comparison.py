import pytest

from architecture_review_board.evaluation.comparison import (
    IncompatibleEvaluationReportsError,
    compare_evaluation_reports,
)
from architecture_review_board.evaluation.models import (
    EvaluationCaseRun,
    EvaluationReport,
    EvaluationRunMetadata,
    EvaluationRunStatus,
    EvaluationSummary,
)


def make_report(
    dataset_fingerprint: str,
    provider: str,
    completion_rate: float,
    expected_risk_recall: float | None,
) -> EvaluationReport:
    run = EvaluationCaseRun(case_id="case-a", repetition=1, status=EvaluationRunStatus.COMPLETED)
    return EvaluationReport(
        dataset_id="test-dataset",
        dataset_version="0.1",
        dataset_fingerprint=dataset_fingerprint,
        run_metadata=EvaluationRunMetadata(
            provider=provider, model="model-x", evidence_mode="none"
        ),
        repetitions=1,
        case_runs=(run,),
        summary=EvaluationSummary(
            total_runs=1,
            completed_runs=1,
            completion_rate=completion_rate,
            full_board_run_rate=1.0,
            acceptable_decision_rate=1.0,
            expected_risk_recall=expected_risk_recall,
            expected_disagreement_recall=None,
            expected_evidence_citation_rate=None,
        ),
        decision_stability=(),
    )


def test_incompatible_reports_are_rejected() -> None:
    baseline = make_report("fingerprint-a", "openai", completion_rate=1.0, expected_risk_recall=0.5)
    different_fingerprint = make_report(
        "fingerprint-b", "openai", completion_rate=1.0, expected_risk_recall=0.5
    )

    with pytest.raises(IncompatibleEvaluationReportsError):
        compare_evaluation_reports(baseline, different_fingerprint)


def test_compatible_reports_compute_deltas_and_preserve_none() -> None:
    baseline = make_report(
        "fingerprint-a", "no-evidence", completion_rate=0.5, expected_risk_recall=None
    )
    candidate = make_report(
        "fingerprint-a", "engineering-knowledge", completion_rate=1.0, expected_risk_recall=0.75
    )

    comparison = compare_evaluation_reports(baseline, candidate)

    assert comparison.completion_rate_delta == pytest.approx(0.5)
    assert comparison.baseline_run_metadata.provider == "no-evidence"
    assert comparison.candidate_run_metadata.provider == "engineering-knowledge"
    # baseline's expected_risk_recall is unmeasured (None), so the delta stays None
    # even though the candidate does have a value.
    assert comparison.expected_risk_recall_delta is None
