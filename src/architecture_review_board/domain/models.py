"""Domain contracts for architecture review requests, findings, and results.

Trust boundary: ArchitectureReviewRequest and ReviewEvidence carry content
supplied by proposal authors and external sources. That content is data,
never instructions. No command execution happens anywhere in this module.
A future LLM-backed reviewer must keep this data separate from its own
system instructions; this module only establishes the boundary, it does
not enforce prompt-level security.

These models validate structural consistency (which reviewer a finding
belongs to, which finding IDs a condition may reference, and so on). They
do not decide what a review's outcome should be; that reasoning belongs to
a future supervisor component.
"""

from pydantic import BaseModel, ConfigDict, ValidationInfo, field_validator, model_validator

from architecture_review_board.domain.enums import (
    REVIEW_DIMENSION_ORDER,
    ArchitectureDecision,
    FindingSeverity,
    ReviewDimension,
    ReviewEvidenceStatus,
)

DEFAULT_EVIDENCE_RESULTS = 8
MAX_EVIDENCE_RESULTS = 12
MAX_EVIDENCE_QUERY_CHARS = 2000


def _clean_str(value: str, field_name: str | None) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name or 'field'} must not be blank")
    return stripped


def _clean_str_tuple(values: tuple[str, ...], field_name: str | None) -> tuple[str, ...]:
    return tuple(_clean_str(value, field_name) for value in values)


class ArchitectureReviewRequest(BaseModel):
    """A proposal submitted for architecture review.

    All text fields are untrusted, proposal-author-supplied content.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    review_id: str
    title: str
    problem_statement: str
    proposed_solution: str
    constraints: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    alternatives_considered: tuple[str, ...] = ()
    affected_components: tuple[str, ...] = ()

    @field_validator("review_id", "title", "problem_statement", "proposed_solution")
    @classmethod
    def _required_not_blank(cls, value: str, info: ValidationInfo) -> str:
        return _clean_str(value, info.field_name)

    @field_validator(
        "constraints", "assumptions", "alternatives_considered", "affected_components"
    )
    @classmethod
    def _entries_not_blank(cls, value: tuple[str, ...], info: ValidationInfo) -> tuple[str, ...]:
        return _clean_str_tuple(value, info.field_name)


class ReviewEvidence(BaseModel):
    """A pointer to material supporting a finding.

    source_type is an open string ("proposal", "engineering-knowledge",
    "policy", ...) rather than a closed enum, so future evidence sources
    can be added without changing this model. source_reference is an
    opaque, provider-owned reference; this model does not interpret it.
    Excerpt content is untrusted, same as the review request.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str
    source_type: str
    source_reference: str
    excerpt: str

    @field_validator("evidence_id", "source_type", "source_reference", "excerpt")
    @classmethod
    def _not_blank(cls, value: str, info: ValidationInfo) -> str:
        return _clean_str(value, info.field_name)


class ReviewEvidenceQuery(BaseModel):
    """A bounded request for external review evidence.

    Deliberately just a query string and a result cap. Which retrieval
    strategy an evidence provider uses (lexical, vector, hybrid) is that
    provider's own implementation concern, not something ARB chooses
    here. Building the query text from proposal facts is the caller's
    job, not this model's.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str
    max_results: int = DEFAULT_EVIDENCE_RESULTS

    @field_validator("query")
    @classmethod
    def _query_bounded(cls, value: str) -> str:
        stripped = _clean_str(value, "query")
        if len(stripped) > MAX_EVIDENCE_QUERY_CHARS:
            raise ValueError(f"query must be at most {MAX_EVIDENCE_QUERY_CHARS} characters")
        return stripped

    @field_validator("max_results")
    @classmethod
    def _max_results_bounded(cls, value: int) -> int:
        if not 1 <= value <= MAX_EVIDENCE_RESULTS:
            raise ValueError(f"max_results must be between 1 and {MAX_EVIDENCE_RESULTS}")
        return value


class ReviewEvidenceSearchResult(BaseModel):
    """The recorded outcome of one external evidence search.

    status and evidence are not independent: each status implies exactly
    one shape, enforced below. UNAVAILABLE belongs to this result, not to
    the evidence provider port, which raises ReviewEvidenceUnavailableError
    for an expected external failure instead of returning this status
    directly; the caller that catches that error is what turns it into a
    result carrying UNAVAILABLE. Whether the external dependency ran at
    all, and what a review result records as its outcome, are different
    facts kept in different places.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: ReviewEvidenceStatus
    evidence: tuple[ReviewEvidence, ...] = ()
    detail: str | None = None

    @field_validator("detail")
    @classmethod
    def _detail_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _clean_str(value, "detail")

    @model_validator(mode="after")
    def _status_shape(self) -> "ReviewEvidenceSearchResult":
        if self.status == ReviewEvidenceStatus.SUCCESS and not self.evidence:
            raise ValueError("SUCCESS requires at least one evidence item")
        if self.status != ReviewEvidenceStatus.SUCCESS and self.evidence:
            raise ValueError(f"{self.status.value} requires empty evidence")
        return self


