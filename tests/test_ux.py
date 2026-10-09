"""The desk's polish that a partner would notice first: legible text, a button
that can be read, and empty lists that say what to do."""

from __future__ import annotations

import re

from console import desk_web
from console.desk_web import CSS, TABS, _empty


def _vars(block: str) -> dict[str, str]:
    return dict(re.findall(r"--([a-z0-9-]+):(#[0-9a-f]{6}|#[0-9a-f]{3})", block))


def _lum(h: str) -> float:
    if len(h) == 4:
        h = "#" + "".join(x * 2 for x in h[1:])
    c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= .03928 else ((x + .055) / 1.055) ** 2.4 for x in c]
    return .2126 * c[0] + .7152 * c[1] + .0722 * c[2]


def _ratio(a: str, b: str) -> float:
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + .05) / (lo + .05)


def test_small_grey_text_passes_aa_in_both_themes():
    # The faintest grey carries dates, field labels and the "any order" band:
    # at 12-13px it must reach 4.5:1 on the page and on a card.
    light = _vars(CSS[:CSS.index("@media (prefers-color-scheme:dark)")])
    dark = _vars(CSS[CSS.index("@media (prefers-color-scheme:dark)"):].split("}}")[0])
    for theme in (light, {**light, **dark}):
        for fg in ("mute", "faint"):
            for bg in ("bg", "panel"):
                assert _ratio(theme[fg], theme[bg]) >= 4.5, (fg, bg, theme[fg], theme[bg])


def test_a_confirm_button_inside_a_button_row_is_readable():
    # `.buttons button` paints every button white; `.go` writes in the panel
    # colour. Without the later rule the vote-link page showed a blank button.
    rule = re.search(r"\.buttons \.go[^{]*\{([^}]*)\}", CSS)
    assert rule and "background:var(--ink)" in rule.group(1)
    assert CSS.index(".buttons .go") > CSS.index(".buttons a,.buttons button")


def test_every_empty_tab_says_why_it_is_empty():
    for tab in TABS:
        said = _empty(tab)
        assert said.startswith("<b>") and "Nothing here" not in said, tab
    assert "your vote" in _empty("todo")


def test_an_empty_search_names_what_was_searched_escaped():
    said = _empty("all", "<i>zz")
    assert "&lt;i&gt;zz" in said and "<i>" not in said


def test_the_vote_words_are_the_same_on_the_list_and_the_card():
    assert desk_web._say("waiting for Ana, Ben", "Ana") == "needs your vote · 1 more"
