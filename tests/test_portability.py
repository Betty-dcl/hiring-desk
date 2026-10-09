"""The repository runs on what the README promises, not just on this machine.

The README and the CI say Python 3.11 and up. Most of what breaks that is
caught by running the suite on 3.11, but only where 3.11 is installed; one
thing is cheap to catch everywhere: an f-string that only parses since
PEP 701 (3.12). `trial.py` shipped one -- a backslash inside the braces --
and on 3.11 the whole module, and the desk that imports it, failed to load.
"""

from __future__ import annotations

import subprocess
import sys
import tokenize
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _tracked_python():
    out = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True,
                         text=True, check=False)
    files = [ROOT / f for f in out.stdout.split()]
    return files or sorted(p for p in ROOT.rglob("*.py") if ".venv" not in p.parts)


def pep701_only(path: Path) -> list[str]:
    """What in `path` parses on 3.12+ but not on 3.11, as `line: why`.

    Read from the 3.12+ tokenizer, which splits an f-string into its literal
    parts and its expressions; on 3.11 an expression may not hold a
    backslash or a comment, span lines in a one-quote string, or reuse the
    enclosing quote.
    """
    found, stack = [], []
    with path.open("rb") as fh:
        for t in tokenize.tokenize(fh.readline):
            if t.type == tokenize.FSTRING_START:
                q = t.string.lstrip("fFrRbB")
                if stack and (q[0] == stack[-1][0]):
                    found.append(f"{t.start[0]}: nested f-string reuses its quote")
                stack.append(q)
            elif t.type == tokenize.FSTRING_END:
                stack.pop()
            elif stack and t.type == tokenize.STRING:
                if t.string.lstrip("rRbBuU")[0] == stack[-1][0]:
                    found.append(f"{t.start[0]}: string reuses the f-string's quote")
                if "\\" in t.string:
                    found.append(f"{t.start[0]}: backslash inside the braces")
            elif stack and t.type == tokenize.COMMENT:
                found.append(f"{t.start[0]}: comment inside the braces")
            elif stack and t.type == tokenize.NL and len(stack[-1]) == 1:
                found.append(f"{t.start[0]}: line break in a one-quote f-string")
    return found


@pytest.mark.skipif(sys.version_info < (3, 12), reason="3.11 itself is the check there")
def test_no_f_string_needs_python_3_12():
    bad = {str(p.relative_to(ROOT)): f for p in _tracked_python() if (f := pep701_only(p))}
    assert not bad, bad


@pytest.mark.skipif(sys.version_info < (3, 12), reason="needs the 3.12 tokenizer")
def test_the_check_sees_what_3_11_refuses(tmp_path):
    src = tmp_path / "x.py"
    src.write_text("a = f\"{'p \\\\ d':<4}\"\nb = f'{'no'}'\nc = f\"{'ok'}\"\n",
                   encoding="utf-8")
    assert pep701_only(src) == ["1: backslash inside the braces",
                                "2: string reuses the f-string's quote"]
