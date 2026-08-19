"""OpenAI-backed StructuredReviewModel and StructuredSupervisorModel.

Optional dependency: this module requires the `openai` package (the
`openai` extra, openai>=3.0,<4). Nothing else in the codebase imports it,
so a base install without `openai` still supports domain, deterministic
test-double reviewers, coordination, and supervision.

Verified against the installed openai==3.3.0 SDK before writing this,
rather than assumed from memory:

- The Responses API's structured-output helper is `client.responses.parse(
  model=..., instructions=..., input=..., text_format=SomeModel, store=...,
  timeout=...)`, returning a `ParsedResponse[SomeModel]`. It never uses
  Chat Completions or the Agents SDK.
- `responses.parse()` never raises for an incomplete, refused, or
  schema-unparseable response on its own terms: those surface as data on
  the returned object instead (`status`, `output_parsed`), unlike the
  older `chat.completions.parse()` helper, whose `LengthFinishReasonError`
  and `ContentFilterFinishReasonError` are raised only from
  `openai.lib._parsing._completions`, never from the Responses parsing
  path this adapter uses. This adapter therefore checks `response.status`
  and `response.output_parsed` explicitly after every call.
- Genuine transport/API failures (connection errors, timeouts, rate
  limits, auth, bad requests, server errors) all raise subclasses of
  `openai.APIError`, caught here as one group. A structured-output schema
  mismatch raises a synchronous `pydantic.ValidationError` from inside
  `parse()` itself, caught separately.
- The SDK's own strict-schema conversion (`openai.lib._pydantic.
  to_strict_json_schema`) marks every declared field required regardless
  of a Python-side default, so SpecialistReviewDraft and
  SupervisorReviewDraft are used directly as `text_format`; no private
  wire-schema mirror is needed the way it would be for chat.completions.

instructions= carries only SpecialistModelRequest.system_instructions or
SupervisorModelRequest.system_instructions, both trusted, fixed rubric
text. input= carries a single deterministic JSON string built from
Pydantic's own JSON-mode serialization of the proposal, evidence, and (for
the supervisor) specialist results: untrusted content, never folded into
instructions=, and never interpreted as anything but data to reason over.
No tools of any kind are requested, so evidence retrieval and any other
external action stay entirely outside the model. No streaming: one
request produces one complete response. store=False, since every
specialist and supervisor invocation is independent and nothing here
relies on provider-side conversation history.
"""

from __future__ import annotations

import json
from typing import TypeVar

from openai import APIError, AsyncOpenAI, OpenAIError
from pydantic import BaseModel, ValidationError

from architecture_review_board.model.base import (
    SpecialistModelRequest,
    StructuredReviewModelError,
)
from architecture_review_board.model.drafts import SpecialistReviewDraft
from architecture_review_board.model.supervisor import (
    StructuredSupervisorModelError,
    SupervisorModelRequest,
)
from architecture_review_board.model.supervisor_drafts import SupervisorReviewDraft

_DEFAULT_TIMEOUT_SECONDS = 60.0

_ModelT = TypeVar("_ModelT", bound=BaseModel)


def _compact_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _specialist_input(request: SpecialistModelRequest) -> str:
    return _compact_json(
        {
            "architecture_request": request.architecture_request.model_dump(mode="json"),
            "available_evidence": [
                evidence.model_dump(mode="json") for evidence in request.available_evidence
            ],
        }
    )


def _supervisor_input(request: SupervisorModelRequest) -> str:
    return _compact_json(
        {
            "architecture_request": request.architecture_request.model_dump(mode="json"),
            "specialist_reviews": [
                review.model_dump(mode="json") for review in request.specialist_reviews
            ],
            "specialist_failures": [
                failure.model_dump(mode="json") for failure in request.specialist_failures
            ],
        }
    )


class OpenAIStructuredReviewModel:
    """Concrete StructuredReviewModel and StructuredSupervisorModel backed by OpenAI.

    One concrete provider can reasonably implement both capabilities, but
    that does not merge the two application-level ports: this class
    simply satisfies generate_specialist_review and
    generate_supervisor_review independently, each against its own draft
    type and its own expected-error type.
    """

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        client: AsyncOpenAI | None = None,
    ) -> None:
        if not model.strip():
            raise ValueError("model must not be blank")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

        self._model = model
        self._timeout_seconds = timeout_seconds
        if client is not None:
            self._client = client
        else:
            # AsyncOpenAI() itself raises openai.OpenAIError when it cannot
            # resolve credentials (no api_key, no OPENAI_API_KEY). Callers
            # outside this module, notably the CLI composition root, should
            # not need the openai package importable just to catch that;
            # ValueError is this adapter's own construction-error contract.
            try:
                self._client = AsyncOpenAI(api_key=api_key)
            except OpenAIError as error:
                raise ValueError(f"could not construct the OpenAI client: {error}") from error

    async def generate_specialist_review(
        self, request: SpecialistModelRequest
    ) -> SpecialistReviewDraft:
        return await self._parse(
            instructions=request.system_instructions,
            input_text=_specialist_input(request),
            text_format=SpecialistReviewDraft,
            error_cls=StructuredReviewModelError,
        )

    async def generate_supervisor_review(
        self, request: SupervisorModelRequest
    ) -> SupervisorReviewDraft:
        return await self._parse(
            instructions=request.system_instructions,
            input_text=_supervisor_input(request),
            text_format=SupervisorReviewDraft,
            error_cls=StructuredSupervisorModelError,
        )

    async def _parse(
        self,
        *,
        instructions: str,
        input_text: str,
        text_format: type[_ModelT],
        error_cls: type[Exception],
    ) -> _ModelT:
        try:
            response = await self._client.responses.parse(
                model=self._model,
                instructions=instructions,
                input=input_text,
                text_format=text_format,
                store=False,
                timeout=self._timeout_seconds,
            )
        except APIError as error:
            raise error_cls("the model provider is unavailable") from error
        except ValidationError as error:
            raise error_cls(
                "the model returned output that did not match the expected schema"
            ) from error

        if response.status != "completed" or response.output_parsed is None:
            raise error_cls(
                f"the model did not return a usable structured response (status={response.status})"
            )

        return response.output_parsed
