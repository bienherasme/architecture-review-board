"""The port through which the application asks for external review evidence.

Failure semantics mirror the model ports: search() raises
ReviewEvidenceUnavailableError for an expected external failure (not
configured to run, unreachable, a timeout, an incompatible response); it
does not return ReviewEvidenceSearchResult(status=UNAVAILABLE) itself.
That status belongs to the caller (ArchitectureReviewService) that
catches this error and turns it into a degraded result. Keeping the two
separate matters: whether the external dependency ran at all, and what a
review result records as its outcome, are different facts that different
layers own.
"""

from typing import Protocol

from architecture_review_board.domain.models import ReviewEvidenceQuery, ReviewEvidenceSearchResult


class ReviewEvidenceUnavailableError(Exception):
    """The external evidence provider could not answer, for any expected reason.

    Not configured to run, unreachable, a protocol error, a timeout, an
    incompatible response: all of it means the same thing to a caller, no
    evidence for this search, an expected outcome to handle, not a bug.
    """


class ReviewEvidenceProvider(Protocol):
    """Provider-neutral port for one bounded architecture-review evidence search."""

    async def search(self, query: ReviewEvidenceQuery) -> ReviewEvidenceSearchResult: ...
