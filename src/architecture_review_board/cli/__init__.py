"""The architecture-review-board CLI: `review` and `evaluate`.

Importing this package must never require openai or mcp; see
composition.py for where those optional SDKs are actually loaded.
"""

from architecture_review_board.cli.app import main

__all__ = ["main"]
