"""Loading and content-addressing the versioned golden evaluation dataset."""

import hashlib
import json
from pathlib import Path

from architecture_review_board.evaluation.models import EvaluationDataset


def load_evaluation_dataset(path: str | Path) -> EvaluationDataset:
    """Load and validate an evaluation dataset from a JSON file.

    Malformed or inconsistent dataset content (duplicate case/risk/
    disagreement IDs, empty anchor groups, empty acceptable_decisions,
    ...) is rejected by EvaluationDataset's own validation. This function
    does not repair or normalize bad benchmark data.
    """
    raw = Path(path).read_text(encoding="utf-8")
    return EvaluationDataset.model_validate_json(raw)


def compute_dataset_fingerprint(dataset: EvaluationDataset) -> str:
    """A deterministic SHA-256 fingerprint over the dataset's canonical content.

    Serialized with sorted keys and compact separators, so the same
    dataset content always produces the same fingerprint regardless of
    source field order or incidental whitespace. This is evaluation
    provenance only: it proves which exact benchmark content produced a
    report, and is never used to identify runtime findings.
    """
    canonical = json.dumps(dataset.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
