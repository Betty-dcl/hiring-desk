"""What stands between the desk and the network.

The desk holds applications: names, emails, CVs, and three people's votes on
each. Every guard the web server applies lives here, as small functions the
handler calls at its entry points, so that the list can be read in one place
and each guard tested on its own. `docs/SECURITY.md` is the threat model;
each guard below names the threat it answers.

Nothing here is a framework. Each function takes what the request carries
(headers, a peer address, a form field) and returns an answer or raises
`Refused` with the status to send; the handler decides nothing itself.

What these guards assume, and cannot check from inside the process, is in
DEPLOY.md: in `header` mode, the proxy overwrites the identity header and is
the only thing that can reach the port.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import re
import secrets
import socket
import threading
from http.server import ThreadingHTTPServer
from typing import Any, Iterable, Mapping
from urllib.parse import quote, urlsplit

#: Seconds a connection may sit without sending anything. Without it, a client
#: that opens a connection and sends half a request holds a server thread for
#: ever (slowloris): http.server has no timeout of its own.
TIMEOUT = 15.0

#: Connections served at once. ThreadingHTTPServer starts a thread per
#: connection with no ceiling; a few thousand idle sockets would exhaust the
#: process long before the timeout frees them. Three voters need a handful.
MAX_CONNECTIONS = 64

#: A form without a file. Uploads have their own, larger ceiling, set by the
#: caller from the document size limit.
MAX_FORM_BYTES = 256 * 1024

#: Names a browser uses for this machine. In `pick` mode the desk answers to
#: nothing else: a page on another site that rebinds its own name to 127.0.0.1
#: (DNS rebinding) would otherwise read the desk -- names, CVs, the form token
#: -- as if it were its own origin.
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


class Refused(Exception):
    """A request the desk will not serve. `status` is what to answer."""

    def __init__(self, status: int, why: str):
        super().__init__(why)
        self.status = status


# --------------------------------------------------------------------------
# Who may reach the desk at all
# --------------------------------------------------------------------------

def networks(trusted: Iterable[str]) -> list[ipaddress._BaseNetwork]:
    """`trusted_proxies` from desk.toml, as networks. Refuses what is not an address."""
    out = []
    for t in trusted:
        try:
            out.append(ipaddress.ip_network(str(t).strip(), strict=False))
        except ValueError as err:
            raise ValueError(f"access.trusted_proxies: {t!r} is not an address or a "
                             f"network") from err
    return out


def check_peer(peer: str, trusted: list[ipaddress._BaseNetwork]) -> None:
    """In header mode, only the proxy may talk to the desk.

    The identity header is a claim anyone can type. It means something only
    when the connection comes from the proxy that set it; a request straight
    to the port, from anywhere else, is refused before the header is read.
    """
    try:
        ip = ipaddress.ip_address(peer.split("%", 1)[0])
    except ValueError:
        raise Refused(403, "not reached through the sign-in proxy") from None
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped  # type: ignore[assignment]
    if not any(ip in n for n in trusted):
        raise Refused(403, "not reached through the sign-in proxy")


def check_host(host: str) -> None:
    """In pick mode, answer only to a loopback name (DNS rebinding)."""
    h = (host or "").strip().lower()
    if h.startswith("["):
        name = h[1:].split("]", 1)[0]
    else:
        name = h.rsplit(":", 1)[0] if h.count(":") == 1 else h
    if name not in LOOPBACK_HOSTS:
        raise Refused(421, "this desk only answers to 127.0.0.1 or localhost")


def check_identity_header(headers: Any, name: str) -> None:
    """One identity header, or none. Two means someone added theirs to the proxy's."""
    got = headers.get_all(name) or [] if hasattr(headers, "get_all") else []
    if len(got) > 1:
        raise Refused(400, f"{name} sent more than once")


# --------------------------------------------------------------------------
# Forms: which page a POST came from
# --------------------------------------------------------------------------

#: One secret per process. Tokens die with the server, as they always did.
_KEY = secrets.token_bytes(32)


