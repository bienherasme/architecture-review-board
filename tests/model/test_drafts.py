import pytest
from pydantic import ValidationError

from architecture_review_board.domain.enums import FindingSeverity
from architecture_review_board.model.drafts import (
    MAX_SPECIALIST_FINDINGS,
    ReviewFindingDraft,
    SpecialistReviewDraft,
)


def make_finding_draft(confidence: float = 0.7) -> ReviewFindingDraft:
    return ReviewFindingDraft(
        title="Single point of failure in the queue consumer",
        description="Only one consumer instance is described, with no failover path.",
        severity=FindingSeverity.HIGH,
        rationale="The proposal does not mention consumer redundancy.",
        recommendation="Run at least two consumer instances behind a shared queue.",
        confidence=confidence,
    )


def test_draft_models_reject_unexpected_fields() -> None:
    with pytest.raises(ValidationError):
        ReviewFindingDraft(
            title="x",
            description="y",
            severity=FindingSeverity.LOW,
            rationale="z",
            confidence=0.5,
            reviewer="security",
        )

    with pytest.raises(ValidationError):
        SpecialistReviewDraft(
            summary="Reliability looks acceptable.",
            overall_confidence=0.8,
            findings=(),
            model_name="test-model",
        )


def test_specialist_review_draft_rejects_more_than_the_finding_bound() -> None:
    with pytest.raises(ValidationError):
        SpecialistReviewDraft(
            summary="Too many findings.",
            overall_confidence=0.5,
            findings=tuple(make_finding_draft() for _ in range(MAX_SPECIALIST_FINDINGS + 1)),
        )
