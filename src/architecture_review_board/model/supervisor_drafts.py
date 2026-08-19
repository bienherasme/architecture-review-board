"""Structured output a supervisor model is allowed to produce.

Excludes identity fields (review_id, condition_id, disagreement_id) and
anything that would let the model rewrite a specialist's work: it can
only reference existing findings by ID, propose text, and choose a
decision. The application layer stamps condition and disagreement
identity, and copies specialist reviews through unchanged rather than
reconstructing them from this draft.
"""

from pydantic import BaseModel, ConfigDict, ValidationInfo, field_validator

from architecture_review_board.domain.enums import ArchitectureDecision, ReviewDimension

MAX_REVIEW_CONDITIONS = 8
MAX_REVIEW_DISAGREEMENTS = 8
MAX_BLOCKING_FINDINGS = 12


def _require_text(value: str, field_name: str | None) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name or 'field'} must not be blank")
    return stripped


def _clean_text_tuple(values: tuple[str, ...], field_name: str | None) -> tuple[str, ...]:
    return tuple(_require_text(value, field_name) for value in values)


class ReviewConditionDraft(BaseModel):
    """A proposed approval condition, before the application assigns its ID."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    description: str
    related_finding_ids: tuple[str, ...] = ()

    @field_validator("description")
    @classmethod
    def _required_not_blank(cls, value: str, info: ValidationInfo) -> str:
        return _require_text(value, info.field_name)

    @field_validator("related_finding_ids")
    @classmethod
    def _entries_not_blank(cls, value: tuple[str, ...], info: ValidationInfo) -> tuple[str, ...]:
        return _clean_text_tuple(value, info.field_name)


class ReviewerPositionDraft(BaseModel):
    """One reviewer's stance within a proposed disagreement.

    reviewer names an existing specialist dimension. The application
    layer still verifies, before this becomes a domain ReviewerPosition,
    that the dimension actually produced a SpecialistReview and that
    every referenced finding belongs to that same reviewer; a model
    cannot attribute a position or a finding to a dimension it does not
    own by writing this field alone.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    reviewer: ReviewDimension
    position: str
    related_finding_ids: tuple[str, ...] = ()

    @field_validator("position")
    @classmethod
    def _required_not_blank(cls, value: str, info: ValidationInfo) -> str:
        return _require_text(value, info.field_name)

    @field_validator("related_finding_ids")
    @classmethod
    def _entries_not_blank(cls, value: tuple[str, ...], info: ValidationInfo) -> tuple[str, ...]:
        return _clean_text_tuple(value, info.field_name)


class ReviewDisagreementDraft(BaseModel):
    """A proposed disagreement, before the application assigns its ID.

    Cardinality and reviewer-uniqueness of positions are not re-validated
    here; the domain ReviewDisagreement built from this draft already
    enforces them, and application mapping surfaces any violation as a
    typed error rather than duplicating that check.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    topic: str
    positions: tuple[ReviewerPositionDraft, ...]
    resolution: str | None = None

    @field_validator("topic")
    @classmethod
    def _required_not_blank(cls, value: str, info: ValidationInfo) -> str:
        return _require_text(value, info.field_name)

    @field_validator("resolution")
    @classmethod
    def _resolution_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _require_text(value, "resolution")


class SupervisorReviewDraft(BaseModel):
    """A supervisor model's proposed reconciliation, before application-layer mapping."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: ArchitectureDecision
    summary: str
    blocking_finding_ids: tuple[str, ...] = ()
    conditions: tuple[ReviewConditionDraft, ...] = ()
    disagreements: tuple[ReviewDisagreementDraft, ...] = ()

    @field_validator("summary")
    @classmethod
    def _required_not_blank(cls, value: str, info: ValidationInfo) -> str:
        return _require_text(value, info.field_name)

    @field_validator("blocking_finding_ids")
    @classmethod
    def _blocking_entries_not_blank(
        cls, value: tuple[str, ...], info: ValidationInfo
    ) -> tuple[str, ...]:
        return _clean_text_tuple(value, info.field_name)

    @field_validator("blocking_finding_ids")
    @classmethod
    def _blocking_bounded(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > MAX_BLOCKING_FINDINGS:
            raise ValueError(
                f"at most {MAX_BLOCKING_FINDINGS} blocking findings are allowed, got {len(value)}"
            )
        return value

    @field_validator("conditions")
    @classmethod
    def _conditions_bounded(
        cls, value: tuple[ReviewConditionDraft, ...]
    ) -> tuple[ReviewConditionDraft, ...]:
        if len(value) > MAX_REVIEW_CONDITIONS:
            raise ValueError(
                f"at most {MAX_REVIEW_CONDITIONS} conditions are allowed, got {len(value)}"
            )
        return value

    @field_validator("disagreements")
    @classmethod
    def _disagreements_bounded(
        cls, value: tuple[ReviewDisagreementDraft, ...]
    ) -> tuple[ReviewDisagreementDraft, ...]:
        if len(value) > MAX_REVIEW_DISAGREEMENTS:
            raise ValueError(
                f"at most {MAX_REVIEW_DISAGREEMENTS} disagreements are allowed, got {len(value)}"
            )
        return value
