import asyncio
from collections.abc import Callable

import pytest

from architecture_review_board.domain.enums import (
    REVIEW_DIMENSION_ORDER,
    ArchitectureDecision,
    FindingSeverity,
    ReviewDimension,
)
from architecture_review_board.domain.models import (
    ArchitectureReviewRequest,
    ReviewFinding,
    SpecialistReview,
    SpecialistReviewFailure,
)
from architecture_review_board.model.supervisor import (
    StructuredSupervisorModelError,
    SupervisorModelRequest,
)
from architecture_review_board.model.supervisor_drafts import (
    ReviewConditionDraft,
    ReviewDisagreementDraft,
    ReviewerPositionDraft,
    SupervisorReviewDraft,
)
from architecture_review_board.reviewers.coordinator import CoordinatedReviews
from architecture_review_board.reviewers.rubrics import build_review_supervisor
from architecture_review_board.reviewers.supervisor import ReviewSupervisorError


class ScriptedSupervisorModel:
    """Test double for StructuredSupervisorModel: replays a fixed draft or error."""

    def __init__(
        self,
        draft: SupervisorReviewDraft | None = None,
        error: Exception | None = None,
    ) -> None:
        self.draft = draft
        self.error = error
        self.received_request: SupervisorModelRequest | None = None

    async def generate_supervisor_review(
        self, request: SupervisorModelRequest
    ) -> SupervisorReviewDraft:
        self.received_request = request
        if self.error is not None:
            raise self.error
        assert self.draft is not None
        return self.draft


def make_request() -> ArchitectureReviewRequest:
    return ArchitectureReviewRequest(
        review_id="rev-1",
        title="Queue-based order processing",
        problem_statement="Order processing needs to absorb bursty traffic.",
        proposed_solution="Introduce a durable queue between checkout and fulfillment.",
    )


def make_finding(finding_id: str, reviewer: ReviewDimension) -> ReviewFinding:
    return ReviewFinding(
        finding_id=finding_id,
        reviewer=reviewer,
        title="A material concern",
        description="Description of the concern grounded in the proposal.",
        severity=FindingSeverity.HIGH,
        rationale="Rationale for the concern.",
        confidence=0.7,
    )


def make_full_reviews(
    extra_findings: dict[ReviewDimension, tuple[ReviewFinding, ...]] | None = None,
) -> tuple[SpecialistReview, ...]:
    extra_findings = extra_findings or {}
    return tuple(
        SpecialistReview(
            review_id="rev-1",
            reviewer=dimension,
            summary=f"{dimension.value} summary.",
            findings=extra_findings.get(dimension, ()),
            overall_confidence=0.8,
        )
        for dimension in REVIEW_DIMENSION_ORDER
    )


def _draft_with_unknown_blocking_finding() -> SupervisorReviewDraft:
    return SupervisorReviewDraft(
        decision=ArchitectureDecision.REQUEST_CHANGES,
        summary="Needs rework.",
        blocking_finding_ids=("nonexistent-finding",),
    )


def _draft_with_unknown_condition_finding() -> SupervisorReviewDraft:
    return SupervisorReviewDraft(
        decision=ArchitectureDecision.APPROVE_WITH_CONDITIONS,
        summary="Proceed conditionally.",
        conditions=(
            ReviewConditionDraft(
                description="Fix the issue.", related_finding_ids=("nonexistent-finding",)
            ),
        ),
    )


def _draft_with_unknown_disagreement_finding() -> SupervisorReviewDraft:
    return SupervisorReviewDraft(
        decision=ArchitectureDecision.APPROVE,
        summary="Looks fine overall.",
        disagreements=(
            ReviewDisagreementDraft(
                topic="Retry ceiling",
                positions=(
                    ReviewerPositionDraft(
                        reviewer=ReviewDimension.SECURITY,
                        position="Blocking.",
                        related_finding_ids=("nonexistent-finding",),
                    ),
                    ReviewerPositionDraft(reviewer=ReviewDimension.COMPLEXITY, position="Fine."),
                ),
            ),
        ),
    )


def _draft_with_misattributed_position_finding() -> SupervisorReviewDraft:
    return SupervisorReviewDraft(
        decision=ArchitectureDecision.APPROVE,
        summary="Looks fine overall.",
        disagreements=(
            ReviewDisagreementDraft(
                topic="Retry ceiling",
                positions=(
                    ReviewerPositionDraft(
                        reviewer=ReviewDimension.COMPLEXITY,
                        position="Unjustified complexity.",
                        related_finding_ids=("security-001",),
                    ),
                    ReviewerPositionDraft(reviewer=ReviewDimension.RELIABILITY, position="Fine."),
                ),
            ),
        ),
    )


def _draft_with_a_single_disagreement_position() -> SupervisorReviewDraft:
    return SupervisorReviewDraft(
        decision=ArchitectureDecision.APPROVE,
        summary="Looks fine overall.",
        disagreements=(
            ReviewDisagreementDraft(
                topic="Retry ceiling",
                positions=(
                    ReviewerPositionDraft(reviewer=ReviewDimension.SECURITY, position="Alone."),
                ),
            ),
        ),
    )


@pytest.mark.parametrize(
    "build_draft",
    [
        _draft_with_unknown_blocking_finding,
        _draft_with_unknown_condition_finding,
        _draft_with_unknown_disagreement_finding,
        _draft_with_misattributed_position_finding,
        _draft_with_a_single_disagreement_position,
    ],
)
def test_supervisor_rejects_invalid_finding_references(
    build_draft: Callable[[], SupervisorReviewDraft],
) -> None:
    reviews = make_full_reviews(
        extra_findings={
            ReviewDimension.SECURITY: (make_finding("security-001", ReviewDimension.SECURITY),)
        }
    )
    coordinated = CoordinatedReviews(review_id="rev-1", reviews=reviews, failures=())
    model = ScriptedSupervisorModel(draft=build_draft())
    supervisor = build_review_supervisor(model)

    with pytest.raises(ReviewSupervisorError):
        asyncio.run(supervisor.review(make_request(), coordinated))


