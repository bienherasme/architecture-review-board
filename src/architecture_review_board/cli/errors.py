"""CLI-facing error types.

Two categories, not a taxonomy: ConfigurationError covers everything
wrong with how the command was set up before any review or evaluation
work starts, including a missing optional SDK (a capability problem, not
a programming defect, from a CLI user's perspective). InputError covers
a user-supplied review request or dataset file that could not be read or
does not validate. Both are caught at the CLI boundary and reported as a
concise stderr message with no traceback; anything else is unexpected and
is allowed to surface normally.
"""


class ConfigurationError(Exception):
    """The command could not be configured to run.

    Covers a missing/blank model, an invalid evidence mode, a missing
    optional SDK (openai or mcp not installed), a provider that failed to
    construct (for example missing OPENAI_API_KEY), and malformed
    evidence command/args/env-allowlist configuration.
    """


class InputError(Exception):
    """A user-supplied review request or evaluation dataset is unusable.

    Covers a missing file, invalid JSON, and domain/dataset validation
    failure. Never repairs the input; only reports it concisely.
    """
