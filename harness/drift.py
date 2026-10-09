"""How far one screening moves on identical input, from every repeat on disk.

The desk prints a fit percentage from one screening. Whether two of those
can be put in order depends on one number: how far a single screening of the
same facts moves from run to run. That number used to be read from one
stability record, candidate by candidate, as each person's range over three
runs -- which gives Mara, whose three runs happened to agree, a noise of half
a point, and anyone never re-screened a noise of zero.

Here it is estimated once per posting, from every identical-input repeat of
the shipped screener that the repository holds, pooled:

* each stability record for the posting (the same facts, the same agenda, n
  times), except records of a scoring prompt that was reverted;
* the anonymised baseline of each name test (`(no name)`): the same facts
  screened n times exactly as the desk screens them.

A pooled figure assumes every candidate's score wobbles by the same amount.
Measured, they do not quite (Mara's keyword list barely moves); the report
prints each source's own figure next to the pool so a reader can see how
much that assumption carries. Three runs a person is too few to estimate
each person's noise separately, and the pool is the lesser error.

No model, no cost: everything here is a file already written.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness import stats

ROOT = Path(__file__).resolve().parent.parent

#: Records of a scoring prompt that was measured, found worse, and reverted
#: (docs/DETAILS.md, "The obvious repair made it worse"). Its drift is a fact
#: about a screener the desk no longer runs.
NOT_THE_SHIPPED_SCREENER = ("rubric_v2",)


@dataclass(frozen=True)
class Group:
    """One set of identical-input repeats: who, from which file, the scores."""

    source: str
    label: str
    scores: tuple[float, ...]

    @property
    def sd(self) -> float:
        return stats.pooled_sd([self.scores])[0]

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "label": self.label, "n": len(self.scores),
                "scores": list(self.scores), "sd": round(self.sd, 6)}


@dataclass
class Pool:
    posting_id: str
    groups: list[Group] = field(default_factory=list)

    @property
    def sd(self) -> float:
        return stats.pooled_sd([g.scores for g in self.groups])[0]

    @property
    def df(self) -> int:
        return stats.pooled_sd([g.scores for g in self.groups])[1]

    @property
    def sources(self) -> list[str]:
        return sorted({g.source for g in self.groups})

    def gap(self, swap: float) -> float | None:
        """The gap below which two single screenings swap more than `swap`."""
        if self.df < 2:
            return None
        return stats.gap_for_swap(swap, self.sd, self.df)

    def to_dict(self) -> dict[str, Any]:
        return {"posting_id": self.posting_id, "sd": round(self.sd, 6), "df": self.df,
                "groups": [g.to_dict() for g in self.groups]}


def _rel(p: Path, root: Path) -> str:
    try:
        return p.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def pool(posting: str, root: Path = ROOT, *, stability_only: bool = False) -> Pool:
    """Every identical-input repeat of the shipped screener on `posting`.

    A record derived from another (a cohort with someone dropped) repeats the
    scores of its parent, and two files can hold the same measurement under
    two labels; a group is counted once however many files carry it, keyed by
    who, when, and the scores themselves.
    """
    out = Pool(posting_id=posting)
    seen: set[tuple] = set()

    def add(source: Path, label: str, at: str, scores: list[float]) -> None:
        key = (label, at, tuple(scores))
        if len(scores) < 2 or key in seen:
            return
        seen.add(key)
        out.groups.append(Group(_rel(source, root), label, tuple(scores)))

    for f in sorted((root / "runs" / "stability").glob(f"{posting}_n*.json")):
        if any(tag in f.name for tag in NOT_THE_SHIPPED_SCREENER):
            continue
        d = json.loads(f.read_text(encoding="utf-8"))
        for c in d.get("candidates", []):
            add(f, c["candidate_id"], d.get("at", ""), list(c.get("scores", [])))
    if stability_only:
        return out
    for f in sorted((root / "runs" / "bias").glob(f"*_{posting}_n*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        blind = d.get("blind") or {}
        if blind.get("scores"):
            add(f, f"{d['candidate_id']} (no name)", d.get("at", ""), list(blind["scores"]))
    return out


# ---------------------------------------------------------------------------
# The desk's "any order" band
# ---------------------------------------------------------------------------

#: Two single screenings closer than the gap at which they would come out in
#: the wrong order this often are shown as "any order". One in twenty is the
#: conventional level, and it is a preference: `[any_order] swap` in desk.toml.
DEFAULT_SWAP = 0.05


@dataclass(frozen=True)
class AnyOrder:
    """What the desk groups under "any order", and where the rule came from."""

    #: "measured", "spread" (each person's own range, the rule before this
    #: one) or "fixed".
    rule: str
    swap: float = DEFAULT_SWAP
    fixed: float | None = None


def any_order_setting(path: str | Path | None = None) -> AnyOrder:
    """`[any_order]` in desk.toml. Absent or unreadable means the measured default.

    Read here rather than through the desk's Config so that the rule lives
    beside the measurement it depends on; the desk only asks for the number.
    """
    import tomllib

    f = Path(path) if path else ROOT / "desk.toml"
    try:
        raw = tomllib.loads(f.read_text(encoding="utf-8")).get("any_order", {})
    except (OSError, tomllib.TOMLDecodeError):
        raw = {}
    gap = raw.get("gap", "measured")
    swap = float(raw.get("swap", DEFAULT_SWAP))
    if not 0.0 < swap < 0.5:
        raise ValueError("desk.toml [any_order] swap must be between 0 and 0.5")
    if isinstance(gap, (int, float)) and not isinstance(gap, bool):
        if gap < 0:
            raise ValueError("desk.toml [any_order] gap must not be negative")
        #: Written in points, like everything a person reads on the desk.
        return AnyOrder("fixed", swap, float(gap) / 100.0)
    if gap not in ("measured", "spread"):
        raise ValueError('desk.toml [any_order] gap must be "measured", "spread" '
                         "or a number of points")
    return AnyOrder(gap, swap)


_CACHE: dict[tuple, float | None] = {}


def measured_gap(posting: str, swap: float = DEFAULT_SWAP, root: Path = ROOT) -> float | None:
    """The measured any-order gap for a posting, or None if nothing was measured.

    Cached on the records' modification times: the desk asks once per row.
    """
    files = sorted([*(root / "runs" / "stability").glob(f"{posting}_n*.json"),
                    *(root / "runs" / "bias").glob(f"*_{posting}_n*.json")])
    key = (str(root), posting, swap, tuple((f.name, f.stat().st_mtime_ns) for f in files))
    if key not in _CACHE:
        _CACHE[key] = pool(posting, root).gap(swap)
    return _CACHE[key]
