"""Runs the golden dataset against the real ArchitectureReviewService and scores behavior.

The evaluator never reimplements review orchestration: every case is
produced by calling ArchitectureReviewService.review exactly as any other
caller would, so the benchmark measures the real application workflow,
not a parallel evaluation-only code path. Cases run sequentially and in
dataset order, repeated by repetition; within one case, ReviewCoordinator
still runs the five specialists concurrently as normal.
"""

from collections import Counter

from architecture_review_board.domain.enums import ArchitectureDecision
from architecture_review_board.domain.models import ArchitectureReviewResult
from architecture_review_board.evaluation.dataset import compute_dataset_fingerprint
from architecture_review_board.evaluation.matching import (
    match_expected_disagreement,
    match_expected_risk,
)
from architecture_review_board.evaluation.models import (
    CaseDecisionStability,
    EvaluationCase,
    EvaluationCaseRun,
    EvaluationDataset,
    EvaluationReport,
    EvaluationRunMetadata,
    EvaluationRunStatus,
    EvaluationSummary,
)
from architecture_review_board.model.supervisor import StructuredSupervisorModelError
from architecture_review_board.reviewers.service import ArchitectureReviewService
from architecture_review_board.reviewers.supervisor import ReviewSupervisorError

MAX_EVALUATION_REPETITIONS = 10

_SUPERVISOR_MODEL_UNAVAILABLE_DETAIL = "supervisor model unavailable"
_SUPERVISOR_PROPOSAL_INVALID_DETAIL = "supervisor proposal invalid"


class ArchitectureReviewEvaluator:
    """Runs every case in a golden EvaluationDataset and scores the resulting behavior.

    Only StructuredSupervisorModelError and ReviewSupervisorError, the two
    expected paths that prevent ArchitectureReviewResult production, are
    captured into a FAILED EvaluationCaseRun. Specialist failures are not
    caught here at all: a coordinator that isolates a specialist already
    represents that as ArchitectureReviewResult.specialist_failures, so
    the run still COMPLETED. Any other exception is a programming defect
    and is left to propagate, failing the benchmark loudly.
    """

    def __init__(
        self,
        service: ArchitectureReviewService,
        dataset: EvaluationDataset,
        *,
        run_metadata: EvaluationRunMetadata,
    ) -> None:
        self._service = service
        self._dataset = dataset
        self._run_metadata = run_metadata

    async def evaluate(self, *, repetitions: int = 1) -> EvaluationReport:
        if not 1 <= repetitions <= MAX_EVALUATION_REPETITIONS:
            raise ValueError(f"repetitions must be between 1 and {MAX_EVALUATION_REPETITIONS}")

        case_runs: list[EvaluationCaseRun] = []
        for repetition in range(1, repetitions + 1):
            for case in self._dataset.cases:
                case_runs.append(await self._run_case(case, repetition))

        return EvaluationReport(
            dataset_id=self._dataset.dataset_id,
            dataset_version=self._dataset.version,
            dataset_fingerprint=compute_dataset_fingerprint(self._dataset),
            run_metadata=self._run_metadata,
            repetitions=repetitions,
            case_runs=tuple(case_runs),
            summary=_summarize(self._dataset, case_runs, repetitions),
            decision_stability=_decision_stability(self._dataset, case_runs),
        )

    async def _run_case(self, case: EvaluationCase, repetition: int) -> EvaluationCaseRun:
        try:
            result = await self._service.review(case.request)
        except StructuredSupervisorModelError:
            return _failed_run(case.case_id, repetition, _SUPERVISOR_MODEL_UNAVAILABLE_DETAIL)
        except ReviewSupervisorError:
            return _failed_run(case.case_id, repetition, _SUPERVISOR_PROPOSAL_INVALID_DETAIL)

        return _score_completed_run(case, repetition, result)


def _failed_run(case_id: str, repetition: int, detail: str) -> EvaluationCaseRun:
    return EvaluationCaseRun(
        case_id=case_id,
        repetition=repetition,
        status=EvaluationRunStatus.FAILED,
        error_detail=detail,
    )


