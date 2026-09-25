"""The desktop client's configuration, checked for the things that rot quietly.

None of this needs Maven, Docker or a network: it reads the files. What it
looks for is the class of mistake that does not show up until somebody runs
the thing on a machine that is not this one -- a dependency that floated to a
new major version, a coverage gate quietly lowered, a jar built for the wrong
platform, a version bumped in one place and not the others.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from nl2sql_agent import __version__

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DESKTOP = REPO_ROOT / "desktop"
POM_NS = {"m": "http://maven.apache.org/POM/4.0.0"}

#: The five classifiers OpenJFX publishes native code under. One jar is one
#: platform, so this is also the list `launch.sh` must be able to produce.
PLATFORMS = {"mac-aarch64", "mac", "linux", "linux-aarch64", "win"}


@pytest.fixture(scope="module")
def pom() -> ET.Element:
    return ET.parse(DESKTOP / "pom.xml").getroot()


@pytest.fixture(scope="module")
def pom_text() -> str:
    return (DESKTOP / "pom.xml").read_text()


@pytest.fixture(scope="module")
def dockerfile() -> str:
    return (DESKTOP / "Dockerfile").read_text()


@pytest.fixture(scope="module")
def compose() -> str:
    return (REPO_ROOT / "docker-compose.yml").read_text()


@pytest.fixture(scope="module")
def launch_sh() -> str:
    return (REPO_ROOT / "launch.sh").read_text()


def _property(pom: ET.Element, name: str) -> str:
    found = pom.find(f"m:properties/m:{name}", POM_NS)
    assert found is not None and found.text, f"pom.xml has no {name} property"
    return found.text


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def test_every_dependency_is_pinned_exactly(pom: ET.Element, pom_text: str):
    """A range makes two clones of this repository different programs. The
    agent's requirements.txt and the GUI's package.json pin the same way and
    for the same reason: a bug that only appears on one machine is the most
    expensive kind."""
    for dependency in pom.findall("m:dependencies/m:dependency", POM_NS):
        version = dependency.find("m:version", POM_NS)
        artifact = dependency.find("m:artifactId", POM_NS).text
        assert version is not None, f"{artifact} has no version"
        resolved = version.text
        if resolved.startswith("${"):
            resolved = _property(pom, resolved[2:-1])
        assert re.fullmatch(r"\d+(\.\d+)*", resolved), f"{artifact} is pinned to {resolved}"


def test_every_plugin_is_pinned_exactly(pom: ET.Element):
    """An unpinned plugin resolves to whatever Maven feels like today, which
    is the same problem one layer down."""
    for plugin in pom.findall("m:build/m:plugins/m:plugin", POM_NS):
        version = plugin.find("m:version", POM_NS)
        artifact = plugin.find("m:artifactId", POM_NS).text
        assert version is not None, f"{artifact} has no version"


def test_javafx_is_the_long_term_support_line(pom: ET.Element):
    """21 rather than the newest. It runs on every JDK from 17 upwards; 25
    refuses to load on anything below 25, which would make the floor for
    running this the newest JDK rather than the oldest supported one."""
    assert _property(pom, "javafx.version").startswith("21.")


def test_the_language_level_is_the_oldest_supported_release(pom: ET.Element):
    assert _property(pom, "maven.compiler.release") == "21"


def test_the_version_follows_the_agent(pom: ET.Element):
    version = pom.find("m:version", POM_NS)
    assert version is not None and version.text == __version__


# ---------------------------------------------------------------------------
# The build
# ---------------------------------------------------------------------------


def test_the_compiler_refuses_code_that_warns(pom_text: str):
    """The same standard the Python and TypeScript sides are held to: a
    warning nobody has to fix is a warning nobody reads."""
    assert "<arg>-Xlint:all</arg>" in pom_text
    assert "<arg>-Werror</arg>" in pom_text


def test_the_coverage_gate_is_a_hundred_percent_of_lines_and_branches(pom_text: str):
    """Matching the Python and TypeScript sides. A threshold below 100 is a
    number nobody looks at; a failing build is read immediately."""
    limits = re.findall(r"<counter>(\w+)</counter>\s*<value>COVEREDRATIO</value>\s*"
                        r"<minimum>([\d.]+)</minimum>", pom_text)
    assert dict(limits) == {"LINE": "1.00", "BRANCH": "1.00"}


def test_only_the_entry_point_is_left_out_of_coverage(pom_text: str):
    """`Main` calls Application.launch(), which does not return until the
    window is closed. Everything else is measured.

    Scoped to JaCoCo's own excludes: the shade plugin has a list of its own,
    and merging the two would let a class quietly leave the measurement by
    being added to the wrong one.
    """
    jacoco = re.search(r"<artifactId>jacoco-maven-plugin</artifactId>(.*?)</plugin>",
                       pom_text, re.DOTALL)
    assert jacoco, "the coverage plugin has gone"
    assert re.findall(r"<exclude>([^<]+)</exclude>", jacoco.group(1)) == [
        "org/nl2sql/desktop/Main.class"
    ]


def test_the_tests_run_headless(pom_text: str):
    """Monocle, because AppKit insists on owning the process's first thread
    and under a test runner that thread is the one running the tests. Without
    it the toolkit comes up and delivers nothing, which is a hang rather than
    a failure -- and an intermittent one."""
    assert "<glass.platform>Monocle</glass.platform>" in pom_text
    assert "<monocle.platform>Headless</monocle.platform>" in pom_text
    assert "openjfx-monocle" in pom_text


def test_the_jar_says_which_class_starts_it(pom_text: str):
    assert "<mainClass>${main.class}</mainClass>" in pom_text
    assert "<main.class>org.nl2sql.desktop.Main</main.class>" in pom_text


def test_the_entry_point_does_not_extend_application():
    """A class that extends Application and is launched from the class path
    makes JavaFX refuse to start, with a message about missing runtime
    components. A launcher that does not is the documented way round it, and
    it is what lets this ship as one jar that runs with `java -jar`."""
    main = (DESKTOP / "src/main/java/org/nl2sql/desktop/Main.java").read_text()
    assert "extends Application" not in main
    assert "Application.launch(DesktopApp.class" in main


def test_native_access_is_enabled_so_the_first_thing_a_user_sees_is_a_window(pom_text: str):
    """JDK 24 and later warn on every native call made from the class path.
    JavaFX makes one before it draws anything."""
    assert "<Enable-Native-Access>ALL-UNNAMED</Enable-Native-Access>" in pom_text


# ---------------------------------------------------------------------------
# The image that builds the jar
# ---------------------------------------------------------------------------


def test_the_build_runs_where_docker_is_and_targets_where_java_is(dockerfile: str):
    """The jar is built in a Linux container and run on somebody's own
    machine. OpenJFX picks the host's native classifier automatically, which
    is right for a developer and wrong here, so the target is passed in."""
    assert re.search(r"^FROM --platform=\$BUILDPLATFORM maven:", dockerfile, re.MULTILINE)
    assert "ARG JAVAFX_PLATFORM" in dockerfile
    assert "-Djavafx.platform=${JAVAFX_PLATFORM}" in dockerfile


def test_the_base_image_is_pinned_to_a_version(dockerfile: str):
    image = re.search(r"^FROM (?:--platform=\S+ )?(\S+)", dockerfile, re.MULTILINE).group(1)
    assert ":" in image and not image.endswith(":latest")


def test_the_dependency_layer_is_cached_on_the_pom(dockerfile: str):
    """Copying the sources first would re-download JavaFX on every edit."""
    assert dockerfile.index("desktop/pom.xml") < dockerfile.index("desktop/src")


def test_the_tests_are_not_run_in_the_image(dockerfile: str):
    """They need a toolkit this image has no display for, and `pytest
    --run-java` is where a failure means the checkout is wrong rather than
    one machine's Docker."""
    assert "-DskipTests package" in dockerfile


