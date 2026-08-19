"""The CLI's composition root: turns resolved configuration into a runnable service.

This is the one place in the codebase allowed to know about concrete
provider adapters. Core application modules (reviewers, evaluation,
evidence) stay provider-neutral; this module is what wires a concrete
OpenAIStructuredReviewModel and, optionally, a concrete
EngineeringKnowledgeMcpEvidenceProvider into an ArchitectureReviewService.

Optional-SDK imports are deliberately deferred into the functions that
actually need them, not placed at module import time: importing this
module, and therefore importing the CLI at all, must not require openai
or mcp to be installed. Only actually building a review/evaluation
service does.
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from architecture_review_board.cli.errors import ConfigurationError
from architecture_review_board.evidence.provider import ReviewEvidenceProvider
from architecture_review_board.reviewers.coordinator import ReviewCoordinator
from architecture_review_board.reviewers.rubrics import (
    build_review_supervisor,
    build_specialist_reviewers,
)
from architecture_review_board.reviewers.service import ArchitectureReviewService

if TYPE_CHECKING:
    from architecture_review_board.providers.engineering_knowledge import (
        EngineeringKnowledgeMcpEvidenceProvider,
    )
    from architecture_review_board.providers.openai_model import OpenAIStructuredReviewModel

MODEL_ENV_VAR = "ARCHITECTURE_REVIEW_BOARD_MODEL"
EVIDENCE_MODE_ENV_VAR = "ARCHITECTURE_REVIEW_BOARD_EVIDENCE"
EVIDENCE_COMMAND_ENV_VAR = "ARCHITECTURE_REVIEW_BOARD_EVIDENCE_COMMAND"
EVIDENCE_ARGS_ENV_VAR = "ARCHITECTURE_REVIEW_BOARD_EVIDENCE_ARGS"
EVIDENCE_ENV_ALLOWLIST_ENV_VAR = "ARCHITECTURE_REVIEW_BOARD_EVIDENCE_ENV_ALLOWLIST"

_EVIDENCE_MODES = ("disabled", "mcp")
_DEFAULT_EVIDENCE_MODE = "disabled"
_DEFAULT_EVIDENCE_COMMAND = "engineering-knowledge"
_ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class ReviewRunConfig:
    """Fully resolved configuration for one CLI invocation, shared by review and evaluate.

    Both commands build this the same way from the same flags/environment
    variables, so evidence-mode comparisons between a review run and an
    evaluation run mean the same thing operationally.
    """

    model: str
    evidence_mode: str
    evidence_command: str
    evidence_args: tuple[str, ...]
    evidence_env_allowlist: tuple[str, ...]


def resolve_review_run_config(
    *,
    model: str | None,
    evidence_mode: str | None,
    evidence_command: str | None,
    evidence_args: str | None,
    evidence_env_allowlist: str | None,
    env: Mapping[str, str] | None = None,
) -> ReviewRunConfig:
    """Resolve CLI flags plus environment fallback into one immutable config.

    Precedence is always explicit flag over environment variable. Every
    problem here (missing model, invalid mode, malformed args/allowlist)
    is raised as ConfigurationError before any network or subprocess work
    happens.
    """
    environ = env if env is not None else os.environ

    resolved_model = model or environ.get(MODEL_ENV_VAR)
    if not resolved_model or not resolved_model.strip():
        raise ConfigurationError(f"no model configured: pass --model or set {MODEL_ENV_VAR}")

    resolved_mode = evidence_mode or environ.get(EVIDENCE_MODE_ENV_VAR) or _DEFAULT_EVIDENCE_MODE
    if resolved_mode not in _EVIDENCE_MODES:
        raise ConfigurationError(
            f"invalid evidence mode '{resolved_mode}': must be one of {', '.join(_EVIDENCE_MODES)}"
        )

    resolved_command = (
        evidence_command or environ.get(EVIDENCE_COMMAND_ENV_VAR) or _DEFAULT_EVIDENCE_COMMAND
    )
    if not resolved_command.strip():
        raise ConfigurationError("evidence command must not be blank")

    raw_args = (
        evidence_args if evidence_args is not None else environ.get(EVIDENCE_ARGS_ENV_VAR, "")
    )
    try:
        parsed_args = tuple(shlex.split(raw_args))
    except ValueError as error:
        raise ConfigurationError(f"could not parse --evidence-args: {error}") from error

    raw_allowlist = (
        evidence_env_allowlist
        if evidence_env_allowlist is not None
        else environ.get(EVIDENCE_ENV_ALLOWLIST_ENV_VAR, "")
    )
    allowlist_names = tuple(name.strip() for name in raw_allowlist.split(",") if name.strip())
    for name in allowlist_names:
        if not _ENV_VAR_NAME_RE.match(name):
            raise ConfigurationError(f"invalid environment variable name in allowlist: '{name}'")

    return ReviewRunConfig(
        model=resolved_model.strip(),
        evidence_mode=resolved_mode,
        evidence_command=resolved_command.strip(),
        evidence_args=parsed_args,
        evidence_env_allowlist=allowlist_names,
    )


def build_architecture_review_service(config: ReviewRunConfig) -> ArchitectureReviewService:
    """The composition root: one concrete model backs both specialists and the supervisor.

    A future CLI feature could justify separate specialist/supervisor
    models; v0.1.0 deliberately keeps one provider object satisfying both
    ports, for reproducibility.
    """
    model = _build_openai_model(config.model)
    coordinator = ReviewCoordinator(build_specialist_reviewers(model))
    supervisor = build_review_supervisor(model)

    evidence_provider: ReviewEvidenceProvider | None = None
    if config.evidence_mode == "mcp":
        evidence_provider = _build_evidence_provider(config)

    return ArchitectureReviewService(coordinator, supervisor, evidence_provider=evidence_provider)


def _build_openai_model(model_id: str) -> OpenAIStructuredReviewModel:
    try:
        from architecture_review_board.providers.openai_model import OpenAIStructuredReviewModel
    except ModuleNotFoundError as error:
        raise ConfigurationError(
            "the openai package is not installed; install the 'openai' extra"
        ) from error

    try:
        return OpenAIStructuredReviewModel(model=model_id)
    except ValueError as error:
        raise ConfigurationError(f"could not configure the OpenAI provider: {error}") from error


def _build_evidence_provider(config: ReviewRunConfig) -> EngineeringKnowledgeMcpEvidenceProvider:
    try:
        from architecture_review_board.providers.engineering_knowledge import (
            EngineeringKnowledgeMcpEvidenceProvider,
        )
    except ModuleNotFoundError as error:
        raise ConfigurationError(
            "the mcp package is not installed; install the 'mcp' extra to use --evidence mcp"
        ) from error

    return EngineeringKnowledgeMcpEvidenceProvider(
        command=config.evidence_command,
        args=config.evidence_args,
        env=_allowlisted_env(config.evidence_env_allowlist),
    )


def _allowlisted_env(allowlist: Sequence[str]) -> dict[str, str] | None:
    """Map only explicitly allowlisted variable names into the MCP child's environment.

    Never forwards os.environ wholesale, and never includes
    OPENAI_API_KEY or any other value unless a user explicitly names it:
    that credential belongs to the parent OpenAI SDK process, not the
    evidence child process.
    """
    if not allowlist:
        return None
    return {name: os.environ[name] for name in allowlist if name in os.environ}
