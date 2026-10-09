# Threat model

What the hiring desk protects, from whom, how, and what it does not cover.
Every guard named here lives in `console/security.py` (the web server) or next
to the data it checks, and has a test in `tests/test_security.py` that fails
when the guard is removed. This is a description of the measures taken, not a
certification: recruiting is a high-risk use under the AI Act (Annex III), and
a real deployment needs its own review.

## What is worth protecting

| asset | why it matters |
|---|---|
| Applications: names, emails, CVs, letters, links | personal data of people who did not choose this tool |
| Votes, reasons, comments | the blind vote only works if nobody sees a class before casting their own |
| The form token | whoever holds it can vote and post notes as a partner |
| The machine running the desk | it opens files strangers sent |

## Who might attack, and from where

1. **A candidate.** Controls their name, email, links, letter, CV (PDF, Word,
   text) and, through Ashby, the fields of their application. The most likely
   attacker, and the one with the most input.
2. **Another website** a partner visits while the desk is open: can make the
   browser send requests (CSRF), or rebind its own DNS name to 127.0.0.1 to
   read the desk as if it were same-origin (DNS rebinding).
3. **Someone on the network** who reaches the port directly, bypassing the
   sign-in proxy, and types the identity header themselves.
4. **A colleague** who is a legitimate voter but tries to act as another
   voter, or to read votes before casting their own.
5. **Anyone** able to send a request: oversized or malformed bodies, slow
   connections, many connections.

Out of scope: an attacker with a shell on the host, a compromised sign-in
proxy, a compromised browser, and the mail client that opens drafts.

## Threats and what answers them

### Candidate content shown in the page (stored XSS)
- Every value interpolated into HTML goes through `html.escape` (`e()` in
  `console/desk_web.py`), in text and in attributes.
- **Content-Security-Policy** without `'unsafe-inline'`: the page's single
  `<style>` and `<script>` are allowed by their SHA-256 hash, nothing else.
  An escaping mistake still cannot run a script. For this, the desk has no
  inline `style=` or `on…=` attribute left; the name menu submits through
  the script, and the settings sample travels in a `data-` attribute.
- **Links** a candidate gave are `https://` only when they come in, and
  checked again when they go out (`security.href`): `javascript:`, `data:`,
  `vbscript:`, relative links and anything with a control character or space
  become plain text, never an `href`. Links open with `rel="noopener
  noreferrer"`.
- Tested end to end: a person whose name, email, links, file name, note, vote
  comment and role title are all markup, served on every page, parsed with an
  HTML parser; plus a fuzz of 300 random cards.

### Candidate files
- Only PDF, Word, text and Markdown, 10 MB each (`MAX_DOC_BYTES`); a `.pdf`
  must start with `%PDF`. Stored under a name the desk makes, served only for
  a document the registry lists (no path from the URL reaches the disk).
- Served with `X-Content-Type-Options: nosniff`, `Content-Security-Policy:
  sandbox`, and **as a download** except plain text: a PDF opened inside the
  browser runs the browser's PDF viewer, which executes embedded scripts in
  some of them. `Content-Disposition` is built from a sanitised name
  (`security.disposition`), so a file name cannot write a header.
- **PDF bombs**: before pdfminer reads anything, the deflated streams are
  inflated in bounded chunks and counted (`intake.pdf.inflates_within`,
  128 MB total); past it, the file is refused. More than 40 pages
  (`MAX_PAGES`) is not a CV and is not read.
- **Malformed PDFs**: any exception from the parser becomes a `PdfError`
  (found by fuzzing: a few flipped bytes raised half a dozen exception types).
- Each file is parsed once per version (`signals._read_once`, keyed on path,
  mtime and size), not once per page view.

### Forged requests (CSRF) and DNS rebinding
- Every form carries a token minted at server start **and bound to the
  viewer** (`form_token`, an HMAC of the server token and the voter name): a
  colleague who reads their own page cannot build a form that votes as
  someone else.
- `Origin` (or `Referer`, when a browser sends no `Origin`) must name the
  desk; `Origin: null` is refused. Behind a proxy that rewrites `Host`, set
  `access.origin` to the public address.
- In `pick` mode the server binds to 127.0.0.1 only and answers only to
  `Host: 127.0.0.1 / localhost / [::1]` (421 otherwise): a rebound name gets
  nothing, not even the page with the token.
