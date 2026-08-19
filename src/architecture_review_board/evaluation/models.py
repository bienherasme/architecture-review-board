"""Data contracts for the golden dataset and evaluation results.

These models are evaluation-only: they never become part of
ArchitectureReviewRequest, ReviewFinding, or any other domain type. The
domain stays ignorant of benchmarking; this package only reads the public
ArchitectureReviewResult a normal caller already sees.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, ValidationInfo, field_validator, model_validator

from architecture_review_board.domain.enums import (
    ArchitectureDecision,
    FindingSeverity,
    ReviewDimension,
    ReviewEvidenceStatus,
)
from architecture_review_board.domain.models import ArchitectureReviewRequest


def _require_text(value: str, field_name: str | None) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name or 'field'} must not be blank")
    return stripped


def _clean_anchor_groups(
    value: tuple[tuple[str, ...], ...], field_name: str
) -> tuple[tuple[str, ...], ...]:
    cleaned: list[tuple[str, ...]] = []
    for group in value:
        if not group:
            raise ValueError(f"{field_name} entries must not be empty anchor groups")
        cleaned.append(tuple(_require_text(alias, "anchor") for alias in group))
    return tuple(cleaned)


class ExpectedRisk(BaseModel):
    """One architecture concern the golden dataset expects a specific dimension to raise.

    anchor_groups is a deterministic lexical heuristic, not a semantic
    equivalence claim: every group must have at least one alias present
    in a candidate finding's normalized text, but a model may legitimately
    phrase the same real risk in wording no anchor group anticipated,
    producing a false negative. This is a behavioral signal, not a
    universal-correctness one.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    risk_id: str
    reviewer: ReviewDimension
    anchor_groups: tuple[tuple[str, ...], ...]
    acceptable_severities: tuple[FindingSeverity, ...] = ()
    expected_evidence_ids: tuple[str, ...] = ()

    @field_validator("risk_id")
    @classmethod
    def _risk_id_not_blank(cls, value: str) -> str:
        return _require_text(value, "risk_id")

    @field_validator("anchor_groups")
    @classmethod
    def _anchor_groups_valid(
        cls, value: tuple[tuple[str, ...], ...]
    ) -> tuple[tuple[str, ...], ...]:
        if not value:
            raise ValueError("anchor_groups must not be empty")
        return _clean_anchor_groups(value, "anchor_groups")

    @field_validator("expected_evidence_ids")
    @classmethod
    def _evidence_ids_not_blank(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_require_text(item, "expected_evidence_ids") for item in value)


class ExpectedDisagreement(BaseModel):
    """A material cross-dimension disagreement the golden dataset expects to appear."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    disagreement_id: str
    reviewers: tuple[ReviewDimension, ...]
    anchor_groups: tuple[tuple[str, ...], ...] = ()

    @field_validator("disagreement_id")
    @classmethod
    def _id_not_blank(cls, value: str) -> str:
        return _require_text(value, "disagreement_id")

    @field_validator("reviewers")
    @classmethod
    def _reviewers_valid(
        cls, value: tuple[ReviewDimension, ...]
    ) -> tuple[ReviewDimension, ...]:
        if len(value) < 2:
            raise ValueError("expected disagreement requires at least two reviewers")
        if len(set(value)) != len(value):
            raise ValueError("expected disagreement reviewers must be unique")
        return value

    @field_validator("anchor_groups")
    @classmethod
    def _anchor_groups_valid(
        cls, value: tuple[tuple[str, ...], ...]
    ) -> tuple[tuple[str, ...], ...]:
        return _clean_anchor_groups(value, "anchor_groups")


class EvaluationCase(BaseModel):
    """One golden benchmark case: a proposal plus the human-authored expected behavior."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    description: str | None = None
    request: ArchitectureReviewRequest
    acceptable_decisions: tuple[ArchitectureDecision, ...]
    expected_risks: tuple[ExpectedRisk, ...] = ()
    expected_disagreements: tuple[ExpectedDisagreement, ...] = ()

    @field_validator("case_id")
    @classmethod
    def _case_id_not_blank(cls, value: str) -> str:
        return _require_text(value, "case_id")

    @field_validator("description")
    @classmethod
    def _description_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _require_text(value, "description")

    @field_validator("acceptable_decisions")
    @classmethod
    def _acceptable_decisions_not_empty(
        cls, value: tuple[ArchitectureDecision, ...]
    ) -> tuple[ArchitectureDecision, ...]:
        if not value:
            raise ValueError("acceptable_decisions must not be empty")
        return value

    @model_validator(mode="after")
    def _no_duplicate_ids(self) -> "EvaluationCase":
        risk_ids = [risk.risk_id for risk in self.expected_risks]
        if len(set(risk_ids)) != len(risk_ids):
            raise ValueError(f"case {self.case_id} has duplicate expected_risks risk_id values")

        disagreement_ids = [d.disagreement_id for d in self.expected_disagreements]
        if len(set(disagreement_ids)) != len(disagreement_ids):
            raise ValueError(
                f"case {self.case_id} has duplicate expected_disagreements disagreement_id values"
            )
        return self