def form_token(server_token: str, viewer: str) -> str:
    """The token one viewer's forms carry.

    The server's own token is the same for everyone; bound to the viewer, a
    colleague who reads their own page cannot build a form that votes as
    someone else from that someone's browser.
    """
    mac = hmac.new(_KEY, f"{server_token}\0{viewer}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac[:24]).decode()


def bind_tokens(page: str, server_token: str, viewer: str) -> str:
    """Every form on a rendered page, carrying this viewer's token.

    Pages are built with the server token as a placeholder, so any form a
    route adds is covered without knowing about this function.
    """
    return page.replace(server_token, form_token(server_token, viewer))


def check_token(given: str, server_token: str, viewer: str) -> None:
    if not hmac.compare_digest(given.encode("utf-8", "replace"),
                               form_token(server_token, viewer).encode()):
        raise Refused(403, "stale or foreign form -- reload the page and try again")


def check_origin(headers: Mapping[str, str], allowed: str = "") -> None:
    """A POST must come from a page of this desk, when the browser says where from.

    Browsers send `Origin` on every POST; `Referer` is the fallback for the
    few that do not. With neither, the form token alone decides. `allowed`
    is the desk's public address (`access.origin`), for a proxy that rewrites
    `Host`; without it, the origin must name the host the request was sent to.
    """
    def netloc(url: str) -> str:
        return urlsplit(url).netloc.lower()

    origin = headers.get("Origin")
    referer = headers.get("Referer")
    host = (headers.get("Host") or "").lower()
    if origin is None and not referer:
        return
    # `Origin: null` (a sandboxed frame, a data: page) names no host, so it
    # matches neither rule below and is refused with the rest.
    said = origin if origin is not None else referer or ""
    if allowed:
        want = allowed.rstrip("/").lower()
        ok = (said.rstrip("/").lower() == want if origin is not None
              else said.lower().startswith(want + "/") or said.lower() == want)
    else:
        ok = bool(host) and netloc(said) == host
    if not ok:
        raise Refused(403, "a form from another site")


def content_length(headers: Mapping[str, str], limit: int) -> int:
    """The body size, refused unless it is a plain number within the limit.

    `int()` alone accepts "-1", and `rfile.read(-1)` reads until the client
    hangs up: an unbounded body behind a check that looked like a bound.
    """
    raw = (headers.get("Content-Length") or "0").strip()
    if headers.get("Transfer-Encoding"):
        raise Refused(411, "send a Content-Length")
    if not raw.isdigit() or not raw.isascii():
        raise Refused(400, "bad Content-Length")
    n = int(raw)
    if n > limit:
        raise Refused(413, "that upload is too large")
    return n


def safe_back(where: str) -> str:
    """A path on this desk to return to after a form, or "/".

    The value comes from a form field. Refused: anything not starting with a
    single "/", a backslash (browsers read "/\\evil" as "//evil"), and any
    control character (CR LF would write a header of the attacker's choosing).
    """
    if (not where.startswith("/") or where.startswith("//") or "\\" in where
            or not re.fullmatch(r"/[A-Za-z0-9._~%/?=&+-]*", where)):
        return "/"
    return where


# --------------------------------------------------------------------------
# What goes out: headers, links, file names
# --------------------------------------------------------------------------

def _hash(text: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


def csp(css: str, js: str) -> str:
    """The page policy: the desk's own style and script, by hash, and nothing else.

    No 'unsafe-inline': a name, a note or a template that escaped escaping
    still could not run. Inline `style=` and `on...=` attributes are therefore
    blocked too; tests/test_security.py fails on any page that uses one.
    """
    return ("default-src 'none'; "
            f"script-src {_hash(js)}; style-src {_hash(css)}; "
            "img-src 'self' data:; form-action 'self'; frame-ancestors 'none'; "
            "base-uri 'none'")


def headers(ctype: str, page_policy: str) -> dict[str, str]:
    """Headers on every answer. Everything the desk serves is candidate data.

    `no-store`: a vote seen after voting must not be served from a cache to
    the colleague who has not voted yet -- by the browser, or by a proxy.
    """
    h = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
         "Referrer-Policy": "same-origin", "Cache-Control": "no-store",
         "Cross-Origin-Opener-Policy": "same-origin",
         "Cross-Origin-Resource-Policy": "same-origin",
         "Permissions-Policy": "camera=(), microphone=(), geolocation=()"}
    h["Content-Security-Policy"] = (page_policy if ctype.startswith("text/html")
                                    else "default-src 'none'; sandbox; frame-ancestors 'none'")
    return h


#: Documents a browser may show in the page rather than download. Plain text
#: only: with nosniff and a sandbox it cannot run. A PDF opened inline runs
#: in the browser's PDF viewer, which executes the file's scripts in some
#: browsers; downloaded, it opens in whatever reader the partner chose.
INLINE = (".txt", ".md")


def disposition(filename: str, *, inline: bool = False) -> str:
    """Content-Disposition for a stored file: never a header of the file's choosing."""
    ascii_name = re.sub(r"[^A-Za-z0-9._ -]", "_", filename)[:120].strip() or "document"
    return (f'{"inline" if inline else "attachment"}; filename="{ascii_name}"; '
            f"filename*=UTF-8''{quote(filename[:200], safe='')}")


def href(url: str) -> str:
    """A link a candidate gave, fit for href, or "" when it is not a web address.

    Links are checked on the way in (https only); this is the second check,
    on the way out, for a registry edited by hand or by a future import.
    """
    u = (url or "").strip()
    if re.search(r"[\x00-\x20\x7f]", u):
        return ""
    return u if re.match(r"(?i)^https?://[^/\s]", u) else ""


def one_line(text: str) -> str:
    """A header value: every kind of line break and whitespace run becomes one space."""
    return " ".join(str(text).split())


# --------------------------------------------------------------------------
# The server
# --------------------------------------------------------------------------

class Server(ThreadingHTTPServer):
    """ThreadingHTTPServer with a ceiling on connections served at once.

    Over the ceiling a connection is closed straight away rather than queued:
    a flood of idle sockets then costs nothing but the sockets.
    """

    daemon_threads = True
    max_connections = MAX_CONNECTIONS

    def __init__(self, *a: Any, **kw: Any) -> None:
        self._slots = threading.BoundedSemaphore(self.max_connections)
        super().__init__(*a, **kw)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._slots.acquire(blocking=False):
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.close_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()
