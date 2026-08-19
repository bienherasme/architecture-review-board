"""Deterministic lexical-anchor matching between expected and actual review output.

No embeddings, no fuzzy-matching library, no stemming, no NLP dependency:
a small stdlib normalizer plus substring containment. This is a
transparent, inspectable, offline heuristic, not a semantic-equivalence
claim. A valid finding phrased in wording no anchor group anticipated
produces a false negative here; that limitation is deliberate and
documented rather than papered over with an opaque matcher.
"""

import re

from architecture_review_board.domain.models import (
    ReviewDisagreement,
    ReviewFinding,
    SpecialistReview,
)
from architecture_review_board.evaluation.models import ExpectedDisagreement, ExpectedRisk

_NON_WORD_RE = re.compile(r"[^\w\s]")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Lowercase, strip punctuation to spaces, and collapse whitespace.

    Deliberately simple and stdlib-only: this is a matching aid, not a
    text-quality tool. "single-instance" and "single instance" normalize
    to the same string, which is the point.
    """
    lowered = text.lower()
    no_punctuation = _NON_WORD_RE.sub(" ", lowered)
    return _WHITESPACE_RE.sub(" ", no_punctuation).strip()


def _normalize_finding(finding: ReviewFinding) -> str:
    parts = [finding.title, finding.description, finding.rationale]
    if finding.recommendation:
        parts.append(finding.recommendation)
    return normalize_text(" ".join(parts))


def _anchor_group_satisfied(group: tuple[str, ...], normalized_text: str) -> bool:
    return any(normalize_text(alias) in normalized_text for alias in group)


def match_expected_risk(
    risk: ExpectedRisk, specialist_reviews: tuple[SpecialistReview, ...]
) -> ReviewFinding | None:
    """Find the first finding, in domain order, from risk.reviewer that satisfies every group.

    Only findings from the specialist review whose reviewer matches
    risk.reviewer are considered: a different dimension mentioning the
    same topic does not satisfy this expectation, because dimension
    ownership is part of what is being evaluated.
    """
    for review in specialist_reviews:
        if review.reviewer != risk.reviewer:
            continue
        for finding in review.findings:
            normalized = _normalize_finding(finding)
            if all(_anchor_group_satisfied(group, normalized) for group in risk.anchor_groups):
                return finding
    return None


def _normalize_disagreement(disagreement: ReviewDisagreement) -> str:
    parts = [disagreement.topic, *(position.position for position in disagreement.positions)]
    if disagreement.resolution:
        parts.append(disagreement.resolution)
    return normalize_text(" ".join(parts))


def match_expected_disagreement(
    expected: ExpectedDisagreement, disagreements: tuple[ReviewDisagreement, ...]
) -> ReviewDisagreement | None:
    """Find a disagreement whose positions cover every expected reviewer and anchor group.

    A disagreement involving more reviewers than expected still counts,
    as long as it contains a position from each expected reviewer.
    """
    expected_reviewers = set(expected.reviewers)
    for disagreement in disagreements:
        actual_reviewers = {position.reviewer for position in disagreement.positions}
        if not expected_reviewers.issubset(actual_reviewers):
            continue
        if not expected.anchor_groups:
            return disagreement
        normalized = _normalize_disagreement(disagreement)
        if all(_anchor_group_satisfied(group, normalized) for group in expected.anchor_groups):
            return disagreement
    return None
