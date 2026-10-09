"""The company's logo in the header, from a file on this machine.

`desk.toml`:

    [team]
    logo = "local-assets/logo.png"

The path is read relative to the desk's folder (or absolute). The image is
inlined as a `data:` URI, which the page policy already allows
(`img-src 'self' data:`), so no new route serves files from disk.

What is refused, and why:

  * anything over MAX_BYTES: the logo is inlined in every page;
  * anything that is not a PNG, JPEG, WebP or SVG *by its first bytes* --
    the extension is a name anyone can type, the bytes are what a browser
    would act on;
  * an SVG is only ever placed in an ``<img>``: there, a browser runs none of
    its scripts and loads none of its links. It is never inlined as markup.

A refused or missing logo is not an error: the header shows the plain square
it always had, and `problem()` says why, for /setup. The logo itself is never
committed: `local-assets/` is in .gitignore (see DEPLOY.md).
"""

from __future__ import annotations

import base64
import functools
import tomllib
from pathlib import Path

#: Inlined in every page: kept small.
MAX_BYTES = 200 * 1024

#: The folder for local files that are never committed (in .gitignore).
LOCAL_DIR = "local-assets"


def sniff(data: bytes) -> str:
    """The image type from the first bytes, or "" when it is not one we show."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    head = data[:512].lstrip(b"\xef\xbb\xbf").lstrip().lower()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in data[:4096].lower()):
        return "image/svg+xml"
    return ""


def configured(root: Path) -> str:
    """The `[team] logo` path as written in desk.toml, or ""."""
    try:
        raw = tomllib.loads((root / "desk.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return ""
    return str(raw.get("team", {}).get("logo", "") or "").strip()


def _resolve(root: Path, said: str) -> Path:
    p = Path(said).expanduser()
    return p if p.is_absolute() else root / p


def load(root: Path) -> tuple[str, str]:
    """(data URI, "") when the logo can be shown; ("", why not) otherwise."""
    said = configured(root)
    if not said:
        return "", ""
    f = _resolve(root, said)
    try:
        st = f.stat()
    except OSError:
        return "", f"logo: {said} cannot be read"
    if not f.is_file():
        return "", f"logo: {said} is not a file"
    if st.st_size > MAX_BYTES:
        return "", f"logo: {said} is {st.st_size // 1024} KB; at most {MAX_BYTES // 1024} KB"
    return _encode(str(f), st.st_mtime_ns, st.st_size, said)


@functools.lru_cache(maxsize=4)
def _encode(path: str, _mtime: int, _size: int, said: str) -> tuple[str, str]:
    try:
        data = Path(path).read_bytes()
    except OSError:
        return "", f"logo: {said} cannot be read"
    kind = sniff(data)
    if not kind:
        return "", f"logo: {said} is not a PNG, JPEG, WebP or SVG image"
    return f"data:{kind};base64,{base64.b64encode(data).decode('ascii')}", ""


def problem(root: Path) -> str:
    """Why the configured logo is not shown, or "" (none configured, or it is shown)."""
    return load(root)[1]
