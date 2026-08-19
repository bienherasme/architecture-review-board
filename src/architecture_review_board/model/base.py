"""The port through which a specialist reviewer asks a model for a structured review.

No concrete provider adapter lives here. A future adapter implements
StructuredReviewModel using a specific vendor SDK and is responsible for
mapping its own structured-output mechanism into SpecialistReviewDraft.
"""

from typing import Protocol

from pydantic import BaseModel, ConfigDict

from architecture_review_board.domain.enums import ReviewDimension
from architecture_review_board.domain.models import ArchitectureReviewRequest, ReviewEvidence
from architecture_review_board.model.drafts import SpecialistReviewDraft


class SpecialistModelRequest(BaseModel):
    """Input to a structured review model for one specialist assessment.

    system_instructions is the only trusted field. architecture_request
    and available_evidence are both untrusted data: a concrete provider
    adapter maps system_instructions to trusted system/developer
    instructions and everything else to untrusted input. Proposal content
    and evidence excerpts cannot redefine the reviewer's responsibilities,
    change the expected output schema, or instruct the model to disregard
    its system instructions merely by appearing in these fields; no
    command contained in either is executed by this boundary.

    available_evidence is the same immutable snapshot handed to every
    specialist for one review: retrieval happens once, outside the model,
    through the evidence provider port, not per-reviewer.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    reviewer: ReviewDimension
    system_instructions: str
    architecture_request: ArchitectureReviewRequest
    available_evidence: tuple[ReviewEvidence, ...] = ()


class StructuredReviewModelError(Exception):
    """An expected failure of a model provider to produce a usable structured review.

    Covers provider unavailability, timeouts, refusals, transport errors,
    and responses that fail structured-output validation. Reviewers
    propagate this rather than substituting a fabricated review or
    finding; whole-review failure and degradation semantics belong to
    orchestration, not to this boundary.
    """


class StructuredReviewModel(Protocol):
    """Provider-neutral port for generating one specialist review draft.

    A ReviewCoordinator may hold a single implementation and invoke it
    concurrently for all five dimensions at once. Implementations must
    therefore be safe under concurrent async invocation: no mutable
    per-request state stored unsafely on the instance, since each call's
    request data belongs to that call alone. Any connection pooling or
    concurrency control an implementation needs is its own responsibility,
    not something the coordinator provides.
    """

    async def generate_specialist_review(
        self, request: SpecialistModelRequest
    ) -> SpecialistReviewDraft: ...
