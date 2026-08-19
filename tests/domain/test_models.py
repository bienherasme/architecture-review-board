import pytest
from pydantic import ValidationError

from architecture_review_board.domain.enums import (
    REVIEW_DIMENSION_ORDER,
    ArchitectureDecision,
    FindingSeverity,
    ReviewDimension,
    ReviewEvidenceStatus,
)
from architecture_review_board.domain.models import (
    ArchitectureReviewRequest,
    ArchitectureReviewResult,
    ReviewCondition,
    ReviewDisagreement,
    ReviewerPosition,
    ReviewEvidence,
    ReviewEvidenceSearchResult,
    ReviewFinding,
    SpecialistReview,
    SpecialistReviewFailure,
)


def make_finding(
    finding_id: str = "f-1",
    reviewer: ReviewDimension = ReviewDimension.SECURITY,
    confidence: float = 0.8,
    evidence: tuple[ReviewEvidence, ...] = (),
) -> ReviewFinding:
    return ReviewFinding(
        finding_id=finding_id,
        reviewer=reviewer,
        title="Unbounded fan-out on retry",
        description="Retries are not capped, which can amplify load during an outage.",
        severity=FindingSeverity.HIGH,
        rationale="No backoff or retry ceiling is described in the proposal.",
        recommendation="Add bounded exponential backoff.",
        confidence=confidence,
        evidence=evidence,
    )


def make_review(
    reviewer: ReviewDimension,
    findings: tuple[ReviewFinding, ...] = (),
    review_id: str = "rev-1",
) -> SpecialistReview:
    return SpecialistReview(
        review_id=review_id,
        reviewer=reviewer,
        summary=f"{reviewer.value} review.",
        findings=findings,
        overall_confidence=0.8,
    )


def make_full_board(
    overrides: dict[ReviewDimension, SpecialistReview] | None = None,
    review_id: str = "rev-1",
) -> tuple[SpecialistReview, ...]:
    overrides = overrides or {}
    return tuple(
        overrides.get(dimension, make_review(dimension, review_id=review_id))
        for dimension in REVIEW_DIMENSION_ORDER
    )


def test_architecture_review_request_normalizes_and_requires_fields() -> None:
    request = ArchitectureReviewRequest(
        review_id="rev-1",
        title="  Payments retry redesign  ",
        problem_statement="Retries overwhelm downstream services during incidents.",
        proposed_solution="Introduce bounded exponential backoff with jitter.",
        constraints=("  must ship this quarter  ",),
    )
    assert request.title == "Payments retry redesign"
    assert request.constraints == ("must ship this quarter",)

    with pytest.raises(ValidationError):
        ArchitectureReviewRequest(
            review_id="rev-1",
            title="   ",
            problem_statement="x",
            proposed_solution="y",
        )


def test_specialist_review_rejects_findings_from_other_reviewers() -> None:
    with pytest.raises(ValidationError):
        SpecialistReview(
            review_id="rev-1",
            reviewer=ReviewDimension.RELIABILITY,
            summary="Reliability review of the proposal.",
            findings=(make_finding(reviewer=ReviewDimension.SECURITY),),
            overall_confidence=0.7,
        )


def test_confidence_bounds_are_enforced() -> None:
    with pytest.raises(ValidationError):
        make_finding(confidence=1.5)

    with pytest.raises(ValidationError):
        SpecialistReview(
            review_id="rev-1",
            reviewer=ReviewDimension.SECURITY,
            summary="Security review.",
            findings=(),
            overall_confidence=-0.1,
        )


def test_disagreement_requires_at_least_two_unique_reviewer_positions() -> None:
    single_position = ReviewerPosition(reviewer=ReviewDimension.SECURITY, position="Needs mTLS.")
    with pytest.raises(ValidationError):
        ReviewDisagreement(
            disagreement_id="d-1", topic="Transport security", positions=(single_position,)
        )

    duplicate_reviewer = (
        single_position,
        ReviewerPosition(reviewer=ReviewDimension.SECURITY, position="mTLS is optional here."),
    )
    with pytest.raises(ValidationError):
        ReviewDisagreement(
            disagreement_id="d-1", topic="Transport security", positions=duplicate_reviewer
        )

    valid = ReviewDisagreement(
        disagreement_id="d-1",
        topic="Transport security",
        positions=(
            single_position,
            ReviewerPosition(
                reviewer=ReviewDimension.COMPLEXITY, position="mTLS adds operational cost."
            ),
        ),
    )
    assert valid.resolution is None


