import pytest

pytest.importorskip("mcp")

import asyncio
import json
from collections.abc import Callable

from mcp.types import CallToolResult

from architecture_review_board.domain.enums import ReviewEvidenceStatus
from architecture_review_board.domain.models import ReviewEvidenceQuery
from architecture_review_board.evidence.provider import ReviewEvidenceUnavailableError
from architecture_review_board.providers.engineering_knowledge import (
    EngineeringKnowledgeMcpEvidenceProvider,
    _parse_search_result,
)


def make_tool_result(structured_content: object = None, is_error: bool = False) -> CallToolResult:
    return CallToolResult(content=[], structuredContent=structured_content, isError=is_error)


def make_hit(chunk_id: str = "chunk-1", rank: int = 1, text: str = "Some retrieved text.") -> dict:
    return {
        "chunk": {"text": text},
        "source_reference": {
            "source_id": "docs-repo",
            "document_id": "doc-1",
            "chunk_id": chunk_id,
            "relative_path": "architecture/idempotency.md",
            "section_path": {"headings": ["Idempotency", "Retry design"]},
        },
        "rank": rank,
    }


@pytest.mark.parametrize("status", ["success", "partial"])
def test_success_and_partial_map_to_success_with_deterministic_evidence(status: str) -> None:
    payload = {
        "status": status,
        "results": [make_hit(chunk_id="chunk-1", rank=1), make_hit(chunk_id="chunk-2", rank=2)],
    }

    result = _parse_search_result(make_tool_result(structured_content=payload))

    assert result.status == ReviewEvidenceStatus.SUCCESS
    assert [item.evidence_id for item in result.evidence] == ["knowledge-001", "knowledge-002"]
    assert all(item.source_type == "engineering-knowledge" for item in result.evidence)
    assert result.evidence[0].excerpt == "Some retrieved text."
    reference = json.loads(result.evidence[0].source_reference)
    assert reference == {
        "chunk_id": "chunk-1",
        "document_id": "doc-1",
        "relative_path": "architecture/idempotency.md",
        "section_path": ["Idempotency", "Retry design"],
        "source_id": "docs-repo",
    }


def test_empty_status_maps_to_empty_result() -> None:
    result = _parse_search_result(
        make_tool_result(structured_content={"status": "empty", "results": []})
    )

    assert result.status == ReviewEvidenceStatus.EMPTY
    assert result.evidence == ()


_MALFORMED_RESULT_BUILDERS: list[Callable[[], CallToolResult]] = [
    lambda: make_tool_result(is_error=True, structured_content={"status": "empty", "results": []}),
    lambda: make_tool_result(structured_content=None),
    lambda: make_tool_result(structured_content={"status": "bogus", "results": []}),
    lambda: make_tool_result(
        structured_content={
            "status": "success",
            "results": [make_hit(chunk_id="dup", rank=1), make_hit(chunk_id="dup", rank=2)],
        }
    ),
]


@pytest.mark.parametrize("build_result", _MALFORMED_RESULT_BUILDERS)
def test_malformed_or_inconsistent_responses_raise_unavailable(
    build_result: Callable[[], CallToolResult],
) -> None:
    with pytest.raises(ReviewEvidenceUnavailableError):
        _parse_search_result(build_result())


def test_search_raises_unavailable_when_transport_fails() -> None:
    provider = EngineeringKnowledgeMcpEvidenceProvider(
        command="/nonexistent-arb-evidence-provider-binary", timeout_seconds=5.0
    )

    with pytest.raises(ReviewEvidenceUnavailableError):
        asyncio.run(provider.search(ReviewEvidenceQuery(query="idempotent retries")))
