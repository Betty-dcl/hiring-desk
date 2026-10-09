# Outlook: "they wrote back"

An option, off by default. When it is on, a **Check Outlook for replies** button
on the Interested tab asks the mailbox of the person who runs hiring one
question per application in *Contacted*: did a mail come from this address
since we wrote? A row that got one says **They wrote back on 6 Oct · Open in
Outlook**. The person reads it and ticks **They replied: OK**, or does not: a
reply can be a no, so the desk never ticks it.

Code: `outlook.py` (the connector), `console/outlook_web.py` (the page),
`tests/test_outlook.py` (a fake Microsoft; nothing here has run against a live
Microsoft 365 account yet).

## Data protection: how it is built, and what is left to the company

This is a description of the design, not legal advice. The company's data
protection officer or counsel should confirm it before it is turned on.

| Question | How the desk answers it |
|---|---|
| What can it read? | `Mail.ReadBasic`, *delegated*: the signed-in person's own mailbox, **without the body, the preview or the attachments**. It cannot read what a candidate wrote. It cannot read anyone else's mailbox. |
| What does it ask for? | Only the people in *Contacted* with an email on file, and only mails from that address since the day they were written to. Two fields per mail (time, link), one mail at most. |
| What does it keep? | Per application waited on: the time of the latest reply and the Outlook link. Nothing in the application's history. A hint for someone no longer waited on is dropped at the next check. **Disconnect and forget** deletes the connection and every hint. |
| Does it decide anything? | No. It shows a hint; a person reads the mail and ticks. No automated decision about a candidate (GDPR Art. 22). |
| Who else sees the data? | Nobody new. The question goes from the desk to Microsoft, which already hosts the mailbox under the company's agreement with it. No AI model is involved. |
| Who connects the mailbox? | The person whose mailbox it is, with their own sign-in (device code): the desk never sees a password. It is not a way for the company to look into an employee's mailbox. |
| When does it run? | When someone presses the button, or runs `outlook.py sync`. Nothing on a timer. |
| Where is the key kept? | `runs/desk/outlook-token.json`, on the machine the desk runs on, readable by its owner only, never in the repository (`.gitignore`). |

Left to the company, before turning it on:

1. **Tell the candidates.** The privacy notice they get when they apply (GDPR
   Art. 13) should say that replies to the hiring team's messages are noted in
   the hiring tool (that a reply came, and when). One sentence is enough.
2. **Write it down.** Add it to the record of processing activities (Art. 30),
   under recruitment: purpose "follow up applications", data "date of the
   candidate's reply", basis the one already used for recruitment
   (steps before a contract, or legitimate interest).
3. **The person's own mailbox.** In Spain, an employer's access to an
   employee's work devices and mail is framed by LOPDGDD art. 87. Here the
   employee connects their own mailbox, the desk sees only mails from
   candidates, and nothing is used to watch the employee (no count of how
   fast they answer is made from the mailbox). Saying so in writing to the
   person who connects it is good practice.

## Setting it up: once, by a Microsoft admin (about five minutes)

1. Microsoft Entra admin center → **App registrations** → **New registration**.
   Name: `Hiring desk`. Accounts: *this organizational directory only*.
   No redirect URI.
2. **Authentication** → *Allow public client flows*: **Yes** (the sign-in with
   a code).
3. **API permissions** → Add → Microsoft Graph → *Delegated* →
   `Mail.ReadBasic` (and `offline_access`). If the tenant does not let people
   consent for themselves, **Grant admin consent**. Nothing else: not
   `Mail.Read`, no *Application* permission.
4. No client secret is needed. Copy the *Application (client) ID* and the
   *Directory (tenant) ID* into `desk.toml`:

   ```toml
   [outlook]
   enabled = true          # or turn it on in House rules
   client_id = "…"
   tenant = "…"
   ```

Then the person who runs hiring opens **House rules → Outlook → Connect my
Outlook**, signs in on Microsoft's page with the code shown, and presses **I
have signed in**. To stop: **Disconnect and forget** on the desk, and remove
the desk under *My Apps* (or the admin removes the registration).