def test_approve_rejects_conditions_and_blocking_findings() -> None:
    finding = make_finding(reviewer=ReviewDimension.SECURITY)
    reviews = make_full_board(
        overrides={ReviewDimension.SECURITY: make_review(ReviewDimension.SECURITY, (finding,))}
    )
    with pytest.raises(ValidationError):
        ArchitectureReviewResult(
            review_id="rev-1",
            decision=ArchitectureDecision.APPROVE,
            summary="Looks fine.",
            specialist_reviews=reviews,
            blocking_finding_ids=(finding.finding_id,),
        )


def test_approve_with_conditions_requires_a_condition() -> None:
    with pytest.raises(ValidationError):
        ArchitectureReviewResult(
            review_id="rev-1",
            decision=ArchitectureDecision.APPROVE_WITH_CONDITIONS,
            summary="Proceed if conditions are met.",
            specialist_reviews=make_full_board(),
        )


def test_request_changes_requires_a_known_blocking_finding() -> None:
    finding = make_finding(reviewer=ReviewDimension.SECURITY)
    reviews = make_full_board(
        overrides={ReviewDimension.SECURITY: make_review(ReviewDimension.SECURITY, (finding,))}
    )
    with pytest.raises(ValidationError):
        ArchitectureReviewResult(
            review_id="rev-1",
            decision=ArchitectureDecision.REQUEST_CHANGES,
            summary="Needs rework.",
            specialist_reviews=reviews,
        )

    result = ArchitectureReviewResult(
        review_id="rev-1",
        decision=ArchitectureDecision.REQUEST_CHANGES,
        summary="Needs rework.",
        specialist_reviews=reviews,
        blocking_finding_ids=(finding.finding_id,),
    )
    assert result.blocking_finding_ids == (finding.finding_id,)


def test_unknown_finding_references_are_rejected() -> None:
    reviews = make_full_board(
        overrides={
            ReviewDimension.SECURITY: make_review(ReviewDimension.SECURITY, (make_finding(),))
        }
    )

    with pytest.raises(ValidationError):
        ArchitectureReviewResult(
            review_id="rev-1",
            decision=ArchitectureDecision.REQUEST_CHANGES,
            summary="Needs rework.",
            specialist_reviews=reviews,
            blocking_finding_ids=("nonexistent-finding",),
        )

    with pytest.raises(ValidationError):
        ArchitectureReviewResult(
            review_id="rev-1",
            decision=ArchitectureDecision.APPROVE_WITH_CONDITIONS,
            summary="Proceed if conditions are met.",
            specialist_reviews=reviews,
            conditions=(
                ReviewCondition(
                    condition_id="c-1",
                    description="Address the retry issue.",
                    related_finding_ids=("nonexistent-finding",),
                ),
            ),
        )


def test_duplicate_finding_id_across_specialist_reviews_is_rejected() -> None:
    reviews = make_full_board(
        overrides={
            ReviewDimension.SECURITY: make_review(
                ReviewDimension.SECURITY,
                (make_finding(finding_id="f-1", reviewer=ReviewDimension.SECURITY),),
            ),
            ReviewDimension.RELIABILITY: make_review(
                ReviewDimension.RELIABILITY,
                (make_finding(finding_id="f-1", reviewer=ReviewDimension.RELIABILITY),),
            ),
        }
    )
    with pytest.raises(ValidationError):
        ArchitectureReviewResult(
            review_id="rev-1",
            decision=ArchitectureDecision.APPROVE,
            summary="Looks fine.",
            specialist_reviews=reviews,
        )


def test_disagreement_position_referencing_unknown_finding_is_rejected() -> None:
    finding = make_finding(reviewer=ReviewDimension.SECURITY)
    reviews = make_full_board(
        overrides={ReviewDimension.SECURITY: make_review(ReviewDimension.SECURITY, (finding,))}
    )
    disagreement = ReviewDisagreement(
        disagreement_id="d-1",
        topic="Retry ceiling",
        positions=(
            ReviewerPosition(
                reviewer=ReviewDimension.SECURITY,
                position="This must block approval.",
                related_finding_ids=("nonexistent-finding",),
            ),
            ReviewerPosition(reviewer=ReviewDimension.COMPLEXITY, position="This is acceptable."),
        ),
    )
    with pytest.raises(ValidationError):
        ArchitectureReviewResult(
            review_id="rev-1",
            decision=ArchitectureDecision.APPROVE,
            summary="Looks fine.",
            specialist_reviews=reviews,
            disagreements=(disagreement,),
        )


