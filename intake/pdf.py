"""A PDF, read the way a person reads it: what is drawn, and what is not.

An ordinary text extractor returns every character in the file. White text
on a white page, a one-point font, a line pushed off the page, text in the
"invisible" render mode, black on a black box -- all of it comes back as
plain text, indistinguishable from the rest, and all of it would reach the
screener. Measured on the test fixtures here before this module existed:
every one of those five came out of the extractor as ordinary lines.

So each character is read with the attributes that decide whether a person
could see it: its render mode, its size, where it sits, its colour, and the
colour of whatever was painted behind it. What a person could not see is
kept out of the text handed to extraction -- and reported, by `scan`, with
the reason, because a candidate whose document was partly ignored is
entitled to know which part.

`pdfminer.six` (MIT) does the parsing. PyMuPDF would do it faster and is
AGPL, which is the wrong licence to hand a company.

What this does not do: OCR. A PDF with no text layer is a picture of a CV,
and it is refused with that sentence rather than read as empty.
"""

from __future__ import annotations

import io
import re
import zlib
from dataclasses import dataclass, field
from typing import Any, Iterable

from intake import hostile

#: Below this, a font is not a font a reader can read. Footnotes sit at 6-8.
MIN_READABLE_PT = 3.0
#: How close two colours are before text is "the same colour as its background".
SAME_COLOUR = 0.08
#: Text render modes that paint nothing: 3 is invisible, 7 is clip-only.
INVISIBLE_MODES = (3, 7)
#: Metadata fields a reader never sees and an extractor might.
METADATA_FIELDS = ("Title", "Subject", "Keywords")
#: A CV is a few pages. Past this, the file is refused rather than read: the
#: desk reads every attached PDF to draw a card, and a candidate's file must
#: not be able to cost it minutes per page view.
MAX_PAGES = 40
#: What the compressed streams of one file may inflate to, in total. A 10 MB
#: upload of zeros deflated inflates to about 10 GB; pdfminer would hold it all.
MAX_INFLATED = 128 * 1024 * 1024
STREAM = re.compile(rb"stream\r?\n")


class PdfError(ValueError):
    pass


@dataclass
class Hidden:
    text: str
    why: str
    page: int


@dataclass
class Read:
    text: str
    pages: int
    hidden: list[Hidden] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)


def _rgb(ncs: Any, colour: Any) -> tuple[float, float, float] | None:
    """A colour as RGB 0-1, or None when it cannot be judged (a pattern, say)."""
    if colour is None:
        return None
    if isinstance(colour, (int, float)):
        colour = (colour,)
    try:
        c = tuple(float(x) for x in colour)
    except (TypeError, ValueError):
        return None
    name = getattr(ncs, "name", "") or ""
    if len(c) == 1 or name == "DeviceGray":
        return (c[0], c[0], c[0])
    if len(c) == 3:
        return c  # type: ignore[return-value]
    if len(c) == 4:
        k = c[3]
        return tuple((1 - x) * (1 - k) for x in c[:3])  # type: ignore[return-value]
    return None


