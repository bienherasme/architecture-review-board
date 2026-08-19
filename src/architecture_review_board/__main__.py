"""Enables `python -m architecture_review_board`, calling the same CLI as the console script."""

from architecture_review_board.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
