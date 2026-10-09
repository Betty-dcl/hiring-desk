"""The hiring desk: cards, votes, one person per human, the recap, the drafts.

The failures worth a test here are the quiet ones: a vote shown too early, a
"later" that never comes back, two strangers merged into one record, a mail
that leaves on a machine's say-so.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import desk
from desk import (
    Application,
    Config,
    DeskError,
    Person,
    Registry,
    card,
    cast,
    draft,
    last_recap_day,
    load_config,
    outcome,
    recap,
    visible_to,
    votes,
)
from pipeline import Event, Pipeline

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
VOTERS = ["Ana", "Ben", "Cy"]


def cfg(**over):
    base = dict(voters=list(VOTERS),
                labels={"contact": "Contact now", "later": "Later",
                        "discuss": "Discuss", "pass": "Pass"},
                pass_reasons=["not this role"], company="Acme", sender="Ana",
                subjects={"contact": "{role} at {company}", "pass": "Your application"},
                templates={"contact": "Hi {first_name},\n\nWe would like\nto talk about {role}.",
                           "pass": "Hi {first_name},\n\nNot for {role}."})
    base.update(over)
    return Config(**base)


def pipe(*cids, received=NOW - timedelta(days=1)):
    p = Pipeline("role")
    for c in cids:
        p.add(Event(candidate_id=c, kind="received", at=received.isoformat()))
        p.add(Event(candidate_id=c, kind="screened", at=received.isoformat(),
                    detail={"score": 0.42}))
    return p


def vote_all(p, cid, c, *labels, **kw):
    for voter, label in zip(VOTERS, labels):
        extra = {k: v for k, v in kw.items()}
        if label == "later" and "until" not in extra:
            extra["until"] = "2027-01-15"
        cast(p, cid, voter, label, c, now=NOW, **extra)


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

def test_the_shipped_settings_file_is_valid():
    c = load_config()
    assert c.blind == "until_you_vote" and c.agreement == "unanimous"
    assert set(c.labels) == set(desk.MEANINGS)
    assert c.mail_mode == "draft"


def test_a_meaning_can_be_renamed_and_is_still_understood():
    c = cfg(labels={"contact": "Meet", "later": "Not yet", "discuss": "Talk", "pass": "No"})
    assert c.meaning("not yet") == "later" and c.meaning("later") == "later"


@pytest.mark.parametrize("over", [
    {"blind": "sometimes"}, {"agreement": "vibes"}, {"voters": []},
    {"voters": ["Ana", "ana"]}, {"recap_weekday": "someday"},
    {"labels": {"contact": "Go", "later": "Go", "discuss": "D", "pass": "P"}},
])
def test_a_settings_file_that_cannot_mean_one_thing_is_refused(over):
    with pytest.raises(DeskError):
        cfg(**over)


# --------------------------------------------------------------------------
# Votes
# --------------------------------------------------------------------------

def test_only_a_named_voter_can_vote():
    with pytest.raises(DeskError, match="does not vote"):
        cast(pipe("x"), "x", "Stranger", "contact", cfg())


def test_later_needs_a_date():
    with pytest.raises(DeskError, match="needs a date"):
        cast(pipe("x"), "x", "Ana", "later", cfg())


def test_a_date_without_later_is_refused():
    with pytest.raises(DeskError, match="only means something"):
        cast(pipe("x"), "x", "Ana", "contact", cfg(), until="2027-01-01")


def test_pass_reason_can_be_required():
    with pytest.raises(DeskError, match="needs a reason"):
        cast(pipe("x"), "x", "Ana", "pass", cfg(pass_reason_required=True))


def test_a_comment_touching_a_protected_subject_is_refused():
    with pytest.raises(DeskError, match="protected subject"):
        cast(pipe("x"), "x", "Ana", "discuss", cfg(), comment="has young children, risky")


def test_a_comment_is_one_line():
    with pytest.raises(DeskError, match="one line"):
        cast(pipe("x"), "x", "Ana", "discuss", cfg(), comment="x" * 400)


def test_a_closed_application_cannot_be_voted_on():
    p = pipe("x")
    p.add(Event(candidate_id="x", kind="closed", by="Ana", reason="withdrew"))
    with pytest.raises(DeskError, match="closed"):
        cast(p, "x", "Ben", "contact", cfg())


def test_a_changed_vote_replaces_the_old_one_in_the_count_not_in_the_log():
    p, c = pipe("x"), cfg()
    cast(p, "x", "Ana", "pass", c, now=NOW)
    cast(p, "x", "Ana", "contact", c, now=NOW + timedelta(minutes=1))
    assert votes(p.standing("x"))["Ana"].meaning == "contact"
    assert sum(e.kind == "voted" for e in p.events) == 2


# --------------------------------------------------------------------------
# Blind voting
# --------------------------------------------------------------------------

def test_others_votes_are_hidden_until_you_vote():
    p, c = pipe("x"), cfg()
    cast(p, "x", "Ben", "contact", c)
    seen = visible_to("Ana", votes(p.standing("x")), c)
    assert seen["Ben"] is None


def test_and_shown_once_you_have():
    p, c = pipe("x"), cfg()
    cast(p, "x", "Ben", "contact", c)
    cast(p, "x", "Ana", "pass", c)
    assert visible_to("Ana", votes(p.standing("x")), c)["Ben"].meaning == "contact"


def test_until_all_voted_keeps_them_hidden_after_your_own_vote():
    p, c = pipe("x"), cfg(blind="until_all_voted")
    cast(p, "x", "Ben", "contact", c)
    cast(p, "x", "Ana", "pass", c)
    assert visible_to("Ana", votes(p.standing("x")), c)["Ben"] is None


def test_blind_off_shows_everything_straight_away():
    p, c = pipe("x"), cfg(blind="off")
    cast(p, "x", "Ben", "contact", c)
    assert visible_to("Ana", votes(p.standing("x")), c)["Ben"].meaning == "contact"


def test_the_card_shows_the_viewer_first_and_only_the_two_others_after():
    p, c = pipe("x"), cfg()
    cast(p, "x", "Ben", "contact", c)
    text = card(Person("x", "Sylvia Hartmann"), Application("role", "x"), p.standing("x"),
                c, "Ana", {"role": "Founders' Associate"})
    assert text.splitlines()[0] == "Sylvia Hartmann · Founders' Associate"
    assert "Your class: not yet" in text
    assert "Ben: voted" in text and "Cy: not yet" in text
    assert "Ana:" not in text
    assert "Contact now" in text  # the button, not Ben's vote


def test_the_card_never_leaks_a_hidden_vote():
    p, c = pipe("x"), cfg()
    cast(p, "x", "Ben", "pass", c, reason="not this role", comment="thin on writing")
    text = card(Person("x", "S H"), Application("role", "x"), p.standing("x"), c, "Ana", {})
    assert "not this role" not in text and "thin on writing" not in text


# --------------------------------------------------------------------------
# What the votes add up to
# --------------------------------------------------------------------------

def test_three_alike_settle_it():
    p, c = pipe("x"), cfg()
    vote_all(p, "x", c, "contact", "contact", "contact")
    o = outcome(votes(p.standing("x")), c)
    assert (o.status, o.meaning) == ("agreed", "contact")


def test_any_disagreement_goes_to_discussion_under_unanimity():
    p, c = pipe("x"), cfg()
    vote_all(p, "x", c, "contact", "pass", "contact")
    o = outcome(votes(p.standing("x")), c)
    assert (o.status, o.meaning) == ("disagreed", "discuss")


def test_nothing_is_settled_while_someone_has_not_voted():
    p, c = pipe("x"), cfg()
    cast(p, "x", "Ana", "contact", c)
    cast(p, "x", "Ben", "contact", c)
    o = outcome(votes(p.standing("x")), c)
    assert o.status == "voting" and o.missing == ["Cy"]


def test_majority_settles_on_two_of_three_without_waiting():
    p, c = pipe("x"), cfg(agreement="majority")
    cast(p, "x", "Ana", "pass", c)
    cast(p, "x", "Ben", "pass", c)
    assert outcome(votes(p.standing("x")), c).meaning == "pass"


def test_one_contact_is_enough_under_any_contact():
    p, c = pipe("x"), cfg(agreement="any_contact")
    cast(p, "x", "Ana", "contact", c)
    assert outcome(votes(p.standing("x")), c).meaning == "contact"


def test_a_settled_later_goes_on_the_board_on_the_earliest_date_asked_for():
    p, c = pipe("x"), cfg()
    cast(p, "x", "Ana", "later", c, until="2027-03-01", comment="after the round", now=NOW)
    cast(p, "x", "Ben", "later", c, until="2026-12-01", now=NOW)
    cast(p, "x", "Cy", "later", c, until="2027-02-01", now=NOW)
    s = p.standing("x")
    assert s.revisit_on.date().isoformat() == "2026-12-01"
    revisit = next(e for e in s.events if e.kind == "revisit")
    assert revisit.by == "Ana, Ben, Cy" and "after the round" in revisit.reason


def test_a_later_that_is_not_settled_does_not_park_anyone():
    p, c = pipe("x"), cfg()
    cast(p, "x", "Ana", "later", c, until="2027-01-01")
    assert p.standing("x").revisit_on is None


def test_a_voter_has_read_the_application():
    p, c = pipe("x"), cfg()
    cast(p, "x", "Ana", "contact", c)
    assert p.standing("x").seen


# --------------------------------------------------------------------------
# One person, one card
# --------------------------------------------------------------------------

def test_the_same_candidate_on_two_roles_is_one_person():
    r = Registry()
    r.add("sylvia", "fa", "Sylvia Hartmann")
    p = r.add("sylvia", "marketing", "Sylvia Hartmann")
    assert len(r.people) == 1 and len(p.applications) == 2


def test_the_same_email_is_the_same_person():
    r = Registry()
    r.add("s1", "fa", "Sylvia Hartmann", "s@x.example")
    r.add("s2", "marketing", "S. Hartmann", "S@X.example")
    assert len(r.people) == 1


def test_the_same_name_alone_is_suggested_never_merged():
    """Merging two strangers is worse than showing one person twice."""
    r = Registry()
    r.add("m1", "fa", "María García")
    r.add("m2", "marketing", "Maria  Garcia")
    assert len(r.people) == 2
    assert r.possible and r.possible[0]["why"].startswith("same name")


def test_a_merge_is_confirmed_by_a_named_person_and_recorded():
    r = Registry()
    r.add("m1", "fa", "María García")
    r.add("m2", "marketing", "Maria Garcia")
    with pytest.raises(DeskError):
        r.merge("m1", "m2", by="")
    p = r.merge("m1", "m2", by="Ana")
    assert len(p.applications) == 2 and not r.possible
    assert r.merges[0]["by"] == "Ana"


def test_the_card_shows_the_other_applications():
    c = cfg()
    p = pipe("x")
    vote_all(p, "x", c, "pass", "pass", "pass")
    text = card(Person("x", "S H"), Application("role2", "x"), pipe("x").standing("x"), c,
                "Ana", {"role": "Founders' Associate"},
                history=[(Application("role", "x"), outcome(votes(p.standing("x")), c))])
    assert "Also applied: Founders' Associate -- Pass" in text


# --------------------------------------------------------------------------
# The recap
# --------------------------------------------------------------------------

def reg_for(*cids):
    r = Registry()
    for c in cids:
        r.add(c, "role", c.title())
    return r


def test_the_recap_looks_back_to_the_last_recap_day():
    assert last_recap_day(NOW, "tuesday").date().isoformat() == "2026-09-22"
    tuesday = datetime(2026, 9, 22, 9, tzinfo=timezone.utc)
    assert last_recap_day(tuesday, "tuesday").date().isoformat() == "2026-09-15"


def test_the_recap_names_who_is_waiting_on_whom():
    p, c = pipe("sara", "tom", received=NOW - timedelta(days=7)), cfg()
    cast(p, "sara", "Ana", "contact", c, now=NOW)
    r = recap({"role": p}, reg_for("sara", "tom"), c, now=NOW, viewer="Ben")
    text = r.render(c)
    assert "2 waiting for your vote" in text
    assert "waiting for Cy's vote" in text
    assert [n for n, _ in r.awaiting["Ana"]] == ["Tom"]


def test_a_recent_application_is_not_yet_late():
    p, c = pipe("sara", received=NOW - timedelta(days=2)), cfg()
    assert not recap({"role": p}, reg_for("sara"), c, now=NOW).awaiting


def test_no_reply_after_the_threshold_is_named():
    p, c = pipe("sara"), cfg()
    p.add(Event(candidate_id="sara", kind="contacted", by="Ana",
                at=(NOW - timedelta(days=9)).isoformat()))
    assert "Sara: written to 9 days ago, no reply" in recap(
        {"role": p}, reg_for("sara"), c, now=NOW).render(c)


def test_a_reply_clears_it():
    p, c = pipe("sara"), cfg()
    p.add(Event(candidate_id="sara", kind="contacted", by="Ana",
                at=(NOW - timedelta(days=9)).isoformat()))
    p.add(Event(candidate_id="sara", kind="replied", at=(NOW - timedelta(days=2)).isoformat()))
    assert not recap({"role": p}, reg_for("sara"), c, now=NOW).no_reply


def test_a_wake_up_carries_the_reason_it_was_parked_for():
    p, c = pipe("ines"), cfg()
    p.add(Event(candidate_id="ines", kind="revisit", by="Ana", reason="after the round",
                detail={"on": "2026-09-25"}))
    text = recap({"role": p}, reg_for("ines"), c, now=NOW).render(c)
    assert 'Wake-up: Ines, put in Later until 2026-09-25 -- "after the round"' in text


def test_disagreements_and_waiting_drafts_are_listed():
    p, c = pipe("a", "b"), cfg()
    vote_all(p, "a", c, "contact", "pass", "discuss")
    vote_all(p, "b", c, "contact", "contact", "contact")
    r = recap({"role": p}, reg_for("a", "b"), c, now=NOW)
    assert r.disagreed == ["A"] and r.drafts_waiting == ["B"]


# --------------------------------------------------------------------------
# Drafts
# --------------------------------------------------------------------------

def test_a_draft_is_addressed_to_the_first_name_and_marked_unsent():
    m = draft(Person("x", "Inès Abadi", "ines@x.example"), "Founders' Associate", "contact", cfg())
    body = m.get_content()
    assert body.startswith("Hi Inès,")
    assert m["X-Unsent"] == "1" and m["To"] == "ines@x.example"
    assert m["Subject"] == "Founders' Associate at Acme"


def test_template_line_breaks_do_not_survive_into_the_mail():
    body = draft(Person("x", "Inès Abadi"), "FA", "contact", cfg()).get_content()
    assert "We would like to talk about FA." in body


def test_the_pass_note_is_attached_only_to_a_pass():
    c = cfg()
    assert "WHAT WAS READ" in draft(Person("x", "A B"), "FA", "pass", c,
                                    note="WHAT WAS READ").get_content()
    assert "WHAT WAS READ" not in draft(Person("x", "A B"), "FA", "contact", c,
                                        note="WHAT WAS READ").get_content()


def test_nothing_is_ever_sent_automatically():
    with pytest.raises(DeskError, match="only 'draft' mode"):
        draft(Person("x", "A B"), "FA", "contact", cfg(mail_mode="auto"))


# --------------------------------------------------------------------------
# Tracking
# --------------------------------------------------------------------------

from desk import Access, next_step, timeline, viewer_from


def test_the_next_step_names_who_it_is_waiting_for():
    p, c = pipe("x"), cfg()
    cast(p, "x", "Ana", "contact", c)
    assert next_step(p.standing("x"), c, NOW) == "waiting for Ben, Cy"


def test_a_settled_class_waits_on_its_mail_then_on_a_reply():
    p, c = pipe("x"), cfg()
    for v in VOTERS:  # real clock throughout: the mail must come after the votes
        cast(p, "x", v, "contact", c)
    assert next_step(p.standing("x"), c) == "mail to send (Contact now)"
    desk.mark(p, "x", "sent", "Ana", c)
    assert next_step(p.standing("x"), c).startswith("no reply for 0 days")
    desk.mark(p, "x", "replied", "", c)
    assert next_step(p.standing("x"), c).startswith("replied")


def test_a_reply_in_the_same_clock_tick_as_the_mail_still_counts():
    # Windows clocks before Python 3.13 tick about every 15 ms: a reply recorded
    # right after the mail can carry the very same timestamp. Order decides.
    p, c = pipe("x"), cfg()
    for v in VOTERS:
        cast(p, "x", v, "contact", c)
    desk.mark(p, "x", "sent", "Ana", c)
    sent = next(e for e in reversed(p.standing("x").events) if e.kind == "contacted")
    p.add(Event(candidate_id="x", kind="replied", at=sent.at))
    assert next_step(p.standing("x"), c).startswith("replied")


def test_sending_the_pass_mail_closes_it_under_the_senders_name():
    p, c = pipe("x"), cfg()
    vote_all(p, "x", c, "pass", "pass", "pass", reason="not this role")
    desk.mark(p, "x", "sent", "Ben", c)
    s = p.standing("x")
    closed = next(e for e in s.events if e.kind == "closed")
    assert s.state == "closed" and closed.by == "Ben" and "not this role" in closed.reason


def test_sending_the_later_mail_keeps_the_wake_up():
    p, c = pipe("x"), cfg()
    vote_all(p, "x", c, "later", "later", "later")
    desk.mark(p, "x", "sent", "Ana", c)
    assert p.standing("x").revisit_on is not None


def test_the_history_follows_the_blind_rule():
    p, c = pipe("x"), cfg()
    cast(p, "x", "Ben", "pass", c, reason="not this role")
    person = Person("x", "S H", applications=[Application("role", "x")])
    for_ana = timeline(person, {"role": p}, c, "Ana")
    vote = next(m for m in for_ana if m.kind == "voted")
    assert vote.detail == "hidden until you vote"
    for_ben = timeline(person, {"role": p}, c, "Ben")
    assert "not this role" in next(m for m in for_ben if m.kind == "voted").detail


def test_the_history_spans_every_role_newest_first():
    c = cfg()
    a, b = pipe("x", received=NOW - timedelta(days=90)), Pipeline("role2")
    b.add(Event(candidate_id="x", kind="received", at=NOW.isoformat()))
    person = Person("x", "S H", applications=[Application("role", "x"),
                                                Application("role2", "x")])
    ms = timeline(person, {"role": a, "role2": b}, c)
    assert ms[0].posting_id == "role2" and ms[-1].posting_id == "role"


# --------------------------------------------------------------------------
# Who is looking
# --------------------------------------------------------------------------

def test_behind_a_proxy_the_signed_in_email_decides_who_is_looking():
    acc = Access(mode="header", emails={"ana@acme.example": "Ana"})
    assert viewer_from(acc, cfg(), {"X-Forwarded-Email": "Ana@Acme.example"}) == "Ana"


def test_behind_a_proxy_the_url_cannot_override_it():
    acc = Access(mode="header", emails={"ana@acme.example": "Ana"})
    assert viewer_from(acc, cfg(), {"X-Forwarded-Email": "ana@acme.example"}, asked="Ben") == "Ana"


def test_behind_a_proxy_an_unknown_or_missing_email_is_refused():
    acc = Access(mode="header", emails={"ana@acme.example": "Ana"})
    with pytest.raises(DeskError):
        viewer_from(acc, cfg(), {"X-Forwarded-Email": "someone@else.example"})
    with pytest.raises(DeskError):
        viewer_from(acc, cfg(), {})


def test_the_name_menu_is_refused_anywhere_but_this_machine():
    from console.desk_web import serve
    with pytest.raises(DeskError, match="127.0.0.1"):
        serve("0.0.0.0", 0, cfg(), Access(mode="pick"))


def test_three_votes_at_the_same_moment_are_all_kept(tmp_path, monkeypatch):
    """The normal case for this desk, not an edge case: without the lock,
    the second write erases the first and nobody ever notices."""
    import threading
    from pipeline import save
    monkeypatch.setattr(desk, "ROOT", tmp_path)
    save(pipe("x"), tmp_path / "runs" / "pipeline" / "role.json")
    c = cfg()
    real_cast = desk.cast

    def slow_cast(*a, **kw):
        import time
        out = real_cast(*a, **kw)
        time.sleep(0.05)  # widen the window a missing lock would lose a vote in
        return out

    monkeypatch.setattr(desk, "cast", slow_cast)
    threads = [threading.Thread(target=desk.vote_now, args=("role", "x", v, "contact", c))
               for v in VOTERS]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    kept = votes(desk._pipelines()["role"].standing("x"))
    assert set(kept) == set(VOTERS)


def test_a_provider_prefix_on_the_email_is_ignored():
    acc = Access(mode="header", header="X-Goog-Authenticated-User-Email",
                 emails={"ana@acme.example": "Ana"})
    got = viewer_from(acc, cfg(),
                      {"X-Goog-Authenticated-User-Email": "accounts.google.com:ana@acme.example"})
    assert got == "Ana"



# --------------------------------------------------------------------------
# By hand, no AI
# --------------------------------------------------------------------------

from desk import add_application


def test_an_application_can_be_added_by_hand_and_classed_with_no_ai():
    p, r, c = Pipeline("role"), Registry(), cfg()
    person = add_application(p, r, "Sara  Núñez", email="sara@x.example")
    assert person.name == "Sara Núñez" and p.standing(person.person_id).score is None
    vote_all(p, person.person_id, c, "contact", "contact", "contact")
    assert outcome(votes(p.standing(person.person_id)), c).meaning == "contact"


def test_the_same_person_added_for_a_second_role_keeps_one_card():
    r = Registry()
    add_application(Pipeline("fa"), r, "Sara Nunez", email="sara@x.example")
    person = add_application(Pipeline("marketing"), r, "Sara Nunez", email="SARA@x.example")
    assert len(r.people) == 1 and len(person.applications) == 2


def test_the_same_person_cannot_be_added_twice_for_one_role():
    p, r = Pipeline("fa"), Registry()
    add_application(p, r, "Sara Nunez", email="sara@x.example")
    with pytest.raises(DeskError, match="already applied"):
        add_application(p, r, "Sara Nunez", email="sara@x.example")


def test_two_different_people_with_one_name_get_two_cards():
    p, r = Pipeline("fa"), Registry()
    a = add_application(p, r, "Sara Nunez")
    b = add_application(p, r, "Sara Nunez")
    assert a.person_id != b.person_id and r.possible


@pytest.mark.parametrize("name,email", [("", ""), ("Sara", "not-an-email")])
def test_a_bad_entry_is_refused(name, email):
    with pytest.raises(DeskError):
        add_application(Pipeline("fa"), Registry(), name, email=email)


# --------------------------------------------------------------------------
# Notes
# --------------------------------------------------------------------------

from desk import notes, write_note


def test_a_note_is_hidden_until_you_have_voted_like_the_vote_itself():
    """"I'd pass on her" is a vote in prose."""
    p, c = pipe("x"), cfg()
    cast(p, "x", "Ben", "pass", c)
    write_note(p, "x", "Ben", "I'd pass: the writing sample is thin", c)
    assert notes(p.standing("x"), "Ana", c)[0].text is None
    cast(p, "x", "Ana", "discuss", c)
    assert "writing sample" in notes(p.standing("x"), "Ana", c)[0].text


def test_your_own_note_is_always_visible_to_you():
    p, c = pipe("x"), cfg()
    write_note(p, "x", "Ana", "want to see her model", c)
    assert notes(p.standing("x"), "Ana", c)[0].text == "want to see her model"


def test_the_history_hides_a_note_the_same_way():
    p, c = pipe("x"), cfg()
    write_note(p, "x", "Ben", "strong yes", c)
    person = Person("x", "S H", applications=[Application("role", "x")])
    m = next(m for m in timeline(person, {"role": p}, c, "Ana") if m.kind == "noted")
    assert m.detail == "hidden until you vote"


@pytest.mark.parametrize("text,why", [("", "empty"), ("x" * 700, "at most"),
                                      ("she mentioned her pregnancy", "protected")])
def test_a_note_that_cannot_go_on_the_record_is_refused(text, why):
    with pytest.raises(DeskError, match=why):
        write_note(pipe("x"), "x", "Ana", text, cfg())


def test_only_a_voter_can_write_a_note():
    with pytest.raises(DeskError, match="does not vote"):
        write_note(pipe("x"), "x", "Stranger", "hello", cfg())


def test_an_arrival_written_without_a_time_zone_does_not_break_the_recap_or_the_card():
    # A hand-edited log or an old export writes "2026-09-10T10:00:00". The
    # desk compared it with an aware `now` and crashed the whole recap and
    # every card; it reads it as UTC, like a bare revisit date.
    from console.desk_web import _days_ago
    naive = (NOW - timedelta(days=7)).replace(tzinfo=None)
    p, c = pipe("sara", received=naive), cfg()
    assert p.standing("sara").events[0].when.tzinfo is not None
    r = recap({"role": p}, reg_for("sara"), c, now=NOW, viewer="Ana")
    assert [n for n, _ in r.awaiting["Ana"]] == ["Sara"]
    assert _days_ago(p.standing("sara"), NOW) == "applied 7 days ago"
