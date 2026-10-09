#!/usr/bin/env python3
"""Outlook: did someone you wrote to write back? A hint on their row, never a tick.

    # once: the person who runs hiring connects their own mailbox
    ./.venv/bin/python outlook.py connect      # prints a code and a Microsoft page
    ./.venv/bin/python outlook.py finish       # once signed in on that page

    # look for replies (also a button on the Interested tab)
    ./.venv/bin/python outlook.py sync

    # forget the connection and every hint
    ./.venv/bin/python outlook.py disconnect

Off unless the team turns it on (House rules, or desk.toml [outlook] enabled)
and a Microsoft admin has registered the desk once (docs/OUTLOOK.md). Built
against Microsoft's published identity platform and Graph v1.0 references.
**It has never run against a live Microsoft 365 account**: every request and
answer it relies on is listed in `ASSUMES`, and the tests replay those shapes.

Five rules, each one a data protection choice (docs/OUTLOOK.md):

**The smallest permission there is.** `Mail.ReadBasic`, delegated: the mails
of the signed-in person only, without their body or attachments. The desk
cannot read what anybody wrote, only that a mail came, when, and from whom.

**Only the people you are waiting on.** One question per application in
"Contacted" with an email on file: "a mail from this address since the day
you wrote to them?". Nothing else in the mailbox is asked for.

**Two facts kept, nothing else.** For each of those: the time the reply came
and a link to open it in Outlook. A hint no longer waited on is dropped at
the next check; "disconnect" deletes the connection and every hint.

**A hint, not a decision.** A reply can be a no. The row says "they wrote
back" with the link; a person reads it and ticks "Replied, OK" or not. The
log of the application holds what people recorded, never what this found.

**Nothing on a timer.** The mailbox is looked at when someone presses
"Check Outlook", or runs `sync`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent
LOGIN = "https://login.microsoftonline.com"
GRAPH = "https://graph.microsoft.com/v1.0"
#: Read the signed-in person's mail without body or attachments; and keep the
#: connection between checks (a refresh token), so nobody signs in each time.
SCOPES = "https://graph.microsoft.com/Mail.ReadBasic offline_access"

#: What this connector takes on trust from the references, in one place, so
#: the first run against a real account knows what to check.
ASSUMES = {
    "device code": "POST {LOGIN}/{tenant}/oauth2/v2.0/devicecode, form client_id + scope; "
                   "answers device_code, user_code, verification_uri, expires_in, interval, "
                   "message",
    "token": "POST {LOGIN}/{tenant}/oauth2/v2.0/token, form grant_type="
             "urn:ietf:params:oauth:grant-type:device_code + client_id + device_code; "
             "error authorization_pending until the person has signed in",
    "refresh": "the same endpoint, grant_type=refresh_token + client_id + refresh_token + "
               "scope; answers a new access_token (and usually a new refresh_token)",
    "messages": "GET {GRAPH}/me/messages with $filter on receivedDateTime and "
                "from/emailAddress/address, $orderby receivedDateTime desc (a property "
                "ordered on must come first in the filter), $select receivedDateTime,webLink,"
                " $top 1; answers {value: [{receivedDateTime, webLink}]}",
    "Mail.ReadBasic": "delegated; no body, bodyPreview, attachments; user consent unless "
                      "the tenant requires an admin's",
}


class OutlookError(RuntimeError):
    """Something Microsoft said no to, or that the desk cannot do: said as it is."""


@dataclass
class Settings:
    #: The desk's registration in the company's Microsoft Entra ID (docs/OUTLOOK.md).
    client_id: str = ""
    #: The company's tenant id or domain; "organizations" lets any work account in.
    tenant: str = "organizations"

    @property
    def ready(self) -> bool:
        return bool(self.client_id.strip())


def load_settings(path: str | Path | None = None) -> Settings:
    f = Path(path or ROOT / "desk.toml")
    raw = tomllib.loads(f.read_text(encoding="utf-8")).get("outlook", {}) if f.exists() else {}
    return Settings(client_id=str(raw.get("client_id", "")).strip(),
                    tenant=str(raw.get("tenant", "organizations")).strip() or "organizations")


# --------------------------------------------------------------------------
# HTTP: one function, replaced in the tests
# --------------------------------------------------------------------------

#: (method, url, form or None, headers) -> the JSON answer. A form is sent as
#: application/x-www-form-urlencoded, as the identity platform expects.
Transport = Callable[[str, str, "dict[str, str] | None", "dict[str, str]"], dict]


def http(method: str, url: str, form: dict[str, str] | None,
         headers: dict[str, str]) -> dict[str, Any]:
    data = urllib.parse.urlencode(form).encode() if form is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        **headers, "Accept": "application/json",
        **({"Content-Type": "application/x-www-form-urlencoded"} if form is not None else {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as err:
        # The identity platform says "not yet" with a 400 and a JSON body.
        try:
            return json.loads(err.read().decode("utf-8") or "{}") | {"_status": err.code}
        except ValueError:
            raise OutlookError(f"Microsoft answered {err.code}") from None
    except (urllib.error.URLError, TimeoutError) as err:
        raise OutlookError(f"could not reach Microsoft: {err}") from None


# --------------------------------------------------------------------------
# Where things are kept: never in the repo (.gitignore: runs/desk/*)
# --------------------------------------------------------------------------

def _dir() -> Path:
    import desk  # the desk's own folder, wherever it runs from
    return desk.ROOT / "runs" / "desk"


def _token_path() -> Path:
    return _dir() / "outlook-token.json"


def _pending_path() -> Path:
    return _dir() / "outlook-pending.json"


def _hints_path() -> Path:
    return _dir() / "outlook-replies.json"


def _read(f: Path) -> dict[str, Any]:
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except (ValueError, OSError):
        return {}


def _write(f: Path, data: dict[str, Any]) -> None:
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)  # a token is a key to a mailbox: its owner only
    except OSError:
        pass
    tmp.replace(f)


def connected() -> bool:
    return bool(_read(_token_path()).get("refresh_token"))


def pending() -> dict[str, Any]:
    """The code to type on Microsoft's page, while a sign-in is under way."""
    return _read(_pending_path())


