"""The percentage beside a name is arithmetic over the documents: checked here on invented text."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import match  # noqa: E402
import match  # noqa: E402
from match import Criterion, Posting, count, words  # noqa: E402

POST = Posting(
    company=frozenset(words("Acme")),
    subject=frozenset(words("agents that settle invoices and payment terms between companies")),
    criteria=(Criterion("ops", 2.0, frozenset(words("month-end close banking payroll invoicing"))),
              Criterion("model", 1.0, frozenset(words("financial model fundraising investors")))))

CV = """Chief of Staff, Talvera.
- Built the month-end close across 3 entities, banking and payroll included.
- Cut the close from 12 days to 5.
"""
LETTER = """I'd want to own the financial operations at Acme.
I built an internal tool with Claude Code that pulls invoice status into a weekly cash view.
Demo: https://example.org/cash-view
"""


def test_the_weights_add_up_to_one():
    assert round(sum(match.WEIGHTS.values()), 6) == 1.0


def test_each_part_rests_on_what_is_written(monkeypatch):
    monkeypatch.setattr(match, "specific", lambda w: 1.0)  # every word says something here
    m = count(CV, LETTER, [], POST)
    # Keywords: a criterion is met in full once a third of what it asks is found.
    have = match._as_written(CV + " " + LETTER).keys()
    share = [min(1.0, len(c.keywords & have) / (match.COVER * len(c.keywords)))
             for c in POST.criteria]
    assert round(m.parts["keywords"], 4) == round((2.0 * share[0] + 1.0 * share[1]) / 3.0, 4)
    assert "payroll" in m.found["keywords"] and "fundraising" not in m.found["keywords"]
    # Motivation: an answer, the company named, a wish, tied to their own work.
    assert m.parts["motivation"] == 1.0
    # Proof: a link, sentences about something made, figures.
    assert 0.4 < m.parts["proof"] <= 1.0
    # AI: a tool named, in a sentence where something was made.
    assert m.parts["ai"] == 1.0 and "claude code" in m.found["ai"]
    assert 0 < m.total <= 1


def test_repeating_a_word_earns_nothing():
    once = count(CV, LETTER, [], POST)
    stuffed = count(CV + " payroll" * 50, LETTER, [], POST)
    assert stuffed.parts["keywords"] == once.parts["keywords"]


def test_no_letter_no_motivation_and_no_documents_no_number():
    m = count(CV, "", [], POST)
    assert m.parts["motivation"] == 0.0 and m.found["motivation"] == []


def test_a_posting_without_criteria_leaves_the_keywords_out():
    m = count(CV, LETTER, [], None)
    assert "keywords" not in m.parts
    # The three other parts carry the total between them.
    w = {k: match.WEIGHTS[k] for k in m.parts}
    assert m.total == round(sum(w[k] * m.parts[k] for k in w) / sum(w.values()), 4)



# --------------------------------------------------------------------------
# What the posting asks for, not words that happen to match
# --------------------------------------------------------------------------

def test_a_word_every_posting_uses_earns_nothing(monkeypatch):
    monkeypatch.setattr(match, "specific", lambda w: 0.0 if w == "system" else 1.0)
    post = Posting(frozenset(), frozenset(), (Criterion("x", 1.0, frozenset({"system"})),))
    assert count("I designed a system.", "", [], post).parts["keywords"] == 0.0


def test_the_heart_of_a_criterion_must_be_there(monkeypatch):
    # "Python" only this role asks for; "design" and "system" every role does.
    spec = {"python": 1.0, "design": 0.25, "system": 0.25}
    monkeypatch.setattr(match, "specific", lambda w: spec.get(w, 0.5))
    post = Posting(frozenset(), frozenset(),
                   (Criterion("py", 2.0, frozenset({"python", "design", "system"})),))
    assert count("I design a system every day.", "", [], post).parts["keywords"] == 0.0
    assert count("I design systems in Python.", "", [], post).parts["keywords"] == 1.0


def test_a_hard_requirement_with_nothing_found_caps_the_total(monkeypatch):
    monkeypatch.setattr(match, "specific", lambda w: 1.0)
    post = Posting(frozenset(), frozenset(),
                   (Criterion("ai", 2.0, frozenset({"agent"}), hard=True),
                    Criterion("ops", 1.0, frozenset({"payroll"}))))
    m = count("Ran payroll for 3 entities. Built a tool, 40% faster.", "", ["https://x.example"],
              post)
    assert m.cap == match.HARD_CAP and m.total <= match.HARD_CAP
    assert m.found["missing"] == ["a requirement the posting calls hard: ai"]


def test_experience_ranks_the_role_it_fits_above_one_it_does_not():
    # Invented CVs, read against the real postings on file.
    consultant = ("Strategy consultant at a top-tier consulting firm for four years: M&A, "
                  "private equity due diligence, financial models and market analysis. "
                  "Built agents with Claude Code to automate a reporting workflow.")
    engineer = ("Backend engineer: Python and TypeScript in production, PostgreSQL with RLS "
                "and event sourcing, Kafka, Kubernetes. Built agents with Claude Code.")
    fa, be = "founders_associate", "senior_backend_platform_engineer"
    c = {pid: count(consultant, "", [], match.posting(pid)).parts["keywords"] for pid in (fa, be)}
    e = {pid: count(engineer, "", [], match.posting(pid)).parts["keywords"] for pid in (fa, be)}
    assert c[fa] > c[be] and e[be] > e[fa]



# --------------------------------------------------------------------------
# Years: only where the posting asks for them, in the field it names
# --------------------------------------------------------------------------

def test_the_years_a_posting_asks_for_are_read_from_its_sentence():
    assert match.asked_years("3–4 years in a high-intensity environment.") == 3
    assert match.asked_years("5+ years designing backend platforms") == 5
    assert match.asked_years("Fluent English. It's our working language.") == 0


CAREER = """Strategy consultant, a consulting firm
Jan 2019 – Dec 2023
Advised private equity funds on due diligence.

Junior developer, a retailer
2024 – present
Wrote Python scripts for reports."""


def test_the_years_in_a_field_are_added_from_the_dated_jobs():
    assert match.years_in(CAREER, frozenset({"consult"}), 2026) == 4
    assert match.years_in(CAREER, frozenset({"python"}), 2026) == 2
    assert match.years_in(CAREER, frozenset({"kubernet"}), 2026) == 0
    assert match.years_in("Python, SQL, Kafka.", frozenset({"python"}), 2026) is None
    assert match.years_in("Six years in consulting.", frozenset({"consult"}), 2026) == 6


def test_asked_eight_years_shown_one_the_criterion_is_worth_an_eighth(monkeypatch):
    monkeypatch.setattr(match, "specific", lambda w: 1.0)
    post = Posting(frozenset(), frozenset(),
                   (Criterion("career", 1.0, frozenset({"python"}), years=8.0),))
    one = count("Developer\n2025 - present\nPython services.", "", [], post)
    assert round(one.parts["keywords"], 3) == round(1 / 8, 3)
    assert one.found["years"] == ["career: asks 8+ years, about 1 shown"]
    undated = count("Python services.", "", [], post)
    assert undated.parts["keywords"] == 0.5 and "none stated" in undated.found["years"][0]


def test_an_open_application_has_nothing_to_compare_to():
    assert "open_application" in match.open_roles()
    m = count("Built a ledger with Claude Code; 40% faster.", "I want to own payments.", [], None)
    assert "keywords" not in m.parts and m.total > 0
