"""What an image installs is what was reviewed (V6-24, the second review's C-07).

Every Python image installs from a lock file -- each package at one version,
each file with its hash -- so a version republished under the same number,
or a file swapped on the index, is refused at build time rather than shipped.
The `requirements.txt` beside each lock is what a person edits;
`uv pip compile --generate-hashes --universal` writes the lock from it, and
these tests catch the two drifting apart. The shared package is the one
thing installed without a hash: it is this repository's own code.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent

#: Each image's Dockerfile and the lock it installs from.
LOCKED = {
    "agent/Dockerfile": "agent/requirements.lock",
    "review/Dockerfile": "review/requirements.lock",
    "auth/Dockerfile": "auth/requirements.lock",
    "ldap/Dockerfile": "ldap/requirements.lock",
    "docker/Dockerfile": "data_gen/requirements.lock",
}


def _name(requirement: str) -> str:
    """A requirement's project name, normalised the way PyPI compares them."""
    return re.split(r"[\s\[<>=~!;]", requirement.strip(), maxsplit=1)[0].lower().replace("_", "-")


def _top_level(path: Path) -> set[str]:
    return {
        _name(line)
        for line in path.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith(("#", "-"))
    }


def _locked(path: Path) -> dict[str, list[str]]:
    """Each locked package's version line and the hashes that follow it."""
    found: dict[str, list[str]] = {}
    current = None
    for line in path.read_text().splitlines():
        if re.match(r"^[A-Za-z0-9]", line):
            current = _name(line)
            found[current] = [line]
        elif current and "--hash=" in line:
            found[current].append(line.strip())
    return found


@pytest.mark.parametrize("dockerfile,lock", sorted(LOCKED.items()))
def test_every_python_image_installs_its_lock_with_hashes(dockerfile: str, lock: str):
    text = (ROOT / dockerfile).read_text()
    installs = [line for line in text.splitlines() if "pip install" in line]
    assert installs, f"{dockerfile} installs nothing"
    for line in installs:
        if "nl2sql-common" in line:
            # This repository's own package, from this checkout.
            assert "--no-deps" in line, line
            continue
        assert "--require-hashes" in line and "requirements.lock" in line, f"{dockerfile}: {line.strip()}"
    assert f"COPY {lock}" in text


@pytest.mark.parametrize("lock", sorted(set(LOCKED.values())))
def test_every_locked_package_is_one_version_with_its_hashes(lock: str):
    locked = _locked(ROOT / lock)
    assert locked, f"{lock} names nothing"
    for name, (pin, *hashes) in locked.items():
        assert "==" in pin, f"{lock}: {name} is not pinned to one version"
        assert hashes and all(re.search(r"--hash=sha256:[0-9a-f]{64}", h) for h in hashes), f"{lock}: {name}"


@pytest.mark.parametrize("lock", sorted(set(LOCKED.values())))
def test_every_requirement_a_person_wrote_is_in_its_lock(lock: str):
    """The lock is generated from `requirements.txt`; a requirement added
    there and not compiled into the lock would not be installed."""
    wanted = _top_level(ROOT / lock.replace(".lock", ".txt"))
    assert wanted <= set(_locked(ROOT / lock)), f"{lock} is stale: missing {wanted - set(_locked(ROOT / lock))}"


def test_the_directorys_alpine_packages_are_pinned():
    """OpenLDAP is Alpine's: `~2.6.8` takes Alpine's own rebuilds of that
    release and nothing newer. Its Python libraries are not Alpine's at all."""
    text = (ROOT / "ldap/Dockerfile").read_text()
    block = re.search(r"apk add --no-cache \\\n(.*?)&&", text, re.S).group(1)
    packages = [word.rstrip("\\").strip() for word in block.split() if word.strip("\\").strip()]
    assert packages and all("~" in package for package in packages), packages
    assert "py3-" not in block


def test_the_shared_package_is_installed_as_a_package_everywhere_it_is_used():
    for dockerfile in ("agent/Dockerfile", "review/Dockerfile", "auth/Dockerfile", "ldap/Dockerfile"):
        text = (ROOT / dockerfile).read_text()
        assert "COPY common/ /tmp/nl2sql-common/" in text, dockerfile
        assert "COPY auth/nl2sql_identity" not in text and "COPY common/nl2sql_identity" not in text, dockerfile
