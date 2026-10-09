"""The settings page: their words, their rules, and nothing the code must trust.

What is typed into the browser ends up in mails to real people, so the tests
are mostly about what a template must not be able to do, and about a change
that would stop the desk being refused before it is kept.
"""

from __future__ import annotations

import json

import pytest

import desk
from console.desk_web import Desk, settings_from_form
from desk import Access, Application, DeskError, Person, fill_in


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, "_settings_path", lambda: tmp_path / "settings.json")
    return tmp_path


def test_only_the_named_fields_are_filled():
    out = fill_in("Hi {first_name} {first_name.__class__} {0} {role}!", {"first_name": "Pablo",
                                                                          "role": "FA"})
    assert out == "Hi Pablo {first_name.__class__} {0} FA!"


def test_a_stray_brace_does_not_break_a_mail():
    assert fill_in("Budget {not a field} and a lone { here", {}) == \
        "Budget {not a field} and a lone { here"


def test_the_example_is_used_until_someone_writes_their_own(home):
    cfg = desk.load_config()
    assert cfg.is_example("contact") and "Dear {first_name}" in cfg.template("contact")
    cfg = desk.save_settings({"templates": {"contact": "Hola {first_name}"}}, by="Ana")
    assert not cfg.is_example("contact") and cfg.template("contact") == "Hola {first_name}"
    assert desk.load_config().template("contact") == "Hola {first_name}"


def test_clearing_a_text_brings_the_example_back(home):
    desk.save_settings({"templates": {"contact": "Hola"}}, by="Ana")
    cfg = desk.save_settings({"templates": {"contact": "   "}}, by="Ana")
    assert cfg.is_example("contact")


def test_every_change_is_kept_with_who_made_it(home):
    desk.save_settings({"sender": "Ana"}, by="Ana")
    desk.save_settings({"recap_weekday": "monday"}, by="Ben")
    h = json.loads((home / "settings.json").read_text())["history"]
    assert [(x["by"], x["changed"]) for x in h] == [("Ana", ["sender"]),
                                                    ("Ben", ["recap_weekday"])]


def test_a_change_that_would_stop_the_desk_is_refused_and_not_kept(home):
    with pytest.raises(DeskError):
        desk.save_settings({"labels": {"contact": "Same", "pass": "same"}}, by="Ana")
    with pytest.raises(DeskError):
        desk.save_settings({"voters": []}, by="Ana")
    assert not (home / "settings.json").exists()


def test_what_decides_access_is_not_a_form_field(home):
    with pytest.raises(DeskError):
        desk.save_settings({"mail_mode": "auto"}, by="Ana")


def test_a_change_needs_a_name(home):
    with pytest.raises(DeskError):
        desk.save_settings({"sender": "x"}, by=" ")


def test_the_form_keeps_empty_as_the_example_and_parses_lists():
    v = settings_from_form({"label_contact": " Meet ", "template_pass": "No\r\nthanks",
                            "voters": "Ana, Ben ,", "pass_reasons": "timing\n\n not now "})
    assert v["labels"]["contact"] == "Meet" and v["labels"]["pass"] == ""
    assert v["templates"]["pass"] == "No\nthanks"
    assert v["voters"] == ["Ana", "Ben"] and v["pass_reasons"] == ["timing", "not now"]


def test_the_settings_page_shows_the_example_in_grey_and_escapes_it(home):
    cfg = desk.save_settings({"templates": {"later": "<script>x</script> {first_name}"}}, by="Ana")
    html = Desk(cfg, Access()).settings("Ana")
    assert 'placeholder="Dear {first_name},' in html      # the example, as a placeholder
    assert "<script>x</script>" not in html and "&lt;script&gt;x&lt;/script&gt;" in html


def test_the_mail_opens_in_their_own_app_with_the_name_filled(home, monkeypatch):
    cfg = desk.save_settings({"templates": {"contact": "Hola {first_name}"}, "sender": "Ana"},
                             by="Ana")
    from email.message import EmailMessage
    m = EmailMessage()
    m["To"], m["Subject"] = "pablo@example.org", "FA at X: next step"
    m.set_content("Hola Pablo\n")
    monkeypatch.setattr(desk, "draft_for", lambda *a, **k: (m, "x.eml"))
    html = Desk(cfg, Access()).mailbox(Person("p", "Pablo García"), Application("fa", "p"),
                                       "contact", "Ana")
    assert 'href="mailto:pablo%40example.org?subject=FA%20at%20X' in html
    assert "Hola%20Pablo" in html and "not yours yet" not in html
