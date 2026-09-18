"""Chunking and embedding pipeline for the knowledge documents."""

import sys
from pathlib import Path


def _ensure_semantic_chunker_on_path() -> None:
    """Make chunking/semantic_chunker.py importable.

    It lives beside this package in the container image and one level up in
    the repo, so both layouts are checked rather than duplicating the file.
    """
    here = Path(__file__).resolve().parent
    for candidate in (here.parent / "chunking", here.parent.parent / "chunking"):
        if (candidate / "semantic_chunker.py").exists():
            sys.path.insert(0, str(candidate))
            return


_ensure_semantic_chunker_on_path()
