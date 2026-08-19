"""Deterministic construction of the one evidence query a review may issue.

ARB owns this, not the model and not the evidence provider: which
proposal fields are worth searching on is a review-domain judgment, and
building it here keeps it a plain, inspectable function rather than
something an LLM would have to be prompted to produce consistently.
"""

from architecture_review_board.domain.models import (
    MAX_EVIDENCE_QUERY_CHARS,
    ArchitectureReviewRequest,
    ReviewEvidenceQuery,
)


def _truncate(text: str, limit: int) -> str:
    """Cut text to at most limit characters, preferring a word boundary.

    Never used to shorten text that already fits; only engages when a
    field would otherwise push the query past MAX_EVIDENCE_QUERY_CHARS.
    """
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    truncated = text[:limit]
    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0]
    return truncated.rstrip()


def build_review_evidence_query(request: ArchitectureReviewRequest) -> ReviewEvidenceQuery | None:
    """Build the single evidence query for one review, or None if there is nothing to search on.

    Uses only bounded, high-value fields: title, affected_components, and
    problem_statement, in that priority order. Deliberately excludes
    proposed_solution, assumptions, and alternatives_considered, which can
    be long and would make the query a near-duplicate of the proposal
    rather than a way to locate relevant engineering context.

    title and affected_components are treated as anchors and kept in
    full whenever the query as a whole still fits MAX_EVIDENCE_QUERY_CHARS;
    problem_statement fills whatever character budget remains and is
    trimmed at a word boundary rather than silently dropped, so an
    unusually long proposal still produces a valid, bounded, deterministic
    query instead of one that fails ReviewEvidenceQuery's own validation.
    """
    anchor_terms = [
        request.title.strip(),
        *(component.strip() for component in request.affected_components),
    ]
    anchor = " ".join(term for term in anchor_terms if term)
    anchor = _truncate(anchor, MAX_EVIDENCE_QUERY_CHARS)

    remaining_budget = MAX_EVIDENCE_QUERY_CHARS - len(anchor) - (1 if anchor else 0)
    problem_statement = _truncate(request.problem_statement.strip(), remaining_budget)

    query_text = " ".join(term for term in (anchor, problem_statement) if term)
    if not query_text:
        return None
    return ReviewEvidenceQuery(query=query_text)