class EvaluationDataset(BaseModel):
    """A versioned, fixed collection of golden evaluation cases."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_id: str
    version: str
    cases: tuple[EvaluationCase, ...]

    @field_validator("dataset_id", "version")
    @classmethod
    def _not_blank(cls, value: str, info: ValidationInfo) -> str:
        return _require_text(value, info.field_name)

    @model_validator(mode="after")
    def _cases_valid(self) -> "EvaluationDataset":
        if not self.cases:
            raise ValueError("dataset must contain at least one case")
        case_ids = [case.case_id for case in self.cases]
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("dataset contains duplicate case_id values")
        return self


class EvaluationRunStatus(StrEnum):
    """Whether one case repetition produced a public ArchitectureReviewResult.

    COMPLETED does not mean all five specialists succeeded; a completed
    run may still carry specialist_failures. Those are two separate
    signals, tracked separately in EvaluationCaseRun and EvaluationSummary.
    """

    COMPLETED = "completed"
    FAILED = "failed"


class EvaluationCaseRun(BaseModel):
    """The scored outcome of running one EvaluationCase once."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    repetition: int
    status: EvaluationRunStatus
    decision: ArchitectureDecision | None = None
    acceptable_decision: bool | None = None
    specialist_failures: tuple[ReviewDimension, ...] = ()
    matched_risk_ids: tuple[str, ...] = ()
    missed_risk_ids: tuple[str, ...] = ()
    severity_mismatch_risk_ids: tuple[str, ...] = ()
    evidence_citation_hit_risk_ids: tuple[str, ...] = ()
    evidence_citation_miss_risk_ids: tuple[str, ...] = ()
    matched_disagreement_ids: tuple[str, ...] = ()
    missed_disagreement_ids: tuple[str, ...] = ()
    evidence_status: ReviewEvidenceStatus | None = None
    error_detail: str | None = None

    @field_validator("case_id")
    @classmethod
    def _case_id_not_blank(cls, value: str) -> str:
        return _require_text(value, "case_id")

    @field_validator("repetition")
    @classmethod
    def _repetition_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("repetition must be >= 1")
        return value

    @field_validator("error_detail")
    @classmethod
    def _error_detail_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _require_text(value, "error_detail")

    @model_validator(mode="after")
    def _status_shape(self) -> "EvaluationCaseRun":
        if self.status == EvaluationRunStatus.FAILED:
            if self.error_detail is None:
                raise ValueError("a FAILED run requires error_detail")
            if self.decision is not None:
                raise ValueError("a FAILED run must not carry a decision")
        if self.status == EvaluationRunStatus.COMPLETED and self.error_detail is not None:
            raise ValueError("a COMPLETED run must not carry error_detail")
        return self


class EvaluationRunMetadata(BaseModel):
    """Caller-supplied reproducibility metadata for one evaluation run.

    The evaluator never introspects a provider's private fields to
    discover model identity; the caller states what it configured. No
    API keys, endpoint secrets, or raw configuration dumps belong here.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    model: str
    evidence_mode: str
    provider_sdk_version: str | None = None

    @field_validator("provider", "model", "evidence_mode")
    @classmethod
    def _not_blank(cls, value: str, info: ValidationInfo) -> str:
        return _require_text(value, info.field_name)

    @field_validator("provider_sdk_version")
    @classmethod
    def _sdk_version_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _require_text(value, "provider_sdk_version")


class EvaluationSummary(BaseModel):
    """Transparent aggregate metrics. Never a weighted composite score.

    See ArchitectureReviewEvaluator for exact denominators. A metric is
    None, never 0.0, when it has no applicable denominator: that
    distinguishes "not measured" from "measured as zero."
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    total_runs: int
    completed_runs: int
    completion_rate: float
    full_board_run_rate: float
    acceptable_decision_rate: float | None
    expected_risk_recall: float | None
    expected_disagreement_recall: float | None
    expected_evidence_citation_rate: float | None


class CaseDecisionStability(BaseModel):
    """Per-case decision stability across repeated runs of the same case."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    completed_runs: int
    modal_decision: ArchitectureDecision | None
    modal_agreement_rate: float | None


class EvaluationReport(BaseModel):
    """The full reproducible output of one evaluator.evaluate() call."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_id: str
    dataset_version: str
    dataset_fingerprint: str
    run_metadata: EvaluationRunMetadata
    repetitions: int
    case_runs: tuple[EvaluationCaseRun, ...]
    summary: EvaluationSummary
    decision_stability: tuple[CaseDecisionStability, ...]


class EvaluationComparison(BaseModel):
    """Deltas between two compatible EvaluationReports. Never a declared winner."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_id: str
    dataset_version: str
    dataset_fingerprint: str
    repetitions: int
    baseline_run_metadata: EvaluationRunMetadata
    candidate_run_metadata: EvaluationRunMetadata
    completion_rate_delta: float
    full_board_run_rate_delta: float
    acceptable_decision_rate_delta: float | None
    expected_risk_recall_delta: float | None
    expected_disagreement_recall_delta: float | None
    expected_evidence_citation_rate_delta: float | None