def _close(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    return max(abs(x - y) for x, y in zip(a, b)) < SAME_COLOUR


def _aggregator():
    from pdfminer.converter import PDFPageAggregator
    from pdfminer.layout import LAParams, LTChar, LTCurve

    class Watching(PDFPageAggregator):
        """Tags every character and filled shape with what the page did to it.

        The layout objects keep a character's glyph and box, and lose its
        render mode and the order it was painted in. Both are recorded here,
        at the moment of drawing, because both decide visibility.
        """

        def __init__(self, rsrc: Any) -> None:
            super().__init__(rsrc, laparams=LAParams())
            self.seq = 0
            self.fills: list[tuple[int, tuple[float, float, float, float],
                                   tuple[float, float, float] | None]] = []
            self.images: list[tuple[float, float, float, float]] = []

        def begin_page(self, page: Any, ctm: Any) -> None:
            self.fills, self.images = [], []
            super().begin_page(page, ctm)

        def render_string(self, textstate: Any, seq: Any, ncs: Any, graphicstate: Any) -> None:
            n = len(self.cur_item._objs)
            super().render_string(textstate, seq, ncs, graphicstate)
            colour = _rgb(ncs, getattr(graphicstate, "ncolor", None))
            for obj in self.cur_item._objs[n:]:
                if isinstance(obj, LTChar):
                    self.seq += 1
                    obj.nbh_seq = self.seq
                    obj.nbh_mode = getattr(textstate, "render", 0)
                    obj.nbh_colour = colour

        def render_image(self, name: Any, stream: Any) -> None:
            n = len(self.cur_item._objs)
            super().render_image(name, stream)
            for obj in self.cur_item._objs[n:]:
                self.seq += 1
                self.images.append(obj.bbox)
                # A picture's colours are not known here, so the text on it
                # is not judged by colour: `None` means "cannot tell".
                self.fills.append((self.seq, obj.bbox, None))

        def paint_path(self, gstate: Any, stroke: Any, fill: Any, evenodd: Any, path: Any) -> None:
            n = len(self.cur_item._objs)
            super().paint_path(gstate, stroke, fill, evenodd, path)
            if not fill:
                return
            colour = _rgb(getattr(gstate, "ncs", None), getattr(gstate, "ncolor", None))
            for obj in self.cur_item._objs[n:]:
                if isinstance(obj, LTCurve):
                    self.seq += 1
                    self.fills.append((self.seq, obj.bbox, colour))

    return Watching


def _inside(x: float, y: float, b: tuple[float, float, float, float]) -> bool:
    return b[0] <= x <= b[2] and b[1] <= y <= b[3]


def _why_hidden(ch: Any, page_box: tuple[float, float, float, float],
                fills: list[Any], images: list[Any] = ()) -> str:
    cx, cy = (ch.x0 + ch.x1) / 2, (ch.y0 + ch.y1) / 2
    if getattr(ch, "nbh_mode", 0) in INVISIBLE_MODES:
        # A scanned page run through OCR is exactly this: a picture of the
        # words, with the words themselves in the invisible mode beneath it
        # so they can be searched. The reader sees them -- as a picture.
        # Calling that hidden would accuse every scanned CV of an attack.
        if any(_inside(cx, cy, b) for b in images):
            return ""
        return "invisible render mode"
    if ch.size < MIN_READABLE_PT:
        return f"a {ch.size:.1f}-point font"
    x0, y0, x1, y1 = page_box
    if ch.x1 <= x0 or ch.x0 >= x1 or ch.y1 <= y0 or ch.y0 >= y1:
        return "outside the page"
    seq = getattr(ch, "nbh_seq", 0)
    # A shape of known colour painted after the glyph, over it: the old
    # white-box-over-the-words trick.
    if any(s > seq and fill is not None and _inside(cx, cy, b) for s, b, fill in fills):
        return "covered by a shape drawn over it"
    colour = getattr(ch, "nbh_colour", None)
    if colour is None:
        return ""
    behind: tuple[float, float, float] | None = (1.0, 1.0, 1.0)
    for seq, (bx0, by0, bx1, by1), fill in fills:
        if seq < getattr(ch, "nbh_seq", 0) and bx0 <= cx <= bx1 and by0 <= cy <= by1:
            behind = fill
    # Text on a picture, or on a fill whose colour could not be read, is read
    # as visible. White text on a photo is ordinary design; calling it hidden
    # would accuse the CV of an attack it did not make.
    if behind is None:
        return ""
    if _close(colour, behind):
        return "the same colour as the page" if behind == (1.0, 1.0, 1.0) \
            else "the same colour as the shape behind it"
    return ""


def read(data: bytes) -> Read:
    """The visible text, and what was not visible with the reason.

    A CV is a file a stranger made, so a file pdfminer trips over is a file
    that cannot be read -- a `PdfError` like any other -- and never an
    exception the caller did not expect. Found by fuzzing
    (tests/test_security.py): a few flipped bytes raise a dozen kinds.
    """
    try:
        return _read(data)
    except PdfError:
        raise
    except Exception as e:  # noqa: BLE001 -- see above
        raise PdfError(f"this PDF cannot be read ({type(e).__name__})") from e


def _read(data: bytes) -> Read:
    from pdfminer.pdfdocument import PDFDocument, PDFEncryptionError, PDFPasswordIncorrect
    from pdfminer.pdfinterp import PDFPageInterpreter, PDFResourceManager
    from pdfminer.pdfpage import PDFPage
    from pdfminer.pdfparser import PDFParser, PDFSyntaxError
    from pdfminer.layout import LTAnno, LTChar, LTTextBox, LTTextLine
    from pdfminer.utils import decode_text

    if not data.startswith(b"%PDF"):
        raise PdfError("not a PDF")
    if not inflates_within(data, MAX_INFLATED):
        raise PdfError(f"this PDF inflates to more than {MAX_INFLATED // (1024 * 1024)} MB "
                       f"of content -- not read")
    try:
        parser = PDFParser(io.BytesIO(data))
        doc = PDFDocument(parser)
    except (PDFSyntaxError, PDFEncryptionError, PDFPasswordIncorrect) as e:
        raise PdfError(f"this PDF cannot be opened ({type(e).__name__})") from e

    meta: dict[str, str] = {}
    for info in doc.info or []:
        for k in METADATA_FIELDS:
            v = info.get(k)
            if isinstance(v, bytes):
                v = decode_text(v)
            if isinstance(v, str) and v.strip():
                meta[k] = v.strip()

    rsrc = PDFResourceManager()
    device = _aggregator()(rsrc)
    interp = PDFPageInterpreter(rsrc, device)
    blocks: list[str] = []
    hidden: list[Hidden] = []
    pages = 0
    for n, page in enumerate(PDFPage.create_pages(doc), 1):
        if n > MAX_PAGES:
            raise PdfError(f"this PDF has more than {MAX_PAGES} pages -- not read as a CV")
        pages = n
        interp.process_page(page)
        layout = device.get_result()
        box = tuple(page.cropbox or page.mediabox)
        for item in layout:
            if not isinstance(item, LTTextBox):
                continue
            lines = []
            for line in item:
                if not isinstance(line, LTTextLine):
                    continue
                shown, run, run_why = [], [], ""
                for ch in line:
                    if isinstance(ch, LTChar):
                        why = _why_hidden(ch, box, device.fills,  # type: ignore[arg-type]
                                          device.images)
                        if why:
                            if run and why != run_why:
                                hidden.append(Hidden("".join(run).strip(), run_why, n))
                                run = []
                            run.append(ch.get_text())
                            run_why = why
                            continue
                        if run:
                            hidden.append(Hidden("".join(run).strip(), run_why, n))
                            run = []
                        shown.append(ch.get_text())
                    elif isinstance(ch, LTAnno):
                        (run if run else shown).append(ch.get_text())
                if run:
                    hidden.append(Hidden("".join(run).strip(), run_why, n))
                text = "".join(shown).strip()
                if text:
                    lines.append(text)
            if lines:
                blocks.append("\n".join(lines))
    text = _clean("\n\n".join(blocks))
    hidden = _merge([h for h in hidden if h.text])
    if not text.strip() and not hidden:
        raise PdfError("this PDF has no text layer -- it is a picture of a CV. Nothing is "
                       "read from it: there is no OCR here, and an empty read would score "
                       "as a CV that says nothing.")
    return Read(text=text, pages=pages, hidden=hidden, metadata=meta)


def inflates_within(data: bytes, limit: int) -> bool:
    """Do the file's deflated streams inflate to `limit` bytes or less?

    Measured before pdfminer sees the file, in bounded chunks that are thrown
    away as they are counted, so the check itself never holds more than the
    limit. A stream that is not deflate is skipped: it is read as it is.
    A stream deflated twice is counted once -- the residual, in SECURITY.md.
    """
    total = 0
    for m in STREAM.finditer(data):
        end = data.find(b"endstream", m.end())
        chunk = data[m.end():end if end >= 0 else len(data)]
        d = zlib.decompressobj()
        try:
            total += len(d.decompress(chunk, limit - total + 1))
            while d.unconsumed_tail and total <= limit:
                total += len(d.decompress(d.unconsumed_tail, limit - total + 1))
        except zlib.error:
            continue
        if total > limit:
            return False
    return True


#: A glyph with no character behind it -- the phone and envelope icons of a
#: CV template. Drawn, but not text.
UNMAPPED_GLYPH = re.compile(r"\(cid:\d+\)\s?")
#: A word the layout broke across two lines. Joined back, so that a quote of
#: "companies" is found in a page that printed "compa-" / "nies".
LINE_BREAK_HYPHEN = re.compile(r"(\w)-\n(?=[a-zà-ÿ])")


def _clean(text: str) -> str:
    return LINE_BREAK_HYPHEN.sub(r"\1", UNMAPPED_GLYPH.sub("", text))


def _merge(hidden: list[Hidden]) -> list[Hidden]:
    """Consecutive lines hidden the same way are one block, as a reader would see it."""
    out: list[Hidden] = []
    for h in hidden:
        if out and out[-1].why == h.why and out[-1].page == h.page:
            out[-1].text += "\n" + h.text
        else:
            out.append(Hidden(h.text, h.why, h.page))
    return out


def visible_text(data: bytes) -> str:
    return read(data).text


def scan(data: bytes, *, vocabulary: Iterable[str] = ()) -> hostile.Report:
    """Everything in this PDF a reader would not see, in `intake.hostile`'s terms.

    Each hidden block is classified the way the text scanner classifies one:
    an instruction if it is addressed to a model, stuffing if it is a dense
    block of the posting's vocabulary, otherwise simply hidden. The visible
    text then goes through the text scanner too, for zero-width characters
    and instructions in plain sight.
    """
    r = read(data)
    vocabulary = set(vocabulary)
    report = hostile.Report()
    for h in r.hidden:
        how = f"pdf: {h.why}"
        if hostile.INSTRUCTION_SHAPED.search(h.text):
            kind, why = "instruction", "addressed to a model, and not drawn on the page"
        elif hostile.looks_like_stuffing(h.text, vocabulary):
            kind, why = "stuffing", ("a dense block of the posting's own terms, not drawn on "
                                     "the page -- the shape of over 90% of real attacks")
        else:
            kind, why = "invisible", "in the file's text layer, not visible on the page"
        report.findings.append(hostile.Finding(kind=kind, how=how,
                                               span=hostile._clip(h.text), why=why))
    for k, v in r.metadata.items():
        if hostile.INSTRUCTION_SHAPED.search(v):
            report.findings.append(hostile.Finding(
                kind="instruction", how=f"pdf metadata ({k})", span=hostile._clip(v),
                why="addressed to a model, in a field no reader opens"))
        elif hostile.looks_like_stuffing(v, vocabulary):
            report.findings.append(hostile.Finding(
                kind="stuffing", how=f"pdf metadata ({k})", span=hostile._clip(v),
                why="the posting's own terms, in a field no reader opens"))
    report.findings.extend(hostile.scan(r.text, vocabulary=vocabulary).findings)
    return report
