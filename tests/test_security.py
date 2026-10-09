"""What the desk refuses at the door, and what it never sends back.

`console/security.py` holds the guards; `docs/SECURITY.md` is the threat
model they answer. Each test here was checked once by hand to fail with its
guard removed -- a guard without such a test is a comment.

The last part fuzzes the functions that see text a candidate or a stranger
wrote, with the standard library's `random` and a fixed seed: the same
inputs on every run, so a failure can be replayed, and no dependency added.
"""

from __future__ import annotations

import http.client
import io
import json
import random
import re
import shutil
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from email.message import Message
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import ashby
import desk
from console import security
from console.security import Refused

REPO = Path(__file__).resolve().parent.parent
SEED = 20260930
CFG = desk.Config(voters=["Ana", "Ben", "Cy"],
                  labels={"contact": "C", "later": "L", "discuss": "D", "pass": "P"})


def hdrs(**kw: str) -> Message:
    """Request headers as http.server hands them over (case-insensitive, repeatable)."""
    m = Message()
    for k, v in kw.items():
        m[k.replace("_", "-")] = v
    return m


# --------------------------------------------------------------------------
# Who may reach the desk at all
# --------------------------------------------------------------------------

@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.1:8711", "localhost:8711",
                                  "LOCALHOST", "[::1]:8711", "::1"])
def test_pick_mode_answers_to_this_machine(host):
    security.check_host(host)


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8711",
                                  "127.0.0.1.evil.example", "localhost.evil.example:80",
                                  "", "[::2]:8711"])
def test_pick_mode_refuses_any_other_name_dns_rebinding(host):
    with pytest.raises(Refused) as no:
        security.check_host(host)
    assert no.value.status == 421


def test_header_mode_hears_only_the_proxy():
    nets = security.networks(["10.0.0.5", "172.16.0.0/12"])
    security.check_peer("10.0.0.5", nets)
    security.check_peer("172.17.0.1", nets)
    security.check_peer("::ffff:10.0.0.5", nets)
    for peer in ("10.0.0.6", "127.0.0.1", "not an address", ""):
        with pytest.raises(Refused):
            security.check_peer(peer, nets)


def test_a_trusted_proxy_must_be_an_address():
    with pytest.raises(ValueError, match="trusted_proxies"):
        security.networks(["proxy.internal"])


def test_an_identity_header_sent_twice_is_refused():
    security.check_identity_header(hdrs(X_Forwarded_Email="ana@co.example"),
                                   "X-Forwarded-Email")
    h = hdrs(X_Forwarded_Email="mallory@co.example")
    h["X-Forwarded-Email"] = "ana@co.example"
    with pytest.raises(Refused):
        security.check_identity_header(h, "X-Forwarded-Email")


def test_header_mode_will_not_start_without_knowing_its_proxy(monkeypatch):
    from console.desk_web import serve

    def would_listen(*a, **k):
        raise AssertionError("started listening without a trusted proxy")

    monkeypatch.setattr(security, "Server", would_listen)
    with pytest.raises(desk.DeskError, match="trusted_proxies"):
        serve("127.0.0.1", 0, CFG, desk.Access(mode="header"))


def test_the_proxy_and_the_origin_are_read_from_desk_toml(tmp_path):
    f = tmp_path / "desk.toml"
    f.write_text('[access]\nmode = "header"\ntrusted_proxies = ["10.0.0.5"]\n'
                 'origin = "https://desk.co.example"\n', encoding="utf-8")
    a = desk.load_access(f)
    assert a.trusted_proxies == ["10.0.0.5"] and a.origin == "https://desk.co.example"


# --------------------------------------------------------------------------
# Forms: which page a POST came from
# --------------------------------------------------------------------------

def test_a_form_token_is_bound_to_its_viewer():
    t = "server-token"
    security.check_token(security.form_token(t, "Ana"), t, "Ana")
    for given in (security.form_token(t, "Ben"), t, "", "é" * 32):
        with pytest.raises(Refused) as no:
            security.check_token(given, t, "Ana")
        assert no.value.status == 403


