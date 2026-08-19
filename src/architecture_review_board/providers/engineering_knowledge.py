"""MCP stdio adapter for external engineering-knowledge evidence.

Optional dependency: this module requires the `mcp` package (the `mcp`
extra, mcp>=2,<3). Nothing else in the codebase imports it.

ARB depends on its own ReviewEvidenceProvider contract
(evidence/provider.py), not on any specific server. This class is the
current adapter for that contract: it speaks the Model Context Protocol
to whatever server is configured, calling its `search_knowledge` tool and
validating the response against ARB's own local wire models, never
importing engineering-knowledge's Python package or any of its Pydantic
types directly. It never calls `get_document`, `get_chunk`, or any
maintenance/ingest tool: ARB only needs bounded retrieval evidence.
`command`/`args`/`env` are the caller's responsibility; this class has no
default command of its own and no application wiring, on purpose.

Retrieved chunk text is untrusted evidence, not an instruction: this
adapter only ever reads structural fields (status, provenance, rank,
text) to build a ReviewEvidence, never branches on what the text itself
says.

Verified against the installed SDK (mcp==2.0.0) and against
engineering-knowledge's actual `search_knowledge` tool source
(src/engineering_knowledge/mcp/server.py and retrieval/service.py)
before writing this:

- The tool signature is `search_knowledge(query: str, strategy: str |
  None = None, max_results: int = 10) -> RetrievalResult`. `strategy` is
  omitted entirely from every call this adapter makes, never sent as
  None or any other placeholder value: the server already treats an
  absent argument as "use the configured default", which matches
  ReviewEvidenceQuery's own position that retrieval strategy is not an
  ARB domain concept.
- The structured response nests identity two levels deep:
  `RetrievalResult.results[].source_reference.section_path.headings` is
  an ordered tuple of heading strings, not a flat string; `RetrievalResult
  .results[].chunk.text` is a sibling field, not part of
  `source_reference`. The local wire models below mirror that nesting
  directly (with `extra="ignore"` at every level), so the fields this
  adapter does not consume (bm25_score, vector_distance, rrf_score,
  lexical_rank, vector_rank, section_occurrence, content_hash, ordinal,
  ...) are simply never modeled, not filtered out procedurally.
- `RetrievalResult.status` is one of "success", "empty", "partial";
  "partial" means the public max_results bound truncated further ranked
  results, not degraded infrastructure, so it maps to
  ReviewEvidenceStatus.SUCCESS the same as "success" does.
- Lifecycle, timeout, and exception classification: one bounded request
  opens and closes its own subprocess and session, wrapped in a single
  asyncio.timeout(), with the same `except*` set translating
  transport/protocol failures into ReviewEvidenceUnavailableError. There
  is no explicit tool-discovery step before calling search_knowledge; if
  the tool is missing, the call itself fails with an MCP protocol error,
  already one of the classified failures below.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import MCPError
from mcp.types import CallToolResult
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from architecture_review_board.domain.enums import ReviewEvidenceStatus
from architecture_review_board.domain.models import ReviewEvidence, ReviewEvidenceQuery
from architecture_review_board.domain.models import ReviewEvidenceSearchResult as SearchResult
from architecture_review_board.evidence.provider import ReviewEvidenceUnavailableError

_TOOL_NAME = "search_knowledge"
_SOURCE_TYPE = "engineering-knowledge"

# Transport/process failures, protocol-level errors, and a lifecycle
# timeout are expected external outcomes; nothing else is caught, so a
# bug in this class or the SDK still surfaces as a real traceback.
_EXPECTED_TRANSPORT_ERRORS = (
    OSError,
    TimeoutError,
    MCPError,
    anyio.BrokenResourceError,
    anyio.ClosedResourceError,
)

_VALID_STATUSES = frozenset({"success", "partial", "empty"})


class _SectionPathWire(BaseModel):
    model_config = ConfigDict(extra="ignore")

    headings: tuple[str, ...] = ()


class _SourceReferenceWire(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source_id: str
    document_id: str
    chunk_id: str
    relative_path: str
    section_path: _SectionPathWire


class _ChunkTextWire(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str


class _RetrievalHitWire(BaseModel):
    model_config = ConfigDict(extra="ignore")

    chunk: _ChunkTextWire
    source_reference: _SourceReferenceWire
    rank: int = Field(ge=1)


class _RetrievalResultWire(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str
    results: tuple[_RetrievalHitWire, ...] = ()


class EngineeringKnowledgeMcpEvidenceProvider:
    def __init__(
        self,
        command: str,
        args: Sequence[str] = (),
        timeout_seconds: float = 15.0,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._command = command
        self._args = list(args)
        self._timeout_seconds = timeout_seconds
        self._env = dict(env) if env else None

    async def search(self, query: ReviewEvidenceQuery) -> SearchResult:
        params = StdioServerParameters(command=self._command, args=self._args, env=self._env)
        arguments = {"query": query.query, "max_results": query.max_results}

        try:
            async with asyncio.timeout(self._timeout_seconds):
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.call_tool(_TOOL_NAME, arguments)
        except* _EXPECTED_TRANSPORT_ERRORS as eg:
            raise ReviewEvidenceUnavailableError(
                "engineering knowledge provider is unavailable"
            ) from eg

        return _parse_search_result(result)


def _source_reference(ref: _SourceReferenceWire) -> str:
    """A deterministic, compact, provenance-preserving opaque reference.

    Canonical JSON with sorted keys and no whitespace, so the same hit
    always produces the same string and no delimiter in any field value
    can create ambiguity the way hand-built string concatenation could.
    """
    payload = {
        "chunk_id": ref.chunk_id,
        "document_id": ref.document_id,
        "relative_path": ref.relative_path,
        "section_path": list(ref.section_path.headings),
        "source_id": ref.source_id,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _map_evidence(hit: _RetrievalHitWire, index: int) -> ReviewEvidence:
    return ReviewEvidence(
        evidence_id=f"knowledge-{index:03d}",
        source_type=_SOURCE_TYPE,
        source_reference=_source_reference(hit.source_reference),
        excerpt=hit.chunk.text,
    )


def _parse_search_result(result: CallToolResult) -> SearchResult:
    """Interprets a completed search_knowledge call.

    A contradictory response (a status inconsistent with whether results
    were returned, an unrecognized status, duplicate chunk ids) is
    treated as an incompatible response, not silently reinterpreted into
    EMPTY: the provider did not produce usable evidence, and guessing
    what it meant would let broken evidence look legitimate.
    """
    if result.is_error:
        raise ReviewEvidenceUnavailableError("engineering knowledge provider reported a tool error")
    if result.structured_content is None:
        raise ReviewEvidenceUnavailableError(
            "engineering knowledge provider returned no structured content"
        )

    try:
        wire = _RetrievalResultWire.model_validate(result.structured_content)
    except ValidationError as exc:
        # Never include the payload itself, which could carry arbitrary
        # provider data.
        raise ReviewEvidenceUnavailableError(
            "engineering knowledge provider returned an incompatible response"
        ) from exc

    if wire.status not in _VALID_STATUSES:
        raise ReviewEvidenceUnavailableError(
            "engineering knowledge provider returned an unrecognized status"
        )

    has_results = bool(wire.results)
    if wire.status == "empty" and has_results:
        raise ReviewEvidenceUnavailableError(
            "engineering knowledge provider returned an inconsistent response"
        )
    if wire.status in ("success", "partial") and not has_results:
        raise ReviewEvidenceUnavailableError(
            "engineering knowledge provider returned an inconsistent response"
        )

    # A well-behaved provider never returns the same chunk twice in one
    # response; accepting a duplicate could later let one piece of
    # evidence look like independent corroboration.
    chunk_ids = [hit.source_reference.chunk_id for hit in wire.results]
    if len(set(chunk_ids)) != len(chunk_ids):
        raise ReviewEvidenceUnavailableError(
            "engineering knowledge provider returned duplicate chunk ids"
        )

    if not has_results:
        return SearchResult(status=ReviewEvidenceStatus.EMPTY)

    # Order is exactly the provider's own rank order; evidence_id follows
    # that same order, never resorted or renumbered here.
    evidence = tuple(_map_evidence(hit, index) for index, hit in enumerate(wire.results, start=1))
    return SearchResult(status=ReviewEvidenceStatus.SUCCESS, evidence=evidence)
