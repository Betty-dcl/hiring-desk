"""The team's logo in the header: a local file, inlined as an image, or nothing.

Each refusal was checked once by hand to fail with its line removed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import desk
from console import logo
from console.desk_web import Desk
from desk import Access, Config

REPO = Path(__file__).resolve().parent.parent
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, "ROOT", tmp_path)
    (tmp_path / "local-assets").mkdir()
    return tmp_path


def configure(home: Path, said: str) -> None:
    (home / "desk.toml").write_text(f'[team]\nvoters = ["Ana", "Ben", "Cy"]\nlogo = "{said}"\n',
                                    encoding="utf-8")


def header() -> str:
    page = Desk(Config(voters=["Ana", "Ben", "Cy"],
                       labels={"contact": "Yes", "discuss": "Not sure", "later": "Later",
                               "pass": "No"}, company="Example Co"), Access()).page("Ana", "desk", "")
    return page.split("<header>")[1].split("</header>")[0]


def test_no_logo_by_default_and_the_square_stays(home):
    configure(home, "")
    assert '<div class="brand"><i></i>' in header() and "<img" not in header()
    shipped = (REPO / "desk.toml").read_text(encoding="utf-8")
    assert '\nlogo = ""\n' in shipped


def test_a_png_is_inlined_as_an_image(home):
    (home / "local-assets" / "logo.png").write_bytes(PNG)
    configure(home, "local-assets/logo.png")
    h = header()
    assert '<img class="logo" src="data:image/png;base64,' in h and 'alt="Example Co logo"' in h


def test_a_file_too_large_is_refused_and_said(home):
    (home / "local-assets" / "logo.png").write_bytes(PNG + b"\x00" * logo.MAX_BYTES)
    configure(home, "local-assets/logo.png")
    assert "<img" not in header()
    assert "at most 200 KB" in logo.problem(home)


def test_a_file_that_is_not_an_image_is_refused_whatever_its_name(home):
    (home / "local-assets" / "logo.png").write_text("<html><script>alert(1)</script>",
                                                    encoding="utf-8")
    configure(home, "local-assets/logo.png")
    assert "<img" not in header() and "not a PNG, JPEG, WebP or SVG" in logo.problem(home)


def test_an_svg_is_only_ever_an_img_never_markup(home):
    (home / "local-assets" / "logo.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        encoding="utf-8")
    configure(home, "local-assets/logo.svg")
    h = header()
    assert '<img class="logo" src="data:image/svg+xml;base64,' in h
    assert "<svg" not in h and "<script" not in h


def test_a_missing_file_is_said_not_a_crash(home):
    configure(home, "local-assets/nope.png")
    assert "<i></i>" in header() and "cannot be read" in logo.problem(home)


def test_local_assets_are_never_committed():
    assert "\nlocal-assets/\n" in (REPO / ".gitignore").read_text(encoding="utf-8")
    assert "put your logo in local-assets/" in (REPO / "DEPLOY.md").read_text(
        encoding="utf-8").lower()
