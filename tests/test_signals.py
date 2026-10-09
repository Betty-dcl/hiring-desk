"""Built, wants to own, AI-native: the three lines a partner reads first.

Read off the documents and the records on disk, with no model. Most of these
tests are about what must NOT be taken for a signal: a past fact read as a
wish, a skills list read as use, a self-description read as proof.
"""

from __future__ import annotations

import re

import desk
import signals as signals_mod
from desk import Document, Person, _links, _place_links
from signals import (classify, is_wish, links_in, role_in, signals, tool_sentence,
                     verbatim, wants_in)


def test_links_are_sorted_code_then_sites_then_profiles():
    ls = links_in(["see https://github.com/a/b and https://demo.example/x."],
                  ["https://www.linkedin.com/in/a"])
    assert [l.kind for l in ls] == ["code", "site", "profile"]
    assert ls[1].url == "https://demo.example/x"  # the full stop is not the link


def test_the_same_link_twice_is_one_link():
    assert len(links_in(["https://github.com/a/b/"], ["https://github.com/a/b"])) == 1


def test_a_look_alike_host_is_not_code():
    assert classify("https://github.com.evil.example/x").kind == "site"


def test_a_wish_is_read_and_a_past_fact_is_not():
    text = "I owned the close for two entities. I'd want to own the treasury stack."
    assert wants_in(text) == ("I'd want to own the treasury stack.",)


def test_a_skills_list_is_not_use():
    assert tool_sentence(["Skills: Claude, Codex, coding agents, LLMs, GTM, fintech"]) == ""


def test_a_quality_claimed_is_not_a_tool_named():
    assert tool_sentence(["I am genuinely AI-native."]) == ""


def test_a_first_person_sentence_naming_a_tool_is_kept_as_written():
    s = "I have been using Claude Code daily to rebuild the reporting pack."
    assert tool_sentence([s]) == s


def test_several_links_are_placed_profile_first():
    p = Person("x", "X")
    _place_links(p, _links("https://github.com/x, https://www.linkedin.com/in/x\nhttps://x.example"))
    assert p.link == "https://www.linkedin.com/in/x"
    assert p.links == ["https://github.com/x", "https://x.example"]
    assert p.all_links[0] == p.link


def test_a_repository_never_takes_the_profile_slot():
    p = Person("x", "X")
    _place_links(p, ["https://github.com/x"])
    assert p.link == "" and p.links == ["https://github.com/x"]


def _demo(pid):
    reg = desk.load_registry(desk._registry_path())
    return signals(reg.people[pid], pid, "founders_associate")


def test_the_demo_reads_the_way_a_partner_would():
    ines, paul, sylvia, mara = (_demo(x) for x in
                                ("ines_abadi", "paul_okonkwo", "sylvia_hartmann", "mara_velichko"))
    assert ines.to_open and ines.ai == "described" and ines.wants
    assert paul.to_open and paul.ai == "silent"
    # the letter names the tool; the screening on file never saw the letter
    assert sylvia.ai == "in_their_words" and "Claude Code" in sylvia.ai_rests_on
    assert not mara.to_open and mara.ai == "claimed"


def test_the_demo_card_is_their_words_or_not_found():
    """Every quoted line on the five demo cards is a sentence of theirs."""
    reg = desk.load_registry(desk._registry_path())
    for pid in ("ines_abadi", "paul_okonkwo", "sylvia_hartmann", "mara_velichko", "tomas_renner"):
        sg = _demo(pid)
        docs = {d.kind: signals_mod._text(desk._files_dir() / d.stored)
                for d in reg.people[pid].documents_for("founders_associate")}
        for q, src in [(sg.ai_rests_on, sg.ai_from), (sg.role, sg.role_from),
                       *zip(sg.built, sg.built_from),
                       *zip(sg.wants, sg.wants_from)]:
            if q:
                assert verbatim(q, docs[src]), (pid, q)
        assert not re.search(r"\b(19|20)\d\d\b|years", sg.role)


