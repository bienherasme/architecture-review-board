"""Human-readable rendering of ArchitectureReviewResult and EvaluationReport.

Both formatters walk REVIEW_DIMENSION_ORDER / dataset order directly
rather than sorting by severity, rate, or any other computed value: the
domain's own canonical order is the one this prints. No prompts, raw
provider payload, or token internals appear here; evidence is shown only
as its id and reference, never its full excerpt.
"""

from architecture_review_board.domain.enums import REVIEW_DIMENSION_ORDER
from architecture_review_board.domain.models import (
    ArchitectureReviewResult,
    ReviewEvidenceSearchResult,
)
from architecture_review_board.evaluation.models import EvaluationReport, EvaluationRunStatus


def format_review_result(result: ArchitectureReviewResult) -> str:
    reviews_by_dimension = {review.reviewer: review for review in result.specialist_reviews}
    failures_by_dimension = {failure.reviewer: failure for failure in result.specialist_failures}

    lines = [
        f"review_id: {result.review_id}",
        f"decision: {result.decision.value}",
        f"summary: {result.summary}",
        f"evidence_status: {_evidence_status_label(result.evidence_context)}",
        "specialists:",
    ]

    for dimension in REVIEW_DIMENSION_ORDER:
        review = reviews_by_dimension.get(dimension)
        if review is not None:
            lines.append(f"  {dimension.value}: {len(review.findings)} finding(s)")
            for finding in review.findings:
                evidence_note = ""
                if finding.evidence:
                    refs = ", ".join(
                        f"{item.evidence_id} ({item.source_reference})" for item in finding.evidence
                    )
                    evidence_note = f" [evidence: {refs}]"
                lines.append(
                    f"    - {finding.finding_id} [{finding.severity.value}] "
                    f"{finding.title}{evidence_note}"
                )
        else:
            failure = failures_by_dimension[dimension]
            lines.append(f"  {dimension.value}: FAILED ({failure.detail})")

    if result.conditions:
        lines.append("conditions:")
        for condition in result.conditions:
            lines.append(f"  - {condition.condition_id}: {condition.description}")

    if result.disagreements:
        lines.append("disagreements:")
        for disagreement in result.disagreements:
            lines.append(f"  - {disagreement.disagreement_id}: {disagreement.topic}")
            for position in disagreement.positions:
                lines.append(f"      {position.reviewer.value}: {position.position}")
            if disagreement.resolution:
                lines.append(f"      resolution: {disagreement.resolution}")

    if result.blocking_finding_ids:
        lines.append(f"blocking_finding_ids: {', '.join(result.blocking_finding_ids)}")

    return "\n".join(lines)


def _evidence_status_label(evidence_context: ReviewEvidenceSearchResult | None) -> str:
    if evidence_context is None:
        return "disabled"
    return evidence_context.status.value


def format_evaluation_report(report: EvaluationReport) -> str:
    lines = [
        f"dataset: {report.dataset_id} v{report.dataset_version}",
        f"dataset_fingerprint: {report.dataset_fingerprint}",
        f"provider: {report.run_metadata.provider}",
        f"model: {report.run_metadata.model}",
        f"evidence_mode: {report.run_metadata.evidence_mode}",
    ]
    if report.run_metadata.provider_sdk_version:
        lines.append(f"provider_sdk_version: {report.run_metadata.provider_sdk_version}")
    lines.append(f"repetitions: {report.repetitions}")
    lines.append("")
    lines.append(f"total_runs: {report.summary.total_runs}")
    lines.append(f"completed_runs: {report.summary.completed_runs}")
    lines.append(f"completion_rate: {_fmt_rate(report.summary.completion_rate)}")
    lines.append(f"full_board_run_rate: {_fmt_rate(report.summary.full_board_run_rate)}")
    lines.append(f"acceptable_decision_rate: {_fmt_rate(report.summary.acceptable_decision_rate)}")
    lines.append(f"expected_risk_recall: {_fmt_rate(report.summary.expected_risk_recall)}")
    lines.append(
        f"expected_disagreement_recall: {_fmt_rate(report.summary.expected_disagreement_recall)}"
    )
    if report.summary.expected_evidence_citation_rate is not None:
        lines.append(
            f"expected_evidence_citation_rate: "
            f"{_fmt_rate(report.summary.expected_evidence_citation_rate)}"
        )

    lines.append("")
    lines.append("decision_stability:")
    for entry in report.decision_stability:
        decision = entry.modal_decision.value if entry.modal_decision else "none"
        lines.append(
            f"  {entry.case_id}: {decision} "
            f"(agreement={_fmt_rate(entry.modal_agreement_rate)}, "
            f"completed_runs={entry.completed_runs})"
        )

    failed_runs = [run for run in report.case_runs if run.status == EvaluationRunStatus.FAILED]
    if failed_runs:
        lines.append("")
        lines.append("failed_runs:")
        for run in failed_runs:
            lines.append(f"  {run.case_id} (repetition {run.repetition}): {run.error_detail}")

    return "\n".join(lines)


def _fmt_rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"
