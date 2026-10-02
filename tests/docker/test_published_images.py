"""The tags `setup.sh` pins, asked of the registry they are pulled from.

Every other test here reads the repository: the Dockerfiles, the version
declarations, the tags `setup.sh` names. None of them can tell whether those
tags were ever *published* -- a release whose version was bumped and whose
push was forgotten passes all of them, and fails for the first person who
runs `setup.sh`. So this asks Docker Hub, per pinned tag, the three things a
pull depends on: that the tag exists, that it covers both architectures this
project publishes for, and that the image under it says it is this
checkout's version rather than an older one that happened to share a name.

Marked `docker` and needs the network; it skips, rather than fails, when the
registry cannot be reached.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from nl2sql_agent import __version__

pytestmark = pytest.mark.docker

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SETUP_SH = (REPO_ROOT / "setup.sh").read_text()

#: What every published tag in this project is built for.
ARCHITECTURES = {"linux/amd64", "linux/arm64"}

#: The desktop client's jar is published once per JavaFX platform.
DESKTOP_PLATFORMS = ("mac-aarch64", "mac", "linux", "linux-aarch64", "win")


def _pinned(name: str) -> str:
    image = re.search(rf'^{name}_IMAGE="([^"]+)"', SETUP_SH, re.MULTILINE).group(1)
    tag = re.search(rf'^{name}_TAG="([^"]+)"', SETUP_SH, re.MULTILINE).group(1)
    return f"{image}:{tag}"


PINNED = [_pinned(name) for name in ("AGENT", "GUI", "REVIEW", "REVIEW_GUI", "CONSOLE_GUI", "MLFLOW", "MLFLOW_DB")]
PINNED += [f"{_pinned('DESKTOP')}-{platform}" for platform in DESKTOP_PLATFORMS]


def _inspect(reference: str) -> dict:
    """Per-platform image configs for a published reference, from the registry."""
    if shutil.which("docker") is None:
        pytest.skip("no docker CLI")
    try:
        done = subprocess.run(
            ["docker", "buildx", "imagetools", "inspect", reference, "--format", "{{json .Image}}"],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        pytest.skip("the registry did not answer in time")
    if done.returncode != 0:
        message = (done.stderr or done.stdout).strip()
        if re.search(r"not found|manifest unknown", message, re.IGNORECASE):
            pytest.fail(f"{reference} is pinned by setup.sh but not published: {message}")
        pytest.skip(f"could not reach the registry: {message}")
    return json.loads(done.stdout)


@pytest.mark.parametrize("reference", PINNED)
def test_every_pinned_tag_is_published_for_both_architectures_at_this_version(reference: str):
    images = _inspect(reference)

    assert set(images) == ARCHITECTURES, f"{reference} is published for {sorted(images)}"
    for platform, image in images.items():
        labels = image.get("config", {}).get("Labels") or {}
        assert labels.get("org.opencontainers.image.version") == __version__, (
            f"{reference} ({platform}) says it is "
            f"{labels.get('org.opencontainers.image.version')}, this checkout is {__version__}"
        )


#: The dataset images version on their own -- `retail-postgres:v1_1`,
#: `rag-*:v3_1` -- so there is no version label to hold them to; what a pull
#: depends on is that the tag exists, for both architectures. The RAG stores'
#: `v3` did not: rag/publish_db_image.sh used to tar one machine's data
#: directory, and this is the test that would have said so.
DATA_PINNED = [_pinned(name) for name in ("POSTGRES", "VECTOR", "CONTEXT")]


def _platforms(images: dict) -> set[str]:
    """`{{json .Image}}` is a map of platform to config for an index, and the
    bare config when the tag covers one platform."""
    if "architecture" in images:
        return {f"{images['os']}/{images['architecture']}"}
    return set(images)


def test_a_single_platform_tag_is_read_as_one_platform():
    """The shape the arm64-only `v3` came back in, which a set of the dict's
    keys would have read as six platforms called `config`, `os` and so on."""
    single = {"architecture": "arm64", "os": "linux", "config": {}, "rootfs": {}}
    assert _platforms(single) == {"linux/arm64"}
    assert _platforms({"linux/amd64": {}, "linux/arm64": {}}) == ARCHITECTURES


@pytest.mark.parametrize("reference", DATA_PINNED)
def test_every_pinned_dataset_image_is_published_for_both_architectures(reference: str):
    assert _platforms(_inspect(reference)) == ARCHITECTURES, f"{reference} is not multi-arch"


def test_setup_sh_does_not_pin_the_retail_image_from_before_the_reader_role():
    """`v1` predates the read-only role and `v1_1` is the first tag that
    carries it, closed to the other databases and to signalling. A pin back
    to `v1` would be a quiet step backwards: setup.sh re-creates the role on
    start, but anyone who runs the image directly would get the old one."""
    assert DATA_PINNED[0] != "mcfaddja/nl2sql-retail-postgres:v1"
    assert DATA_PINNED[0].startswith("mcfaddja/nl2sql-retail-postgres:")


def test_the_list_is_every_image_setup_sh_moves_together():
    """Eight image families, the desktop one per platform: twelve references."""
    assert len(PINNED) == 12
    assert len({reference.split(":")[0] for reference in PINNED}) == 8
