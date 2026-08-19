import pytest

from architecture_review_board.cli.composition import (
    EVIDENCE_MODE_ENV_VAR,
    MODEL_ENV_VAR,
    ConfigurationError,
    _allowlisted_env,
    resolve_review_run_config,
)


def test_resolve_review_run_config_applies_precedence_and_validates() -> None:
    env = {
        MODEL_ENV_VAR: "env-model",
        EVIDENCE_MODE_ENV_VAR: "mcp",
    }

    flag_wins = resolve_review_run_config(
        model="flag-model",
        evidence_mode=None,
        evidence_command=None,
        evidence_args='--strategy lexical --max 5',
        evidence_env_allowlist="  FOO , BAR ",
        env=env,
    )
    assert flag_wins.model == "flag-model"
    assert flag_wins.evidence_mode == "mcp"
    assert flag_wins.evidence_command == "engineering-knowledge"
    assert flag_wins.evidence_args == ("--strategy", "lexical", "--max", "5")
    assert flag_wins.evidence_env_allowlist == ("FOO", "BAR")

    env_fallback = resolve_review_run_config(
        model=None,
        evidence_mode=None,
        evidence_command=None,
        evidence_args=None,
        evidence_env_allowlist=None,
        env=env,
    )
    assert env_fallback.model == "env-model"

    with pytest.raises(ConfigurationError):
        resolve_review_run_config(
            model=None,
            evidence_mode=None,
            evidence_command=None,
            evidence_args=None,
            evidence_env_allowlist=None,
            env={},
        )

    with pytest.raises(ConfigurationError):
        resolve_review_run_config(
            model="m",
            evidence_mode=None,
            evidence_command=None,
            evidence_args="'unterminated",
            evidence_env_allowlist=None,
            env={},
        )

    with pytest.raises(ConfigurationError):
        resolve_review_run_config(
            model="m",
            evidence_mode=None,
            evidence_command=None,
            evidence_args=None,
            evidence_env_allowlist="not a valid name",
            env={},
        )


def test_allowlisted_env_only_forwards_named_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARB_TEST_ALLOWED", "visible")
    monkeypatch.setenv("OPENAI_API_KEY", "super-secret")

    result = _allowlisted_env(("ARB_TEST_ALLOWED",))

    assert result == {"ARB_TEST_ALLOWED": "visible"}
    assert result is not None
    assert "OPENAI_API_KEY" not in result
    assert _allowlisted_env(()) is None
