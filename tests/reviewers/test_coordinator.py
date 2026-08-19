import asyncio

import pytest

from architecture_review_board.domain.enums import FindingSeverity, ReviewDimension
from architecture_review_board.domain.models import ArchitectureReviewRequest, ReviewEvidence
from architecture_review_board.model.base import (
    SpecialistModelRequest,
    StructuredReviewModelError,
)
from architecture_review_board.model.drafts import ReviewFindingDraft, SpecialistReviewDraft
from architecture_review_board.reviewers import REVIEW_DIMENSION_ORDER
from architecture_review_board.reviewers.coordinator import ReviewCoordinator
from architecture_review_board.reviewers.rubrics import build_specialist_reviewers


def make_request() -> ArchitectureReviewRequest:
    return ArchitectureReviewRequest(
        review_id="rev-1",
        title="Queue-based order processing",
        problem_statement="Order processing needs to absorb bursty traffic.",
        proposed_solution="Introduce a durable queue between checkout and fulfillment.",
    )


def success_draft() -> SpecialistReviewDraft:
    return SpecialistReviewDraft(summary="No material issues.", overall_confidence=0.7)


class ScriptedModel:
    """Test double for StructuredReviewModel: replays a fixed outcome per dimension."""

    def __init__(self, outcomes: dict[ReviewDimension, SpecialistReviewDraft | Exception]) -> None:
        self._outcomes = outcomes
        self.received_requests: dict[ReviewDimension, SpecialistModelRequest] = {}

    async def generate_specialist_review(
        self, request: SpecialistModelRequest
    ) -> SpecialistReviewDraft:
        self.received_requests[request.reviewer] = request
        outcome = self._outcomes[request.reviewer]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class ConcurrencyProbeModel:
    """Blocks every call at a barrier until all parties have arrived.

    A caller can only make progress past generate_specialist_review once
    every configured party has entered it, so a max_concurrent reading
    equal to `parties` deterministically proves overlapping execution
    without relying on sleep-based timing.
    """

    def __init__(self, parties: int) -> None:
        self._barrier = asyncio.Barrier(parties)
        self._active = 0
        self.max_concurrent = 0

    async def generate_specialist_review(
        self, request: SpecialistModelRequest
    ) -> SpecialistReviewDraft:
        self._active += 1
        self.max_concurrent = max(self.max_concurrent, self._active)
        await self._barrier.wait()
        self._active -= 1
        return success_draft()


class GatedModel:
    """Holds each dimension's call open until its gate is explicitly released."""

    def __init__(self) -> None:
        self.gates: dict[ReviewDimension, asyncio.Event] = {
            dimension: asyncio.Event() for dimension in REVIEW_DIMENSION_ORDER
        }
        self.completion_order: list[ReviewDimension] = []

    async def generate_specialist_review(
        self, request: SpecialistModelRequest
    ) -> SpecialistReviewDraft:
        await self.gates[request.reviewer].wait()
        self.completion_order.append(request.reviewer)
        return success_draft()


def test_build_specialist_reviewers_returns_five_in_canonical_order() -> None:
    model = ScriptedModel({dimension: success_draft() for dimension in REVIEW_DIMENSION_ORDER})
    reviewers = build_specialist_reviewers(model)

    assert [reviewer.reviewer for reviewer in reviewers] == list(REVIEW_DIMENSION_ORDER)


def test_coordinator_rejects_missing_and_duplicate_dimensions() -> None:
    model = ScriptedModel({dimension: success_draft() for dimension in REVIEW_DIMENSION_ORDER})
    reviewers = list(build_specialist_reviewers(model))

    with pytest.raises(ValueError):
        ReviewCoordinator(reviewers[:-1])

    with pytest.raises(ValueError):
        ReviewCoordinator([*reviewers, reviewers[0]])


def test_all_reviewers_receive_the_same_request_independently() -> None:
    model = ScriptedModel({dimension: success_draft() for dimension in REVIEW_DIMENSION_ORDER})
    coordinator = ReviewCoordinator(build_specialist_reviewers(model))
    request = make_request()

    asyncio.run(coordinator.review(request))

    assert set(model.received_requests) == set(REVIEW_DIMENSION_ORDER)
    for dimension, received in model.received_requests.items():
        assert received.reviewer == dimension
        assert received.architecture_request == request


def test_specialists_execute_concurrently() -> None:
    model = ConcurrencyProbeModel(parties=len(REVIEW_DIMENSION_ORDER))
    coordinator = ReviewCoordinator(build_specialist_reviewers(model))

    asyncio.run(coordinator.review(make_request()))

    assert model.max_concurrent == len(REVIEW_DIMENSION_ORDER)


def test_scrambled_completion_order_preserves_canonical_result_order() -> None:
    async def scenario() -> tuple[list[ReviewDimension], list[ReviewDimension]]:
        model = GatedModel()
        coordinator = ReviewCoordinator(build_specialist_reviewers(model))
        task = asyncio.create_task(coordinator.review(make_request()))
        await asyncio.sleep(0)

        release_order = [
            ReviewDimension.COMPLEXITY,
            ReviewDimension.RELIABILITY,
            ReviewDimension.OPERABILITY,
            ReviewDimension.SECURITY,
            ReviewDimension.DATA,
        ]
        for dimension in release_order:
            model.gates[dimension].set()
            await asyncio.sleep(0)

        result = await task
        return model.completion_order, [review.reviewer for review in result.reviews]

    completion_order, result_order = asyncio.run(scenario())

    assert completion_order == [
        ReviewDimension.COMPLEXITY,
        ReviewDimension.RELIABILITY,
        ReviewDimension.OPERABILITY,
        ReviewDimension.SECURITY,
        ReviewDimension.DATA,
    ]
    assert result_order == list(REVIEW_DIMENSION_ORDER)


