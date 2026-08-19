import pytest
from pydantic import ValidationError

from architecture_review_board.domain.enums import ArchitectureDecision, ReviewDimension
from architecture_review_board.model.supervisor_drafts import (
    MAX_BLOCKING_FINDINGS,
    MAX_REVIEW_CONDITIONS,
    MAX_REVIEW_DISAGREEMENTS,
    ReviewConditionDraft,
    ReviewDisagreementDraft,
    ReviewerPositionDraft,
    SupervisorReviewDraft,
)


def test_supervisor_drafts_reject_unexpected_fields() -> None:
    with pytest.raises(ValidationError):
        ReviewerPositionDraft(
            reviewer=ReviewDimension.SECURITY,
            position="Needs mTLS.",
            finding_id="security-001",
        )

    with pytest.raises(ValidationError):
        SupervisorReviewDraft(
            decision=ArchitectureDecision.APPROVE,
            summary="Looks fine.",
            model_name="test-model",
        )


def test_supervisor_draft_output_bounds_are_enforced() -> None:
    with pytest.raises(ValidationError):
        SupervisorReviewDraft(
            decision=ArchitectureDecision.REQUEST_CHANGES,
            summary="Too many blocking findings.",
            blocking_finding_ids=tuple(f"f-{i}" for i in range(MAX_BLOCKING_FINDINGS + 1)),
        )

    with pytest.raises(ValidationError):
        SupervisorReviewDraft(
            decision=ArchitectureDecision.APPROVE_WITH_CONDITIONS,
            summary="Too many conditions.",
            conditions=tuple(
                ReviewConditionDraft(description=f"Condition {i}")
                for i in range(MAX_REVIEW_CONDITIONS + 1)
            ),
        )

    with pytest.raises(ValidationError):
        SupervisorReviewDraft(
            decision=ArchitectureDecision.APPROVE,
            summary="Too many disagreements.",
            disagreements=tuple(
                ReviewDisagreementDraft(
                    topic=f"Topic {i}",
                    positions=(
                        ReviewerPositionDraft(reviewer=ReviewDimension.SECURITY, position="A"),
                        ReviewerPositionDraft(reviewer=ReviewDimension.COMPLEXITY, position="B"),
                    ),
                )
                for i in range(MAX_REVIEW_DISAGREEMENTS + 1)
            ),
        )
