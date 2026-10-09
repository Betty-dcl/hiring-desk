# Running it for a team

What it takes to go from this repository to one person, or a team, using the
desk every day, what
it costs, and what has to be decided first. Everything here is a default you
can change; nothing assumes a particular cloud.

## What it costs: nothing, by default

**The desk makes no model calls.** Adding an application, the decisions, the
recap, each person's history, the mail drafts — all plain Python. One person
or a team can run the whole process on it without any AI at all:

    python desk.py add --name "Sara Núñez" --email sara@x.example --posting founders_associate

The percentage beside a name (the AI guess) is a count of the words in the CV and
the "Why us" answer (`match.py`): arithmetic, no model, shown as soon as a
document is on file.

**A model screener is optional, and free by default.** The desk does not need
it and does not show its number; it is there for a team that wants a model's
reading of each CV beside the count. Extraction and screening run
on the Claude Code CLI (`NBH_BACKEND=claude-code`, the default), which bills a
Claude subscription somebody on the team already has rather than per call. One
person with that subscription runs `triage.py score` on their own machine;
the readings are kept in `runs/` and open from a person's page. This is how every number in
this repository was produced, at no cost beyond the subscription.

**A paid key is a later choice, not a requirement.** `NBH_BACKEND=api` with
`ANTHROPIC_API_KEY` bills per call — roughly $0.07 per document extracted,
as `python -m intake` prints it at list price — and brings a data-processing agreement that covers candidates'
documents. Worth it at volume; not needed to start. It needs the model's
library, which the desk does not install: `pip install -r requirements-model.txt`.

| | default | only if you choose |
|---|---|---|
| **AI** | 0 € — none on the desk; the optional screener on an existing subscription | an API key, per call |
| **Hosting** | 0 € — one laptop to try it, or a machine you already run | a small container on a cloud (free tiers exist; their terms change, check them) |
| **Sign-in** | 0 € — the Google or Microsoft accounts you already have, or a proxy's free tier for small teams (check current terms) | |
| **Setup** | half a day to two days of one engineer's time (estimate) | |
| **Deciding** | about an hour, once: `desk.toml` and `configure.py` | |

The only real cost is the hour it takes to decide what the settings should
say, and that hour is the point: every one of those choices is currently a
default somebody outside your company made.

## The three decisions to make first

1. **Who decides.** `desk.toml` assumes one person, whoever runs hiring: a
   click is then the decision. Several names turn the same click into a vote,
   cast without seeing the others'.
2. **Whether to use a model at all, and on what.** None (the desk works
   without it), a subscription someone already has (the default, free), or a
   paid key. Candidates' CVs go to the model provider in the last two cases,
   so this is also a data-processing question: settle it before the first real
   CV, not after.
3. **Where it runs, and who can reach it.** Below.

## Where it runs

**One laptop, to try it.** Nothing to deploy:

    python desk.py sync && python desk.py serve      # http://127.0.0.1:8765

`access.mode = "pick"` shows a name menu, and the server refuses to listen on
anything but this machine — anyone who reached the page could vote as anyone.

**A team.** One container behind a sign-in proxy. `desk.toml` is copied into
the image, so set it first; the container listens on `0.0.0.0`, which the
desk refuses in `pick` mode, so it will not start until this is done:

```toml
[access]
mode = "header"
header = "X-Forwarded-Email"     # whatever your proxy sends, see below
trusted_proxies = ["172.17.0.1"]  # where the proxy connects from (here: Docker's bridge)
origin = "https://desk.yourcompany.example"  # if the proxy rewrites Host

[access.emails]
"you@yourcompany.example" = "Recruiter"   # one line per name under [team] voters

[tracking]
base_url = "https://desk.yourcompany.example"  # where the recap's vote links point
```

Then build and run it:

    docker build -t hiring-desk .
    docker run -p 127.0.0.1:8765:8765 -v /srv/hiring-desk:/app/runs hiring-desk

The mounted `runs/` starts empty: the invented demo stays in the repository.
The one-click vote links in the recap are signed with a key the desk makes on
first use in `runs/desk/.link_secret`, on that volume and never committed. To
keep the key in a secret store instead, pass it as `-e NBH_LINK_SECRET=...`
(32 characters at least, e.g. `openssl rand -hex 32` run once) and pass the
same value on every restart: a new key voids every link in circulation.

The proxy signs people in with the company accounts they already have and
passes their email to the desk in a header. The desk maps it to a voter and
refuses everyone else; the name menu disappears, and `?as=` in a URL is
ignored. Common choices and the header each sends:

| proxy | header |
|---|---|
| oauth2-proxy (Google, Microsoft, GitHub…) | `X-Forwarded-Email` |
| Cloudflare Access | `Cf-Access-Authenticated-User-Email` |
| Google Cloud IAP | `X-Goog-Authenticated-User-Email` (the `accounts.google.com:` prefix is handled) |
| Azure App Service authentication | `X-MS-CLIENT-PRINCIPAL-NAME` |

**Two rules that make header mode safe**, and neither is optional:

- the container must be reachable **only through the proxy** — never with its
  port open to the internet, or anyone can send the header themselves;
- the proxy must **overwrite** that header on every request, not pass along
  one a browser sent. All of the above do by default.

