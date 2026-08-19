import asyncio
from typing import Any

from architecture_review_board.domain.enums import (
    ArchitectureDecision,
    FindingSeverity,
    ReviewDimension,
    ReviewEvidenceStatus,
)
from architecture_review_board.domain.models import (
    ArchitectureReviewRequest,
    ReviewEvidence,
    ReviewEvidenceQuery,
    ReviewEvidenceSearchResult,
)
from architecture_review_board.evaluation.evaluator import ArchitectureReviewEvaluator
from architecture_review_board.evaluation.models import (
    EvaluationCase,
    EvaluationDataset,
    EvaluationRunMetadata,
    EvaluationRunStatus,
    ExpectedRisk,
)
from architecture_review_board.model.base import SpecialistModelRequest
from architecture_review_board.model.drafts import ReviewFindingDraft, SpecialistReviewDraft
from architecture_review_board.model.supervisor import (
    StructuredSupervisorModelError,
    SupervisorModelRequest,
)
from architecture_review_board.model.supervisor_drafts import (
    ReviewConditionDraft,
    SupervisorReviewDraft,
)
from architecture_review_board.reviewers import REVIEW_DIMENSION_ORDER
from architecture_review_board.reviewers.coordinator import ReviewCoordinator
from architecture_review_board.reviewers.rubrics import (
    build_review_supervisor,
    build_specialist_reviewers,
)
from architecture_review_board.reviewers.service import ArchitectureReviewService


def make_request(review_id: str) -> ArchitectureReviewRequest:
    return ArchitectureReviewRequest(
        review_id=review_id,
        title="A proposal",
        problem_statement="A problem statement long enough to be meaningful for review.",
        proposed_solution="A proposed solution long enough to be meaningful for review.",
    )


def make_case(
    case_id: str,
    acceptable_decisions: tuple[ArchitectureDecision, ...] = (ArchitectureDecision.APPROVE,),
    expected_risks: tuple[ExpectedRisk, ...] = (),
) -> EvaluationCase:
    return EvaluationCase(
        case_id=case_id,
        request=make_request(case_id),
        acceptable_decisions=acceptable_decisions,
        expected_risks=expected_risks,
    )


def make_dataset(cases: list[EvaluationCase]) -> EvaluationDataset:
    return EvaluationDataset(dataset_id="test-dataset", version="0.1", cases=tuple(cases))


def make_run_metadata() -> EvaluationRunMetadata:
    return EvaluationRunMetadata(
        provider="test-double", model="deterministic-v1", evidence_mode="none"
    )


class ScriptedSpecialistModel:
    """Returns a fixed draft per (review_id, reviewer); other pairs get no findings."""

    def __init__(
        self, drafts: dict[tuple[str, ReviewDimension], SpecialistReviewDraft] | None = None
    ) -> None:
        self._drafts = drafts or {}

    async def generate_specialist_review(
        self, request: SpecialistModelRequest
    ) -> SpecialistReviewDraft:
        key = (request.architecture_request.review_id, request.reviewer)
        return self._drafts.get(
            key, SpecialistReviewDraft(summary="No material issues.", overall_confidence=0.8)
        )


class ScriptedSupervisorModel:
    """Replays a queued sequence of outcomes per review_id, clamping past the end."""

    def __init__(self, outcomes: dict[str, list[Any]]) -> None:
        self._outcomes = {key: list(value) for key, value in outcomes.items()}
        self._index: dict[str, int] = dict.fromkeys(outcomes, 0)

    async def generate_supervisor_review(
        self, request: SupervisorModelRequest
    ) -> SupervisorReviewDraft:
        review_id = request.architecture_request.review_id
        queue = self._outcomes[review_id]
        index = min(self._index[review_id], len(queue) - 1)
        self._index[review_id] = index + 1
        outcome = queue[index]
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, SupervisorReviewDraft)
        return outcome


class StaticEvidenceProvider:
    def __init__(self, evidence: tuple[ReviewEvidence, ...]) -> None:
        self._result = ReviewEvidenceSearchResult(
            status=ReviewEvidenceStatus.SUCCESS, evidence=evidence
        )

    async def search(self, query: ReviewEvidenceQuery) -> ReviewEvidenceSearchResult:
        return self._result


def build_service(
    specialist_model: Any,
    supervisor_model: ScriptedSupervisorModel,
    evidence_provider: Any = None,
) -> ArchitectureReviewService:
    return ArchitectureReviewService(
        coordinator=ReviewCoordinator(build_specialist_reviewers(specialist_model)),
        supervisor=build_review_supervisor(supervisor_model),
        evidence_provider=evidence_provider,
    )


