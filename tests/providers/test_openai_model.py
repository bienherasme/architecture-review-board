import pytest

pytest.importorskip("openai")
pytest.importorskip("httpx2")

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import httpx2
import openai
from pydantic import ValidationError

from architecture_review_board.domain.enums import ArchitectureDecision, ReviewDimension
from architecture_review_board.domain.models import ArchitectureReviewRequest, ReviewEvidence
from architecture_review_board.model.base import SpecialistModelRequest, StructuredReviewModelError
from architecture_review_board.model.drafts import SpecialistReviewDraft
from architecture_review_board.model.supervisor import (
    StructuredSupervisorModelError,
    SupervisorModelRequest,
)
from architecture_review_board.model.supervisor_drafts import SupervisorReviewDraft
from architecture_review_board.providers.openai_model import OpenAIStructuredReviewModel


@dataclass
class _FakeParsedResponse:
    status: str
    output_parsed: Any = None


class _FakeResponses:
    def __init__(self, result: object = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.received_kwargs: dict[str, Any] | None = None

    async def parse(self, **kwargs: Any) -> Any:
        self.received_kwargs = kwargs
        if self.error is not None:
            raise self.error
        return self.result


class _FakeClient:
    def __init__(self, result: object = None, error: Exception | None = None) -> None:
        self.responses = _FakeResponses(result=result, error=error)


def make_request() -> ArchitectureReviewRequest:
    return ArchitectureReviewRequest(
        review_id="rev-1",
        title="Queue-based order processing",
        problem_statement="Order processing needs to absorb bursty traffic.",
        proposed_solution="Introduce a durable queue between checkout and fulfillment.",
    )


def _connection_error() -> openai.APIConnectionError:
    return openai.APIConnectionError(request=httpx2.Request("POST", "https://api.openai.com"))


def test_specialist_and_supervisor_map_instructions_separately_from_untrusted_data() -> None:
    specialist_draft = SpecialistReviewDraft(summary="No material issues.", overall_confidence=0.8)
    evidence = (
        ReviewEvidence(
            evidence_id="e-1",
            source_type="engineering-knowledge",
            source_reference="ref",
            excerpt="text",
        ),
    )
    specialist_client = _FakeClient(
        result=_FakeParsedResponse(status="completed", output_parsed=specialist_draft)
    )
    specialist_model = OpenAIStructuredReviewModel(
        model="gpt-test", client=specialist_client  # type: ignore[arg-type]
    )
    specialist_request = SpecialistModelRequest(
        reviewer=ReviewDimension.RELIABILITY,
        system_instructions="Trusted reliability instructions.",
        architecture_request=make_request(),
        available_evidence=evidence,
    )

    returned_draft = asyncio.run(specialist_model.generate_specialist_review(specialist_request))

    assert returned_draft == specialist_draft
    kwargs = specialist_client.responses.received_kwargs
    assert kwargs is not None
    assert kwargs["instructions"] == "Trusted reliability instructions."
    assert kwargs["text_format"] is SpecialistReviewDraft
    assert kwargs["store"] is False
    assert "tools" not in kwargs
    payload = json.loads(kwargs["input"])
    assert payload["architecture_request"]["title"] == "Queue-based order processing"
    assert payload["available_evidence"][0]["evidence_id"] == "e-1"
    assert "Trusted reliability instructions." not in kwargs["input"]

    supervisor_draft = SupervisorReviewDraft(decision=ArchitectureDecision.APPROVE, summary="Fine.")
    supervisor_client = _FakeClient(
        result=_FakeParsedResponse(status="completed", output_parsed=supervisor_draft)
    )
    supervisor_model = OpenAIStructuredReviewModel(
        model="gpt-test", client=supervisor_client  # type: ignore[arg-type]
    )
    supervisor_request = SupervisorModelRequest(
        system_instructions="Trusted supervisor instructions.",
        architecture_request=make_request(),
        specialist_reviews=(),
        specialist_failures=(),
    )

    returned_supervisor_draft = asyncio.run(
        supervisor_model.generate_supervisor_review(supervisor_request)
    )

    assert returned_supervisor_draft == supervisor_draft
    supervisor_kwargs = supervisor_client.responses.received_kwargs
    assert supervisor_kwargs is not None
    assert supervisor_kwargs["instructions"] == "Trusted supervisor instructions."
    assert supervisor_kwargs["text_format"] is SupervisorReviewDraft
    assert "Trusted supervisor instructions." not in supervisor_kwargs["input"]


@pytest.mark.parametrize(
    ("is_supervisor", "result", "error"),
    [
        (False, None, _connection_error()),
        (False, None, ValidationError.from_exception_data("SpecialistReviewDraft", [])),
        (False, _FakeParsedResponse(status="completed", output_parsed=None), None),
        (True, None, _connection_error()),
    ],
)
def test_provider_failure_modes_translate_to_typed_errors(
    is_supervisor: bool, result: object, error: Exception | None
) -> None:
    client = _FakeClient(result=result, error=error)
    model = OpenAIStructuredReviewModel(model="gpt-test", client=client)  # type: ignore[arg-type]

    if is_supervisor:
        supervisor_request = SupervisorModelRequest(
            system_instructions="Trusted instructions.",
            architecture_request=make_request(),
            specialist_reviews=(),
            specialist_failures=(),
        )
        with pytest.raises(StructuredSupervisorModelError):
            asyncio.run(model.generate_supervisor_review(supervisor_request))
    else:
        specialist_request = SpecialistModelRequest(
            reviewer=ReviewDimension.SECURITY,
            system_instructions="Trusted instructions.",
            architecture_request=make_request(),
        )
        with pytest.raises(StructuredReviewModelError):
            asyncio.run(model.generate_specialist_review(specialist_request))


def test_constructor_validates_model_and_timeout() -> None:
    with pytest.raises(ValueError):
        OpenAIStructuredReviewModel(model="   ")

    with pytest.raises(ValueError):
        OpenAIStructuredReviewModel(model="gpt-test", timeout_seconds=0)
