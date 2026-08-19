import copy

import pytest
from pydantic import ValidationError

from architecture_review_board.domain.enums import REVIEW_DIMENSION_ORDER, ArchitectureDecision
from architecture_review_board.evaluation.dataset import (
    compute_dataset_fingerprint,
    load_evaluation_dataset,
)
from architecture_review_board.evaluation.models import EvaluationDataset

GOLDEN_DATASET_PATH = "evaluation/golden_v0_1.json"


def _minimal_case() -> dict:
    return {
        "case_id": "case-a",
        "request": {
            "review_id": "case-a",
            "title": "A proposal",
            "problem_statement": "A problem statement.",
            "proposed_solution": "A proposed solution.",
        },
        "acceptable_decisions": ["approve"],
    }


def _minimal_dataset(cases: list[dict]) -> dict:
    return {"dataset_id": "test-dataset", "version": "0.1", "cases": cases}


def test_golden_dataset_loads_and_covers_all_dimensions_and_guardrails() -> None:
    dataset = load_evaluation_dataset(GOLDEN_DATASET_PATH)

    assert 6 <= len(dataset.cases) <= 8

    risk_reviewers = {
        risk.reviewer for case in dataset.cases for risk in case.expected_risks
    }
    assert risk_reviewers == set(REVIEW_DIMENSION_ORDER)

    assert any(case.expected_disagreements for case in dataset.cases)

    healthy_cases = [
        case
        for case in dataset.cases
        if ArchitectureDecision.REQUEST_CHANGES not in case.acceptable_decisions
    ]
    assert healthy_cases


def test_dataset_fingerprint_is_deterministic_and_content_sensitive() -> None:
    dataset = load_evaluation_dataset(GOLDEN_DATASET_PATH)

    assert compute_dataset_fingerprint(dataset) == compute_dataset_fingerprint(dataset)

    mutated = EvaluationDataset.model_validate(
        {**dataset.model_dump(mode="json"), "version": "0.2"}
    )
    assert compute_dataset_fingerprint(mutated) != compute_dataset_fingerprint(dataset)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data["cases"].append(copy.deepcopy(data["cases"][0])),
        lambda data: data["cases"][0].__setitem__(
            "expected_risks",
            [
                {
                    "risk_id": "dup",
                    "reviewer": "reliability",
                    "anchor_groups": [["single instance"]],
                },
                {
                    "risk_id": "dup",
                    "reviewer": "security",
                    "anchor_groups": [["shared credential"]],
                },
            ],
        ),
        lambda data: data["cases"][0].__setitem__(
            "expected_disagreements",
            [
                {"disagreement_id": "dup", "reviewers": ["reliability", "complexity"]},
                {"disagreement_id": "dup", "reviewers": ["security", "data"]},
            ],
        ),
        lambda data: data["cases"][0].__setitem__(
            "expected_risks",
            [{"risk_id": "r-1", "reviewer": "reliability", "anchor_groups": [[]]}],
        ),
        lambda data: data["cases"][0].__setitem__("acceptable_decisions", []),
    ],
)
def test_malformed_dataset_content_is_rejected(mutate) -> None:
    data = _minimal_dataset([_minimal_case()])
    mutate(data)

    with pytest.raises(ValidationError):
        EvaluationDataset.model_validate(data)