def test_completed_and_failed_runs_are_distinguished() -> None:
    completed_case = make_case("completed-case")
    model_unavailable_case = make_case("model-unavailable-case")
    invalid_proposal_case = make_case(
        "invalid-proposal-case", acceptable_decisions=(ArchitectureDecision.REQUEST_CHANGES,)
    )
    dataset = make_dataset([completed_case, model_unavailable_case, invalid_proposal_case])

    supervisor_model = ScriptedSupervisorModel(
        {
            "completed-case": [
                SupervisorReviewDraft(decision=ArchitectureDecision.APPROVE, summary="Fine.")
            ],
            "model-unavailable-case": [StructuredSupervisorModelError("provider unavailable")],
            # REQUEST_CHANGES with no blocking_finding_ids violates a domain invariant.
            "invalid-proposal-case": [
                SupervisorReviewDraft(
                    decision=ArchitectureDecision.REQUEST_CHANGES, summary="Needs rework."
                )
            ],
        }
    )
    service = build_service(ScriptedSpecialistModel(), supervisor_model)
    evaluator = ArchitectureReviewEvaluator(service, dataset, run_metadata=make_run_metadata())

    report = asyncio.run(evaluator.evaluate())
    runs = {run.case_id: run for run in report.case_runs}

    assert runs["completed-case"].status == EvaluationRunStatus.COMPLETED
    assert runs["completed-case"].decision == ArchitectureDecision.APPROVE
    assert runs["completed-case"].acceptable_decision is True

    assert runs["model-unavailable-case"].status == EvaluationRunStatus.FAILED
    assert runs["model-unavailable-case"].decision is None
    assert runs["model-unavailable-case"].error_detail == "supervisor model unavailable"

    assert runs["invalid-proposal-case"].status == EvaluationRunStatus.FAILED
    assert runs["invalid-proposal-case"].error_detail == "supervisor proposal invalid"


def test_summary_denominators_include_failed_runs_and_use_none_when_not_applicable() -> None:
    risk = ExpectedRisk(
        risk_id="r-1",
        reviewer=ReviewDimension.RELIABILITY,
        anchor_groups=(("single instance",),),
    )
    risky_case = make_case(
        "risky-case",
        acceptable_decisions=(ArchitectureDecision.REQUEST_CHANGES,),
        expected_risks=(risk,),
    )
    healthy_case = make_case("healthy-case")
    dataset = make_dataset([risky_case, healthy_case])

    supervisor_model = ScriptedSupervisorModel(
        {
            "risky-case": [StructuredSupervisorModelError("boom")],
            "healthy-case": [
                SupervisorReviewDraft(decision=ArchitectureDecision.APPROVE, summary="Fine.")
            ],
        }
    )
    service = build_service(ScriptedSpecialistModel(), supervisor_model)
    evaluator = ArchitectureReviewEvaluator(service, dataset, run_metadata=make_run_metadata())

    report = asyncio.run(evaluator.evaluate())

    assert report.summary.total_runs == 2
    assert report.summary.completed_runs == 1
    assert report.summary.completion_rate == 0.5
    # risky-case's expected risk is scheduled once and its run failed, so it's a miss.
    assert report.summary.expected_risk_recall == 0.0
    assert report.summary.expected_disagreement_recall is None
    assert report.summary.expected_evidence_citation_rate is None


def test_severity_assertion_is_optional_and_flags_mismatch_when_configured() -> None:
    strict_risk = ExpectedRisk(
        risk_id="strict",
        reviewer=ReviewDimension.RELIABILITY,
        anchor_groups=(("single instance",),),
        acceptable_severities=(FindingSeverity.CRITICAL,),
    )
    loose_risk = ExpectedRisk(
        risk_id="loose",
        reviewer=ReviewDimension.SECURITY,
        anchor_groups=(("shared credential",),),
    )
    case = make_case(
        "severity-case",
        acceptable_decisions=(ArchitectureDecision.REQUEST_CHANGES,),
        expected_risks=(strict_risk, loose_risk),
    )
    dataset = make_dataset([case])

    reliability_finding = ReviewFindingDraft(
        title="Single instance failure domain",
        description="One instance holds all state.",
        severity=FindingSeverity.MEDIUM,
        rationale="No redundancy is described.",
        confidence=0.6,
    )
    security_finding = ReviewFindingDraft(
        title="Shared credential across services",
        description="One credential grants broad access.",
        severity=FindingSeverity.LOW,
        rationale="No per-service scoping is described.",
        confidence=0.6,
    )
    specialist_model = ScriptedSpecialistModel(
        {
            ("severity-case", ReviewDimension.RELIABILITY): SpecialistReviewDraft(
                summary="s", overall_confidence=0.6, findings=(reliability_finding,)
            ),
            ("severity-case", ReviewDimension.SECURITY): SpecialistReviewDraft(
                summary="s", overall_confidence=0.6, findings=(security_finding,)
            ),
        }
    )
    supervisor_model = ScriptedSupervisorModel(
        {
            "severity-case": [
                SupervisorReviewDraft(
                    decision=ArchitectureDecision.REQUEST_CHANGES,
                    summary="Needs rework.",
                    blocking_finding_ids=("reliability-001",),
                )
            ]
        }
    )
    service = build_service(specialist_model, supervisor_model)
    evaluator = ArchitectureReviewEvaluator(service, dataset, run_metadata=make_run_metadata())

    report = asyncio.run(evaluator.evaluate())
    run = report.case_runs[0]

    assert set(run.matched_risk_ids) == {"strict", "loose"}
    assert run.severity_mismatch_risk_ids == ("strict",)


