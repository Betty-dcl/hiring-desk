"""A vote from the recap in one click -- and never from a click nobody made.

The Tuesday recap lists what waits on each voter. A link beside each name
opens the desk on a page that says "Yes, let's talk for Mara Velichko?" and
one button. The link carries a signed token; the button casts the vote.

Why a confirmation page rather than a vote on the link itself: mail clients
and corporate gateways open links on their own -- Outlook's Safe Links,
Gmail's image and link prefetch, antivirus scanners -- and a GET that voted
would let a scanner class a candidate. So a GET only ever *shows*; the vote
is a POST from a page the desk served, carrying the desk's own form token.

What the token binds, and why each:

* **voter, role, candidate, class** -- a link forwarded, or copied into the
  wrong thread, casts nothing but that one vote, for that one person, in
  that one name. In header mode the signed-in person must also *be* that
  voter: a forwarded link does not let a colleague vote as someone else.
* **expiry** -- a recap is read in the week it was written.
* **a nonce, used once** -- a link that worked is spent. A second click, a
  replayed request, a browser's back-and-resubmit, all find it used.
* **HMAC-SHA256 with a local secret** -- the secret is generated on first use
  in `runs/desk/.link_secret` (never committed; `runs/desk/*` is ignored), or
  read from `NBH_LINK_SECRET`. Changing it voids every link in circulation.

The token is not encrypted: it names a voter, a candidate id and a class,
which the recap next to it already says in plain words.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import desk
from desk import MEANINGS, Config, DeskError

#: A link that names the application but not the class: the page it opens
#: offers all four. Used where one link per line is all a reader can take.
ANY = "*"

#: Domain separation: a signature over something else can never pass as a link.
PURPOSE = b"nbh-vote-link/1|"


class LinkError(DeskError):
    """A link the desk will not act on. The message says why, plainly."""


@dataclass(frozen=True)
class Link:
    voter: str
    posting: str
    candidate: str
    meaning: str
    expires: int  # unix seconds
    nonce: str

    def payload(self) -> dict[str, Any]:
        return {"v": self.voter, "p": self.posting, "c": self.candidate, "m": self.meaning,
                "e": self.expires, "n": self.nonce}


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _secret_path() -> Path:
    return desk.ROOT / "runs" / "desk" / ".link_secret"


def _used_path() -> Path:
    return desk.ROOT / "runs" / "desk" / "links_used.json"


def secret() -> bytes:
    """The signing key: the environment's, or a local one made once."""
    env = os.environ.get("NBH_LINK_SECRET", "").strip()
    if env:
        if len(env) < 32:
            raise LinkError("NBH_LINK_SECRET is too short: 32 characters at least")
        return env.encode("utf-8")
    f = _secret_path()
    if not f.exists():
        f.parent.mkdir(parents=True, exist_ok=True)
        # O_EXCL: two processes starting at once cannot both write a key and
        # leave half the links signed with the one that lost.
        try:
            fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "w", encoding="ascii") as fh:
                fh.write(secrets.token_hex(32))
    key = f.read_text(encoding="ascii").strip()
    if len(key) < 32:
        raise LinkError(f"{f} is not a usable key; delete it to make a new one")
    return key.encode("ascii")


def _sign(body: str, key: bytes) -> str:
    return _b64(hmac.new(key, PURPOSE + body.encode("ascii"), hashlib.sha256).digest())


def mint(voter: str, posting: str, candidate: str, meaning: str, cfg: Config, *,
         ttl_hours: float, now: datetime | None = None, key: bytes | None = None) -> str:
    """A token for one vote by one voter on one application."""
    voter = cfg.voter(voter)
    if meaning not in MEANINGS and meaning != ANY:
        raise LinkError(f"{meaning!r} is not a class")
    now = now or datetime.now(timezone.utc)
    link = Link(voter, posting, candidate, meaning,
                int((now + timedelta(hours=ttl_hours)).timestamp()), secrets.token_urlsafe(12))
    body = _b64(json.dumps(link.payload(), separators=(",", ":"),
                           ensure_ascii=True).encode("ascii"))
    return f"{body}.{_sign(body, key or secret())}"