def test_a_rendered_page_carries_the_viewers_token_not_the_servers():
    page = security.bind_tokens('<input name="t" value="srv"><input name="t" value="srv">',
                                "srv", "Ana")
    assert "srv" not in page and page.count(security.form_token("srv", "Ana")) == 2


@pytest.mark.parametrize("h", [
    {"Host": "127.0.0.1:8711", "Origin": "http://127.0.0.1:8711"},
    {"Host": "127.0.0.1:8711", "Referer": "http://127.0.0.1:8711/person/sam"},
    {"Host": "127.0.0.1:8711"},
])
def test_a_post_from_the_desks_own_page_passes(h):
    security.check_origin(hdrs(**h))


@pytest.mark.parametrize("h", [
    {"Host": "127.0.0.1:8711", "Origin": "https://evil.example"},
    {"Host": "127.0.0.1:8711", "Origin": "null"},
    {"Host": "127.0.0.1:8711", "Referer": "https://evil.example/127.0.0.1:8711"},
    {"Host": "127.0.0.1:8711", "Origin": "http://127.0.0.1:8711.evil.example"},
], ids=["foreign", "opaque", "foreign-referer", "suffix"])
def test_a_post_from_another_site_is_refused(h):
    with pytest.raises(Refused):
        security.check_origin(hdrs(**h))


def test_behind_a_proxy_the_public_origin_is_the_one_allowed():
    ok = "https://desk.co.example"
    security.check_origin(hdrs(Host="internal:8765", Origin=ok), ok)
    security.check_origin(hdrs(Host="internal:8765", Referer=ok + "/settings"), ok)
    for bad in ({"Origin": "https://desk.co.example.evil.example"},
                {"Referer": "https://desk.co.example.evil.example/"}):
        with pytest.raises(Refused):
            security.check_origin(hdrs(Host="internal:8765", **bad), ok)


@pytest.mark.parametrize("value,status", [("-1", 400), ("+5", 400), ("1e3", 400),
                                          ("٣", 400), ("x", 400), ("1025", 413)])
def test_a_body_size_is_a_plain_number_within_the_limit(value, status):
    with pytest.raises(Refused) as no:
        security.content_length(hdrs(Content_Length=value), 1024)
    assert no.value.status == status


def test_a_chunked_body_is_refused():
    with pytest.raises(Refused) as no:
        security.content_length(hdrs(Transfer_Encoding="chunked"), 1024)
    assert no.value.status == 411
    assert security.content_length(hdrs(Content_Length="1024"), 1024) == 1024
    assert security.content_length(hdrs(), 1024) == 0


@pytest.mark.parametrize("back", ["//evil.example", "/\\evil.example", "https://evil.example",
                                  "/x\r\nSet-Cookie: a=b", "/x\nLocation: //evil", "evil",
                                  "/\t/evil.example"])
def test_after_a_form_the_desk_only_sends_you_back_to_itself(back):
    assert security.safe_back(back) == "/"


def test_a_page_of_the_desk_is_a_fine_place_to_go_back_to():
    assert security.safe_back("/person/sam_lee") == "/person/sam_lee"
    assert security.safe_back("/?tab=all&q=sam") == "/?tab=all&q=sam"


# --------------------------------------------------------------------------
# What goes out
# --------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "JaVaScRiPt:alert(1)", " javascript:alert(1)",
    "java\tscript:alert(1)", "java\nscript:alert(1)", "data:text/html,<script>",
    "vbscript:x", "//evil.example", "/relative", "https:///x", "https://a b", "",
    "https://ok.example/\x00", "file:///etc/passwd",
])
def test_a_link_that_is_not_a_web_address_is_not_a_link(url):
    assert security.href(url) == ""


def test_a_web_address_stays_a_link():
    assert security.href("https://github.com/ana") == "https://github.com/ana"
    assert security.href(" http://ana.example ") == "http://ana.example"


def test_a_file_name_cannot_write_a_header():
    d = security.disposition('cv"\r\nSet-Cookie: a=b; x="é.pdf')
    assert "\r" not in d and "\n" not in d
    ascii_part = d.split('filename="', 1)[1].split('"', 1)[0]
    assert '"' not in ascii_part and d.startswith("attachment;")
    assert security.disposition("notes.txt", inline=True).startswith("inline;")


