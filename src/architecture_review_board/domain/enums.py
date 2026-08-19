"""Closed vocabularies used across the review domain."""

from enum import StrEnum


class ReviewDimension(StrEnum):
    """A system-level architecture concern a specialist reviewer covers.

    These are professional review responsibilities, not personas. Values
    stay technology-agnostic (no "frontend"/"database"/"cloud" reviewers)
    so the same dimensions apply regardless of stack.
    """

    RELIABILITY = "reliability"
    SECURITY = "security"
    DATA = "data"
    OPERABILITY = "operability"
    COMPLEXITY = "complexity"


REVIEW_DIMENSION_ORDER: tuple[ReviewDimension, ...] = (
    ReviewDimension.RELIABILITY,
    ReviewDimension.SECURITY,
    ReviewDimension.DATA,
    ReviewDimension.OPERABILITY,
    ReviewDimension.COMPLEXITY,
)
"""Canonical presentation order for the five dimensions.

Used consistently for reviewer construction, coordinated and final result
ordering, and test determinism, rather than task completion order or set
iteration.
"""


class FindingSeverity(StrEnum):
    """How serious a single review finding is, independent of reviewer confidence.

    Severity is not confidence: a reviewer can be highly confident about a
    LOW severity finding, or unsure about a CRITICAL one. Severity is also
    not the final review decision or a measure of reviewer consensus; those
    are represented separately by ArchitectureDecision and ReviewDisagreement.
    """

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ReviewEvidenceStatus(StrEnum):
    """The outcome of one external evidence search.

    SUCCESS: the search ran and returned at least one evidence item.
    EMPTY: the search ran and legitimately found nothing.
    UNAVAILABLE: the external dependency could not provide usable
    evidence at all (not configured to run, unreachable, an incompatible
    response). Distinct from EMPTY, which means the search itself worked
    and simply had no matches; collapsing the two would hide a broken
    dependency behind what looks like a clean negative result.
    """

    SUCCESS = "success"
    EMPTY = "empty"
    UNAVAILABLE = "unavailable"


class ArchitectureDecision(StrEnum):
    """The outcome of an architecture review.

    This is an advisory review decision, not a deployment authorization or
    a replacement for organizational approval processes.

    APPROVE: no blocking changes required.
    APPROVE_WITH_CONDITIONS: the architecture can proceed provided the
        stated conditions are satisfied.
    REQUEST_CHANGES: material architectural issues should be addressed
        before approval.
    """

    APPROVE = "approve"
    APPROVE_WITH_CONDITIONS = "approve_with_conditions"
    REQUEST_CHANGES = "request_changes"