- After a form, the redirect target is a path on the desk or `/`
  (`safe_back`): no `//host`, no backslash, no CR/LF.

### Identity (header mode)
- The server **refuses to start** in `header` mode without
  `access.trusted_proxies`, and refuses every connection from any other
  address before reading the identity header (`check_peer`).
- An identity header sent twice is refused (400): someone added theirs next
  to the proxy's.
- Only emails mapped in `[access.emails]` get in; `?as=` is ignored.

### Blind vote and caches
- `Cache-Control: no-store` on every answer: a page seen after voting must
  not come back from a cache for a colleague who has not voted.
- `X-Frame-Options: DENY` and `frame-ancestors 'none'` (click-jacking),
  `Referrer-Policy: same-origin`, `Cross-Origin-Opener-Policy` and
  `Cross-Origin-Resource-Policy: same-origin`, a `Permissions-Policy` that
  turns off camera, microphone and location. The `Server` header does not
  name Python or its version.

### Denial of service
- `Content-Length` must be plain ASCII digits (`int("-1")` would otherwise
  make `rfile.read(-1)` read until the client hangs up); chunked bodies are
  refused (411); forms are capped at 256 KB, uploads at two documents plus
  64 KB, webhooks at 1 MB.
- A connection idle for 15 s is closed (slowloris); at most 64 connections
  are served at once, and those over the ceiling are closed, not queued.

### Mail drafts
- The subject is one line whatever the settings or the CV say, and `To` is
  filled only with something that is an email address: a line break cannot
  add a `Bcc`.

### Answers in a batch (`/batch`)
- The same form token and origin check as every other form: the download is
  a POST, so a link on another site cannot fetch a file of addresses.
- The file holds only people the desk holds a no for (Ready to answer no);
  anyone else ticked refuses the whole file. Marking as answered goes through
  the same check as the single step, per person, under one lock.
- A cell starting with `=`, `+`, `-` or `@` is written as text: a name or a
  role typed by a stranger is not a formula in the spreadsheet that opens it.
- The file is personal data on the disk of whoever downloads it. The desk
  keeps no copy; deleting it once the mails are sent is the reader's part.

### Imports and Ashby
- A CSV export's CV column is read only inside the export's folder:
  `../../.ssh/id_rsa.txt` in a cell is reported and skipped.
- Ashby webhooks are verified by HMAC-SHA256 in constant time, compared as
  bytes (a non-ASCII signature is simply wrong, not a crash). Nothing is
  taken from a webhook body alone: the application is fetched from Ashby with
  the API key. File links must be `https://`, and a file over the desk's size
  limit is refused without being held in memory.

## What is not covered (residuals)

- **Header mode trusts the proxy.** If the proxy passes along a header a
  browser sent, or the port is reachable from an address listed in
  `trusted_proxies`, identity can be forged. DEPLOY.md says how to check.
- **No TLS in the desk.** In `pick` mode traffic stays on the machine; in
  `header` mode TLS is the proxy's job.
- **A stream deflated twice** is counted once by the inflation check; a
  nested bomb would pass it and hit pdfminer's own limits. Word files are
  zip archives and are not measured the same way.
- **The token lives as long as the process**, and there is no sign-out in
  `pick` mode: anyone at the keyboard is whoever they pick.
- **Hidden text in CVs** (white on white, tiny, off-page) is detected and
  shown, not scored away — see `intake/hostile.py`; prompt injection against
  the screener is out of this document's scope.
- **Opening a downloaded file** happens in the partner's own reader, with its
  own vulnerabilities.
- **Data at rest** (`runs/`) is not encrypted by the desk; that is the
  host's disk encryption and backup policy.

## Settings this adds

| setting | default | alternatives |
|---|---|---|
| `access.trusted_proxies` | `[]` (header mode will not start) | the proxy's IPs or networks, e.g. `["172.17.0.1"]` |
| `access.origin` | `""` (the request's own `Host`) | `"https://desk.company.example"` when the proxy rewrites `Host` |

Constants, in code: `TIMEOUT` 15 s, `MAX_CONNECTIONS` 64, `MAX_FORM_BYTES`
256 KB, `intake.pdf.MAX_PAGES` 40, `intake.pdf.MAX_INFLATED` 128 MB.
