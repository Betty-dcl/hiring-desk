# The desk on a server (an option)

The desk runs on one laptop, for free, with nothing to deploy (`install.py`).
This folder is for the day the person who runs hiring wants to open it from
anywhere, or to share it with partners: one small server, the company's Google
sign-in in front, HTTPS, the data on the server's disk, a backup every day.

**Not run yet.** These files follow the published documentation of each piece
(Docker Compose, Caddy, oauth2-proxy) and the desk's own header mode, which
the tests cover. The first deployment is to be done with someone who can read
a server log, on invented data first.

## What it costs

- **The server**: the only cost. One small virtual machine (1 vCPU, 1 GB of
  memory is plenty). Several clouds have a free tier or charge a few euros a
  month; their terms change, check them on the day.
- **Google sign-in**: free, with the Google accounts the company already has.
- **HTTPS certificate**: free (Caddy obtains it from Let's Encrypt).
- **The desk itself**: no AI calls, no licence.

## What it needs

1. A server with Docker, and a name for it: a DNS record
   `desk.yourcompany.example` pointing at its address, ports 80 and 443 open.
2. A Google OAuth client: Google Cloud console, APIs & Services, Credentials,
   *Create OAuth client ID*, type *Web application*, authorised redirect URI
   `https://desk.yourcompany.example/oauth2/callback`.

## Steps

From this folder, on the server:

```sh
cp .env.example .env                # fill in every line
cp emails.txt.example emails.txt    # the Google accounts let in, one per line
cp ../desk.toml desk.toml           # the settings you use on the laptop
# then replace the [access] section of desk.toml with desk-access.toml,
# with your domain and the same emails mapped to the names under [team] voters
docker compose up -d --build
```

Open `https://desk.yourcompany.example`: Google asks who you are, and only the
accounts in `emails.txt` get in.

## Moving the laptop's data to the server

On the laptop: `python backup.py --to <folder>`. On the server, unzip that
backup's `runs/` folder into `deploy/data/` before the first start. The desk
reads it as it was.

## Why it is safe, and what is left to you

- Only Caddy listens on the internet. The desk has no published port: it is
  reached from oauth2-proxy alone, on a private network the desk trusts by
  address (`trusted_proxies`), and it refuses every other connection before
  reading who someone claims to be.
- oauth2-proxy overwrites `X-Forwarded-Email` on every request: a browser
  cannot pretend to be someone else.
- The Ashby webhook path skips the sign-in and is checked by its signature
  instead; with `ASHBY_WEBHOOK_SECRET` empty, every webhook is refused.
- Left to you: keeping the server updated, copying `deploy/backups/` off the
  server (another disk, a company drive), and removing people from
  `emails.txt` when they leave. The full threat model is in
  [../docs/SECURITY.md](../docs/SECURITY.md).