def test_the_demo_lines_before_and_after():
    mara, sylvia, tomas = (_demo(x) for x in ("mara_velichko", "sylvia_hartmann", "tomas_renner"))
    # Was: claimed only -- "Candidate describes self as an AI-native operator",
    # a model's summary printed in quotation marks as if she had written it.
    assert mara.ai == "claimed" and mara.ai_rests_on == "Genuinely AI-native operator."
    assert mara.ai_from == "cv"
    # Was: the extractor's "Rebuilt the budgeting process after the ERP migration".
    assert sylvia.built[0].startswith(
        "Rebuilt the budgeting process after the ERP migration, because")
    assert sylvia.ai_from == "letter"
    # Her letter says why Causa Prima, not what she would own: not found, not invented.
    assert sylvia.wants == () and "open their “Why us”" in sylvia.lines()[2]
    assert "no “Why us” answer" in tomas.lines()[2]
    assert tomas.role == "Head of Strategic Initiatives — Nordhaven Group"


# -- the wants search, measured ------------------------------------------------

def test_every_wish_in_the_corpus_is_found_and_nothing_else():
    from wants_corpus import HELD_OUT_NOT, HELD_OUT_WISH, NOT_A_WISH, WISH
    assert [s for s in WISH + HELD_OUT_WISH if not is_wish(s)] == []
    assert [s for s in NOT_A_WISH + HELD_OUT_NOT if is_wish(s)] == []


def test_what_the_patterns_still_miss_is_written_down():
    """Three wishes in eight, on sentences written to break the patterns.
    Not hidden: this is what `intake/wants.py` would be for."""
    from wants_corpus import STRESS_NOT, STRESS_WISH
    assert [s for s in STRESS_NOT if is_wish(s)] == []
    assert sum(map(is_wish, STRESS_WISH)) == 3


def test_politeness_does_not_hide_a_wish_later_in_the_sentence():
    assert is_wish("I'd like to thank you, and I'd like to own the close.")
    assert not is_wish("I'd like to thank you for reading.")


def test_a_wish_is_found_in_the_cv_too():
    assert wants_in("Profile\n\nMy goal is to run finance operations.") == (
        "My goal is to run finance operations.",)


# -- the gate: nothing is shown that is not in their document ------------------

def _person(tmp_path, monkeypatch, cv="", letter=""):
    files = tmp_path / "files"
    (files / "x").mkdir(parents=True)
    p = Person("x", "X")
    for kind, text in (("cv", cv), ("letter", letter)):
        if text:
            (files / "x" / f"{kind}.md").write_text(text, encoding="utf-8")
            p.documents.append(Document("p", kind, f"x/{kind}.md", f"{kind}.md"))
    monkeypatch.setattr(desk, "_files_dir", lambda: files)
    monkeypatch.setattr(signals_mod, "_facts", lambda c: {})
    monkeypatch.setattr(signals_mod, "_ai", lambda c, p, f: ("silent", None))
    monkeypatch.setattr(signals_mod, "_wants_on_file", lambda c: [])
    return p


def _fact(fid, quote, kind="experience", evidence="instance", period="", source="cv",
          claim="a model's words"):
    from intake.cv import Fact
    return Fact(fid, kind, claim, quote, period, evidence, source)


def test_a_record_from_an_older_cv_is_not_quoted(tmp_path, monkeypatch):
    """The CV was replaced after intake: its facts quote words no longer there.

    `verbatim` is the only thing between them and the card.
    """
    p = _person(tmp_path, monkeypatch, cv="Experience\n\n- Ran the close for three entities.")
    old = _fact("f1", "Built the treasury dashboard used by the CFO.", kind="project")
    monkeypatch.setattr(signals_mod, "_facts", lambda c: {"f1": old})
    sg = signals(p, "x", "p")
    assert sg.built == () and sg.lines()[0] == "built: no link given"


def test_a_wish_on_file_that_is_not_in_the_letter_is_not_quoted(tmp_path, monkeypatch):
    p = _person(tmp_path, monkeypatch, letter="I chased receipts. Nobody else was going to.")
    monkeypatch.setattr(signals_mod, "_wants_on_file",
                        lambda c: [("I'd want to own the treasury stack.", "letter")])
    sg = signals(p, "x", "p")
    assert sg.wants == () and sg.lines()[2] == "wants to own: not found -- open their “Why us”"