def test_the_page_policy_allows_the_desks_own_code_and_nothing_else():
    from console.desk_web import CSS, JS
    p = security.csp(CSS, JS)
    assert "unsafe-inline" not in p and "unsafe-eval" not in p
    assert security._hash(JS) in p and security._hash(CSS) in p
    assert "default-src 'none'" in p and "frame-ancestors 'none'" in p


def test_what_is_not_a_page_is_sandboxed():
    h = security.headers("application/pdf", "page-policy")
    assert "sandbox" in h["Content-Security-Policy"] and h["X-Content-Type-Options"] == "nosniff"
    assert security.headers("text/html; charset=utf-8", "page-policy")[
        "Content-Security-Policy"] == "page-policy"
    assert security.headers("text/html", "p")["Cache-Control"] == "no-store"


def test_a_pdf_is_downloaded_not_opened_in_the_browser():
    assert ".pdf" not in security.INLINE


# --------------------------------------------------------------------------
# The server
# --------------------------------------------------------------------------

def test_connections_over_the_ceiling_are_closed_not_queued():
    import socketserver

    class Slow(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.recv(1)

    class Tiny(security.Server):
        max_connections = 1

    srv = Tiny(("127.0.0.1", 0), Slow)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        first = socket.create_connection(srv.server_address, timeout=5)
        time.sleep(0.2)
        second = socket.create_connection(srv.server_address, timeout=5)
        assert second.recv(1) == b""  # closed at once
        first.close()
        second.close()
    finally:
        srv.shutdown()
        srv.server_close()


# -- end to end ----------------------------------------------------------------

@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, "ROOT", tmp_path)
    (tmp_path / "mandates" / "generated").mkdir(parents=True)
    (tmp_path / "mandates" / "generated" / "role_fa.provenance.json").write_text(
        json.dumps({"posting_id": "fa", "title": "<b>Founders'</b> Associate"}),
        encoding="utf-8")
    shutil.copy(REPO / "desk.toml", tmp_path / "desk.toml")
    return tmp_path



def start(access: desk.Access | None = None) -> tuple[str, ThreadingHTTPServer]:
    from console import desk_web
    started, real_init = {}, ThreadingHTTPServer.__init__

    def capture(self, addr, handler):
        real_init(self, ("127.0.0.1", 0), handler)
        started["port"] = self.server_address[1]
        started["srv"] = self

    ThreadingHTTPServer.__init__ = capture
    try:
        threading.Thread(target=desk_web.serve, args=("127.0.0.1", 0, CFG,
                                                      access or desk.Access()),
                         daemon=True).start()
        while "port" not in started:
            time.sleep(0.01)
    finally:
        ThreadingHTTPServer.__init__ = real_init
    return f"127.0.0.1:{started['port']}", started["srv"]


def raw(where: str, method: str, path: str, body: bytes = b"",
        headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], str]:
    """One request with exactly these headers -- urllib would fix some of them up."""
    host, port = where.split(":")
    c = http.client.HTTPConnection(host, int(port), timeout=10)
    c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    h = {"Host": where, **(headers or {})}
    if body and "Content-Length" not in h:
        h["Content-Length"] = str(len(body))
    for k, v in h.items():
        c.putheader(k, v)
    c.endheaders(body or None)
    r = c.getresponse()
    out = r.status, {k: v for k, v in r.getheaders()}, r.read().decode("utf-8", "replace")
    c.close()
    return out


HOSTILE = '"><script>alert(1)</script><img src=x onerror=alert(2)>'