def test_duplicate_specialist_review_dimension_is_rejected() -> None:
    first = SpecialistReview(
        review_id="rev-1",
        reviewer=ReviewDimension.SECURITY,
        summary="First security pass.",
        findings=(),
        overall_confidence=0.8,
    )
    second = SpecialistReview(
        review_id="rev-1",
        reviewer=ReviewDimension.SECURITY,
        summary="Second security pass.",
        findings=(),
        overall_confidence=0.7,
    )
    with pytest.raises(ValidationError):
        ArchitectureReviewResult(
            review_id="rev-1",
            decision=ArchitectureDecision.APPROVE,
            summary="Looks fine.",
            specialist_reviews=(first, second),
        )


def test_specialist_coverage_must_be_complete_and_approve_requires_no_failures() -> None:
    incomplete_reviews = make_full_board()[:-1]
    with pytest.raises(ValidationError):
        ArchitectureReviewResult(
            review_id="rev-1",
            decision=ArchitectureDecision.APPROVE,
            summary="Looks fine.",
            specialist_reviews=incomplete_reviews,
        )

    failure = SpecialistReviewFailure(
        reviewer=ReviewDimension.COMPLEXITY, detail="specialist review unavailable"
    )
    with pytest.raises(ValidationError):
        ArchitectureReviewResult(
            review_id="rev-1",
            decision=ArchitectureDecision.APPROVE,
            summary="Looks fine.",
            specialist_reviews=incomplete_reviews,
            specialist_failures=(failure,),
        )

    result = ArchitectureReviewResult(
        review_id="rev-1",
        decision=ArchitectureDecision.APPROVE_WITH_CONDITIONS,
        summary="Proceed once complexity coverage is restored.",
        specialist_reviews=incomplete_reviews,
        specialist_failures=(failure,),
        conditions=(ReviewCondition(condition_id="c-1", description="Re-run complexity review."),),
    )
    assert result.specialist_failures == (failure,)


def test_disagreement_position_ownership_is_enforced() -> None:
    security_finding = make_finding(finding_id="security-001", reviewer=ReviewDimension.SECURITY)
    reviews = make_full_board(
        overrides={
            ReviewDimension.SECURITY: make_review(
                ReviewDimension.SECURITY, (security_finding,)
            )
        }
    )

    misattributed = ReviewDisagreement(
        disagreement_id="d-1",
        topic="Retry ceiling",
        positions=(
            ReviewerPosition(
                reviewer=ReviewDimension.COMPLEXITY,
                position="This is unjustified complexity.",
                related_finding_ids=(security_finding.finding_id,),
            ),
            ReviewerPosition(reviewer=ReviewDimension.RELIABILITY, position="This is required."),
        ),
    )
    with pytest.raises(ValidationError):
        ArchitectureReviewResult(
            review_id="rev-1",
            decision=ArchitectureDecision.APPROVE_WITH_CONDITIONS,
            summary="Tradeoff noted.",
            specialist_reviews=reviews,
            conditions=(ReviewCondition(condition_id="c-1", description="Resolve tradeoff."),),
            disagreements=(misattributed,),
        )

    failed_reviews = make_full_board()[:-1]
    failure = SpecialistReviewFailure(
        reviewer=ReviewDimension.COMPLEXITY, detail="specialist review unavailable"
    )
    position_for_failed_reviewer = ReviewDisagreement(
        disagreement_id="d-2",
        topic="Retry ceiling",
        positions=(
            ReviewerPosition(reviewer=ReviewDimension.COMPLEXITY, position="Fabricated stance."),
            ReviewerPosition(reviewer=ReviewDimension.RELIABILITY, position="This is required."),
        ),
    )
    with pytest.raises(ValidationError):
        ArchitectureReviewResult(
            review_id="rev-1",
            decision=ArchitectureDecision.APPROVE_WITH_CONDITIONS,
            summary="Tradeoff noted.",
            specialist_reviews=failed_reviews,
            specialist_failures=(failure,),
            conditions=(ReviewCondition(condition_id="c-1", description="Resolve tradeoff."),),
            disagreements=(position_for_failed_reviewer,),
        )


