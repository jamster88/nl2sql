"""V6-35: every base image pinned by digest, and one Alpine.

The repository's own check (`tools/pin_images.py`) run against it, and the
tool itself against files of its own, with a resolver that stands in for the
registry.
"""

from __future__ import annotations

import importlib.util
import re
import runpy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
spec = importlib.util.spec_from_file_location("pin_images", ROOT / "tools" / "pin_images.py")
pins = importlib.util.module_from_spec(spec)
sys.modules["pin_images"] = pins  # a dataclass looks its module up there
spec.loader.exec_module(pins)

DIGEST = "sha256:" + "a" * 64
OTHER = "sha256:" + "b" * 64


def test_every_base_image_here_is_pinned_and_each_tag_to_one_digest(capsys):
    assert pins.main([]) == 0, capsys.readouterr().err
    references = [ref for path in pins.files() for ref in pins.references(path)]
    assert len(references) >= 20, "the patterns stopped finding the references"
    assert {ref.name for ref in references} >= {"python", "postgres", "pgvector/pgvector", "nginx", "node", "alpine"}


def test_there_is_one_alpine():
    """M-12: alpine:3.21 and alpine:3.22 in one release."""
    tags = {ref.tag for path in pins.files() for ref in pins.references(path) if ref.name == "alpine"}
    assert len(tags) == 1, tags


def test_the_six_page_images_are_one(tmp_path):
    """V6-37: no nginx image but the proxy's."""
    nginx = [ref.path for path in pins.files() for ref in pins.references(path) if ref.name == "nginx"]
    assert nginx == ["proxy/Dockerfile"]


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "svc").mkdir()
    (tmp_path / "svc" / "Dockerfile").write_text(
        "FROM --platform=$BUILDPLATFORM node:26-alpine AS build\n"
        "FROM python:3.12-slim\n"
        "FROM build AS again\n"
        "FROM mcfaddja/nl2sql-agent:v6_2\n"
    )
    (tmp_path / "x.Dockerfile").write_text("ARG BASE_IMAGE=postgres:18@" + DIGEST + "\nFROM ${BASE_IMAGE}\n")
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n"
        "  a:\n    image: ${A_IMAGE:-pgvector/pgvector:pg18}\n"
        "  b:\n    image: ${B_IMAGE_NAME:-nl2sql-gui}:${B_TAG:-latest}\n"
    )
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "Dockerfile").write_text("FROM ignored:1\n")
    return tmp_path


def test_what_is_not_pinned_is_named_with_where_it_is(tree, capsys):
    assert pins.main([], root=tree) == 1
    err = capsys.readouterr().err.splitlines()
    assert err == [
        "docker-compose.yml:3: pgvector/pgvector:pg18: not pinned by digest",
        "svc/Dockerfile:1: node:26-alpine: not pinned by digest",
        "svc/Dockerfile:2: python:3.12-slim: not pinned by digest",
    ], "a stage name, this repository's own images and node_modules are not references"


def test_one_tag_pinned_to_two_digests_is_a_problem(tmp_path):
    (tmp_path / "Dockerfile").write_text(f"FROM alpine:3.22@{DIGEST}\nFROM alpine:3.22@{OTHER}\n")
    refs = pins.references(tmp_path / "Dockerfile", tmp_path)
    assert pins.problems(refs) == ["alpine:3.22 is pinned to 2 different digests"]


def test_an_update_pins_each_tag_once_and_keeps_everything_else(tree, capsys):
    asked = []

    def resolver(tagged):
        asked.append(tagged)
        return OTHER

    (tree / "y.Dockerfile").write_text("FROM node:26-alpine\n")  # a second place for one tag
    assert pins.main(["--update"], root=tree, resolver=resolver) == 0
    assert sorted(asked) == ["node:26-alpine", "pgvector/pgvector:pg18", "postgres:18", "python:3.12-slim"]
    assert f"FROM --platform=$BUILDPLATFORM node:26-alpine@{OTHER} AS build" in (tree / "svc" / "Dockerfile").read_text()
    assert "FROM mcfaddja/nl2sql-agent:v6_2\n" in (tree / "svc" / "Dockerfile").read_text()
    assert f"ARG BASE_IMAGE=postgres:18@{OTHER}" in (tree / "x.Dockerfile").read_text(), "a pin is moved on too"
    compose = (tree / "docker-compose.yml").read_text()
    assert f"${{A_IMAGE:-pgvector/pgvector:pg18@{OTHER}}}" in compose and "nl2sql-gui}:${B_TAG:-latest}" in compose
    assert pins.main([], root=tree) == 0
    assert f"python:3.12-slim@{OTHER}" in capsys.readouterr().out


def test_the_registry_is_asked_for_the_index_digest(monkeypatch):
    seen = []

    class Done:
        stdout = '{"digest": "%s", "mediaType": "application/vnd.oci.image.index.v1+json"}' % DIGEST

    monkeypatch.setattr(pins.subprocess, "run", lambda args, **kw: seen.append(args) or Done())
    assert pins.resolve("alpine:3.22") == DIGEST
    assert seen == [["docker", "buildx", "imagetools", "inspect", "alpine:3.22", "--format", "{{json .Manifest}}"]]


def test_the_reference_pattern_wants_a_whole_digest():
    assert re.fullmatch(pins.REF, f"alpine:3.22@{DIGEST}")
    assert not re.fullmatch(pins.REF, "alpine:3.22@sha256:abc")


def test_run_as_a_script_it_finds_this_checkout_pinned(monkeypatch, capsys):
    """`python tools/pin_images.py`, as the README tells someone to run it --
    against this checkout, where it has nothing to report."""
    monkeypatch.setattr(sys, "argv", ["pin_images.py"])
    with pytest.raises(SystemExit) as finished:
        runpy.run_path(str(ROOT / "tools" / "pin_images.py"), run_name="__main__")
    assert finished.value.code == 0
    assert capsys.readouterr().err == ""