def test_the_image_label_says_the_version_this_actually_is(dockerfile: str):
    label = re.search(r'org\.opencontainers\.image\.version="([^"]+)"', dockerfile)
    assert label and label.group(1) == __version__


# ---------------------------------------------------------------------------
# How it is asked for
# ---------------------------------------------------------------------------


def test_compose_builds_it_for_the_platform_it_is_told_to(compose: str):
    service = re.search(r"\n  desktop:\n(.*?)(?=\n  \w|\nvolumes:)", compose, re.DOTALL)
    assert service, "docker-compose.yml has no desktop service"
    body = service.group(1)
    assert "dockerfile: desktop/Dockerfile" in body
    assert "JAVAFX_PLATFORM: ${JAVAFX_PLATFORM:-linux}" in body
    # Tagged by platform: a cached image built for another machine is a jar
    # that will not start on this one.
    assert "${JAVAFX_PLATFORM:-linux}" in re.search(r"image: ([^\n]+)", body).group(1)
    assert 'profiles: ["desktop"]' in body
    assert "./desktop/target:/out" in body


def test_launch_can_name_every_platform_openjfx_publishes(launch_sh: str):
    """A classifier this script cannot produce is a machine the client
    cannot be built for, however many JDKs it has."""
    block = re.search(r"javafx_platform\(\) \{(.*?)\n\}", launch_sh, re.DOTALL)
    assert block, "launch.sh no longer works out which platform to build for"
    named = set(re.findall(r"printf '([a-z0-9-]+)'", block.group(1)))
    assert named == PLATFORMS


def test_launch_rebuilds_when_the_platform_or_the_sources_changed(launch_sh: str):
    """Both are ways of holding a jar that is not this checkout, and neither
    changes the jar's timestamp on its own."""
    assert "desktop/target/.platform" in launch_sh
    assert re.search(r"find desktop/src desktop/pom\.xml -newer", launch_sh)


def test_the_client_is_given_the_certificate_rather_than_told_to_skip_it(launch_sh: str):
    """The API writes itself a self-signed certificate, which every client
    that checks will refuse. Copying it out is the answer; --insecure is the
    fallback and says so in the status bar for as long as it is on."""
    assert "cp api:/etc/nl2sql/tls/server.crt" in launch_sh


@pytest.mark.parametrize("path", ["desktop/target"])
def test_the_build_output_is_not_committed_or_sent_to_the_daemon(path: str):
    assert f"{path}/" in (REPO_ROOT / ".gitignore").read_text()
    assert path in (REPO_ROOT / ".dockerignore").read_text()