def test_a_wish_on_file_is_shown_as_their_sentence(tmp_path, monkeypatch):
    p = _person(tmp_path, monkeypatch,
                letter="Hello. The close is what I'd sink my teeth into, honestly.")
    monkeypatch.setattr(signals_mod, "_wants_on_file",
                        lambda c: [("The close is what I'd sink my teeth into", "letter")])
    sg = signals(p, "x", "p")
    assert sg.wants == ("The close is what I'd sink my teeth into, honestly.",)


def test_the_ai_line_quotes_the_document_not_the_extractor(tmp_path, monkeypatch):
    p = _person(tmp_path, monkeypatch, cv="Profile\n\nGenuinely AI-native operator. I move fast.")
    f = _fact("p", "Genuinely AI-native operator.", kind="other", evidence="assertion",
              claim="Candidate describes self as an AI-native operator")
    monkeypatch.setattr(signals_mod, "_ai", lambda c, q, facts: ("claimed", f))
    sg = signals(p, "x", "p")
    assert sg.ai_rests_on == "Genuinely AI-native operator." and sg.ai_from == "cv"


def test_a_fragment_is_widened_to_its_sentence_in_the_document(tmp_path, monkeypatch):
    cv = ("- Built an internal tool with Claude Code that pulls invoice status from the\n"
          "  accounting system into a weekly cash view, after doing it by hand twice.\n")
    p = _person(tmp_path, monkeypatch, cv=cv)
    f = _fact("t", "Built an internal tool with Claude Code", kind="project")
    monkeypatch.setattr(signals_mod, "_facts", lambda c: {"t": f})
    assert signals(p, "x", "p").built[0].endswith("after doing it by hand twice.")


def test_the_role_never_carries_years_or_a_career_break(tmp_path, monkeypatch):
    cv = ("Experience\n\nFull-time carer (2023 - present)\n\n"
          "Finance Manager — Ardenne (2017 - 2022)\n\nTen years in finance, 2012.")
    p = _person(tmp_path, monkeypatch, cv=cv)
    facts = {"a": _fact("a", "Full-time carer", period="2023 - present"),
             "b": _fact("b", "Ten years in finance, 2012", period="2012 - present"),
             "c": _fact("c", "Finance Manager — Ardenne", period="2017 - 2022")}
    monkeypatch.setattr(signals_mod, "_facts", lambda c: facts)
    sg = signals(p, "x", "p")
    assert sg.role == "Finance Manager — Ardenne"
    assert sg.lines()[3] == 'current role: "Finance Manager — Ardenne" (from CV -- check it)'


def test_the_role_without_a_record_is_the_first_line_under_experience():
    assert role_in("# A\n\n## Experience\n\n**Chief of Staff — Talvera** (Jan 2024 – present)\n"
                   "- Ran things.") == "Chief of Staff — Talvera"
    assert role_in("No heading here.\nChief of Staff") == ""


def test_no_role_is_shown_rather_than_a_guess(tmp_path, monkeypatch):
    p = _person(tmp_path, monkeypatch, cv="I like numbers.")
    sg = signals(p, "x", "p")
    assert sg.role == "" and len(sg.lines()) == 3


def test_the_card_on_the_page_quotes_only_them_and_names_the_document():
    import html as _html
    from console.card_web import card
    from signals import Signals
    sg = Signals(links=(), built=(), wants=("I'd own <the> close.",), ai="silent",
                 ai_rests_on="", wants_from=("letter",), has_letter=True)
    page = card(sg)
    assert "<q title=\"I&#x27;d own &lt;the&gt; close.\">" in page and "from their “Why us”" in page
    assert "no link given" in page and "not stated" in page
    assert "Current role" not in page  # no role found: no row, not a guess
    quoted = [_html.unescape(q) for q in re.findall(r"<q[^>]*>(.*?)</q>", page)]
    assert quoted == ["I'd own <the> close."]
