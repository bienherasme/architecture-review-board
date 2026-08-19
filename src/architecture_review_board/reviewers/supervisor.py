"""Application service that reconciles specialist output into a final result."""

from pydantic import ValidationError

from architecture_review_board.domain.models import (
    ArchitectureReviewRequest,
    ArchitectureReviewResult,
    ReviewCondition,
    ReviewDisagreement,
    ReviewerPosition,
    ReviewEvidenceSearchResult,
)
from architecture_review_board.model.supervisor import (
    StructuredSupervisorModel,
    SupervisorModelRequest,
)
from architecture_review_board.model.supervisor_drafts import (
    ReviewConditionDraft,
    ReviewDisagreementDraft,
)
from architecture_review_board.reviewers.coordinator import CoordinatedReviews

_INVALID_PROPOSAL_MESSAGE = "supervisor proposal violates board invariants"


class ReviewSupervisorError(Exception):
    """A structurally valid supervisor draft still violates a board or domain invariant.

    Raised for an unknown finding reference, a position attributed to a
    reviewer that did not produce it or to a dimension with no specialist
    review, or a decision inconsistent with the board's coverage or
    findings. Distinct from StructuredSupervisorModelError, which means
    the provider could not produce structured output at all; this means
    it did, but the content itself is invalid.
    """


class ReviewSupervisor:
    """Turns a model's structured draft into the final ArchitectureReviewResult.

    Reconciliation, synthesis, disagreement, conditions, blocking
    findings, and the final decision, is the supervisor's job. It never
    rewrites a specialist finding's severity, confidence, recommendation,
    or evidence: the SpecialistReview objects the coordinator produced
    pass through unchanged and are only ever referenced by finding_id.

    evidence_context is accepted, not computed: the supervisor does not
    call the evidence provider or interpret its outcome, only forwards
    whatever the application composition already resolved so the final
    result can be constructed once, fully validated.
    """

    def __init__(self, model: StructuredSupervisorModel, system_instructions: str) -> None:
        self._model = model
        self._system_instructions = system_instructions

    async def review(
        self,
        request: ArchitectureReviewRequest,
        coordinated_reviews: CoordinatedReviews,
        *,
        evidence_context: ReviewEvidenceSearchResult | None = None,
    ) -> ArchitectureReviewResult:
        if request.review_id != coordinated_reviews.review_id:
            raise ValueError("request.review_id does not match coordinated_reviews.review_id")

        model_request = SupervisorModelRequest(
            system_instructions=self._system_instructions,
            architecture_request=request,
            specialist_reviews=coordinated_reviews.reviews,
            specialist_failures=coordinated_reviews.failures,
        )
        draft = await self._model.generate_supervisor_review(model_request)

        conditions = tuple(
            self._stamp_condition(condition_draft, index)
            for index, condition_draft in enumerate(draft.conditions, start=1)
        )
        disagreements = tuple(
            self._stamp_disagreement(disagreement_draft, index)
            for index, disagreement_draft in enumerate(draft.disagreements, start=1)
        )

        try:
            return ArchitectureReviewResult(
                review_id=request.review_id,
                decision=draft.decision,
                summary=draft.summary,
                specialist_reviews=coordinated_reviews.reviews,
                specialist_failures=coordinated_reviews.failures,
                disagreements=disagreements,
                conditions=conditions,
                blocking_finding_ids=draft.blocking_finding_ids,
                evidence_context=evidence_context,
            )
        except ValidationError as error:
            raise ReviewSupervisorError(_INVALID_PROPOSAL_MESSAGE) from error

    @staticmethod
    def _stamp_condition(draft: ReviewConditionDraft, index: int) -> ReviewCondition:
        return ReviewCondition(
            condition_id=f"condition-{index:03d}",
            description=draft.description,
            related_finding_ids=draft.related_finding_ids,
        )

    @staticmethod
    def _stamp_disagreement(draft: ReviewDisagreementDraft, index: int) -> ReviewDisagreement:
        positions = tuple(
            ReviewerPosition(
                reviewer=position.reviewer,
                position=position.position,
                related_finding_ids=position.related_finding_ids,
            )
            for position in draft.positions
        )
        try:
            return ReviewDisagreement(
                disagreement_id=f"disagreement-{index:03d}",
                topic=draft.topic,
                positions=positions,
                resolution=draft.resolution,
            )
        except ValidationError as error:
            raise ReviewSupervisorError(_INVALID_PROPOSAL_MESSAGE) from error
