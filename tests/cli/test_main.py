import io
import json
from pathlib import Path

import pytest

import architecture_review_board.cli.app as cli_main
from architecture_review_board.domain.enums import (
    ArchitectureDecision,
    FindingSeverity,
    ReviewDimension,
)
from architecture_review_board.domain.models import (
    ArchitectureReviewRequest,
    ArchitectureReviewResult,
    ReviewFinding,
    SpecialistReview,
    SpecialistReviewFailure,
)
from architecture_review_board.model.supervisor import StructuredSupervisorModelError


def write_request(tmp_path: Path, review_id: str = "cli-test") -> Path:
    path = tmp_path / "request.json"
    path.write_text(
        json.dumps(
            {
                "review_id": review_id,
                "title": "A proposal",
                "problem_statement": "A problem statement.",
                "proposed_solution": "A proposed solution.",
            }
        ),
        encoding="utf-8",
    )
    return path


def make_result(review_id: str = "cli-test") -> ArchitectureReviewResult:
    finding = ReviewFinding(
        finding_id="reliability-001",
        reviewer=ReviewDimension.RELIABILITY,
        title="Single instance failure domain",
        description="One instance holds all state.",
        severity=FindingSeverity.HIGH,
        rationale="No redundancy is described.",
        confidence=0.7,
    )
    reviews = tuple(
        SpecialistReview(
            review_id=review_id,
            reviewer=dimension,
            summary=f"{dimension.value} summary.",
            findings=(finding,) if dimension == ReviewDimension.RELIABILITY else (),
            overall_confidence=0.7,
        )
        for dimension in (
            ReviewDimension.RELIABILITY,
            ReviewDimension.DATA,
            ReviewDimension.OPERABILITY,
            ReviewDimension.COMPLEXITY,
        )
    )
    failure = SpecialistReviewFailure(
        reviewer=ReviewDimension.SECURITY, detail="specialist review unavailable"
    )
    return ArchitectureReviewResult(
        review_id=review_id,
        decision=ArchitectureDecision.REQUEST_CHANGES,
        summary="Needs rework before approval.",
        specialist_reviews=reviews,
        specialist_failures=(failure,),
        blocking_finding_ids=("reliability-001",),
    )


class FakeService:
    def __init__(
        self, result: ArchitectureReviewResult | None = None, error: Exception | None = None
    ) -> None:
        self._result = result
        self._error = error

    async def review(self, request: ArchitectureReviewRequest) -> ArchitectureReviewResult:
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def test_load_review_request_reads_file_and_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_request(tmp_path, review_id="from-file")
    from_file = cli_main._load_review_request(str(path))
    assert from_file.review_id == "from-file"

    stdin_payload = json.dumps(
        {
            "review_id": "from-stdin",
            "title": "t",
            "problem_statement": "p",
            "proposed_solution": "s",
        }
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_payload))
    from_stdin = cli_main._load_review_request("-")
    assert from_stdin.review_id == "from-stdin"


@pytest.mark.parametrize(
    "content",
    ["not json", json.dumps({"review_id": "", "title": "t", "problem_statement": "p"})],
)
def test_load_review_request_rejects_invalid_json_and_domain_errors(
    tmp_path: Path, content: str
) -> None:
    path = tmp_path / "bad.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(cli_main.InputError):
        cli_main._load_review_request(str(path))


def test_main_rejects_missing_model_before_building_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ARCHITECTURE_REVIEW_BOARD_MODEL", raising=False)

    def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("build_architecture_review_service must not be called")

    monkeypatch.setattr(cli_main, "build_architecture_review_service", fail_if_called)
    path = write_request(tmp_path)

    exit_code = cli_main.main(["review", str(path)])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "configuration error" in captured.err


def test_review_json_output_is_only_the_result_for_request_changes_with_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    result = make_result()
    monkeypatch.setattr(
        cli_main, "build_architecture_review_service", lambda config: FakeService(result=result)
    )
    path = write_request(tmp_path)

    exit_code = cli_main.main(["review", str(path), "--model", "gpt-test", "--json"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    parsed = ArchitectureReviewResult.model_validate_json(captured.out)
    assert parsed == result
    assert parsed.decision == ArchitectureDecision.REQUEST_CHANGES


def test_review_human_output_contains_key_structural_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    result = make_result()
    monkeypatch.setattr(
        cli_main, "build_architecture_review_service", lambda config: FakeService(result=result)
    )
    path = write_request(tmp_path)

    exit_code = cli_main.main(["review", str(path), "--model", "gpt-test"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "cli-test" in out
    assert "request_changes" in out
    assert "reliability-001" in out
    assert "security: FAILED" in out
    assert "blocking_finding_ids: reliability-001" in out


def test_evaluate_json_output_is_only_the_report_with_correct_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(
        json.dumps(
            {
                "dataset_id": "test-dataset",
                "version": "0.1",
                "cases": [
                    {
                        "case_id": "case-a",
                        "request": {
                            "review_id": "case-a",
                            "title": "t",
                            "problem_statement": "p",
                            "proposed_solution": "s",
                        },
                        "acceptable_decisions": ["request_changes"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    result = make_result(review_id="case-a")
    monkeypatch.setattr(
        cli_main, "build_architecture_review_service", lambda config: FakeService(result=result)
    )

    exit_code = cli_main.main(
        ["evaluate", str(dataset_path), "--model", "gpt-test", "--evidence", "disabled", "--json"]
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["run_metadata"]["provider"] == "openai"
    assert payload["run_metadata"]["model"] == "gpt-test"
    assert payload["run_metadata"]["evidence_mode"] == "disabled"
    assert payload["summary"]["completed_runs"] == 1


def test_evaluate_report_with_failed_case_runs_still_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(
        json.dumps(
            {
                "dataset_id": "test-dataset",
                "version": "0.1",
                "cases": [
                    {
                        "case_id": "case-a",
                        "request": {
                            "review_id": "case-a",
                            "title": "t",
                            "problem_statement": "p",
                            "proposed_solution": "s",
                        },
                        "acceptable_decisions": ["approve"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cli_main,
        "build_architecture_review_service",
        lambda config: FakeService(error=StructuredSupervisorModelError("provider unavailable")),
    )

    exit_code = cli_main.main(["evaluate", str(dataset_path), "--model", "gpt-test"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "failed_runs" in out
    assert "case-a" in out


def test_evaluate_rejects_malformed_dataset_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dataset_path = tmp_path / "bad_dataset.json"
    dataset_path.write_text(json.dumps({"dataset_id": "x", "version": "0.1", "cases": []}))

    exit_code = cli_main.main(["evaluate", str(dataset_path), "--model", "gpt-test"])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "evaluation error" in captured.err