def test_specialist_coverage_must_be_canonically_ordered() -> None:
    board = make_full_board()
    reordered_reviews = (board[1], board[0], board[2], board[3], board[4])
    with pytest.raises(ValidationError):
        ArchitectureReviewResult(
            review_id="rev-1",
            decision=ArchitectureDecision.APPROVE,
            summary="Looks fine.",
            specialist_reviews=reordered_reviews,
        )

    reviews = (
        make_review(ReviewDimension.RELIABILITY),
        make_review(ReviewDimension.DATA),
        make_review(ReviewDimension.COMPLEXITY),
    )
    security_failure = SpecialistReviewFailure(
        reviewer=ReviewDimension.SECURITY, detail="specialist review unavailable"
    )
    operability_failure = SpecialistReviewFailure(
        reviewer=ReviewDimension.OPERABILITY, detail="specialist review unavailable"
    )
    condition = ReviewCondition(condition_id="c-1", description="Re-run missing reviews.")

    with pytest.raises(ValidationError):
        ArchitectureReviewResult(
            review_id="rev-1",
            decision=ArchitectureDecision.APPROVE_WITH_CONDITIONS,
            summary="Proceed conditionally.",
            specialist_reviews=reviews,
            specialist_failures=(operability_failure, security_failure),
            conditions=(condition,),
        )

    result = ArchitectureReviewResult(
        review_id="rev-1",
        decision=ArchitectureDecision.APPROVE_WITH_CONDITIONS,
        summary="Proceed conditionally.",
        specialist_reviews=reviews,
        specialist_failures=(security_failure, operability_failure),
        conditions=(condition,),
    )
    assert [review.reviewer for review in result.specialist_reviews] == [
        ReviewDimension.RELIABILITY,
        ReviewDimension.DATA,
        ReviewDimension.COMPLEXITY,
    ]
    assert [failure.reviewer for failure in result.specialist_failures] == [
        ReviewDimension.SECURITY,
        ReviewDimension.OPERABILITY,
    ]


def test_evidence_context_consistency_is_enforced() -> None:
    shared_evidence = ReviewEvidence(
        evidence_id="knowledge-001",
        source_type="engineering-knowledge",
        source_reference="ref",
        excerpt="text",
    )
    success_context = ReviewEvidenceSearchResult(
        status=ReviewEvidenceStatus.SUCCESS, evidence=(shared_evidence,)
    )
    finding_with_shared_evidence = make_finding(
        finding_id="security-001", reviewer=ReviewDimension.SECURITY, evidence=(shared_evidence,)
    )
    reviews_with_cited_evidence = make_full_board(
        overrides={
            ReviewDimension.SECURITY: make_review(
                ReviewDimension.SECURITY, (finding_with_shared_evidence,)
            )
        }
    )

    valid_result = ArchitectureReviewResult(
        review_id="rev-1",
        decision=ArchitectureDecision.APPROVE,
        summary="Looks fine.",
        specialist_reviews=reviews_with_cited_evidence,
        evidence_context=success_context,
    )
    assert valid_result.evidence_context == success_context

    outside_evidence = ReviewEvidence(
        evidence_id="knowledge-999",
        source_type="engineering-knowledge",
        source_reference="other-ref",
        excerpt="other text",
    )
    finding_with_unlisted_evidence = make_finding(
        finding_id="security-001", reviewer=ReviewDimension.SECURITY, evidence=(outside_evidence,)
    )
    reviews_with_unlisted_evidence = make_full_board(
        overrides={
            ReviewDimension.SECURITY: make_review(
                ReviewDimension.SECURITY, (finding_with_unlisted_evidence,)
            )
        }
    )
    with pytest.raises(ValidationError):
        ArchitectureReviewResult(
            review_id="rev-1",
            decision=ArchitectureDecision.APPROVE,
            summary="Looks fine.",
            specialist_reviews=reviews_with_unlisted_evidence,
            evidence_context=success_context,
        )

    with pytest.raises(ValidationError):
        ArchitectureReviewResult(
            review_id="rev-1",
            decision=ArchitectureDecision.APPROVE,
            summary="Looks fine.",
            specialist_reviews=reviews_with_cited_evidence,
            evidence_context=None,
        )