def test_condition_and_disagreement_ids_are_assigned_deterministically() -> None:
    reviews = make_full_reviews(
        extra_findings={
            ReviewDimension.SECURITY: (make_finding("security-001", ReviewDimension.SECURITY),)
        }
    )
    coordinated = CoordinatedReviews(review_id="rev-1", reviews=reviews, failures=())
    draft = SupervisorReviewDraft(
        decision=ArchitectureDecision.APPROVE_WITH_CONDITIONS,
        summary="Proceed conditionally.",
        conditions=(
            ReviewConditionDraft(description="First condition."),
            ReviewConditionDraft(
                description="Second condition.", related_finding_ids=("security-001",)
            ),
        ),
        disagreements=(
            ReviewDisagreementDraft(
                topic="First topic",
                positions=(
                    ReviewerPositionDraft(reviewer=ReviewDimension.SECURITY, position="A"),
                    ReviewerPositionDraft(reviewer=ReviewDimension.COMPLEXITY, position="B"),
                ),
            ),
            ReviewDisagreementDraft(
                topic="Second topic",
                positions=(
                    ReviewerPositionDraft(reviewer=ReviewDimension.RELIABILITY, position="C"),
                    ReviewerPositionDraft(reviewer=ReviewDimension.DATA, position="D"),
                ),
            ),
        ),
    )
    supervisor = build_review_supervisor(ScriptedSupervisorModel(draft=draft))

    result = asyncio.run(supervisor.review(make_request(), coordinated))

    assert [c.condition_id for c in result.conditions] == ["condition-001", "condition-002"]
    assert [d.disagreement_id for d in result.disagreements] == [
        "disagreement-001",
        "disagreement-002",
    ]


def test_supervisor_review_preserves_findings_and_disagreement_details() -> None:
    reliability_finding = make_finding("reliability-001", ReviewDimension.RELIABILITY)
    complexity_finding = make_finding("complexity-001", ReviewDimension.COMPLEXITY)
    reviews = make_full_reviews(
        extra_findings={
            ReviewDimension.RELIABILITY: (reliability_finding,),
            ReviewDimension.COMPLEXITY: (complexity_finding,),
        }
    )
    coordinated = CoordinatedReviews(review_id="rev-1", reviews=reviews, failures=())
    draft = SupervisorReviewDraft(
        decision=ArchitectureDecision.APPROVE_WITH_CONDITIONS,
        summary="Board synthesis.",
        conditions=(ReviewConditionDraft(description="Address the tradeoff."),),
        disagreements=(
            ReviewDisagreementDraft(
                topic="Synchronous replication",
                positions=(
                    ReviewerPositionDraft(
                        reviewer=ReviewDimension.RELIABILITY,
                        position="Synchronous replication is required.",
                        related_finding_ids=(reliability_finding.finding_id,),
                    ),
                    ReviewerPositionDraft(
                        reviewer=ReviewDimension.COMPLEXITY,
                        position="Synchronous replication is unjustified complexity.",
                        related_finding_ids=(complexity_finding.finding_id,),
                    ),
                ),
                resolution=None,
            ),
        ),
    )
    supervisor = build_review_supervisor(ScriptedSupervisorModel(draft=draft))

    result = asyncio.run(supervisor.review(make_request(), coordinated))

    assert result.specialist_reviews == reviews
    assert len(result.disagreements) == 1
    disagreement = result.disagreements[0]
    assert disagreement.topic == "Synchronous replication"
    assert disagreement.resolution is None
    assert {position.reviewer for position in disagreement.positions} == {
        ReviewDimension.RELIABILITY,
        ReviewDimension.COMPLEXITY,
    }


def test_incomplete_board_rejects_approve_but_allows_conditional_approval() -> None:
    reviews = make_full_reviews()[:-1]
    failure = SpecialistReviewFailure(
        reviewer=ReviewDimension.COMPLEXITY, detail="specialist review unavailable"
    )
    coordinated = CoordinatedReviews(review_id="rev-1", reviews=reviews, failures=(failure,))

    approve_draft = SupervisorReviewDraft(decision=ArchitectureDecision.APPROVE, summary="Fine.")
    with pytest.raises(ReviewSupervisorError):
        asyncio.run(
            build_review_supervisor(ScriptedSupervisorModel(draft=approve_draft)).review(
                make_request(), coordinated
            )
        )

    conditional_draft = SupervisorReviewDraft(
        decision=ArchitectureDecision.APPROVE_WITH_CONDITIONS,
        summary="Proceed once complexity review is available.",
        conditions=(ReviewConditionDraft(description="Re-run the complexity review."),),
    )
    result = asyncio.run(
        build_review_supervisor(ScriptedSupervisorModel(draft=conditional_draft)).review(
            make_request(), coordinated
        )
    )

    assert result.decision == ArchitectureDecision.APPROVE_WITH_CONDITIONS
    assert result.specialist_failures == (failure,)


def test_structured_supervisor_model_error_propagates_without_a_fallback_result() -> None:
    coordinated = CoordinatedReviews(review_id="rev-1", reviews=make_full_reviews(), failures=())
    model = ScriptedSupervisorModel(error=StructuredSupervisorModelError("provider unavailable"))
    supervisor = build_review_supervisor(model)

    with pytest.raises(StructuredSupervisorModelError):
        asyncio.run(supervisor.review(make_request(), coordinated))
