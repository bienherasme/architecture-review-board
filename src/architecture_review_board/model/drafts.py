"""Structured output a specialist model is allowed to produce.

These models define exactly what the model may propose. They exclude
identity and decision fields (finding_id, reviewer, evidence, final
decision, conditions, disagreements) that the application layer owns, so
a response crossing this boundary can never spoof a reviewer identity or
short-circuit review logic that belongs elsewhere.
"""

from pydantic import BaseModel, ConfigDict, ValidationInfo, field_validator

from architecture_review_board.domain.enums import FindingSeverity

MAX_SPECIALIST_FINDINGS = 8


def _require_text(value: str, field_name: str | None) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name or 'field'} must not be blank")
    return stripped


class ReviewFindingDraft(BaseModel):
    """One finding as proposed by a specialist model, before identity is stamped.

    evidence_ids may reference only SpecialistModelRequest.available_evidence
    entries the model was actually given; it does not create new evidence.
    The application layer resolves each id to the exact ReviewEvidence
    object and rejects the draft if an id is unknown, so a model can point
    at supplied evidence but never fabricate provenance.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str
    description: str
    severity: FindingSeverity
    rationale: str
    recommendation: str | None = None
    confidence: float
    evidence_ids: tuple[str, ...] = ()

    @field_validator("title", "description", "rationale")
    @classmethod
    def _required_not_blank(cls, value: str, info: ValidationInfo) -> str:
        return _require_text(value, info.field_name)

    @field_validator("recommendation")
    @classmethod
    def _recommendation_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _require_text(value, "recommendation")

    @field_validator("confidence")
    @classmethod
    def _confidence_in_range(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        return value

    @field_validator("evidence_ids")
    @classmethod
    def _evidence_ids_valid(cls, value: tuple[str, ...], info: ValidationInfo) -> tuple[str, ...]:
        cleaned = tuple(_require_text(item, info.field_name) for item in value)
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("evidence_ids must not reference the same evidence item twice")
        return cleaned


class SpecialistReviewDraft(BaseModel):
    """A specialist model's proposed analysis, before application-layer mapping.

    Findings are kept in the order the model returned them; ordering is
    part of the model's assessment and is preserved when the application
    layer later assigns finding identity.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    summary: str
    overall_confidence: float
    findings: tuple[ReviewFindingDraft, ...] = ()

    @field_validator("summary")
    @classmethod
    def _required_not_blank(cls, value: str, info: ValidationInfo) -> str:
        return _require_text(value, info.field_name)

    @field_validator("overall_confidence")
    @classmethod
    def _confidence_in_range(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("overall_confidence must be between 0.0 and 1.0")
        return value

    @field_validator("findings")
    @classmethod
    def _bounded(cls, value: tuple[ReviewFindingDraft, ...]) -> tuple[ReviewFindingDraft, ...]:
        if len(value) > MAX_SPECIALIST_FINDINGS:
            raise ValueError(
                f"a specialist review may report at most {MAX_SPECIALIST_FINDINGS} findings, "
                f"got {len(value)}"
            )
        return value
