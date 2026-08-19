import asyncio

import pytest

from architecture_review_board.domain.enums import FindingSeverity, ReviewDimension
from architecture_review_board.domain.models import ArchitectureReviewRequest, ReviewEvidence
from architecture_review_board.model.base import (
    SpecialistModelRequest,
    StructuredReviewModelError,
)
from architecture_review_board.model.drafts import ReviewFindingDraft, SpecialistReviewDraft
from architecture_review_board.reviewers.rubrics import (
    RELIABILITY_REVIEW_INSTRUCTIONS,
    build_reliability_reviewer,
)
from architecture_review_board.reviewers.specialist import SpecialistReviewerError


class RecordingModel:
    """Test double for StructuredReviewModel: records the request and replays a canned result."""

    def __init__(
        self,
        draft: SpecialistReviewDraft | None = None,
        error: Exception | None = None,
    ) -> None:
        self.draft = draft
        self.error = error
        self.received_request: SpecialistModelRequest | None = None

    async def generate_specialist_review(
        self, request: SpecialistModelRequest
    ) -> SpecialistReviewDraft:
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


def make_finding_draft(
    title: str, evidence_ids: tuple[str, ...] = ()
) -> ReviewFindingDraft:
    return ReviewFindingDraft(
        title=title,
        description="The proposal does not describe consumer redundancy.",
        severity=FindingSeverity.HIGH,
        rationale="No failover path is mentioned for the queue consumer.",
        recommendation="Run at least two consumer instances.",
        confidence=0.75,
        evidence_ids=evidence_ids,
    )


def test_reviewer_sends_configured_instructions_separate_from_the_proposal() -> None:
    model = RecordingModel(draft=SpecialistReviewDraft(summary="Fine.", overall_confidence=0.9))
    reviewer = build_reliability_reviewer(model)
    request = make_request()

    asyncio.run(reviewer.review(request))

    assert model.received_request is not None
    assert model.received_request.reviewer == ReviewDimension.RELIABILITY
    assert model.received_request.system_instructions == RELIABILITY_REVIEW_INSTRUCTIONS
    assert model.received_request.architecture_request == request


def test_review_maps_draft_to_specialist_review_with_deterministic_ordered_ids() -> None:
    draft = SpecialistReviewDraft(
        summary="Two reliability concerns identified.",
        overall_confidence=0.6,
        findings=(
            make_finding_draft("Unbounded retry amplification"),
            make_finding_draft("No dead-letter path for poison messages"),
        ),
    )
    model = RecordingModel(draft=draft)
    reviewer = build_reliability_reviewer(model)
    request = make_request()

    review = asyncio.run(reviewer.review(request))

    assert review.review_id == request.review_id
    assert review.reviewer == ReviewDimension.RELIABILITY
    assert review.summary == draft.summary
    assert review.overall_confidence == draft.overall_confidence
    assert [f.finding_id for f in review.findings] == ["reliability-001", "reliability-002"]
    assert [f.title for f in review.findings] == [f.title for f in draft.findings]
    assert all(f.reviewer == ReviewDimension.RELIABILITY for f in review.findings)
    assert all(f.evidence == () for f in review.findings)


def test_zero_findings_is_a_valid_review() -> None:
    model = RecordingModel(
        draft=SpecialistReviewDraft(
            summary="No material reliability issues.", overall_confidence=0.85
        )
    )
    reviewer = build_reliability_reviewer(model)

    review = asyncio.run(reviewer.review(make_request()))

    assert review.findings == ()


def test_structured_review_model_error_propagates_without_a_fallback_review() -> None:
    model = RecordingModel(error=StructuredReviewModelError("provider unavailable"))
    reviewer = build_reliability_reviewer(model)

    with pytest.raises(StructuredReviewModelError):
        asyncio.run(reviewer.review(make_request()))


def test_specialist_reviewer_maps_evidence_ids_or_rejects_unknown_reference() -> None:
    supplied_evidence = (
        ReviewEvidence(
            evidence_id="knowledge-001",
            source_type="engineering-knowledge",
            source_reference="ref",
            excerpt="Retry ceilings should be bounded per downstream dependency.",
        ),
    )
    draft = SpecialistReviewDraft(
        summary="One reliability concern identified.",
        overall_confidence=0.7,
        findings=(make_finding_draft("Unbounded retry amplification", ("knowledge-001",)),),
    )
    model = RecordingModel(draft=draft)
    reviewer = build_reliability_reviewer(model)

    review = asyncio.run(
        reviewer.review(make_request(), available_evidence=supplied_evidence)
    )

    assert review.findings[0].evidence == supplied_evidence
    assert model.received_request is not None
    assert model.received_request.available_evidence == supplied_evidence

    unknown_draft = SpecialistReviewDraft(
        summary="One reliability concern identified.",
        overall_confidence=0.7,
        findings=(make_finding_draft("Unbounded retry amplification", ("knowledge-999",)),),
    )
    unknown_model = RecordingModel(draft=unknown_draft)
    unknown_reviewer = build_reliability_reviewer(unknown_model)

    with pytest.raises(SpecialistReviewerError):
        asyncio.run(
            unknown_reviewer.review(make_request(), available_evidence=supplied_evidence)
        )
