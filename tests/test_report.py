"""`python -m harness report`: the README's numbers against the records.

The report is only worth something if it cannot quietly fall behind the
text it checks. So the suite refuses a README number that no claim or
exemption covers, a claim whose words left the README, a claim that stopped
holding without being listed as disputed, and a committed report that no
longer matches what the command prints. And it runs the whole thing with the
network and subprocesses cut, because a statistics report that could call a
model would not be free.
"""

from __future__ import annotations

import json
import socket
import subprocess

import pytest

from harness import report


@pytest.fixture(scope="module")
def rep():
    return report.build(report.ROOT)


def test_every_number_in_the_readme_is_a_claim_or_exempt():
    found = report.claims(report.ROOT, simulate=False)
    quotes = ([c.quote for c in found if c.doc == "README.md"]
              + [q for d, q, _ in report.EXEMPT if d == "README.md"])
    text = (report.ROOT / "README.md").read_text(encoding="utf-8")
    assert report.uncovered(text, quotes) == []


def test_every_claim_still_quotes_its_document():
    assert report.missing_quotes(report.ROOT, report.claims(report.ROOT, simulate=False)) == []


def test_a_new_number_is_caught_and_code_is_not_prose():
    text = ("Screened three times.\n\n    python -m harness stability --n 3\n\n"
            "```\nrun 7 times\n```\n"
            "See [the 2 notes](docs/v2.md). It caught 9 of 10.\n")
    left = report.uncovered(text, ["Screened three times."])
    assert len(left) == 3 and all(("2 notes" in x) or ("9 of 10" in x) for x in left)


def test_a_quote_survives_rewrapping_and_emphasis():
    assert report.uncovered("It **caught** 25\nof 32.", ["It caught 25 of 32."]) == []


def test_what_does_not_hold_is_exactly_what_is_listed_as_disputed(rep):
    failing = {c["id"] for c in rep["claims"] if not c["holds"]}
    assert failing == set(report.DISPUTED)
    # A disputed claim says what text the data would support.
    for c in rep["claims"]:
        if not c["holds"]:
            assert c["suggested"], c["id"]


def test_the_text_and_the_harness_agree_on_the_name_floor(rep):
    """The six disputes the first report found were settled by moving the text
    to the stricter method. What keeps them settled is that the claim checks
    the harness's own number, not only this module's arithmetic."""
    from harness.counterfactual import load

    cf = load(report.ROOT / report.BIAS_PAUL)
    by = {c["id"]: c for c in rep["claims"]}
    v = by["names_floor"]["values"]
    assert cf.detection_floor == pytest.approx(v["floor_t"], abs=1e-4)
    assert f"{cf.detection_floor:.1%}" in cf.render()
    assert f"{v['floor_permutation']:.1%}" in cf.render()
    assert by["names_floor"]["holds"]


def test_a_claim_fails_when_the_harness_prints_another_number(monkeypatch):
    """If `harness bias --report` went back to the normal curve (5.5), the
    README's 5.8 would no longer be what the tool prints, and the claim must
    say so even though this module's own t floor is unchanged."""
    from harness import counterfactual

    monkeypatch.setattr(counterfactual.Counterfactual, "detection_floor",
                        property(lambda self: report.stats.detectable(
                            self.pooled_sd, self.n, comparisons=self.comparisons)))
    by = {c.id: c for c in report.claims(report.ROOT, simulate=False)}
    assert not by["names_floor"].holds and not by["details_names_table"].holds


def test_the_committed_report_is_what_the_command_prints(rep):
    # Also the reproducibility test: the committed files came from another
    # process, and every simulated digit in them must come out the same here.
    committed = report.ROOT / "runs" / "report"
    assert json.loads((committed / "claims.json").read_text(encoding="utf-8")) == json.loads(
        json.dumps(rep))
    md = (committed / "claims.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert md == report.render(rep)


def test_intervals_are_printed_with_the_rates(rep):
    by = {c["id"]: c for c in rep["claims"]}
    judge = by["judge_25_of_32"]
    assert "Wilson 95%" in judge["interval"] and "Clopper-Pearson" in judge["interval"]
    lo, hi = report.stats.wilson(25, 32)
    assert f"{lo * 100:.1f}%" in judge["interval"] and f"{hi * 100:.1f}%" in judge["interval"]
    # The floor is given three ways, and the simulated one is not smaller than
    # the t formula's: a permutation test on lumpy scores has less power.
    v = by["names_floor"]["values"]
    assert v["floor_normal"] < v["floor_t"] <= v["floor_permutation"]


def test_rank_pairs_are_graded_by_the_pooled_drift_not_their_own_runs(rep):
    pool = rep["drift_pool"]
    for p in rep["rank_pairs"]:
        want = report.stats.swap_probability(p["gap"], pool["sd"], pool["df"])
        assert p["predicted_swap"] == pytest.approx(want, abs=1e-4)


def test_the_report_calls_nothing(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("the report tried to leave the machine")

    for name in ("run", "Popen", "call", "check_output"):
        monkeypatch.setattr(subprocess, name, refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    import nbh.llm

    monkeypatch.setattr(nbh.llm.Client, "__init__", refuse)
    monkeypatch.setattr(nbh.llm.ClaudeCodeClient, "__init__", refuse)
    assert report.build(report.ROOT, simulate=False)["claims"]


# --------------------------------------------------------------------------
# The command
# --------------------------------------------------------------------------

def test_command_writes_both_files_and_passes_on_listed_disputes(rep, tmp_path, monkeypatch,
                                                                  capsys):
    from harness.__main__ import _report

    monkeypatch.setattr(report, "build", lambda root, simulate: rep)
    assert _report(str(tmp_path / "out"), True, False) == 0
    out = capsys.readouterr().out
    assert f"{len(report.DISPUTED)} do not hold as written" in out
    assert (tmp_path / "out" / "claims.md").read_bytes().count(b"\r") == 0
    assert json.loads((tmp_path / "out" / "claims.json").read_text(encoding="utf-8"))["seed"]


def test_command_fails_on_an_unlisted_change(rep, tmp_path, monkeypatch):
    """With nothing disputed, a disagreement is fabricated: one claim is made to
    fail in a copy of the report, so the test does not depend on any real
    claim being wrong."""
    from harness.__main__ import _report

    broken = json.loads(json.dumps(rep))
    broken["claims"][0]["holds"] = False
    victim = broken["claims"][0]["id"]
    # A claim that stopped holding without anyone listing it...
    monkeypatch.setattr(report, "build", lambda root, simulate: broken)
    monkeypatch.setattr(report, "DISPUTED", {})
    assert _report(str(tmp_path), True, True) == 1
    # ...is accepted once someone lists it, with a reason...
    monkeypatch.setattr(report, "DISPUTED", {victim: "x"})
    assert _report(str(tmp_path), True, True) == 0
    # ...and a listed dispute that quietly started holding again fails too.
    monkeypatch.setattr(report, "build", lambda root, simulate: rep)
    assert _report(str(tmp_path), True, True) == 1
