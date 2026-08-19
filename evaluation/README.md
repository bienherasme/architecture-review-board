# Evaluation

`golden_v0_1.json` is the versioned, synthetic golden dataset for Architecture Review Board's
deterministic behavioral evaluation harness (`src/architecture_review_board/evaluation/`).

    dataset_id: architecture-review-board-golden
    version: 0.1
    fingerprint: 96be2ec7bed747f9339844889818530a055eb880f5ac2c0ff9f29d1c5b74d17f

The fingerprint is a SHA-256 hash over the dataset's canonical JSON content
(`compute_dataset_fingerprint`). It is recorded in every `EvaluationReport` as provenance: it
proves which exact benchmark content produced a given result, and changes if and only if the
dataset content changes.

## The seven cases

One case per specialist dimension exercising a clear primary concern (reliability, security,
data, operability, complexity), one case built around a genuine reliability-versus-complexity
tradeoff with an expected cross-dimension disagreement, and one comparatively healthy,
bounded, stateless design whose acceptable decisions exclude `REQUEST_CHANGES`, guarding
against unconditional alarmism.

Each case declares:

- `acceptable_decisions`: the set of `ArchitectureDecision` values a reasonable supervisor
  could reach, not one forced "correct" answer. Architecture review involves legitimate
  judgment; a case requiring a blocking change allows only `REQUEST_CHANGES`, while a tradeoff
  or a healthy design allows more than one.
- `expected_risks` (optional): a risk a specific reviewer dimension should recognize.
- `expected_disagreements` (optional): a material, competing position between two or more
  dimensions that the board should surface explicitly, not resolve into false consensus.

## What this measures

Whether the board completes, whether all five specialist dimensions execute, whether expected
material risks appear in the correct specialist dimension, whether the final decision falls
within the case's acceptable set, whether expected cross-dimension disagreements are
represented, and whether cited evidence is valid.

It does not measure universal architecture quality, intelligence, correctness of every
natural-language finding, business value, or produce an overall AI score. There is no
LLM-as-judge: every assertion is a deterministic, offline, lexically-anchored check over the
same public `ArchitectureReviewResult` any caller sees.

## Deterministic matcher

Expected risks and disagreements are matched with normalized lexical anchor groups (see
`evaluation/matching.py`): lowercase, punctuation-stripped, whitespace-collapsed text, checked
for substring containment. A finding matches a risk only if it comes from that risk's declared
reviewer dimension and every anchor group has at least one alias present in its normalized
text. No embeddings, no fuzzy matching, no semantic equivalence claim.

**Limitation:** a model may phrase a real, valid risk in wording no anchor group anticipated,
producing a false negative. This is a behavioral recall signal against a fixed vocabulary, not
a claim of universal correctness, and recall below 1.0 does not by itself mean a specialist
missed the underlying concern.

## Metrics and exact denominators

`EvaluationSummary`, computed once over every scheduled run (`repetitions x len(dataset.cases)`):

- `completion_rate` = completed runs / total runs
- `full_board_run_rate` = completed runs with zero specialist failures / total runs
- `acceptable_decision_rate` = completed runs whose decision is in that case's
  `acceptable_decisions` / completed runs
- `expected_risk_recall` = matched expected-risk instances / total expected-risk instances
  scheduled across all runs. A failed run contributes misses for its case's expected risks; it
  is never dropped from the denominator.
- `expected_disagreement_recall` = matched expected disagreements / total expected
  disagreements scheduled across all runs, same failed-run handling.
- `expected_evidence_citation_rate` = expected-risk instances with `expected_evidence_ids`
  whose matched finding actually cites one of them / total expected-risk instances that
  configure `expected_evidence_ids`.

A metric is `None`, never `0.0`, when its denominator is zero (for example, no case in the
active dataset declares `expected_evidence_ids`). `None` means "not measured," not "measured
as zero." There is no overall score: `compare_evaluation_reports` reports deltas per metric,
never a composite gain or a declared winner.

## Decision stability

With `--repetitions 1` (the default), `CaseDecisionStability.modal_agreement_rate` is `1.0` for
every completed case: a single run trivially agrees with itself. This is a real data point, not
evidence of repeat-run stability, and should not be read as such. Meaningful variability
analysis requires `repetitions > 1`; with a tie across repeated runs, `modal_decision` is
`None` rather than an arbitrary tiebreak.

## Dataset integrity rule

The golden dataset and the current reviewer/supervisor rubrics are considered fixed once a
public hosted-model baseline has been recorded from them. After that point:

- do not edit cases to improve results
- do not tune rubrics against benchmark failures and overwrite the same baseline
- any benchmark-changing modification requires a new dataset version and/or a clearly
  identified new model/prompt configuration

No hosted-model baseline has been recorded in this repository yet: no `OPENAI_API_KEY` has been
available during this evaluation harness's development. The harness and dataset are complete
and reproducible independent of that; nothing here is a fabricated or placeholder result.
