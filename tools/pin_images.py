#!/usr/bin/env python3
"""Every base image this repository builds on, pinned by digest (V6-35).

A tag moves: `python:3.12-slim` today is not the image it was when the last
release was built, so two builds of one commit could differ, and a release
could change underneath its own tag. Each reference is written
`name:tag@sha256:<digest>` instead -- the tag for the reader, the digest for
Docker, which pulls exactly that and nothing else. The digest is the
multi-architecture index's, so one pin serves amd64 and arm64 alike.

Where a reference is: a Dockerfile's `FROM`, an `ARG ..._IMAGE=` default
that a `FROM` uses, and a compose file's `image: ${VAR:-...}` default for an
image this repository does not build itself.

    python tools/pin_images.py            # check: every reference pinned, and
                                          #   one tag pinned to one digest
    python tools/pin_images.py --update   # each tag re-resolved and rewritten

Checking reads files only; it is what tests/security/test_supply_chain.py
runs. Updating asks the registry (`docker buildx imagetools inspect`), and
is how a base image is moved on, deliberately, in a commit of its own.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent

#: An image reference: registry/name, a tag, and perhaps a digest.
REF = r"(?P<ref>(?P<name>[a-z0-9][a-z0-9._/-]*):(?P<tag>[A-Za-z0-9][A-Za-z0-9._-]*)(?:@(?P<digest>sha256:[0-9a-f]{64}))?)"
PATTERNS = (
    re.compile(r"^FROM\s+(?:--platform=\S+\s+)?" + REF + r"(?=\s|$)", re.MULTILINE),
    re.compile(r"^ARG\s+\w*IMAGE=" + REF + r"\s*$", re.MULTILINE),
    re.compile(r"image:\s*\$\{\w+:-" + REF + r"\}"),
)
#: This repository's own images, which a release tags rather than pins.
OWN = re.compile(r"^(mcfaddja/)?nl2sql-")
SKIP = {"node_modules", "target", ".venv", ".git", "dist", "coverage"}


@dataclass(frozen=True)
class Reference:
    path: str
    line: int
    name: str
    tag: str
    digest: str | None

    @property
    def tagged(self) -> str:
        return f"{self.name}:{self.tag}"

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.tagged}"


def files(root: Path = ROOT) -> list[Path]:
    found = []
    for path in sorted(root.rglob("*")):
        if any(part in SKIP for part in path.relative_to(root).parts) or not path.is_file():
            continue
        if path.name == "Dockerfile" or path.name.endswith(".Dockerfile") or path.name == "docker-compose.yml":
            found.append(path)
    return found


def references(path: Path, root: Path = ROOT) -> list[Reference]:
    text = path.read_text()
    found = []
    for pattern in PATTERNS:
        for match in pattern.finditer(text):
            if OWN.match(match["name"]):
                continue
            line = text.count("\n", 0, match.start("ref")) + 1
            found.append(Reference(str(path.relative_to(root)), line, match["name"], match["tag"], match["digest"]))
    return sorted(found, key=lambda ref: ref.line)


def problems(refs: list[Reference]) -> list[str]:
    """What is not pinned, and any tag pinned to two digests."""
    found = [f"{ref}: not pinned by digest" for ref in refs if ref.digest is None]
    digests: dict[str, set[str]] = defaultdict(set)
    for ref in refs:
        if ref.digest:
            digests[ref.tagged].add(ref.digest)
    found += [f"{tagged} is pinned to {len(seen)} different digests" for tagged, seen in sorted(digests.items())
              if len(seen) > 1]
    return found


def resolve(tagged: str) -> str:
    """The digest of the tag's multi-architecture index, from the registry."""
    out = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", tagged, "--format", "{{json .Manifest}}"],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)["digest"]


def update(paths: list[Path], resolver: Callable[[str], str] = resolve, root: Path = ROOT) -> dict[str, str]:
    """Every reference rewritten to its tag's digest now. Returns the pins."""
    pins: dict[str, str] = {}
    for path in paths:
        for ref in references(path, root):
            if ref.tagged not in pins:
                pins[ref.tagged] = resolver(ref.tagged)
    for path in paths:
        text = path.read_text()
        for pattern in PATTERNS:
            def pin(match: re.Match) -> str:
                if OWN.match(match["name"]):
                    return match[0]
                tagged = f"{match['name']}:{match['tag']}"
                start, end = match.start("ref") - match.start(), match.end("ref") - match.start()
                return match[0][:start] + f"{tagged}@{pins[tagged]}" + match[0][end:]
            text = pattern.sub(pin, text)
        path.write_text(text)
    return pins


def main(argv: list[str] | None = None, root: Path = ROOT, resolver: Callable[[str], str] = resolve) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--update", action="store_true", help="re-resolve every tag and rewrite its digest")
    args = parser.parse_args(argv)
    paths = files(root)
    if args.update:
        for tagged, digest in sorted(update(paths, resolver, root).items()):
            print(f"{tagged}@{digest}")
        return 0
    found = problems([ref for path in paths for ref in references(path, root)])
    for problem in found:
        print(problem, file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":  # pragma: no cover - the script's own entry
    sys.exit(main())
