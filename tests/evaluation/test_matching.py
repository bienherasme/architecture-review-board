from architecture_review_board.domain.enums import FindingSeverity, ReviewDimension
from architecture_review_board.domain.models import (
    ReviewDisagreement,
    ReviewerPosition,
    ReviewFinding,
    SpecialistReview,
)
from architecture_review_board.evaluation.matching import (
    match_expected_disagreement,
    match_expected_risk,
)
from architecture_review_board.evaluation.models import ExpectedDisagreement, ExpectedRisk


def make_finding(
    finding_id: str, reviewer: ReviewDimension, title: str, description: str
) -> ReviewFinding:
    return ReviewFinding(
        finding_id=finding_id,
        reviewer=reviewer,
        title=title,
        description=description,
        severity=FindingSeverity.HIGH,
        rationale="Rationale text.",
        confidence=0.7,
    )


def test_match_expected_risk_requires_all_anchor_groups_and_dimension_ownership() -> None:
    reliability_finding = make_finding(
        "reliability-001",
        ReviewDimension.RELIABILITY,
        "Single-instance payment state",
        "A crash of the one instance loses in-flight authorization state.",
    )
    security_finding = make_finding(
        "security-001",
        ReviewDimension.SECURITY,
        "Single instance also exposes an admin port",
        "Unrelated security concern that happens to mention single instance too.",
    )
    reviews = (
        SpecialistReview(
            review_id="rev-1",
            reviewer=ReviewDimension.RELIABILITY,
            summary="s",
            findings=(reliability_finding,),
            overall_confidence=0.7,
        ),
        SpecialistReview(
            review_id="rev-1",
            reviewer=ReviewDimension.SECURITY,
            summary="s",
            findings=(security_finding,),
            overall_confidence=0.7,
        ),
    )

    risk = ExpectedRisk(
        risk_id="r-1",
        reviewer=ReviewDimension.RELIABILITY,
        anchor_groups=(("single instance", "single node"), ("crash", "failure")),
    )
    matched = match_expected_risk(risk, reviews)
    assert matched is not None
    assert matched.finding_id == "reliability-001"

    unsatisfied_risk = ExpectedRisk(
        risk_id="r-2",
        reviewer=ReviewDimension.RELIABILITY,
        anchor_groups=(("single instance",), ("no evidence of this phrase",)),
    )
    assert match_expected_risk(unsatisfied_risk, reviews) is None

    wrong_dimension_risk = ExpectedRisk(
        risk_id="r-3",
        reviewer=ReviewDimension.DATA,
        anchor_groups=(("single instance",),),
    )
    assert match_expected_risk(wrong_dimension_risk, reviews) is None


def test_match_expected_disagreement_requires_all_reviewers_and_anchor_groups() -> None:
    disagreement = ReviewDisagreement(
        disagreement_id="disagreement-001",
        topic="Synchronous cross-region replication",
        positions=(
            ReviewerPosition(
                reviewer=ReviewDimension.RELIABILITY,
                position="Synchronous replication is required for correctness.",
            ),
            ReviewerPosition(
                reviewer=ReviewDimension.COMPLEXITY,
                position="Synchronous replication adds unjustified operational cost.",
            ),
        ),
    )

    matching = ExpectedDisagreement(
        disagreement_id="expected-1",
        reviewers=(ReviewDimension.RELIABILITY, ReviewDimension.COMPLEXITY),
        anchor_groups=(("synchronous", "sync replication"),),
    )
    assert match_expected_disagreement(matching, (disagreement,)) is disagreement

    wrong_reviewers = ExpectedDisagreement(
        disagreement_id="expected-2",
        reviewers=(ReviewDimension.SECURITY, ReviewDimension.DATA),
    )
    assert match_expected_disagreement(wrong_reviewers, (disagreement,)) is None

    unsatisfied_anchor = ExpectedDisagreement(
        disagreement_id="expected-3",
        reviewers=(ReviewDimension.RELIABILITY, ReviewDimension.COMPLEXITY),
        anchor_groups=(("phrase not present anywhere",),),
    )
    assert match_expected_disagreement(unsatisfied_anchor, (disagreement,)) is None
