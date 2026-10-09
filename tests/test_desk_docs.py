"""Documents on the desk: the CV a partner needs before they can class anyone.

Uploads are the one place outside data enters the desk and is served back
from its own address, so most of these tests are about what is refused.
"""

from __future__ import annotations

import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import desk
from desk import DeskError, Person, Registry, attach, check_document, document_file

PDF = b"%PDF-1.4\n% a tiny pdf\n"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, "ROOT", tmp_path)
    (tmp_path / "mandates" / "generated").mkdir(parents=True)
    (tmp_path / "mandates" / "generated" / "role_fa.provenance.json").write_text(
        '{"posting_id": "fa", "title": "Founders\' Associate"}', encoding="utf-8")
    return tmp_path


def reg_with(pid="sam"):
    r = Registry()
    r.people[pid] = Person(pid, "Sam Lee")
    return r


@pytest.mark.parametrize("name,data,why", [
    ("cv.html", b"<script>", "only"),
    ("cv.exe", b"MZ", "only"),
    ("cv.pdf", b"", "empty"),
    ("cv.pdf", b"<html>not a pdf", "not a PDF"),
    ("cv.pdf", b"%PDF" + b"x" * desk.MAX_DOC_BYTES, "over"),
], ids=["html", "exe", "empty", "fake-pdf", "too-big"])
def test_what_cannot_be_vouched_for_is_refused(name, data, why):
    with pytest.raises(DeskError, match=why):
        check_document("cv", name, data)


def test_a_cv_is_stored_and_found_again(home):
    r = reg_with()
    d = attach(r, "sam", "fa", "cv", "Sam Lee CV.pdf", PDF, by="Ana")
    f, doc = document_file(r, "sam", d.stored)
    assert f.read_bytes() == PDF and doc.original == "Sam Lee CV.pdf" and doc.added_by == "Ana"


def test_only_a_document_the_registry_lists_can_be_read(home):
    r = reg_with()
    attach(r, "sam", "fa", "cv", "cv.pdf", PDF)
    (home / "runs" / "desk" / "secret.txt").write_text("no")
    for stored in ("../secret.txt", "sam/../../secret.txt", "other/fa_cv_1.pdf"):
        with pytest.raises(DeskError):
            document_file(r, "sam", stored)


def test_a_cv_sent_for_one_role_is_shown_on_the_next():
    p = Person("sam", "Sam Lee")
    p.documents = [desk.Document("fa", "cv", "sam/fa_cv_1.pdf", "cv.pdf")]
    assert [d.stored for d in p.documents_for("marketing")] == ["sam/fa_cv_1.pdf"]


def test_a_profile_link_must_be_https():
    with pytest.raises(DeskError, match="https"):
        desk.add_application(desk.Pipeline("fa"), Registry(), "Sam Lee",
                             link="javascript:alert(1)")


def test_adding_with_a_bad_file_leaves_nothing_behind(home):
    with pytest.raises(DeskError):
        desk.add_now("fa", "Sam Lee", files=[("cv", "cv.pdf", b"not a pdf")])
    assert not desk.load_registry(desk._registry_path()).people
    assert "fa" not in desk._pipelines()


def test_an_unknown_role_is_refused(home):
    with pytest.raises(DeskError, match="no posting"):
        desk.add_now("nope", "Sam Lee")


# --------------------------------------------------------------------------
# CSV import
# --------------------------------------------------------------------------

def test_an_export_comes_in_line_by_line(home):
    (home / "cvs").mkdir()
    (home / "cvs" / "ana.pdf").write_bytes(PDF)
    f = home / "export.csv"
    f.write_text("First Name,Last Name,Email,LinkedIn URL,Resume,Job\n"
                 "Ana,Ruiz,ana@x.example,https://www.linkedin.com/in/ana,cvs/ana.pdf,Founders' Associate\n"
                 "Ben,Ode,ben@x.example,,,Founders' Associate\n"
                 "Cy,Lo,cy@x.example,,,Chief Vibes Officer\n"
                 "Ana,Ruiz,ana@x.example,,,Founders' Associate\n", encoding="utf-8")
    report = desk.import_csv(f)
    assert "added" in report[0] and "added" in report[1]
    assert "unknown role" in report[2]
    assert "already applied" in report[3]
    reg = desk.load_registry(desk._registry_path())
    ana = next(p for p in reg.people.values() if p.email == "ana@x.example")
    assert ana.link.startswith("https://www.linkedin.com") and ana.documents[0].kind == "cv"


def test_an_export_without_a_name_column_is_refused(home):
    f = home / "export.csv"
    f.write_text("Email\nana@x.example\n", encoding="utf-8")
    with pytest.raises(DeskError, match="no name column"):
        desk.import_csv(f, "fa")


# --------------------------------------------------------------------------
# Through the browser, end to end
# --------------------------------------------------------------------------

def test_a_cv_uploaded_through_the_form_can_be_opened(home):
    from console import desk_web
    cfg = desk.Config(voters=["Ana", "Ben", "Cy"],
                      labels={"contact": "C", "later": "L", "discuss": "D", "pass": "P"})
    started = {}
    real_init = ThreadingHTTPServer.__init__

    def capture(self, addr, handler):
        real_init(self, ("127.0.0.1", 0), handler)
        started["port"] = self.server_address[1]
        started["srv"] = self

    ThreadingHTTPServer.__init__ = capture
    try:
        t = threading.Thread(target=desk_web.serve, args=("127.0.0.1", 0, cfg, desk.Access()),
                             daemon=True)
        t.start()
        while "port" not in started:
            pass
    finally:
        ThreadingHTTPServer.__init__ = real_init
    base = f"http://127.0.0.1:{started['port']}"
    page = urllib.request.urlopen(f"{base}/?as=Ana").read().decode()
    token = page.split('name="t" value="')[1].split('"')[0]

    boundary = "----deskboundary"
    parts = []
    for k, v in (("t", token), ("as", "Ana"), ("name", "Sam Lee"), ("posting", "fa")):
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n')
    body = "".join(parts).encode() + (
        f'--{boundary}\r\nContent-Disposition: form-data; name="cv"; filename="Sam CV.pdf"\r\n'
        f"Content-Type: application/pdf\r\n\r\n").encode() + PDF + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"{base}/add", data=body, method="POST", headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}"})
    urllib.request.urlopen(req).read()

    reg = desk.load_registry(desk._registry_path())
    sam = next(iter(reg.people.values()))
    got = urllib.request.urlopen(f"{base}/file/{sam.person_id}/{sam.documents[0].stored}?as=Ana")
    assert got.read() == PDF
    assert got.headers["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in got.headers["Content-Security-Policy"]
    started["srv"].shutdown()
