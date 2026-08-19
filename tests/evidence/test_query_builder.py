from architecture_review_board.domain.models import (
    MAX_EVIDENCE_QUERY_CHARS,
    ArchitectureReviewRequest,
)
from architecture_review_board.evidence.query_builder import build_review_evidence_query


def test_build_review_evidence_query_uses_bounded_high_value_fields() -> None:
    request = ArchitectureReviewRequest(
        review_id="rev-1",
        title="Payments retry redesign",
        problem_statement="Retries overwhelm downstream services during incidents.",
        proposed_solution="Introduce bounded exponential backoff with jitter and a dead letter"
        " queue for terminally failed messages.",
        assumptions=("Traffic is bursty during regional failover events.",),
        alternatives_considered=("Client-side circuit breaking without a shared queue.",),
        affected_components=("payment-api", "checkout-service"),
    )

    query = build_review_evidence_query(request)

    assert query is not None
    assert "Payments retry redesign" in query.query
    assert "payment-api" in query.query
    assert "checkout-service" in query.query
    assert "Retries overwhelm downstream services during incidents." in query.query
    assert "bounded exponential backoff" not in query.query
    assert "circuit breaking" not in query.query


def test_build_review_evidence_query_bounds_and_trims_an_oversized_problem_statement() -> None:
    huge_problem_statement = "retry amplification during regional failover " * 200
    request = ArchitectureReviewRequest(
        review_id="rev-1",
        title="Payments retry redesign",
        problem_statement=huge_problem_statement,
        proposed_solution="Introduce bounded exponential backoff with jitter.",
        affected_components=("payment-api", "checkout-service"),
    )

    query = build_review_evidence_query(request)
    repeated_query = build_review_evidence_query(request)

    assert query is not None
    assert len(query.query) <= MAX_EVIDENCE_QUERY_CHARS
    assert query.query.startswith("Payments retry redesign payment-api checkout-service")
    assert query == repeated_query