class ReviewFinding(BaseModel):
    """A single structured observation produced by one specialist reviewer.

    confidence is the reviewer's certainty in this specific finding. It is
    not a probability of architecture failure, not a severity measure, and
    not a decision weight; it must never be combined with severity into a
    numeric score.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    finding_id: str
    reviewer: ReviewDimension
    title: str
    description: str
    severity: FindingSeverity
    rationale: str
    recommendation: str | None = None
    confidence: float
    evidence: tuple[ReviewEvidence, ...] = ()

    @field_validator("finding_id", "title", "description", "rationale")
    @classmethod
    def _required_not_blank(cls, value: str, info: ValidationInfo) -> str:
        return _clean_str(value, info.field_name)

    @field_validator("recommendation")
    @classmethod
    def _recommendation_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _clean_str(value, "recommendation")

    @field_validator("confidence")
    @classmethod
    def _confidence_in_range(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        return value


class SpecialistReview(BaseModel):
    """The structured output of one specialist reviewing one proposal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    review_id: str
    reviewer: ReviewDimension
    summary: str
    findings: tuple[ReviewFinding, ...] = ()
    overall_confidence: float

    @field_validator("review_id", "summary")
    @classmethod
    def _required_not_blank(cls, value: str, info: ValidationInfo) -> str:
        return _clean_str(value, info.field_name)

    @field_validator("overall_confidence")
    @classmethod
    def _confidence_in_range(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("overall_confidence must be between 0.0 and 1.0")
        return value

    @model_validator(mode="after")
    def _findings_belong_to_reviewer(self) -> "SpecialistReview":
        mismatched = [f.finding_id for f in self.findings if f.reviewer != self.reviewer]
        if mismatched:
            raise ValueError(
                f"findings must be authored by {self.reviewer}: mismatched finding_ids {mismatched}"
            )
        return self


class SpecialistReviewFailure(BaseModel):
    """An explicit, non-fabricated outcome for a dimension whose specialist call failed.

    detail is a stable, application-owned message, never the raw provider
    exception, HTTP body, model identifier, or traceback: those belong to
    logging and observability, not to this domain-visible result. A failed
    dimension is a coverage gap, not an architecture finding, and must not
    be turned into a ReviewFinding.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    reviewer: ReviewDimension
    detail: str

    @field_validator("detail")
    @classmethod
    def _detail_not_blank(cls, value: str) -> str:
        return _clean_str(value, "detail")


class ReviewCondition(BaseModel):
    """A condition that must be satisfied for an APPROVE_WITH_CONDITIONS decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    condition_id: str
    description: str
    related_finding_ids: tuple[str, ...] = ()

    @field_validator("condition_id", "description")
    @classmethod
    def _required_not_blank(cls, value: str, info: ValidationInfo) -> str:
        return _clean_str(value, info.field_name)

    @field_validator("related_finding_ids")
    @classmethod
    def _entries_not_blank(cls, value: tuple[str, ...], info: ValidationInfo) -> tuple[str, ...]:
        return _clean_str_tuple(value, info.field_name)


class ReviewerPosition(BaseModel):
    """One reviewer's stance within a disagreement."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reviewer: ReviewDimension
    position: str
    related_finding_ids: tuple[str, ...] = ()

    @field_validator("position")
    @classmethod
    def _required_not_blank(cls, value: str, info: ValidationInfo) -> str:
        return _clean_str(value, info.field_name)

    @field_validator("related_finding_ids")
    @classmethod
    def _entries_not_blank(cls, value: tuple[str, ...], info: ValidationInfo) -> tuple[str, ...]:
        return _clean_str_tuple(value, info.field_name)


class ReviewDisagreement(BaseModel):
    """Explicit, unreconciled disagreement between two or more reviewers.

    resolution may remain None: the supervisor is not required to force a
    single truth onto every disagreement it surfaces.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    disagreement_id: str
    topic: str
    positions: tuple[ReviewerPosition, ...]
    resolution: str | None = None

    @field_validator("disagreement_id", "topic")
    @classmethod
    def _required_not_blank(cls, value: str, info: ValidationInfo) -> str:
        return _clean_str(value, info.field_name)

    @field_validator("resolution")
    @classmethod
    def _resolution_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _clean_str(value, "resolution")

    @model_validator(mode="after")
    def _positions_valid(self) -> "ReviewDisagreement":
        if len(self.positions) < 2:
            raise ValueError("a disagreement requires at least two reviewer positions")
        reviewers = [position.reviewer for position in self.positions]
        if len(set(reviewers)) != len(reviewers):
            raise ValueError("each reviewer may hold only one position within a disagreement")
        return self


class ArchitectureReviewResult(BaseModel):
    """The final, structured outcome of an architecture review.

    This model validates internal consistency only. It does not derive
    decision from findings; that policy belongs to the supervisor.

    Architecture Review Board is defined as five specialist dimensions, so
    a result silently missing one (neither a review nor a recorded
    failure) is not a complete board outcome: specialist_reviews and
    specialist_failures together must cover every canonical dimension
    exactly once, each in REVIEW_DIMENSION_ORDER's relative order. This is
    public, immutable state, so out-of-order input is rejected rather than
    silently re-sorted; the producing application is expected to construct
    it deterministically in the first place.

    evidence_context records whether external evidence retrieval ran and
    what it found; it is observability/provenance state, not decision
    input, and never changes what a valid decision is. When it reports a
    successful search, every finding's cited evidence must come from that
    same shared snapshot; otherwise no finding may carry evidence at all,
    since v0.1.0 has no other materialized evidence source.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    review_id: str
    decision: ArchitectureDecision
    summary: str
    specialist_reviews: tuple[SpecialistReview, ...]
    specialist_failures: tuple[SpecialistReviewFailure, ...] = ()
    disagreements: tuple[ReviewDisagreement, ...] = ()
    conditions: tuple[ReviewCondition, ...] = ()
    blocking_finding_ids: tuple[str, ...] = ()
    evidence_context: ReviewEvidenceSearchResult | None = None

    @field_validator("review_id", "summary")
    @classmethod
    def _required_not_blank(cls, value: str, info: ValidationInfo) -> str:
        return _clean_str(value, info.field_name)

    @field_validator("blocking_finding_ids")
    @classmethod
    def _entries_not_blank(cls, value: tuple[str, ...], info: ValidationInfo) -> tuple[str, ...]:
        return _clean_str_tuple(value, info.field_name)

    @model_validator(mode="after")
    def _consistent(self) -> "ArchitectureReviewResult":
        for review in self.specialist_reviews:
            if review.review_id != self.review_id:
                raise ValueError(
                    f"specialist review for {review.reviewer} targets a different review_id"
                )

        review_dimensions = [review.reviewer for review in self.specialist_reviews]
        failure_dimensions = [failure.reviewer for failure in self.specialist_failures]

        if len(set(review_dimensions)) != len(review_dimensions):
            raise ValueError("specialist_reviews must not contain duplicate dimensions")
        if len(set(failure_dimensions)) != len(failure_dimensions):
            raise ValueError("specialist_failures must not contain duplicate dimensions")

        overlap = set(review_dimensions) & set(failure_dimensions)
        if overlap:
            raise ValueError(f"dimensions cannot both succeed and fail: {sorted(overlap)}")

        covered = set(review_dimensions) | set(failure_dimensions)
        missing = set(REVIEW_DIMENSION_ORDER) - covered
        if missing:
            raise ValueError(f"missing specialist coverage for dimensions: {sorted(missing)}")
        unexpected = covered - set(REVIEW_DIMENSION_ORDER)
        if unexpected:
            raise ValueError(f"unexpected dimensions in specialist coverage: {sorted(unexpected)}")

        expected_review_order = [d for d in REVIEW_DIMENSION_ORDER if d in review_dimensions]
        if review_dimensions != expected_review_order:
            raise ValueError("specialist_reviews must be ordered canonically by dimension")

        expected_failure_order = [d for d in REVIEW_DIMENSION_ORDER if d in failure_dimensions]
        if failure_dimensions != expected_failure_order:
            raise ValueError("specialist_failures must be ordered canonically by dimension")

        all_finding_ids = [
            finding.finding_id
            for review in self.specialist_reviews
            for finding in review.findings
        ]
        duplicate_finding_ids = {
            finding_id for finding_id in all_finding_ids if all_finding_ids.count(finding_id) > 1
        }
        if duplicate_finding_ids:
            raise ValueError(
                f"finding_id values must be unique across specialist reviews: "
                f"{sorted(duplicate_finding_ids)}"
            )

        known_finding_ids = set(all_finding_ids)
        finding_reviewer_by_id = {
            finding.finding_id: finding.reviewer
            for review in self.specialist_reviews
            for finding in review.findings
        }

        unknown_blocking = set(self.blocking_finding_ids) - known_finding_ids
        if unknown_blocking:
            raise ValueError(
                f"blocking_finding_ids reference unknown findings: {sorted(unknown_blocking)}"
            )

        for condition in self.conditions:
            unknown = set(condition.related_finding_ids) - known_finding_ids
            if unknown:
                raise ValueError(
                    f"condition {condition.condition_id} references unknown findings: "
                    f"{sorted(unknown)}"
                )

        for disagreement in self.disagreements:
            for position in disagreement.positions:
                if position.reviewer not in review_dimensions:
                    raise ValueError(
                        f"disagreement {disagreement.disagreement_id} references "
                        f"{position.reviewer}, which has no specialist review"
                    )
                misattributed = {
                    finding_id
                    for finding_id in position.related_finding_ids
                    if finding_reviewer_by_id.get(finding_id) != position.reviewer
                }
                if misattributed:
                    raise ValueError(
                        f"disagreement {disagreement.disagreement_id} position for "
                        f"{position.reviewer} references findings it does not own: "
                        f"{sorted(misattributed)}"
                    )

        known_evidence: set[ReviewEvidence] = set()
        if (
            self.evidence_context is not None
            and self.evidence_context.status == ReviewEvidenceStatus.SUCCESS
        ):
            known_evidence = set(self.evidence_context.evidence)

        for review in self.specialist_reviews:
            for finding in review.findings:
                unknown_evidence = set(finding.evidence) - known_evidence
                if unknown_evidence:
                    raise ValueError(
                        f"finding {finding.finding_id} cites evidence outside the shared "
                        f"evidence_context: {sorted(item.evidence_id for item in unknown_evidence)}"
                    )

        if self.decision == ArchitectureDecision.APPROVE and self.specialist_failures:
            raise ValueError("APPROVE is not valid when specialist coverage is incomplete")

        if self.decision == ArchitectureDecision.APPROVE and (
            self.blocking_finding_ids or self.conditions
        ):
            raise ValueError("APPROVE must have no blocking findings and no conditions")

        if self.decision == ArchitectureDecision.APPROVE_WITH_CONDITIONS and not self.conditions:
            raise ValueError("APPROVE_WITH_CONDITIONS requires at least one condition")

        if self.decision == ArchitectureDecision.REQUEST_CHANGES and not self.blocking_finding_ids:
            raise ValueError("REQUEST_CHANGES requires at least one blocking finding")

        return self
