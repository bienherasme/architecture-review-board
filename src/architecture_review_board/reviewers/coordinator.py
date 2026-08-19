"""Runs the specialist board: five independent reviews, executed concurrently.

This module owns execution, not judgment. It does not reconcile
disagreement, rank or merge findings across reviewers, summarize the
board, or decide anything about the architecture. Its only output is the
recorded outcome, success or explicit failure, of each independent
specialist assessment.

Both StructuredReviewModelError (the provider could not produce output at
all) and SpecialistReviewerError (it did, but the draft violates a
specialist-level application invariant, such as an unknown evidence
reference) are expected outcomes of one specialist invocation and are
captured the same way: a SpecialistReviewFailure with a stable, generic
detail. Neither is a ReviewCoordinator bug; both mean this dimension's
model output was not usable, and the board continues without it. The
public failure detail never distinguishes which of the two occurred; that
distinction, if useful, belongs in execution/evaluation metadata, not in
the architecture-review domain result.
"""

import asyncio
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from architecture_review_board.domain.models import (
    ArchitectureReviewRequest,
    ReviewEvidence,
    SpecialistReview,
    SpecialistReviewFailure,
)
from architecture_review_board.model.base import StructuredReviewModelError
from architecture_review_board.reviewers import REVIEW_DIMENSION_ORDER
from architecture_review_board.reviewers.specialist import (
    SpecialistReviewer,
    SpecialistReviewerError,
)

_UNAVAILABLE_DETAIL = "specialist review unavailable"


class CoordinatedReviews(BaseModel):
    """The outcome of running the specialist board once, before any reconciliation.

    This is not ArchitectureReviewResult: it carries no decision, no
    conditions, no disagreements, and no synthesis across reviewers. It is
    the input a future supervisor reads.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    review_id: str
    reviews: tuple[SpecialistReview, ...]
    failures: tuple[SpecialistReviewFailure, ...]

    @field_validator("review_id")
    @classmethod
    def _review_id_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("review_id must not be blank")
        return stripped

    @model_validator(mode="after")
    def _consistent(self) -> "CoordinatedReviews":
        for review in self.reviews:
            if review.review_id != self.review_id:
                raise ValueError(
                    f"specialist review for {review.reviewer} targets a different review_id"
                )

        review_dimensions = [review.reviewer for review in self.reviews]
        failure_dimensions = [failure.reviewer for failure in self.failures]

        if len(set(review_dimensions)) != len(review_dimensions):
            raise ValueError("reviews must not contain duplicate dimensions")
        if len(set(failure_dimensions)) != len(failure_dimensions):
            raise ValueError("failures must not contain duplicate dimensions")

        overlap = set(review_dimensions) & set(failure_dimensions)
        if overlap:
            raise ValueError(f"dimensions cannot both succeed and fail: {sorted(overlap)}")

        covered = set(review_dimensions) | set(failure_dimensions)
        missing = set(REVIEW_DIMENSION_ORDER) - covered
        if missing:
            raise ValueError(f"missing outcomes for dimensions: {sorted(missing)}")
        unexpected = covered - set(REVIEW_DIMENSION_ORDER)
        if unexpected:
            raise ValueError(f"unexpected dimensions in outcome: {sorted(unexpected)}")

        expected_review_order = [d for d in REVIEW_DIMENSION_ORDER if d in review_dimensions]
        if review_dimensions != expected_review_order:
            raise ValueError("reviews must be ordered canonically by dimension")

        expected_failure_order = [d for d in REVIEW_DIMENSION_ORDER if d in failure_dimensions]
        if failure_dimensions != expected_failure_order:
            raise ValueError("failures must be ordered canonically by dimension")

        return self


class ReviewCoordinator:
    """Runs the standard five-dimension specialist board concurrently.

    Every reviewer receives the same ArchitectureReviewRequest and the
    same available_evidence tuple, and nothing else: no other reviewer's
    output, no prior result, no shared mutable state. The five
    assessments are independent by construction, and the model
    implementation backing them may receive all five calls at once; see
    StructuredReviewModel for the concurrency contract that implies.
    Evidence retrieval itself does not happen here: the coordinator only
    distributes whatever snapshot its caller already resolved.

    The coordinator holds only its configured reviewers. Per-run state
    stays local to review() so one instance is safe to reuse across
    multiple, unrelated review requests.
    """

    def __init__(self, reviewers: Sequence[SpecialistReviewer]) -> None:
        by_dimension = {reviewer.reviewer: reviewer for reviewer in reviewers}
        if len(by_dimension) != len(reviewers):
            raise ValueError("reviewers must not contain duplicate dimensions")

        missing = set(REVIEW_DIMENSION_ORDER) - set(by_dimension)
        if missing:
            raise ValueError(f"missing reviewers for dimensions: {sorted(missing)}")

        unexpected = set(by_dimension) - set(REVIEW_DIMENSION_ORDER)
        if unexpected:
            raise ValueError(f"unexpected reviewer dimensions: {sorted(unexpected)}")

        self._reviewers = tuple(by_dimension[dimension] for dimension in REVIEW_DIMENSION_ORDER)

    async def review(
        self,
        request: ArchitectureReviewRequest,
        *,
        available_evidence: tuple[ReviewEvidence, ...] = (),
    ) -> CoordinatedReviews:
        outcomes: list[SpecialistReview | SpecialistReviewFailure | None]
        outcomes = [None] * len(self._reviewers)

        async def run_one(index: int, reviewer: SpecialistReviewer) -> None:
            try:
                outcomes[index] = await reviewer.review(
                    request, available_evidence=available_evidence
                )
            except (StructuredReviewModelError, SpecialistReviewerError):
                outcomes[index] = SpecialistReviewFailure(
                    reviewer=reviewer.reviewer, detail=_UNAVAILABLE_DETAIL
                )

        async with asyncio.TaskGroup() as group:
            for index, reviewer in enumerate(self._reviewers):
                group.create_task(run_one(index, reviewer))

        reviews = tuple(outcome for outcome in outcomes if isinstance(outcome, SpecialistReview))
        failures = tuple(
            outcome for outcome in outcomes if isinstance(outcome, SpecialistReviewFailure)
        )

        return CoordinatedReviews(review_id=request.review_id, reviews=reviews, failures=failures)
