import asyncio

from architecture_review_board.domain.enums import (
    REVIEW_DIMENSION_ORDER,
    ArchitectureDecision,
    FindingSeverity,
    ReviewEvidenceStatus,
)
from architecture_review_board.domain.models import (
    ArchitectureReviewRequest,
    ReviewEvidence,
    ReviewEvidenceQuery,
    ReviewEvidenceSearchResult,
)
from architecture_review_board.evidence.provider import ReviewEvidenceUnavailableError
from architecture_review_board.model.base import SpecialistModelRequest
from architecture_review_board.model.drafts import ReviewFindingDraft, SpecialistReviewDraft
from architecture_review_board.model.supervisor import SupervisorModelRequest
from architecture_review_board.model.supervisor_drafts import SupervisorReviewDraft
from architecture_review_board.reviewers.coordinator import ReviewCoordinator
from architecture_review_board.reviewers.rubrics import (
    build_review_supervisor,
    build_specialist_reviewers,
)
from architecture_review_board.reviewers.service import ArchitectureReviewService


def make_request() -> ArchitectureReviewRequest:
    return ArchitectureReviewRequest(
        review_id="rev-1",
        title="Queue-based order processing",
        problem_statement="Order processing needs to absorb bursty traffic.",
        proposed_solution="Introduce a durable queue between checkout and fulfillment.",
    )


class ImmediateSpecialistModel:
    """Test double for StructuredReviewModel: reports no findings for every dimension."""

    async def generate_specialist_review(
        self, request: SpecialistModelRequest
    ) -> SpecialistReviewDraft:
        return SpecialistReviewDraft(
            summary=f"No material {request.reviewer.value} issues.", overall_confidence=0.8
        )


class ApprovingSupervisorModel:
    """Test double for StructuredSupervisorModel: approves once every dimension has reported."""

    def __init__(self) -> None:
        self.received_request: SupervisorModelRequest | None = None

    async def generate_supervisor_review(
        self, request: SupervisorModelRequest
    ) -> SupervisorReviewDraft:
        self.received_request = request
        return SupervisorReviewDraft(
            decision=ArchitectureDecision.APPROVE,
            summary="Board synthesis: no material issues across any dimension.",
        )


def test_architecture_review_service_runs_full_board_end_to_end() -> None:
    supervisor_model = ApprovingSupervisorModel()
    service = ArchitectureReviewService(
        coordinator=ReviewCoordinator(build_specialist_reviewers(ImmediateSpecialistModel())),
        supervisor=build_review_supervisor(supervisor_model),
    )

    result = asyncio.run(service.review(make_request()))

    assert [review.reviewer for review in result.specialist_reviews] == list(
        REVIEW_DIMENSION_ORDER
    )
    assert result.specialist_failures == ()
    assert result.decision == ArchitectureDecision.APPROVE
    assert result.disagreements == ()
    assert result.conditions == ()
    assert supervisor_model.received_request is not None
    assert len(supervisor_model.received_request.specialist_reviews) == 5
    assert supervisor_model.received_request.specialist_failures == ()
    assert result.evidence_context is None


class FailingEvidenceProvider:
    async def search(self, query: ReviewEvidenceQuery) -> ReviewEvidenceSearchResult:
        raise ReviewEvidenceUnavailableError("engineering knowledge provider is unavailable")


class SuccessfulEvidenceProvider:
    def __init__(self, evidence: tuple[ReviewEvidence, ...]) -> None:
        self._evidence = evidence
        self.received_query: ReviewEvidenceQuery | None = None

    async def search(self, query: ReviewEvidenceQuery) -> ReviewEvidenceSearchResult:
        self.received_query = query
        return ReviewEvidenceSearchResult(
            status=ReviewEvidenceStatus.SUCCESS, evidence=self._evidence
        )


class EvidenceCitingSpecialistModel:
    """Cites the first available evidence item, if any, in one finding per dimension."""

    async def generate_specialist_review(
        self, request: SpecialistModelRequest
    ) -> SpecialistReviewDraft:
        if not request.available_evidence:
            return SpecialistReviewDraft(summary="No evidence available.", overall_confidence=0.8)
        cited_id = request.available_evidence[0].evidence_id
        return SpecialistReviewDraft(
            summary="Reasoned over supplied evidence.",
            overall_confidence=0.8,
            findings=(
                ReviewFindingDraft(
                    title="Concern grounded in evidence",
                    description="See cited evidence.",
                    severity=FindingSeverity.MEDIUM,
                    rationale="The supplied evidence supports this concern.",
                    confidence=0.7,
                    evidence_ids=(cited_id,),
                ),
            ),
        )


def test_service_degrades_to_unavailable_or_shares_evidence_on_success() -> None:
    unavailable_service = ArchitectureReviewService(
        coordinator=ReviewCoordinator(build_specialist_reviewers(EvidenceCitingSpecialistModel())),
        supervisor=build_review_supervisor(ApprovingSupervisorModel()),
        evidence_provider=FailingEvidenceProvider(),
    )

    unavailable_result = asyncio.run(unavailable_service.review(make_request()))

    assert unavailable_result.evidence_context is not None
    assert unavailable_result.evidence_context.status == ReviewEvidenceStatus.UNAVAILABLE
    assert all(
        finding.evidence == ()
        for review in unavailable_result.specialist_reviews
        for finding in review.findings
    )

    shared_evidence = (
        ReviewEvidence(
            evidence_id="knowledge-001",
            source_type="engineering-knowledge",
            source_reference="ref",
            excerpt="Bounded retry budgets prevent amplification during outages.",
        ),
    )
    evidence_provider = SuccessfulEvidenceProvider(shared_evidence)
    success_service = ArchitectureReviewService(
        coordinator=ReviewCoordinator(build_specialist_reviewers(EvidenceCitingSpecialistModel())),
        supervisor=build_review_supervisor(ApprovingSupervisorModel()),
        evidence_provider=evidence_provider,
    )

    success_result = asyncio.run(success_service.review(make_request()))

    assert evidence_provider.received_query is not None
    assert success_result.evidence_context is not None
    assert success_result.evidence_context.status == ReviewEvidenceStatus.SUCCESS
    assert success_result.evidence_context.evidence == shared_evidence
    cited_evidence = [
        finding.evidence
        for review in success_result.specialist_reviews
        for finding in review.findings
    ]
    assert len(cited_evidence) == 5
    assert all(evidence_tuple == shared_evidence for evidence_tuple in cited_evidence)
