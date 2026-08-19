"""The stable application entry point for running a full architecture review."""

from architecture_review_board.domain.enums import ReviewEvidenceStatus
from architecture_review_board.domain.models import (
    ArchitectureReviewRequest,
    ArchitectureReviewResult,
    ReviewEvidence,
    ReviewEvidenceSearchResult,
)
from architecture_review_board.evidence.provider import (
    ReviewEvidenceProvider,
    ReviewEvidenceUnavailableError,
)
from architecture_review_board.evidence.query_builder import build_review_evidence_query
from architecture_review_board.reviewers.coordinator import ReviewCoordinator
from architecture_review_board.reviewers.supervisor import ReviewSupervisor

_EVIDENCE_UNAVAILABLE_DETAIL = "evidence provider unavailable"


class ArchitectureReviewService:
    """Runs the full board: optional evidence search, specialist review, then supervision.

    Contains no review policy of its own beyond sequencing; execution
    stays owned by ReviewCoordinator and reconciliation by ReviewSupervisor.
    Evidence retrieval is not the coordinator's concern either: this
    service resolves at most one shared evidence snapshot per review and
    hands it to the coordinator, which only distributes it.

    Without an evidence_provider, no lookup is attempted and the final
    result's evidence_context stays None. An expected evidence-provider
    failure never aborts the review: it degrades to
    evidence_context=UNAVAILABLE and the board runs with no evidence,
    same as if no provider had been configured at all except for that
    recorded outcome.
    """

    def __init__(
        self,
        coordinator: ReviewCoordinator,
        supervisor: ReviewSupervisor,
        evidence_provider: ReviewEvidenceProvider | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._supervisor = supervisor
        self._evidence_provider = evidence_provider

    async def review(self, request: ArchitectureReviewRequest) -> ArchitectureReviewResult:
        evidence_context = await self._search_evidence(request)

        available_evidence: tuple[ReviewEvidence, ...] = ()
        if evidence_context is not None and evidence_context.status == ReviewEvidenceStatus.SUCCESS:
            available_evidence = evidence_context.evidence

        coordinated_reviews = await self._coordinator.review(
            request, available_evidence=available_evidence
        )
        return await self._supervisor.review(
            request, coordinated_reviews, evidence_context=evidence_context
        )

    async def _search_evidence(
        self, request: ArchitectureReviewRequest
    ) -> ReviewEvidenceSearchResult | None:
        if self._evidence_provider is None:
            return None

        query = build_review_evidence_query(request)
        if query is None:
            return None

        try:
            return await self._evidence_provider.search(query)
        except ReviewEvidenceUnavailableError:
            return ReviewEvidenceSearchResult(
                status=ReviewEvidenceStatus.UNAVAILABLE,
                detail=_EVIDENCE_UNAVAILABLE_DETAIL,
            )
