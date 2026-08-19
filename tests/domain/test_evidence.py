import pytest
from pydantic import ValidationError

from architecture_review_board.domain.enums import ReviewEvidenceStatus
from architecture_review_board.domain.models import (
    MAX_EVIDENCE_RESULTS,
    ReviewEvidence,
    ReviewEvidenceQuery,
    ReviewEvidenceSearchResult,
)


def test_review_evidence_query_and_search_result_shape_invariants() -> None:
    with pytest.raises(ValidationError):
        ReviewEvidenceQuery(query="   ")

    with pytest.raises(ValidationError):
        ReviewEvidenceQuery(query="payments architecture", max_results=MAX_EVIDENCE_RESULTS + 1)

    default_query = ReviewEvidenceQuery(query="payments architecture")
    assert 0 < default_query.max_results <= MAX_EVIDENCE_RESULTS

    evidence = ReviewEvidence(
        evidence_id="knowledge-001",
        source_type="engineering-knowledge",
        source_reference="ref",
        excerpt="text",
    )
    with pytest.raises(ValidationError):
        ReviewEvidenceSearchResult(status=ReviewEvidenceStatus.SUCCESS, evidence=())

    with pytest.raises(ValidationError):
        ReviewEvidenceSearchResult(status=ReviewEvidenceStatus.EMPTY, evidence=(evidence,))

    with pytest.raises(ValidationError):
        ReviewEvidenceSearchResult(status=ReviewEvidenceStatus.UNAVAILABLE, evidence=(evidence,))

    success = ReviewEvidenceSearchResult(status=ReviewEvidenceStatus.SUCCESS, evidence=(evidence,))
    assert success.evidence == (evidence,)