def test_evidence_citation_metric_tracks_hits_and_evidence_status() -> None:
    evidence_item = ReviewEvidence(
        evidence_id="knowledge-001",
        source_type="engineering-knowledge",
        source_reference="ref",
        excerpt="Bounded retry budgets prevent amplification during outages.",
    )
    risk = ExpectedRisk(
        risk_id="r-1",
        reviewer=ReviewDimension.RELIABILITY,
        anchor_groups=(("single instance",),),
        expected_evidence_ids=("knowledge-001",),
    )
    case = make_case(
        "evidence-case",
        acceptable_decisions=(ArchitectureDecision.REQUEST_CHANGES,),
        expected_risks=(risk,),
    )
    dataset = make_dataset([case])

    finding = ReviewFindingDraft(
        title="Single instance failure domain",
        description="One instance holds all state.",
        severity=FindingSeverity.HIGH,
        rationale="No redundancy is described.",
        confidence=0.7,
        evidence_ids=("knowledge-001",),
    )
    specialist_model = ScriptedSpecialistModel(
        {
            ("evidence-case", ReviewDimension.RELIABILITY): SpecialistReviewDraft(
                summary="s", overall_confidence=0.7, findings=(finding,)
            )
        }
    )
    supervisor_model = ScriptedSupervisorModel(
        {
            "evidence-case": [
                SupervisorReviewDraft(
                    decision=ArchitectureDecision.REQUEST_CHANGES,
                    summary="Needs rework.",
                    blocking_finding_ids=("reliability-001",),
                )
            ]
        }
    )
    service = build_service(
        specialist_model, supervisor_model, StaticEvidenceProvider((evidence_item,))
    )
    evaluator = ArchitectureReviewEvaluator(service, dataset, run_metadata=make_run_metadata())

    report = asyncio.run(evaluator.evaluate())
    run = report.case_runs[0]

    assert run.evidence_status == ReviewEvidenceStatus.SUCCESS
    assert run.evidence_citation_hit_risk_ids == ("r-1",)
    assert report.summary.expected_evidence_citation_rate == 1.0


def test_decision_stability_across_repetitions_including_a_tie() -> None:
    stable_case = make_case("stable-case")
    tied_case = make_case(
        "tied-case",
        acceptable_decisions=(
            ArchitectureDecision.APPROVE,
            ArchitectureDecision.APPROVE_WITH_CONDITIONS,
        ),
    )
    dataset = make_dataset([stable_case, tied_case])

    supervisor_model = ScriptedSupervisorModel(
        {
            "stable-case": [
                SupervisorReviewDraft(decision=ArchitectureDecision.APPROVE, summary="Fine.")
            ],
            "tied-case": [
                SupervisorReviewDraft(decision=ArchitectureDecision.APPROVE, summary="Fine."),
                SupervisorReviewDraft(
                    decision=ArchitectureDecision.APPROVE_WITH_CONDITIONS,
                    summary="Proceed with a condition.",
                    conditions=(ReviewConditionDraft(description="Address the tradeoff."),),
                ),
            ],
        }
    )
    service = build_service(ScriptedSpecialistModel(), supervisor_model)
    evaluator = ArchitectureReviewEvaluator(service, dataset, run_metadata=make_run_metadata())

    report = asyncio.run(evaluator.evaluate(repetitions=2))
    stability = {entry.case_id: entry for entry in report.decision_stability}

    assert stability["stable-case"].modal_decision == ArchitectureDecision.APPROVE
    assert stability["stable-case"].modal_agreement_rate == 1.0
    assert stability["tied-case"].modal_decision is None
    assert stability["tied-case"].modal_agreement_rate is None


def test_evaluator_runs_the_real_application_workflow_not_a_shortcut() -> None:
    case = make_case("full-pipeline-case")
    dataset = make_dataset([case])
    call_log: list[ReviewDimension] = []

    class CountingSpecialistModel:
        async def generate_specialist_review(
            self, request: SpecialistModelRequest
        ) -> SpecialistReviewDraft:
            call_log.append(request.reviewer)
            return SpecialistReviewDraft(summary="No material issues.", overall_confidence=0.8)

    supervisor_model = ScriptedSupervisorModel(
        {
            "full-pipeline-case": [
                SupervisorReviewDraft(decision=ArchitectureDecision.APPROVE, summary="Fine.")
            ]
        }
    )
    service = build_service(CountingSpecialistModel(), supervisor_model)
    evaluator = ArchitectureReviewEvaluator(service, dataset, run_metadata=make_run_metadata())

    report = asyncio.run(evaluator.evaluate())

    assert sorted(call_log) == sorted(REVIEW_DIMENSION_ORDER)
    assert report.case_runs[0].specialist_failures == ()
    assert report.summary.full_board_run_rate == 1.0