def read(token: str, cfg: Config, *, now: datetime | None = None,
         key: bytes | None = None) -> Link:
    """The link inside a token, or a refusal. Checks everything but "used"."""
    body, dot, sig = (token or "").strip().partition(".")
    if not dot or not body or not sig or len(token) > 2048:
        raise LinkError("this is not a desk link")
    # The signature before anything else is read out of the token: nothing
    # unsigned is ever parsed, so a crafted payload never reaches json.
    if not hmac.compare_digest(sig, _sign(body, key or secret())):
        raise LinkError("this link was not made by this desk, or was altered")
    try:
        d = json.loads(_unb64(body))
        link = Link(str(d["v"]), str(d["p"]), str(d["c"]), str(d["m"]), int(d["e"]),
                    str(d["n"]))
    except (ValueError, KeyError, TypeError) as e:
        raise LinkError("this link is damaged") from e
    if (now or datetime.now(timezone.utc)).timestamp() >= link.expires:
        raise LinkError("this link has expired -- vote from the desk, or use the next recap")
    if link.voter not in cfg.voters:
        raise LinkError(f"{link.voter} no longer votes on this desk")
    if link.meaning not in MEANINGS and link.meaning != ANY:
        raise LinkError("this link is damaged")
    return link


def _load_used() -> dict[str, int]:
    f = _used_path()
    if not f.exists():
        return {}
    try:
        return {k: int(v) for k, v in json.loads(f.read_text(encoding="utf-8")).items()}
    except (ValueError, OSError, AttributeError):
        # A damaged store must not reopen spent links. Refuse all of them
        # until someone looks, rather than forgetting which were used.
        raise LinkError("the record of used links is unreadable; vote from the desk")


def is_used(link: Link) -> bool:
    return link.nonce in _load_used()


def redeem(token: str, cfg: Config, *, meaning: str = "", viewer: str | None = None,
           until: str = "",
           comment: str = "", reason: str = "", now: datetime | None = None,
           key: bytes | None = None) -> tuple[Link, list[Any]]:
    """Cast the vote a link stands for, once. Everything under the desk's lock.

    The used-link check, the vote and the spending of the link happen inside
    one lock, so two clicks in the same second cast one vote, not two -- and
    a vote the desk refuses (a closed application, "later" without a date)
    leaves the link unspent, to be used again once the reason is fixed.
    """
    link = read(token, cfg, now=now, key=key)
    if viewer is not None and viewer != link.voter:
        raise LinkError(f"this link is {link.voter}'s; you are signed in as {viewer}")
    # A link for one class casts that class and no other, whatever the form
    # says; only a link for any class takes its class from the form.
    if link.meaning == ANY:
        meaning = cfg.meaning(meaning) if meaning else ""
        if not meaning:
            raise LinkError("choose a class")
    elif meaning and cfg.meaning(meaning) != link.meaning:
        raise LinkError("this link is for another class")
    else:
        meaning = link.meaning
    with desk.locked():
        used = _load_used()
        if link.nonce in used:
            raise LinkError("this link has already been used")
        pipe = desk._pipelines().get(link.posting)
        if pipe is None:
            raise LinkError(f"nothing on the desk for {link.posting!r}")
        written = desk.cast(pipe, link.candidate, link.voter, meaning, cfg,
                            until=until if meaning == "later" else "",
                            comment=comment, reason=reason if meaning == "pass" else "",
                            now=now)
        desk._save(pipe)
        t = int((now or datetime.now(timezone.utc)).timestamp())
        # Spent links are forgotten once they would have expired anyway.
        used = {n: e for n, e in used.items() if e > t}
        used[link.nonce] = link.expires
        f = _used_path()
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(used, indent=0), encoding="utf-8")
        os.replace(tmp, f)
    return link, written
