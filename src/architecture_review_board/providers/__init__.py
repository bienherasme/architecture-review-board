"""Concrete adapters behind ARB's provider-neutral ports.

Every module here depends on an optional third-party SDK. Nothing
outside this package imports any of them, so importing
architecture_review_board itself never requires openai or mcp; only
explicitly importing a specific adapter module does, and only that
module's own optional dependency.
"""
