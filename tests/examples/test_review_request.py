import json
from pathlib import Path

from architecture_review_board.domain.models import ArchitectureReviewRequest

EXAMPLE_PATH = Path("examples/review_request.json")


def test_example_review_request_validates() -> None:
    data = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))

    request = ArchitectureReviewRequest.model_validate(data)

    assert request.review_id
    assert request.title
    assert request.affected_components
