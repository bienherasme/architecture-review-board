"""The architecture-review-board command line: `review` and `evaluate`.

stdout/stderr contract: with --json, stdout carries only the serialized
result and nothing else (no banners, no progress, no "done" lines), so
output composes with jq/scripts. Without --json, stdout carries a
concise human report. Every error path prints one line to stderr and
returns a nonzero exit code; stdout stays empty on failure. Expected
errors (configuration, input, review/evaluation execution) never print a
traceback; only a genuinely unexpected defect does.

Exit code 0 means "a result was produced," not "the result was positive":
REQUEST_CHANGES, specialist failures inside a valid result, and a
benchmark report containing FAILED case runs all exit 0, because those
are board/evaluation outcomes, not CLI infrastructure failures.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from architecture_review_board.cli.composition import (
    ReviewRunConfig,
    build_architecture_review_service,
    resolve_review_run_config,
)
from architecture_review_board.cli.errors import ConfigurationError, InputError
from architecture_review_board.cli.output import format_evaluation_report, format_review_result
from architecture_review_board.domain.models import ArchitectureReviewRequest
from architecture_review_board.evaluation.dataset import load_evaluation_dataset
from architecture_review_board.evaluation.evaluator import (
    MAX_EVALUATION_REPETITIONS,
    ArchitectureReviewEvaluator,
)
from architecture_review_board.evaluation.models import EvaluationRunMetadata
from architecture_review_board.model.supervisor import StructuredSupervisorModelError
from architecture_review_board.reviewers.supervisor import ReviewSupervisorError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="architecture-review-board",
        description="Run one structured architecture review or the golden evaluation dataset.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    review_parser = subparsers.add_parser("review", help="Run one architecture review.")
    review_parser.add_argument(
        "input", help="Path to a JSON ArchitectureReviewRequest file, or - for stdin."
    )
    review_parser.add_argument(
        "--json", action="store_true", help="Emit only the serialized result as JSON."
    )
    _add_shared_arguments(review_parser)

    evaluate_parser = subparsers.add_parser("evaluate", help="Run an evaluation dataset.")
    evaluate_parser.add_argument("dataset", help="Path to an evaluation dataset JSON file.")
    evaluate_parser.add_argument(
        "--repetitions",
        type=int,
        default=1,
        help=f"Times to repeat each case (1-{MAX_EVALUATION_REPETITIONS}, default: 1).",
    )
    evaluate_parser.add_argument(
        "--json", action="store_true", help="Emit only the serialized report as JSON."
    )
    _add_shared_arguments(evaluate_parser)

    return parser


def _add_shared_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--model",
        default=None,
        help="Hosted model ID. Falls back to ARCHITECTURE_REVIEW_BOARD_MODEL.",
    )
    parser.add_argument(
        "--evidence",
        choices=("disabled", "mcp"),
        default=None,
        help="Evidence provider mode. Falls back to ARCHITECTURE_REVIEW_BOARD_EVIDENCE "
        "(default: disabled).",
    )
    parser.add_argument(
        "--evidence-command",
        default=None,
        help="MCP evidence server command, used when --evidence mcp. Falls back to "
        "ARCHITECTURE_REVIEW_BOARD_EVIDENCE_COMMAND (default: engineering-knowledge).",
    )
    parser.add_argument(
        "--evidence-args",
        default=None,
        help="Shell-quoted MCP server arguments. Falls back to "
        "ARCHITECTURE_REVIEW_BOARD_EVIDENCE_ARGS.",
    )
    parser.add_argument(
        "--evidence-env-allowlist",
        default=None,
        help="Comma-separated environment variable names to forward to the MCP evidence "
        "process. Falls back to ARCHITECTURE_REVIEW_BOARD_EVIDENCE_ENV_ALLOWLIST.",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = resolve_review_run_config(
            model=args.model,
            evidence_mode=args.evidence,
            evidence_command=args.evidence_command,
            evidence_args=args.evidence_args,
            evidence_env_allowlist=args.evidence_env_allowlist,
        )
    except ConfigurationError as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return 1

    if args.command == "review":
        return _run_review(args, config)
    return _run_evaluate(args, config)


def _run_review(args: argparse.Namespace, config: ReviewRunConfig) -> int:
    try:
        request = _load_review_request(args.input)
    except InputError as error:
        print(f"input error: {error}", file=sys.stderr)
        return 1

    try:
        service = build_architecture_review_service(config)
    except ConfigurationError as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return 1

    try:
        result = asyncio.run(service.review(request))
    except (StructuredSupervisorModelError, ReviewSupervisorError) as error:
        print(f"review execution error: {error}", file=sys.stderr)
        return 1

    if args.json:
        print(result.model_dump_json())
    else:
        print(format_review_result(result))
    return 0


def _run_evaluate(args: argparse.Namespace, config: ReviewRunConfig) -> int:
    try:
        dataset = load_evaluation_dataset(args.dataset)
    except OSError as error:
        print(f"evaluation error: could not read '{args.dataset}': {error}", file=sys.stderr)
        return 1
    except ValidationError as error:
        print(f"evaluation error: {_summarize_validation_error(error)}", file=sys.stderr)
        return 1

    try:
        service = build_architecture_review_service(config)
    except ConfigurationError as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return 1

    run_metadata = EvaluationRunMetadata(
        provider="openai",
        model=config.model,
        evidence_mode=config.evidence_mode,
        provider_sdk_version=_openai_sdk_version(),
    )
    evaluator = ArchitectureReviewEvaluator(service, dataset, run_metadata=run_metadata)

    try:
        report = asyncio.run(evaluator.evaluate(repetitions=args.repetitions))
    except ValueError as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return 1

    if args.json:
        print(report.model_dump_json())
    else:
        print(format_evaluation_report(report))
    return 0


def _load_review_request(path: str) -> ArchitectureReviewRequest:
    raw = _read_input(path)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise InputError(f"invalid JSON: {error}") from error

    try:
        return ArchitectureReviewRequest.model_validate(data)
    except ValidationError as error:
        raise InputError(_summarize_validation_error(error)) from error


def _read_input(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as error:
        raise InputError(f"could not read '{path}': {error}") from error


def _summarize_validation_error(error: ValidationError) -> str:
    first = error.errors()[0]
    location = ".".join(str(part) for part in first["loc"]) or "<root>"
    count = error.error_count()
    suffix = "" if count == 1 else f" (and {count - 1} more)"
    return f"{location}: {first['msg']}{suffix}"


def _openai_sdk_version() -> str | None:
    try:
        return importlib.metadata.version("openai")
    except importlib.metadata.PackageNotFoundError:
        return None