# --------------------------------------------------------------------------
# Signing in: the device code flow, so the desk never sees a password
# --------------------------------------------------------------------------

def _endpoint(s: Settings, what: str) -> str:
    return f"{LOGIN}/{urllib.parse.quote(s.tenant, safe='')}/oauth2/v2.0/{what}"


def _need(s: Settings) -> None:
    if not s.ready:
        raise OutlookError("the desk is not registered with Microsoft yet: an admin does it "
                           "once and puts its client id in desk.toml [outlook] "
                           "(docs/OUTLOOK.md)")


def connect(s: Settings, send: Transport = http, now: datetime | None = None) -> dict[str, Any]:
    """Start a sign-in. Returns the code and the page where the person types it."""
    _need(s)
    now = now or datetime.now(timezone.utc)
    r = send("POST", _endpoint(s, "devicecode"), {"client_id": s.client_id, "scope": SCOPES},
             {})
    if "device_code" not in r:
        raise OutlookError(r.get("error_description") or r.get("error") or
                           "Microsoft gave no sign-in code")
    out = {"device_code": r["device_code"], "user_code": str(r.get("user_code", "")),
           "verification_uri": str(r.get("verification_uri", "https://microsoft.com/devicelogin")),
           "expires_at": (now + timedelta(seconds=int(r.get("expires_in", 900)))).isoformat()}
    _write(_pending_path(), out)
    return out


def _keep(r: dict[str, Any], now: datetime, old: dict[str, Any] | None = None) -> None:
    _write(_token_path(), {
        "access_token": r["access_token"],
        #: Microsoft may keep the old refresh token: then it is kept here too.
        "refresh_token": r.get("refresh_token") or (old or {}).get("refresh_token", ""),
        "expires_at": (now + timedelta(seconds=int(r.get("expires_in", 3600)) - 60)).isoformat(),
        "since": (old or {}).get("since") or now.isoformat()})


def finish(s: Settings, send: Transport = http, now: datetime | None = None) -> None:
    """Once the person has signed in on Microsoft's page: keep the connection."""
    _need(s)
    now = now or datetime.now(timezone.utc)
    p = pending()
    if not p:
        raise OutlookError("no sign-in under way: press Connect first")
    if p.get("expires_at", "") < now.isoformat():
        _pending_path().unlink(missing_ok=True)
        raise OutlookError("the sign-in code has expired: press Connect again")
    r = send("POST", _endpoint(s, "token"), {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "client_id": s.client_id, "device_code": p["device_code"]}, {})
    if r.get("error") == "authorization_pending":
        raise OutlookError(f"not signed in yet: type {p.get('user_code', 'the code')} on "
                           f"{p.get('verification_uri', 'the Microsoft page')}, then try again")
    if "access_token" not in r:
        raise OutlookError(r.get("error_description") or r.get("error") or
                           "Microsoft refused the sign-in")
    _keep(r, now)
    _pending_path().unlink(missing_ok=True)


def disconnect() -> None:
    """Forget the connection and every hint. Revoking the desk's access on the
    Microsoft side is done there (My Apps, or the admin)."""
    for f in (_token_path(), _pending_path(), _hints_path()):
        f.unlink(missing_ok=True)


