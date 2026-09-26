"""The image that builds the desktop client, built and read.

The jar is the one artefact in this repository that is produced for a machine
other than the one producing it, so the thing worth proving is that the
crossing worked: a Linux container asked for a macOS build and got macOS
native libraries, not its own.

Marked `docker` because it builds. The same Dockerfile's structural
properties -- pinned base, cached dependency layer, the platform argument --
are checked without Docker in `tests/java/test_desktop_project.py`.
"""

from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.docker

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: One that is not the builder's, so a jar carrying the container's own
#: natives would fail this rather than passing by accident.
CROSS_PLATFORM = "mac-aarch64"

#: What OpenJFX's macOS build carries and its Linux build does not.
MAC_NATIVE = "libglass.dylib"
LINUX_NATIVE = "libglass.so"


@pytest.fixture(scope="module")
def jar(docker_cli: str | None, docker_daemon_available: bool, tmp_path_factory) -> Path:
    if not docker_daemon_available:
        pytest.skip("no Docker daemon")

    tag = "nl2sql-desktop-build:test-mac-aarch64"
    build = subprocess.run(
        [docker_cli, "build", "-f", "desktop/Dockerfile",
         "--build-arg", f"JAVAFX_PLATFORM={CROSS_PLATFORM}", "-t", tag, "."],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=2400,
    )
    assert build.returncode == 0, build.stdout + build.stderr

    out = tmp_path_factory.mktemp("desktop")
    copied = subprocess.run(
        [docker_cli, "run", "--rm", "-v", f"{out}:/out", tag],
        capture_output=True, text=True, timeout=300,
    )
    assert copied.returncode == 0, copied.stdout + copied.stderr
    built = out / "nl2sql-desktop.jar"
    assert built.is_file(), "the image's default command did not produce the jar"
    return built


def test_the_jar_carries_the_natives_of_the_platform_it_was_built_for(jar: Path):
    """The whole reason the platform is a build argument. Without it the
    container builds for itself, and the result starts on nothing the user
    is likely to be sitting at."""
    names = {Path(name).name for name in zipfile.ZipFile(jar).namelist()}

    assert MAC_NATIVE in names
    assert LINUX_NATIVE not in names


def test_the_jar_knows_what_starts_it(jar: Path):
    manifest = zipfile.ZipFile(jar).read("META-INF/MANIFEST.MF").decode()

    assert "Main-Class: org.nl2sql.desktop.Main" in manifest
    # JDK 24 and later warn on every native call made from the class path,
    # and JavaFX makes one before it draws anything.
    assert "Enable-Native-Access: ALL-UNNAMED" in manifest


def test_the_application_and_its_dependencies_are_all_in_one_file(jar: Path):
    """`java -jar` and nothing else is the whole deployment story, so the
    jar has to carry JavaFX and the JSON parser as well as this code."""
    names = zipfile.ZipFile(jar).namelist()

    assert "org/nl2sql/desktop/Main.class" in names
    assert "org/nl2sql/desktop/nl2sql.css" in names
    assert any(name.startswith("javafx/scene/control/") for name in names)
    assert any(name.startswith("com/fasterxml/jackson/databind/") for name in names)


def test_no_signature_survives_being_merged_into_another_jar(jar: Path):
    """The class files are the same, but the archive they were signed as
    part of is not, and a JVM rejects a signature that does not match."""
    leftovers = [
        name for name in zipfile.ZipFile(jar).namelist()
        if name.upper().endswith((".SF", ".DSA", ".RSA"))
    ]
    assert leftovers == []


def test_the_compose_service_builds_the_same_thing(docker_cli: str | None,
                                                   docker_daemon_available: bool):
    """`launch.sh --desktop` asks compose rather than docker, so the service
    has to reach the same Dockerfile with the same argument."""
    if not docker_daemon_available:
        pytest.skip("no Docker daemon")
    config = subprocess.run(
        [docker_cli, "compose", "--profile", "desktop", "config", "--format", "json"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
        env={**__import__("os").environ, "JAVAFX_PLATFORM": CROSS_PLATFORM},
    )
    assert config.returncode == 0, config.stderr
    service = json.loads(config.stdout)["services"]["desktop"]

    assert service["build"]["dockerfile"].endswith("desktop/Dockerfile")
    assert service["build"]["args"]["JAVAFX_PLATFORM"] == CROSS_PLATFORM
    assert service["image"].endswith(CROSS_PLATFORM)
    assert service["profiles"] == ["desktop"]


def test_the_published_image_carries_the_jar_and_not_the_toolchain(
        docker_cli: str | None, docker_daemon_available: bool, jar: Path):
    """The builder stage is Maven, a JDK and half a gigabyte of dependency
    cache; the stage that ships is alpine and one file. The `jar` fixture has
    already built it, so this only reads what came out."""
    tag = "nl2sql-desktop-build:test-mac-aarch64"
    listing = subprocess.run(
        [docker_cli, "run", "--rm", "--entrypoint", "sh", tag, "-c",
         "ls /opt/nl2sql; command -v mvn java javac || true"],
        capture_output=True, text=True, timeout=120,
    )
    assert listing.returncode == 0, listing.stderr
    assert "nl2sql-desktop.jar" in listing.stdout
    # Nothing that could build it is left in the thing that carries it.
    assert "mvn" not in listing.stdout
    assert "javac" not in listing.stdout

    size = subprocess.run(
        [docker_cli, "image", "inspect", tag, "--format", "{{.Size}}"],
        capture_output=True, text=True, timeout=60,
    )
    # The jar is about 11 MB and alpine about 8. A gigabyte means the
    # shipping stage went away and the builder is being published instead.
    assert int(size.stdout.strip()) < 100 * 1024 * 1024, size.stdout

