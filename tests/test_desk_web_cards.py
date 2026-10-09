"""What a row and a card say before anyone clicks: the documents, the other
applications of the same person, and what the fit percentage is.

Each test was checked once by hand to fail with the line it guards removed.
"""

from __future__ import annotations

from datetime import datetime, timezone

from console.desk_web import AI_HINT, Desk
from desk import Access, Application, Config, Document, Person
from pipeline import Event, Pipeline

NOW = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
TITLES = {"fa": "Founders' Associate", "be": "Backend Engineer"}


def cfg():
    return Config(voters=["Ana", "Ben", "Cy"],
                  labels={"contact": "Interview", "later": "Keep on file",
                          "discuss": "Discuss", "pass": "Not interested"})


def pipes():
    fa, be = Pipeline("fa"), Pipeline("be")
    fa.add(Event(candidate_id="sam", kind="received", at="2026-09-18T10:00:00+00:00"))
    fa.add(Event(candidate_id="sam", kind="screened", at="2026-09-18T10:00:00+00:00",
                 detail={"score": 0.41}))
    be.add(Event(candidate_id="sam", kind="received", at="2026-09-26T10:00:00+00:00"))
    return {"fa": fa, "be": be}


def sam(*apps: str) -> Person:
    p = Person("sam", "Sam Lee", applications=[Application(a, "sam") for a in apps])
    p.documents.append(Document("fa", "cv", "sam/fa_cv_1.pdf", "Sam CV.pdf"))
    return p


def row(person: Person, posting: str = "fa") -> str:
    ps = pipes()
    return Desk(cfg(), Access()).row(person, Application(posting, "sam"),
                                     ps[posting].standing("sam"), "Ana", TITLES, NOW, pipes=ps)


# -- documents -----------------------------------------------------------------

def test_a_document_is_a_button_that_says_open():
    html = row(sam("fa"))
    assert 'title="Sam CV.pdf">CV</a>' in html


def test_a_missing_letter_is_said_not_left_out():
    html = row(sam("fa"))
    assert '<span class="none">no “Why us” answer</span>' in html
    assert "no CV" not in html


def test_the_card_names_the_missing_document_too():
    p, ps = sam("fa"), pipes()
    card = Desk(cfg(), Access()).application_card(p, Application("fa", "sam"),
                                                  ps["fa"].standing("sam"), "Ana", TITLES,
                                                  NOW, pipes=ps)
    assert ">Open CV</a>" in card and "no “Why us” answer" in card


# -- one person, several applications --------------------------------------------

def test_one_application_carries_no_badge():
    html = row(sam("fa"))
    assert 'class="multi"' not in html and "Also applied" not in html


def card(person: Person, posting: str = "fa") -> str:
    ps = pipes()
    return Desk(cfg(), Access()).application_card(person, Application(posting, "sam"),
                                                  ps[posting].standing("sam"), "Ana", TITLES,
                                                  NOW, pipes=ps)


def test_a_second_application_is_a_discreet_badge_on_the_row_and_said_on_the_card():
    html = row(sam("fa", "be"))
    assert ">2 applications</a>" in html and "Also applied" not in html
    assert ("Also applied to <a href=\"#app-be\">Backend Engineer</a> "
            "on 2026-09-26") in card(sam("fa", "be"))


def test_the_other_role_is_said_from_either_side():
    assert "Founders&#x27; Associate</a> on 2026-09-18" in card(sam("fa", "be"), "be")


def test_on_the_persons_page_each_card_points_to_the_other():
    p, ps = sam("fa", "be"), pipes()
    d = Desk(cfg(), Access())
    card = d.application_card(p, Application("fa", "sam"), ps["fa"].standing("sam"), "Ana",
                              TITLES, NOW, pipes=ps)
    assert 'id="app-fa"' in card and 'href="#app-be"' in card


# -- the fit percentage ------------------------------------------------------------

def test_the_fit_says_it_is_a_reading_and_the_vote_decides():
    # The number is counted from the documents (match.py, tests/test_match.py). What it is
    # is said wherever it is shown, and the legend points to the page that explains it.
    legend = Desk(cfg(), Access()).legend()
    assert f"AI guess: {AI_HINT}" in legend and "AI guess tab" in legend
    assert "your decision is what counts" in AI_HINT
