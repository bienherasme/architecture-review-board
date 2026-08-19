"""Application service that runs one specialist's independent review."""

from architecture_review_board.domain.enums import ReviewDimension
from architecture_review_board.domain.models import (
    ArchitectureReviewRequest,
    ReviewEvidence,
    ReviewFinding,
    SpecialistReview,
)
from architecture_review_board.model.base import SpecialistModelRequest, StructuredReviewModel
from architecture_review_board.model.drafts import ReviewFindingDraft


class SpecialistReviewerError(Exception):
    """A structurally valid specialist draft violates a specialist-level application invariant.

    Currently raised when a finding cites an evidence_id absent from the
    available_evidence it was actually supplied, which
    SpecialistReviewDraft's own validation alone cannot catch: only the
    reviewer, which knows both the draft and the evidence snapshot it
    sent, can. Distinct from StructuredReviewModelError, which means the
    provider failed to produce usable structured output at all; this
    means it did, but the content itself is invalid.

    ReviewCoordinator treats this the same way as
    StructuredReviewModelError: an expected outcome of one specialist
    invocation, captured into a SpecialistReviewFailure rather than
    aborting the board. Both causes collapse into the same generic public
    detail; a caller that needs to distinguish "provider unavailable" from
    "invalid model output" needs execution/evaluation metadata, not this
    domain result.
    """


class SpecialistReviewer:
    """Turns a model's structured draft into a domain SpecialistReview.

    reviewer identity is owned here, not by the model: ReviewFindingDraft
    has no reviewer field, so a draft can never claim a dimension other
    than the one this reviewer is configured for. Finding IDs are also
    assigned here, deterministically from draft order, rather than
    requested from the model or generated randomly. Evidence objects are
    likewise owned here: the model may only reference evidence_ids from
    the snapshot it was given, never invent a ReviewEvidence itself.

    Receives only the ArchitectureReviewRequest and the shared evidence
    snapshot under review; it has no access to other specialists' output,
    supervisor state, or prior reviewer runs, so it can later run
    independently and concurrently with the other dimensions.
    """

    def __init__(
        self,
        model: StructuredReviewModel,
        reviewer: ReviewDimension,
        system_instructions: str,
    ) -> None:
        self._model = model
        self._reviewer = reviewer
        self._system_instructions = system_instructions

    @property
    def reviewer(self) -> ReviewDimension:
        return self._reviewer

    async def review(
        self,
        request: ArchitectureReviewRequest,
        *,
        available_evidence: tuple[ReviewEvidence, ...] = (),
    ) -> SpecialistReview:
        model_request = SpecialistModelRequest(
            reviewer=self._reviewer,
            system_instructions=self._system_instructions,
            architecture_request=request,
            available_evidence=available_evidence,
        )
        draft = await self._model.generate_specialist_review(model_request)

        evidence_by_id = {item.evidence_id: item for item in available_evidence}
        findings = tuple(
            self._stamp_finding(draft_finding, index, evidence_by_id)
            for index, draft_finding in enumerate(draft.findings, start=1)
        )

        return SpecialistReview(
            review_id=request.review_id,
            reviewer=self._reviewer,
            summary=draft.summary,
            findings=findings,
            overall_confidence=draft.overall_confidence,
        )

    def _stamp_finding(
        self,
        draft: ReviewFindingDraft,
        index: int,
        evidence_by_id: dict[str, ReviewEvidence],
    ) -> ReviewFinding:
        unknown_ids = [
            evidence_id for evidence_id in draft.evidence_ids if evidence_id not in evidence_by_id
        ]
        if unknown_ids:
            raise SpecialistReviewerError(
                f"finding '{draft.title}' cites evidence outside the supplied snapshot: "
                f"{unknown_ids}"
            )

        return ReviewFinding(
            finding_id=f"{self._reviewer.value}-{index:03d}",
            reviewer=self._reviewer,
            title=draft.title,
            description=draft.description,
            severity=draft.severity,
            rationale=draft.rationale,
            recommendation=draft.recommendation,
            confidence=draft.confidence,
            evidence=tuple(evidence_by_id[evidence_id] for evidence_id in draft.evidence_ids),
        )