def plant(home) -> str:
    """A person whose every field was written by an attacker, as if the registry
    had been filled by a careless import: the output guards must hold alone."""
    p = desk.add_now("fa", "Sam " + HOSTILE, "sam@x.example",
                     files=[("cv", f"cv{HOSTILE}.txt".replace("/", ""), b"plain text cv")])
    reg = desk.load_registry(desk._registry_path())
    person = reg.people[p.person_id]
    person.link = "javascript:alert(3)"
    person.links = ["JavaScript:alert(4)", "data:text/html;base64,PHNjcmlwdD4=",
                    "https://ok.example/" + HOSTILE]
    desk.save_registry(reg, desk._registry_path())
    cid = person.applications[0].candidate_id
    desk.note_now("fa", cid, "Ana", "note " + HOSTILE, CFG)
    desk.vote_now("fa", cid, "Ana", "pass", CFG, comment="c " + HOSTILE, reason="r " + HOSTILE)
    return person.person_id


class Tags(__import__("html.parser").parser.HTMLParser):
    """The page as a browser parses it: every tag and attribute that could run.

    Read with a parser, not a regex: `onerror=` inside an escaped attribute
    value is text, and the same words in a real attribute are a script.
    """

    #: Tags that load or run something. Everything else the desk may add freely.
    LOADS = {"script", "img", "svg", "math", "iframe", "frame", "frameset", "object", "embed",
             "link", "base", "audio", "video", "source", "track", "portal", "template",
             "applet", "style", "meta"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.scripts: list[str] = []
        self.styles: list[str] = []
        self.bad: list[str] = []
        self._in = ""

    def handle_starttag(self, tag, attrs):
        if tag in self.LOADS and tag not in ("script", "style", "meta"):
            self.bad.append(f"<{tag}>")
        if tag == "meta" and any(k == "http-equiv" for k, _ in attrs):
            self.bad.append("<meta http-equiv>")
        for k, v in attrs:
            if k.startswith("on") or k in ("style", "srcdoc", "formaction"):
                self.bad.append(f"{tag} {k}=")
            if k in ("href", "src", "action") and re.match(
                    r"(?i)\s*(javascript|data|vbscript):", v or ""):
                self.bad.append(f"{tag} {k}={v}")
        self._in = tag if tag in ("script", "style") else ""

    def handle_data(self, data):
        if self._in == "script":
            self.scripts.append(data)
        elif self._in == "style":
            self.styles.append(data)
        self._in = ""


def test_nothing_a_candidate_wrote_runs_on_any_page(home):
    pid = plant(home)
    where, srv = start()
    try:
        for path in ("/?as=Ana", "/?as=Ana&tab=all", "/?as=Ana&tab=all&q=sam",
                     f"/person/{pid}?as=Ana", "/recap?as=Ana", "/settings?as=Ana",
                     "/setup?as=Ana", "/setup/answers?as=Ana",
                     f"/person/{pid}?as=Ana&error=" + urllib.parse.quote(HOSTILE)):
            code, h, page = raw(where, "GET", path)
            assert code == 200, path
            policy = h["Content-Security-Policy"]
            tags = Tags()
            tags.feed(page)
            assert len(tags.scripts) == 1 and security._hash(tags.scripts[0]) in policy, path
            assert len(tags.styles) == 1 and security._hash(tags.styles[0]) in policy, path
            assert not tags.bad, (path, tags.bad)
        assert "Sam &quot;&gt;&lt;script&gt;" in raw(where, "GET", f"/person/{pid}?as=Ana")[2]
    finally:
        srv.shutdown()


def test_every_answer_carries_the_security_headers(home):
    where, srv = start()
    try:
        for path in ("/?as=Ana", "/nope", "/file/x/y"):
            _, h, _ = raw(where, "GET", path)
            assert h["X-Content-Type-Options"] == "nosniff", path
            assert h["X-Frame-Options"] == "DENY" and h["Cache-Control"] == "no-store", path
            assert "frame-ancestors 'none'" in h["Content-Security-Policy"], path
            assert h.get("Server") == "desk", path
    finally:
        srv.shutdown()


def token_of(where: str, viewer: str = "Ana") -> str:
    page = raw(where, "GET", f"/?as={viewer}")[2]
    return page.split('name="t" value="')[1].split('"')[0]


def note_form(token: str, viewer: str = "Ana") -> bytes:
    return urllib.parse.urlencode({"t": token, "as": viewer, "posting": "fa",
                                   "candidate": "nobody", "text": "hi"}).encode()


def test_a_rebound_name_gets_nothing(home):
    where, srv = start()
    try:
        code, _, body = raw(where, "GET", "/?as=Ana", headers={"Host": "evil.example"})
        assert code == 421 and "Ana" not in body
    finally:
        srv.shutdown()


def test_a_form_posted_from_another_site_is_refused(home):
    where, srv = start()
    try:
        t = token_of(where)
        form = {"Content-Type": "application/x-www-form-urlencoded"}
        assert raw(where, "POST", "/note", note_form(t),
                   {**form, "Origin": "https://evil.example"})[0] == 403
        # Same page, same token: only the origin differs, and this one gets through.
        assert raw(where, "POST", "/note", note_form(t), {**form, "Origin": f"http://{where}"}
                   )[0] == 303
    finally:
        srv.shutdown()


def test_one_viewers_token_cannot_vote_as_another(home):
    where, srv = start()
    try:
        form = {"Content-Type": "application/x-www-form-urlencoded"}
        code, _, msg = raw(where, "POST", "/note", note_form(token_of(where, "Ben"), "Ana"), form)
        assert code == 403 and "stale or foreign" in msg
    finally:
        srv.shutdown()


@pytest.mark.parametrize("length,status", [("-1", 400), ("99999999", 413)])
def test_a_body_that_lies_about_its_size_is_refused_before_it_is_read(home, length, status):
    where, srv = start()
    try:
        assert raw(where, "POST", "/note", b"x", {"Content-Length": length})[0] == status
    finally:
        srv.shutdown()


def test_header_mode_refuses_whoever_is_not_the_proxy(home):
    acc = desk.Access(mode="header", emails={"ana@co.example": "Ana"},
                      trusted_proxies=["10.9.9.9"])
    where, srv = start(acc)
    try:
        code, _, body = raw(where, "GET", "/", headers={"X-Forwarded-Email": "ana@co.example"})
        assert code == 403 and "Ana" not in body
    finally:
        srv.shutdown()


def test_header_mode_refuses_a_second_identity_header(home):
    acc = desk.Access(mode="header", emails={"ana@co.example": "Ana"},
                      trusted_proxies=["127.0.0.1"])
    where, srv = start(acc)
    try:
        assert raw(where, "GET", "/", headers={"X-Forwarded-Email": "ana@co.example"})[0] == 200
        host, port = where.split(":")
        c = http.client.HTTPConnection(host, int(port), timeout=10)
        c.putrequest("GET", "/")
        c.putheader("X-Forwarded-Email", "mallory@evil.example")
        c.putheader("X-Forwarded-Email", "ana@co.example")
        c.endheaders()
        assert c.getresponse().status == 400
        c.close()
    finally:
        srv.shutdown()


# --------------------------------------------------------------------------
# Candidate data elsewhere: mail drafts, imports, Ashby, PDFs
# --------------------------------------------------------------------------

def test_a_line_break_in_a_setting_does_not_add_a_mail_header():
    cfg = desk.Config(voters=["Ana"], labels=dict(CFG.labels), company="Co\r\nBcc: all@evil.example",
                      subjects={"contact": "Hello from {company}"},
                      templates={"contact": "Hi {first_name}"})
    m = desk.draft(desk.Person("sam", "Sam Lee", "sam@x.example\nBcc: x@evil.example"),
                   "FA", "contact", cfg)
    assert "\n" not in m["Subject"] and m["Bcc"] is None
    head = bytes(m).decode().replace("\r\n", "\n").split("\n\n", 1)[0]
    assert m["To"] == "" and not re.search(r"(?mi)^bcc:", head)


def test_a_cv_cell_cannot_reach_outside_the_exports_folder(home):
    (home / "secret.txt").write_text("private", encoding="utf-8")
    (home / "export").mkdir()
    f = home / "export" / "export.csv"
    f.write_text("Name,Email,Resume,Job\nSam Lee,sam@x.example,../secret.txt,fa\n",
                 encoding="utf-8")
    report = desk.import_csv(f)
    assert "outside" in report[0]
    assert not desk.load_registry(desk._registry_path()).people


def test_a_webhook_signature_in_any_characters_is_just_wrong():
    for header in ("sha256=é", "sha256= ", "sha256=" + "ÿ" * 64):
        assert ashby.verify(b"{}", header, "k") is False


def test_a_file_from_ashby_over_the_size_limit_is_not_held(monkeypatch):
    class Big(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: Big(b"%PDF" + b"x" * desk.MAX_DOC_BYTES))
    _, download = ashby._http("key")
    with pytest.raises(ashby.AshbyError, match="size limit"):
        download("https://files.example/cv.pdf")


def deflated_pdf(inflated: int) -> bytes:
    body = zlib.compress(b"\0" * inflated, 9)
    return (b"%%PDF-1.4\n1 0 obj\n<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(body)
            + body + b"\nendstream\nendobj\n%%EOF\n")


def test_a_pdf_that_inflates_past_the_limit_is_not_read():
    from intake import pdf
    assert pdf.inflates_within(deflated_pdf(1000), 1000)
    assert not pdf.inflates_within(deflated_pdf(1001), 1000)
    bomb = deflated_pdf(pdf.MAX_INFLATED + 1)
    assert len(bomb) < desk.MAX_DOC_BYTES
    with pytest.raises(pdf.PdfError, match="inflates"):
        pdf.read(bomb)


def test_a_pdf_with_too_many_pages_is_not_read_as_a_cv(monkeypatch):
    from reportlab.pdfgen import canvas

    from intake import pdf
    b = io.BytesIO()
    c = canvas.Canvas(b)
    for i in range(4):
        c.drawString(72, 700, f"page {i}")
        c.showPage()
    c.save()
    monkeypatch.setattr(pdf, "MAX_PAGES", 3)
    with pytest.raises(pdf.PdfError, match="more than 3 pages"):
        pdf.read(b.getvalue())
    monkeypatch.setattr(pdf, "MAX_PAGES", 4)
    assert pdf.read(b.getvalue()).pages == 4


def test_a_cv_is_read_again_once_it_changes(tmp_path):
    import signals
    f = tmp_path / "cv.txt"
    f.write_text("first version", encoding="utf-8")
    assert "first" in signals._text(f)
    f.write_text("second version, longer", encoding="utf-8")
    assert "second" in signals._text(f)


# --------------------------------------------------------------------------
# Fuzzing, standard library only
# --------------------------------------------------------------------------

#: Characters that break things: markup, quotes, every kind of line break,
#: controls, a right-to-left override, and plain letters to carry them.
NASTY = list("<>\"'&/\\:;=%?#@ \t\r\n\x00\x0b\x0c\x7f\x85  ‮﻿é")
NASTY += list("abcjavscriptdtJS0123456789") + ["javascript:", "data:", "https://", "//",
                                                "&#106;", "%0d%0a", "\\u003c"]


def fuzz(rng: random.Random, n: int = 40) -> str:
    return "".join(rng.choice(NASTY) for _ in range(rng.randint(0, n)))


def test_fuzz_links_are_web_addresses_or_nothing():
    rng = random.Random(SEED)
    for _ in range(5000):
        s = fuzz(rng)
        if rng.random() < 0.3:
            s = rng.choice(["https://", "http://", " HTTPS://", "javascript:"]) + s
        out = security.href(s)
        assert out == "" or (re.match(r"(?i)https?://", out)
                             and not re.search(r"[\x00-\x20\x7f]", out)), repr(s)


def test_fuzz_the_way_back_never_leaves_the_desk():
    rng = random.Random(SEED + 1)
    for _ in range(5000):
        s = rng.choice(["/", "//", "/\\", ""]) + fuzz(rng)
        out = security.safe_back(s)
        assert out.startswith("/") and not out.startswith("//"), repr(s)
        assert re.fullmatch(r"[\x21-\x7e]+", out) and "\\" not in out, repr(s)


def test_fuzz_a_file_name_never_breaks_its_header():
    rng = random.Random(SEED + 2)
    for _ in range(5000):
        d = security.disposition(fuzz(rng, 300), inline=rng.random() < 0.5)
        assert re.fullmatch(r"[\x20-\x7e]+", d), repr(d)
        assert d.count('"') == 2


def test_fuzz_body_sizes_are_refused_or_bounded():
    rng = random.Random(SEED + 3)
    for _ in range(5000):
        v = rng.choice([fuzz(rng, 8), str(rng.randint(-10, 3000)), " 12 ", "0x10"])
        try:
            n = security.content_length(hdrs(Content_Length=v), 2048)
        except Refused as no:
            assert no.status in (400, 413)
        else:
            assert 0 <= n <= 2048


def test_fuzz_host_and_origin_checks_only_refuse():
    rng = random.Random(SEED + 4)
    for _ in range(3000):
        for check in (lambda: security.check_host(fuzz(rng)),
                      lambda: security.check_origin(
                          hdrs(Host="127.0.0.1:8711", Origin=fuzz(rng).replace("\n", ""))),
                      lambda: security.check_token(fuzz(rng), "srv", fuzz(rng)),
                      lambda: ashby.verify(fuzz(rng).encode("utf-8", "surrogatepass"),
                                           fuzz(rng), "k")):
            try:
                check()
            except Refused:
                pass


def test_fuzz_nothing_typed_into_a_card_becomes_markup():
    """Every field of a card, filled with random markup-ish text: the only tags
    on the page are the desk's own, whatever the text."""
    from datetime import datetime, timezone

    from console.desk_web import Desk
    from pipeline import Event, Pipeline
    rng = random.Random(SEED + 5)
    d = Desk(CFG, desk.Access())
    for _ in range(300):
        marker = "<zz" + str(rng.randint(0, 9))
        pipe = Pipeline("fa")
        pipe.add(Event(candidate_id="x", kind="received"))
        desk.cast(pipe, "x", "Ana", "pass", CFG, reason=fuzz(rng) + marker,
                  comment=fuzz(rng) + marker)
        p = desk.Person("x", fuzz(rng) + marker, fuzz(rng) + marker,
                        link=fuzz(rng) + marker, links=[fuzz(rng) + marker])
        p.documents = [desk.Document("fa", "cv", "x/fa_cv_1.pdf", fuzz(rng) + marker)]
        app = desk.Application("fa", "x")
        p.applications = [app]
        page = d.row(p, app, pipe.standing("x"), "Ana", {"fa": fuzz(rng) + marker},
                     datetime.now(timezone.utc))
        assert marker not in page and "<zz" not in page


def test_fuzz_a_broken_pdf_is_a_pdf_error_and_nothing_else():
    """A candidate's file, mangled at random: `read` refuses it or reads it,
    and never raises anything the desk does not expect."""
    from reportlab.pdfgen import canvas

    from intake import pdf
    b = io.BytesIO()
    c = canvas.Canvas(b)
    c.drawString(72, 700, "Built a ledger reconciler nobody asked for")
    c.save()
    good = b.getvalue()
    rng = random.Random(SEED + 6)
    for _ in range(150):
        data = bytearray(good)
        for _ in range(rng.randint(1, 12)):
            op = rng.random()
            i = rng.randrange(4, len(data))
            if op < 0.5:
                data[i] = rng.randrange(256)
            elif op < 0.75:
                del data[i:i + rng.randint(1, 50)]
            else:
                data[i:i] = bytes(rng.randrange(256) for _ in range(rng.randint(1, 20)))
        try:
            pdf.read(bytes(data))
        except pdf.PdfError:
            pass


def test_the_pages_added_beside_the_desk_bring_no_style_or_script_of_their_own():
    # The CSP allows the desk's one hashed <style> and <script>, and no style
    # or on...= attribute: anything a page module adds is silently dropped by
    # the browser. Their rules belong in their CSS, joined to the desk's.
    import importlib
    from console import desk_web
    pages = sorted(f.stem for f in (Path(desk_web.__file__).parent).glob('*_web.py')
                   if f.stem != 'desk_web')
    for mod in (importlib.import_module(f'console.{n}') for n in pages):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert not re.search(r'<style|<script|\sstyle="|\son[a-z]+="', src), mod.__name__
        assert getattr(mod, "CSS", "") in desk_web.CSS  # a module with no style adds none
