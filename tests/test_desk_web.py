"""The desk in a browser: what the page may show, and to whom.

The page is where the blind rule is easiest to break by accident -- a colour,
a tooltip, a CSS class -- so most of these tests read the HTML for a vote
that should not be in it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from console.desk_web import Desk, _needs, _say
from desk import Access, Application, Config, Person, cast
from pipeline import Event, Pipeline

VOTERS = ["Ana", "Ben", "Cy"]


def cfg():
    return Config(voters=list(VOTERS), labels={"contact": "Contact now", "later": "Later",
                                               "discuss": "Discuss", "pass": "Pass"})


def pipe():
    p = Pipeline("role")
    p.add(Event(candidate_id="x", kind="received"))
    return p


def test_the_viewers_own_turn_is_said_in_their_words():
    assert _say("waiting for Ana, Ben, Cy", "Ana") == "needs your vote · 2 more"
    assert _say("waiting for Ben", "Ana") == "waiting for Ben"


def test_only_what_waits_on_the_viewer_is_theirs():
    assert _needs("waiting for Ben, Ana", "Ana")
    assert not _needs("waiting for Ben", "Ana")
    assert _needs("mail to send (Contact now)", "Ana")
    assert not _needs("no reply for 9 days", "Ana")


def test_a_hidden_vote_leaves_no_trace_in_the_page():
    p, c = pipe(), cfg()
    cast(p, "x", "Ben", "pass", c, reason="not this role", comment="thin on writing")
    d = Desk(c, Access())
    html = d.row(Person("x", "Sam Lee"), Application("role", "x"), p.standing("x"), "Ana",
                 {}, datetime.now(timezone.utc))
    # The row says how many voted, never who said what.
    assert "Voting · 1 of 3" in html
    for leak in ("av pass", "not this role", "thin on writing", ">Pass --", "Ben"):
        assert leak not in html
    card = d.application_card(Person("x", "Sam Lee"), Application("role", "x"),
                              p.standing("x"), "Ana", {}, datetime.now(timezone.utc))
    assert "voted, hidden until you vote" in card
    for leak in ("not this role", "thin on writing", "Pass ("):
        assert leak not in card


def test_once_the_viewer_has_voted_the_colour_appears():
    p, c = pipe(), cfg()
    cast(p, "x", "Ben", "pass", c)
    cast(p, "x", "Ana", "contact", c)
    html = Desk(c, Access()).dots({k: v for k, v in
                                   __import__("desk").votes(p.standing("x")).items()}, "Ana")
    assert "av pass" in html and "av contact you" in html


def test_class_buttons_are_open_on_your_turn_and_folded_after():
    p, c = pipe(), cfg()
    d = Desk(c, Access())
    now = datetime.now(timezone.utc)
    before = d.row(Person("x", "Sam Lee"), Application("role", "x"), p.standing("x"), "Ana",
                   {}, now)
    assert 'class="change"' not in before and 'name="label"' in before
    cast(p, "x", "Ana", "contact", c)
    after = d.row(Person("x", "Sam Lee"), Application("role", "x"), p.standing("x"), "Ana",
                  {}, now)
    assert "Change my vote" in after


def test_every_form_carries_the_servers_token():
    p, c = pipe(), cfg()
    d = Desk(c, Access())
    html = d.row(Person("x", "Sam Lee"), Application("role", "x"), p.standing("x"), "Ana", {},
                 datetime.now(timezone.utc))
    assert f'name="t" value="{d.token}"' in html


def test_behind_a_proxy_there_is_no_name_menu():
    d = Desk(cfg(), Access(mode="header", emails={"a@x.example": "Ana"}))
    page = d.page("Ana", "desk", "")
    assert "<select name=\"as\"" not in page and "Signed in as <b>Ana</b>" in page


# --------------------------------------------------------------------------
# The line under a name, and the order of the list
# --------------------------------------------------------------------------

from console.desk_web import _banded  # noqa: E402
from triage import bands, brief, label  # noqa: E402


def test_labels_read_as_words():
    assert label("ai_native") == "AI native"
    assert label("nice_fintech_saas_cfo") == "Fintech SaaS CFO"


def test_a_band_forms_only_where_the_gap_is_inside_the_drift():
    assert bands([("a", 0.90, 0.04), ("b", 0.35, 0.01), ("c", 0.33, 0.04)]) == [["a"], ["b", "c"]]
    assert bands([("b", 0.35, 0.01), ("c", 0.20, 0.04)]) == [["b"], ["c"]]


def test_with_no_drift_measured_nothing_is_banded():
    assert bands([("a", 0.34, None), ("b", 0.33, None)]) == [["a"], ["b"]]


def test_a_band_never_crosses_from_yours_to_not_yours():
    items = [((False, -0.34, 0), "<A>", "p/a", 0.05), ((True, -0.33, 0), "<B>", "p/b", 0.05)]
    assert "band" not in "".join(_banded(items))
    items = [((False, -0.34, 0), "<A>", "p/a", 0.05), ((False, -0.33, 0), "<B>", "p/b", 0.05)]
    out = _banded(items)
    assert 'class="band"' in out[0] and out[1:] == ["<A>", "<B>"]


def test_the_brief_rests_on_what_was_done_not_on_languages():
    b = brief("ines_abadi", "founders_associate")
    assert b.strong[0][0] == "AI native" and "Claude Code" in b.strong[0][1]
    assert not any(lab in ("Fluent english", "Languages") for lab, _ in b.strong)


def test_strong_on_languages_alone_is_said_not_praised():
    b = brief("mara_velichko", "founders_associate")
    assert b.strong == () and b.only_languages


def test_an_open_gate_comes_with_the_question_to_ask():
    b = brief("paul_okonkwo", "founders_associate")
    assert b.open_gates == ("AI native",)
    assert b.ask_first.startswith("Show me a workflow")


def test_the_brief_on_a_row_never_carries_a_vote():
    p, c = pipe(), cfg()
    cast(p, "x", "Ben", "pass", c, reason="not this role")
    html = Desk(c, Access()).row(Person("x", "Sam Lee"), Application("founders_associate", "x"),
                                 p.standing("x"), "Ana", {}, datetime.now(timezone.utc))
    assert "not this role" not in html and "av pass" not in html
