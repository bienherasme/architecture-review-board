"""Pure comparison of two evaluation reports run against a compatible dataset.

Reports deltas only. The human interprets tradeoffs: this module never
declares a winner, computes a percent improvement, or produces a
composite gain figure.
"""

from architecture_review_board.evaluation.models import EvaluationComparison, EvaluationReport


class IncompatibleEvaluationReportsError(Exception):
    """Two EvaluationReports do not share dataset identity/fingerprint or repetitions.

    Comparing reports produced from different benchmark content, or from
    a different number of repetitions, would not isolate what actually
    changed between the two runs.
    """


def compare_evaluation_reports(
    baseline: EvaluationReport, candidate: EvaluationReport
) -> EvaluationComparison:
    if (
        baseline.dataset_id != candidate.dataset_id
        or baseline.dataset_version != candidate.dataset_version
        or baseline.dataset_fingerprint != candidate.dataset_fingerprint
        or baseline.repetitions != candidate.repetitions
    ):
        raise IncompatibleEvaluationReportsError(
            "reports must share dataset_id, dataset_version, dataset_fingerprint, "
            "and repetitions"
        )

    return EvaluationComparison(
        dataset_id=baseline.dataset_id,
        dataset_version=baseline.dataset_version,
        dataset_fingerprint=baseline.dataset_fingerprint,
        repetitions=baseline.repetitions,
        baseline_run_metadata=baseline.run_metadata,
        candidate_run_metadata=candidate.run_metadata,
        completion_rate_delta=(
            candidate.summary.completion_rate - baseline.summary.completion_rate
        ),
        full_board_run_rate_delta=(
            candidate.summary.full_board_run_rate - baseline.summary.full_board_run_rate
        ),
        acceptable_decision_rate_delta=_delta(
            baseline.summary.acceptable_decision_rate, candidate.summary.acceptable_decision_rate
        ),
        expected_risk_recall_delta=_delta(
            baseline.summary.expected_risk_recall, candidate.summary.expected_risk_recall
        ),
        expected_disagreement_recall_delta=_delta(
            baseline.summary.expected_disagreement_recall,
            candidate.summary.expected_disagreement_recall,
        ),
        expected_evidence_citation_rate_delta=_delta(
            baseline.summary.expected_evidence_citation_rate,
            candidate.summary.expected_evidence_citation_rate,
        ),
    )


def _delta(baseline_value: float | None, candidate_value: float | None) -> float | None:
    if baseline_value is None or candidate_value is None:
        return None
    return candidate_value - baseline_value
