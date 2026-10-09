"""`intake/wants.py`, with a fake client: written, never run on anyone.

What is tested is the part that does not depend on the model being good:
a span not in the document is dropped, and what is kept reaches the card
only through `signals`, which checks it again.
"""

from __future__ import annotations

import json

from intake import wants

LETTER = "Dear team,\n\nThe close is what I'd sink my teeth into.\nThank you."
CV = "Experience\n\n- Ran the close for two entities."


class FakeClient:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def structured(self, **kw):
        self.calls.append(kw)
        return self.answer


def test_a_span_not_in_the_document_is_dropped_and_counted():
    client = FakeClient({"wants": [
        {"source_quote": "The close is what I'd sink my teeth into.", "source": "letter"},
        # paraphrased by the model: not their words
        {"source_quote": "She wants to own the close.", "source": "letter"},
        # their words, but in the other document than the one named
        {"source_quote": "Ran the close for two entities.", "source": "letter"},
    ]})
    w = wants.extract(client, "x", {"letter": LETTER, "cv": CV}, model="fake")
    assert w.wants == [{"source_quote": "The close is what I'd sink my teeth into.",
                        "source": "letter"}]
    assert len(w.rejected) == 2


def test_the_model_sees_both_documents_and_is_asked_for_spans_only():
    client = FakeClient({"wants": []})
    wants.extract(client, "x", {"letter": LETTER, "cv": CV}, model="fake")
    kw = client.calls[0]
    assert "# letter" in kw["user"] and "# cv" in kw["user"]
    assert "character-for-character" in kw["system"]
    assert kw["schema"]["properties"]["wants"]["items"]["required"] == ["source_quote", "source"]


def test_what_is_saved_is_where_the_card_reads_it(tmp_path, monkeypatch):
    import signals
    w = wants.verify("x", {"letter": LETTER},
                     [{"source_quote": "The close is what I'd sink my teeth into.",
                       "source": "letter"}])
    p = wants.save(w, tmp_path / "runs" / "wants")
    assert json.loads(p.read_text(encoding="utf-8"))["wants"][0]["source"] == "letter"
    monkeypatch.setattr(signals, "ROOT", tmp_path)
    assert signals._wants_on_file("x") == [("The close is what I'd sink my teeth into.", "letter")]
