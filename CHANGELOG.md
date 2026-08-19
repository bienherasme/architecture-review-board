# Changelog

## 0.1.0

Initial release: a structured, provider-neutral architecture review workflow built around
independent specialist assessment and explicit supervised reconciliation.

### Architecture

- `ArchitectureReviewRequest` domain contract and five independent specialist review
  dimensions (reliability, security, data, operability, complexity), executed concurrently by
  `ReviewCoordinator` with deterministic, canonically ordered results.
- `ReviewSupervisor` reconciles specialist output into a structured
  `ArchitectureReviewResult`: an `ArchitectureDecision`, explicit blocking findings and
  approval conditions, and explicit cross-dimension disagreements rather than forced consensus.
- Incomplete-board safety: a specialist failure is represented explicitly as
  `SpecialistReviewFailure`, the board continues, and an unconditional `APPROVE` is
  structurally invalid over incomplete coverage.
- Provider-neutral core ports (`StructuredReviewModel`, `StructuredSupervisorModel`,
  `ReviewEvidenceProvider`) with deterministic finding/condition/disagreement identity owned
  by application code, never by a model.

### Providers

- Optional OpenAI Responses API structured-output reference adapter
  (`OpenAIStructuredReviewModel`), installed through the `openai` extra.
- Optional MCP engineering-knowledge evidence reference adapter
  (`EngineeringKnowledgeMcpEvidenceProvider`), installed through the `mcp` extra, with
  provenance-preserving evidence references.
- Base install works without either optional SDK.

### Evaluation

- Deterministic, offline evaluation harness (`ArchitectureReviewEvaluator`) driving the real
  `ArchitectureReviewService`, with a lexical-anchor matcher (no embeddings, no LLM-as-judge).
- Fixed synthetic golden dataset v0.1 (seven cases covering all five dimensions plus an
  explicit disagreement case and a healthy-design guardrail case).
- Transparent per-metric denominators, no composite score.

### CLI

- `architecture-review-board review`: run one review from a JSON file or stdin.
- `architecture-review-board evaluate`: run an explicit evaluation dataset.
- `python -m architecture_review_board` as an equivalent entry point.

### Safety / trust boundaries

- Trusted rubric instructions are structurally separate from untrusted proposal/evidence
  content at every model boundary; no free-text prompt construction.
- No tool access on model calls; evidence retrieval happens outside model execution.
- MCP evidence child process uses explicit environment allowlisting, no shell execution, and a
  bounded timeout.
- Expected failures (provider unavailable, invalid structured output, evidence unavailable)
  are represented explicitly; unexpected errors propagate rather than being silently absorbed.