def _access(s: Settings, send: Transport, now: datetime) -> str:
    t = _read(_token_path())
    if not t.get("refresh_token"):
        raise OutlookError("Outlook is not connected: connect it on House rules")
    if t.get("access_token") and t.get("expires_at", "") > now.isoformat():
        return t["access_token"]
    r = send("POST", _endpoint(s, "token"), {
        "grant_type": "refresh_token", "client_id": s.client_id,
        "refresh_token": t["refresh_token"], "scope": SCOPES}, {})
    if "access_token" not in r:
        raise OutlookError("the Outlook connection has lapsed: connect it again on House rules"
                           + (f" ({r['error']})" if r.get("error") else ""))
    _keep(r, now, t)
    return r["access_token"]


# --------------------------------------------------------------------------
# Looking for replies
# --------------------------------------------------------------------------

@dataclass
class Waiting:
    """One application in "Contacted": who wrote to whom, and since when."""

    posting: str
    candidate_id: str
    email: str
    since: datetime


def waiting(cfg: Any, now: datetime | None = None) -> list[Waiting]:
    """Every application waiting on a reply, with an email on file. Nobody else."""
    import desk
    import stages
    now = now or datetime.now(timezone.utc)
    reg = desk.load_registry(desk._registry_path())
    out = []
    for posting, pipe in desk._pipelines().items():
        for cid in pipe.candidates:
            s = pipe.standing(cid)
            if stages.derive(s, cfg, now).id != "contacted":
                continue
            person = reg.person_of(cid)
            email = (person.email or "").strip() if person is not None else ""
            if not desk.EMAIL.fullmatch(email):
                continue
            last = next(e for e in reversed(s.events) if stages.step_of(e) == "contacted")
            #: From the start of the day they were written to: a reply the
            #: same morning counts, an older mail from them does not.
            day = stages.instant(last).replace(hour=0, minute=0, second=0, microsecond=0)
            out.append(Waiting(posting, cid, email, day))
    return out


def _query(w: Waiting) -> str:
    #: OData string literals double their single quotes.
    addr = w.email.replace("'", "''")
    since = w.since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    q = {"$filter": f"receivedDateTime ge {since} and from/emailAddress/address eq '{addr}'",
         "$orderby": "receivedDateTime desc", "$select": "receivedDateTime,webLink",
         "$top": "1"}
    return f"{GRAPH}/me/messages?" + urllib.parse.urlencode(q, quote_via=urllib.parse.quote)


def sync(cfg: Any, s: Settings, send: Transport = http,
         now: datetime | None = None) -> tuple[int, int]:
    """Ask Outlook, once per application waiting on a reply. Returns (replies, asked).

    Keeps, per application, the time of the latest reply and its Outlook
    link; drops every hint for an application that is no longer waiting.
    """
    if not getattr(cfg, "outlook", False):
        raise OutlookError("checking Outlook is off: turn it on in House rules")
    _need(s)
    now = now or datetime.now(timezone.utc)
    token = _access(s, send, now)
    found: dict[str, dict[str, str]] = {}
    asked = 0
    for w in waiting(cfg, now):
        r = send("GET", _query(w), None, {"Authorization": f"Bearer {token}"})
        asked += 1
        if r.get("_status") or "error" in r:
            err = r.get("error", {})
            raise OutlookError("Outlook refused the question: "
                               + (err.get("message", "") if isinstance(err, dict) else str(err)))
        got = (r.get("value") or [None])[0]
        if got and got.get("receivedDateTime"):
            found[f"{w.posting}/{w.candidate_id}"] = {
                "at": str(got["receivedDateTime"]), "link": str(got.get("webLink", ""))}
    _write(_hints_path(), {"checked": now.isoformat(), "replies": found})
    return len(found), asked


def reply(posting: str, candidate_id: str) -> dict[str, str] | None:
    """The reply Outlook found for this application at the last check, if any."""
    return _read(_hints_path()).get("replies", {}).get(f"{posting}/{candidate_id}")


def last_check() -> str:
    return str(_read(_hints_path()).get("checked", ""))


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=("connect", "finish", "sync", "disconnect", "status"))
    a = ap.parse_args()
    s = load_settings()
    try:
        if a.cmd == "connect":
            p = connect(s)
            print(f"Open {p['verification_uri']} and type {p['user_code']}, "
                  f"then run: outlook.py finish")
        elif a.cmd == "finish":
            finish(s)
            print("connected")
        elif a.cmd == "sync":
            import desk
            n, asked = sync(desk.load_config(), s)
            print(f"{n} repl{'y' if n == 1 else 'ies'} among {asked} people waited on")
        elif a.cmd == "disconnect":
            disconnect()
            print("disconnected; every hint deleted")
        else:
            print("connected" if connected() else "not connected",
                  f"(last check {last_check() or 'never'})")
    except OutlookError as err:
        print(f"outlook: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