The desk enforces the first rule as far as it can see: in header mode it will
not start without `access.trusted_proxies`, and refuses any connection from
another address before reading the header. An Ashby webhook reaches the desk
through the same proxy, on a path the proxy lets through without sign-in
(`/webhook/ashby`); it is checked by its signature instead. The full threat
model, and what it leaves uncovered, is in [docs/SECURITY.md](docs/SECURITY.md).

## What a deployment has to look after

- **`runs/` is candidate data.** In this repository it holds invented people
  and is committed on purpose. In a real deployment it is personal data: mount
  it as a volume, back it up, keep it out of git, and give it a retention
  period — keeping someone "for later" needs their agreement.
- **One instance.** Votes are serialised with a file lock, which is correct on
  one machine and wrong across several. A team of three does not need two.
- **Mail stays a draft.** The desk writes a file your mail client opens as an
  unsent message; a person sends it and clicks "I sent the mail". There is no
  automatic sending, by design: it would need a mail account, and it would be
  the one place the desk acts outside the company without a person.
- **Files are candidate data too.** CVs and letters attached on the desk are
  stored under `runs/desk/files/` (PDF, Word, text, Markdown; 10 MB each), are
  only served for a document the registry lists, and are never executed by the
  browser.
- **Ashby, if you want it.** `ashby.py` reads applications from Ashby and
  writes a settled class back as a note on the candidate. Nothing happens
  without `ASHBY_API_KEY` set; applications can still come in by hand or from a
  CSV export (`desk.py import --csv`).
  - **Key permissions**: `candidatesRead` to pull; `candidatesWrite` only if
    notes are sent back. Nothing broader.
  - **Pull**, on a schedule or by hand: `ashby.py pull --dry-run` says what
    would come in, `ashby.py pull` brings it (CV, the form's "why" question,
    the LinkedIn link). On a computer, `[ashby] auto_pull = true` makes
    `daily.py` (the launcher and the evening task) do it by itself. Ashby's
    sync tokens expire after 14 days, so run it at
    least weekly; an expired token restarts a full sync, which is safe because
    nobody is added twice.
  - **Webhook**, optional, for applications to appear at once: point Ashby's
    `applicationSubmit` webhook at `https://<desk>/webhook/ashby` with a secret
    token, and set the same value in `ASHBY_WEBHOOK_SECRET`. The desk refuses
    every webhook while that variable is empty. The webhook path is the only
    one the sign-in proxy must let through unauthenticated — its signature is
    its login.
  - **Push** is a person's act: `ashby.py push` prints the notes,
    `ashby.py push --send --as <voter>` writes them. Only classes the voters
    agreed on, one note per class, private and without notification by
    default (`desk.toml` `[ashby]`).
  - **Never run against a live account.** Built from Ashby's published API
    reference; `ashby.py assumes` lists every shape it relies on, including
    the one the reference does not spell out. Run `pull --dry-run` first.
  - **PDF CVs are attached and read.** The screener reads what is drawn on the
    page; hidden text is left out and reported. A PDF with no text layer (a
    scan without OCR) is refused, and counts for nothing in the AI guess.
  - `runs/desk/ashby.json` holds real Ashby ids; it is git-ignored.

## Adapting it

| to change | edit |
|---|---|
| who votes, class names, blind voting, what settles a class | `desk.toml` `[team]`, `[votes]` |
| recap day, "waiting" and "no reply" thresholds | `desk.toml` `[recap]` |
| every word of every mail | `desk.toml` `[mail.templates]` |
| what a role is measured on, and how much each thing counts | `configure.py --posting <id> --by <you>` |
| how a partial answer is credited | the same command; it shows what each choice does to the people already scored |
| what comes in from Ashby, what goes back, how private | `desk.toml` `[ashby]` |
| the look of the desk | `console/desk_web.py` — one file, standard library, no build step |
| your logo in the header | `desk.toml` `[team] logo` |
| drafts written by AI (off by default) | `desk.toml` `[mail] ai_drafts` |

**Your logo.** Put your logo in local-assets/; it is never committed
(`local-assets/` is in `.gitignore`). Then set `[team] logo = "local-assets/logo.png"`.
PNG, JPEG, WebP or SVG, 200 KB at most, recognised by its content rather than its
extension. It is inlined in the page as an image (an SVG only ever in an `<img>`,
where it runs no script); anything else is ignored, the plain square stays, and
Set up says why.

**Drafts written by AI.** Off by default: no part of the desk needs a model. With
`[mail] ai_drafts = true`, a decided application (Ready to contact, Ready to answer
no, Talk later) shows "Draft with AI" beside the template's draft. One press asks
the model this repository uses (`NBH_BACKEND`; by default your Claude Code
subscription, at no extra cost) for a draft written from the outcome, the role, the
first name, the comments of those who voted, the candidate's own sentences and your
template. Limits: the draft is only a draft, shown to whoever asked and sent by
nobody; numbers, links and names that are not in those sources are listed above it
to check, but the check reads words, not meaning, so a true fact can still be
misread; a draft that touches a protected subject (age, family, health, origin...)
is refused whole; who asked, when and for whom is kept in
`runs/desk/ai_drafts.jsonl` (not the text). The candidate's sentences and the
partners' comments go to the model: with the default backend, to your own Claude
account.