def test_one_expected_failure_is_isolated_while_others_complete() -> None:
    outcomes: dict[ReviewDimension, SpecialistReviewDraft | Exception] = {
        dimension: success_draft() for dimension in REVIEW_DIMENSION_ORDER
    }
    outcomes[ReviewDimension.SECURITY] = StructuredReviewModelError("provider unavailable")
    model = ScriptedModel(outcomes)
    coordinator = ReviewCoordinator(build_specialist_reviewers(model))

    result = asyncio.run(coordinator.review(make_request()))

    assert [review.reviewer for review in result.reviews] == [
        dimension for dimension in REVIEW_DIMENSION_ORDER if dimension != ReviewDimension.SECURITY
    ]
    assert len(result.failures) == 1
    assert result.failures[0].reviewer == ReviewDimension.SECURITY


def test_multiple_expected_failures_stay_explicit_and_canonically_ordered() -> None:
    outcomes: dict[ReviewDimension, SpecialistReviewDraft | Exception] = {
        dimension: success_draft() for dimension in REVIEW_DIMENSION_ORDER
    }
    outcomes[ReviewDimension.COMPLEXITY] = StructuredReviewModelError("boom")
    outcomes[ReviewDimension.DATA] = StructuredReviewModelError("boom")
    model = ScriptedModel(outcomes)
    coordinator = ReviewCoordinator(build_specialist_reviewers(model))

    result = asyncio.run(coordinator.review(make_request()))

    assert [failure.reviewer for failure in result.failures] == [
        ReviewDimension.DATA,
        ReviewDimension.COMPLEXITY,
    ]
    assert [review.reviewer for review in result.reviews] == [
        ReviewDimension.RELIABILITY,
        ReviewDimension.SECURITY,
        ReviewDimension.OPERABILITY,
    ]


def test_unexpected_exception_propagates_instead_of_becoming_a_failure_result() -> None:
    outcomes: dict[ReviewDimension, SpecialistReviewDraft | Exception] = {
        dimension: success_draft() for dimension in REVIEW_DIMENSION_ORDER
    }
    outcomes[ReviewDimension.DATA] = TypeError("unexpected bug")
    model = ScriptedModel(outcomes)
    coordinator = ReviewCoordinator(build_specialist_reviewers(model))

    with pytest.raises(ExceptionGroup) as excinfo:
        asyncio.run(coordinator.review(make_request()))

    assert any(isinstance(exc, TypeError) for exc in excinfo.value.exceptions)


def test_coordinator_gives_every_reviewer_the_identical_evidence_snapshot() -> None:
    evidence = (
        ReviewEvidence(
            evidence_id="knowledge-001",
            source_type="engineering-knowledge",
            source_reference="ref",
            excerpt="Bounded retry budgets prevent amplification during outages.",
        ),
    )
    model = ScriptedModel({dimension: success_draft() for dimension in REVIEW_DIMENSION_ORDER})
    coordinator = ReviewCoordinator(build_specialist_reviewers(model))

    asyncio.run(coordinator.review(make_request(), available_evidence=evidence))

    assert set(model.received_requests) == set(REVIEW_DIMENSION_ORDER)
    for received in model.received_requests.values():
        assert received.available_evidence == evidence


def test_invalid_evidence_reference_is_isolated_as_a_specialist_failure() -> None:
    shared_evidence = (
        ReviewEvidence(
            evidence_id="knowledge-001",
            source_type="engineering-knowledge",
            source_reference="ref",
            excerpt="Bounded retry budgets prevent amplification during outages.",
        ),
    )
    invalid_finding = ReviewFindingDraft(
        title="Unbounded retry amplification",
        description="The proposal does not describe consumer redundancy.",
        severity=FindingSeverity.HIGH,
        rationale="No failover path is mentioned for the queue consumer.",
        confidence=0.7,
        evidence_ids=("knowledge-999",),
    )
    outcomes: dict[ReviewDimension, SpecialistReviewDraft | Exception] = {
        dimension: success_draft() for dimension in REVIEW_DIMENSION_ORDER
    }
    outcomes[ReviewDimension.SECURITY] = SpecialistReviewDraft(
        summary="One security concern identified.",
        overall_confidence=0.7,
        findings=(invalid_finding,),
    )
    model = ScriptedModel(outcomes)
    coordinator = ReviewCoordinator(build_specialist_reviewers(model))

    result = asyncio.run(coordinator.review(make_request(), available_evidence=shared_evidence))

    assert [review.reviewer for review in result.reviews] == [
        dimension for dimension in REVIEW_DIMENSION_ORDER if dimension != ReviewDimension.SECURITY
    ]
    assert len(result.failures) == 1
    assert result.failures[0].reviewer == ReviewDimension.SECURITY
    assert result.failures[0].detail == "specialist review unavailable"
    assert all(finding.evidence == () for review in result.reviews for finding in review.findings)