def _score_completed_run(
    case: EvaluationCase, repetition: int, result: ArchitectureReviewResult
) -> EvaluationCaseRun:
    matched_risk_ids: list[str] = []
    missed_risk_ids: list[str] = []
    severity_mismatch_risk_ids: list[str] = []
    evidence_hit_ids: list[str] = []
    evidence_miss_ids: list[str] = []

    for risk in case.expected_risks:
        finding = match_expected_risk(risk, result.specialist_reviews)
        if finding is None:
            missed_risk_ids.append(risk.risk_id)
            continue

        matched_risk_ids.append(risk.risk_id)

        if risk.acceptable_severities and finding.severity not in risk.acceptable_severities:
            severity_mismatch_risk_ids.append(risk.risk_id)

        if risk.expected_evidence_ids:
            cited_ids = {evidence.evidence_id for evidence in finding.evidence}
            if cited_ids & set(risk.expected_evidence_ids):
                evidence_hit_ids.append(risk.risk_id)
            else:
                evidence_miss_ids.append(risk.risk_id)

    matched_disagreement_ids: list[str] = []
    missed_disagreement_ids: list[str] = []
    for expected in case.expected_disagreements:
        disagreement = match_expected_disagreement(expected, result.disagreements)
        if disagreement is None:
            missed_disagreement_ids.append(expected.disagreement_id)
        else:
            matched_disagreement_ids.append(expected.disagreement_id)

    return EvaluationCaseRun(
        case_id=case.case_id,
        repetition=repetition,
        status=EvaluationRunStatus.COMPLETED,
        decision=result.decision,
        acceptable_decision=result.decision in case.acceptable_decisions,
        specialist_failures=tuple(failure.reviewer for failure in result.specialist_failures),
        matched_risk_ids=tuple(matched_risk_ids),
        missed_risk_ids=tuple(missed_risk_ids),
        severity_mismatch_risk_ids=tuple(severity_mismatch_risk_ids),
        evidence_citation_hit_risk_ids=tuple(evidence_hit_ids),
        evidence_citation_miss_risk_ids=tuple(evidence_miss_ids),
        matched_disagreement_ids=tuple(matched_disagreement_ids),
        missed_disagreement_ids=tuple(missed_disagreement_ids),
        evidence_status=result.evidence_context.status if result.evidence_context else None,
    )


def _summarize(
    dataset: EvaluationDataset, case_runs: list[EvaluationCaseRun], repetitions: int
) -> EvaluationSummary:
    total_runs = len(case_runs)
    completed = [run for run in case_runs if run.status == EvaluationRunStatus.COMPLETED]
    completed_runs = len(completed)

    full_board_runs = sum(1 for run in completed if not run.specialist_failures)

    total_expected_risks = repetitions * sum(len(case.expected_risks) for case in dataset.cases)
    matched_risks = sum(len(run.matched_risk_ids) for run in case_runs)

    total_expected_disagreements = repetitions * sum(
        len(case.expected_disagreements) for case in dataset.cases
    )
    matched_disagreements = sum(len(run.matched_disagreement_ids) for run in case_runs)

    total_evidence_required = repetitions * sum(
        1 for case in dataset.cases for risk in case.expected_risks if risk.expected_evidence_ids
    )
    evidence_hits = sum(len(run.evidence_citation_hit_risk_ids) for run in case_runs)

    return EvaluationSummary(
        total_runs=total_runs,
        completed_runs=completed_runs,
        completion_rate=completed_runs / total_runs,
        full_board_run_rate=full_board_runs / total_runs,
        acceptable_decision_rate=(
            sum(1 for run in completed if run.acceptable_decision) / completed_runs
            if completed_runs
            else None
        ),
        expected_risk_recall=(
            matched_risks / total_expected_risks if total_expected_risks else None
        ),
        expected_disagreement_recall=(
            matched_disagreements / total_expected_disagreements
            if total_expected_disagreements
            else None
        ),
        expected_evidence_citation_rate=(
            evidence_hits / total_evidence_required if total_evidence_required else None
        ),
    )


def _decision_stability(
    dataset: EvaluationDataset, case_runs: list[EvaluationCaseRun]
) -> tuple[CaseDecisionStability, ...]:
    """Per-case modal decision across all repetitions, including repetitions == 1.

    A single completed run is treated as trivially 100% self-agreeing
    (modal_agreement_rate=1.0) rather than reported as None: it is one
    real data point, and folding it into the same computation as
    repetitions > 1 keeps this function's logic uniform. modal_decision
    is None on a tie (no single most-common decision) or when no
    repetition of that case completed.
    """
    stability: list[CaseDecisionStability] = []
    for case in dataset.cases:
        completed = [
            run
            for run in case_runs
            if run.case_id == case.case_id and run.status == EvaluationRunStatus.COMPLETED
        ]
        completed_runs = len(completed)
        if completed_runs == 0:
            stability.append(
                CaseDecisionStability(
                    case_id=case.case_id,
                    completed_runs=0,
                    modal_decision=None,
                    modal_agreement_rate=None,
                )
            )
            continue

        counts: Counter[ArchitectureDecision] = Counter(
            run.decision for run in completed if run.decision is not None
        )
        modal_decision, modal_rate = _modal_decision(counts, completed_runs)
        stability.append(
            CaseDecisionStability(
                case_id=case.case_id,
                completed_runs=completed_runs,
                modal_decision=modal_decision,
                modal_agreement_rate=modal_rate,
            )
        )
    return tuple(stability)


def _modal_decision(
    counts: "Counter[ArchitectureDecision]", completed_runs: int
) -> tuple[ArchitectureDecision | None, float | None]:
    if not counts:
        return None, None
    highest = max(counts.values())
    modes = [decision for decision, count in counts.items() if count == highest]
    if len(modes) > 1:
        return None, None
    decision = modes[0]
    return decision, counts[decision] / completed_runs


