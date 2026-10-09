"""Verification has to throw things out, or it is not verification.

On the five real Causa Prima postings the extractor rejected nothing, which
is the good outcome and also means the rejection path never ran. These tests
run it, with the failures hand-written so the check is exercised whether or
not a model ever misbehaves again.

`verify()` is a pure function over the extractor's output, so all of this
runs with no model and no network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from intake.posting import Posting, normalise, to_toml, verify

POSTINGS = Path(__file__).resolve().parent.parent / "intake" / "postings"

TEXT = """\
What we're looking for

 - 3-4 years in a high-intensity environment. Top-tier consulting, banking, VC/PE.

 - Genuinely AI-native. This is a hard requirement, not a buzzword.

 - Fluent English. It's our working language.

This is a wishlist, not a checklist. If the role excites you but you don't
tick every box, we would encourage you to apply anyway.

Nice to have

 - Spanish or German
"""


def posting(text: str = TEXT) -> Posting:
    return Posting(id="t", title="Test Role", company="Acme", location="Madrid", text=text)


def item(**over):
    base = {
        "id": "fluent_english",
        "question": "Are you fluent in English?",
        "source_quote": "Fluent English. It's our working language.",
        "section": "requirement",
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------
# Quote verification
# --------------------------------------------------------------------------

def test_a_quoted_criterion_survives():
    a = verify(posting(), [item()])
    assert [c.id for c in a.criteria] == ["fluent_english"]
    assert not a.rejected


def test_an_invented_criterion_is_rejected():
    """The failure mode the whole module exists to stop."""
    a = verify(posting(), [item(id="spanish_national",
                                source_quote="Must be a Spanish national.")])
    assert not a.criteria
    assert a.rejected[0].id == "spanish_national"
    assert "does not appear" in a.rejected[0].reason


def test_a_paraphrase_is_rejected():
    """Close is not quoted. A paraphrase is exactly what we cannot verify."""
    a = verify(posting(), [item(source_quote="You must speak English fluently.")])
    assert not a.criteria and a.rejected


@pytest.mark.parametrize("quote", [
    "fluent english.  it's our working language.",
    "Fluent English. It’s our working language.",
    "Fluent  English.\nIt's our working language.",
])
def test_reflowed_and_retyped_quotes_still_match(quote):
    """Whitespace, curly quotes and case are not evidence of invention."""
    assert verify(posting(), [item(source_quote=quote)]).criteria


def test_rejections_are_counted_not_swallowed():
    a = verify(posting(), [item(), item(id="made_up", source_quote="We offer a company car.")])
    assert a.to_dict()["extraction_yield"] == {"kept": 1, "rejected": 1, "rate": 0.5}


# --------------------------------------------------------------------------
# Gates
# --------------------------------------------------------------------------

def test_a_gate_the_posting_declares_is_kept():
    a = verify(posting(), [item(id="ai_native",
                                source_quote="Genuinely AI-native. This is a hard requirement, not a buzzword.",
                                hard=True,
                                hard_quote="This is a hard requirement, not a buzzword.")])
    assert a.criteria[0].hard


def test_a_gate_the_posting_never_declared_is_demoted_not_dropped():
    """The criterion is real; only its claim to be a gate is not.

    Dropping the whole item would lose something the posting did ask for, and
    keeping the gate would invent an eliminator. It survives, unarmed.
    """
    a = verify(posting(), [item(hard=True, hard_quote="This is non-negotiable.")])
    assert a.criteria[0].hard is False
    assert a.rejected[0].id == "fluent_english.hard"


def test_enthusiasm_is_not_hardness():
    """No hard_quote at all means no gate, whatever `hard` claims."""
    a = verify(posting(), [item(hard=True, hard_quote="")])
    assert a.criteria[0].hard is False


def test_the_wishlist_declaration_is_read_off_the_posting():
    assert posting().declares_wishlist
    assert not posting("We want someone great.").declares_wishlist


# --------------------------------------------------------------------------
# Bookkeeping
# --------------------------------------------------------------------------

def test_sections_set_the_default_weight():
    a = verify(posting(), [
        item(),
        item(id="nice_langs", source_quote="Spanish or German", section="nice_to_have"),
    ])
    assert [c.weight for c in a.criteria] == [2.0, 1.0]


def test_every_criterion_starts_unconfirmed():
    """Weights are never extracted, so nothing may arrive already trusted."""
    assert all(c.needs_confirmation for c in verify(posting(), [item()]).criteria)


def test_responsibilities_are_not_scored():
    a = verify(posting(), [item(id="r", source_quote="Fluent English.", section="responsibility")])
    assert a.criteria and not a.scored


def test_a_duplicate_id_is_rejected():
    a = verify(posting(), [item(), item()])
    assert len(a.criteria) == 1 and a.rejected[0].reason == "duplicate id"


def test_an_unknown_section_is_rejected():
    a = verify(posting(), [item(section="culture")]).rejected
    assert a and "culture" in a[0].reason


def test_normalise_is_not_so_loose_it_matches_anything():
    assert normalise("Fluent English") != normalise("Fluent German")


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def test_the_toml_carries_its_provenance():
    p = posting()
    text = to_toml(verify(p, [item()]), p)
    assert 'from the posting: "Fluent English' in text
    assert "confirm before use" in text
    assert "wishlist, not a checklist" in text


def test_the_toml_loads_as_a_role_mandate(tmp_path):
    """The generated file has to be readable by the thing that consumes it."""
    from nbh.mandates import load_role

    p = posting()
    f = tmp_path / "role.toml"
    f.write_text(to_toml(verify(p, [item()]), p), encoding="utf-8")
    role = load_role(f)
    assert role.id == "t" and [c.id for c in role.criteria] == ["fluent_english"]


# --------------------------------------------------------------------------
# The real postings
# --------------------------------------------------------------------------

@pytest.mark.skipif(not POSTINGS.exists(), reason="postings not vendored")
def test_the_real_postings_load_and_carry_their_source():
    files = sorted(POSTINGS.glob("*.md"))
    assert len(files) >= 5
    for f in files:
        p = Posting.from_markdown(f)
        assert p.text and "Source :" not in p.text
        assert p.title and not p.title.startswith("#")


@pytest.mark.skipif(not POSTINGS.exists(), reason="postings not vendored")
def test_the_gate_in_the_founders_associate_posting_is_really_there():
    """The one declared gate across five postings, checked against the text."""
    p = Posting.from_markdown(POSTINGS / "founders-associate.md")
    assert p.contains("This is a hard requirement, not a buzzword")
    assert p.declares_wishlist

    ml = Posting.from_markdown(POSTINGS / "marketing-lead.md")
    assert not ml.contains("This is a hard requirement")
