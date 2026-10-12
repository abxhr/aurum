# Reading statement e-mails (Gmail)

Aurum can read the statements your banks e-mail you. It only **reads**; it cannot send, delete or change anything.

## The easy way: an app password (no Google Cloud needed)

1. Google Account → **Security** → turn on **2-Step Verification**.
2. Google Account → Security → **App passwords** → create one named "Aurum" and copy the 16-character code.
3. In Aurum: **Settings → Gmail Email Sync** → enter your address and the code → **Connect with App Password**.

It never expires. Then tell Aurum which e-mails are statements:

* **Cards:** add each card (Cards → Add card). Give the bank name, the statement day and, if your issuer sends from a particular address, its sender address and a word from the subject. If the PDF is password-protected, add the password on the card (stored encrypted).
* **Bank accounts:** Settings → **Bank accounts** → add the sender address (and optionally a subject word) of each account's statement e-mail.
* **Payslips:** Income → set the sender and PDF password of your payroll e-mails.

Press **Email sync** (Cards) or **Read statements now** (Bank) to import. Re-running is safe: duplicates are ignored.

No e-mail, or a bank whose PDF cannot be read? **Import a CSV** instead (Cards → Import CSV, Bank → Import CSV). Almost every bank can export one.

## The other way: Google sign-in (OAuth)

Only needed if you prefer it to an app password.

1. <https://console.cloud.google.com> → create a project → **APIs & Services → Library** → enable the **Gmail API**.
2. **Credentials → Create credentials → OAuth client ID** (configure the consent screen first if asked: *External*, add your own address as a test user). Application type **Web application**; under *Authorized redirect URIs* add exactly:
   ```
   http://localhost:5000/api/email/callback
   ```
3. Download the JSON, rename it `credentials.json` and put it in the project folder (next to `run.bat`). It is git-ignored; never commit it.
4. Aurum → Settings → Gmail Email Sync → **Connect Gmail**, sign in and press Allow.

Google keeps apps in *Testing* mode for 7 days at a time. To avoid reconnecting weekly, publish the consent screen (APIs & Services → OAuth consent screen → *Publish app*; no verification is needed for personal use) and reconnect once.

## Troubleshooting

| Message | Fix |
|---|---|
| "Gmail libraries not found" | `pip install -r requirements.txt` |
| "credentials.json not found" | It must sit in the project folder, not in `backend/` |
| `redirect_uri_mismatch` | The URI in Google Cloud must be exactly `http://localhost:5000/api/email/callback` (use your port if you changed it) |
| Nothing imported | Check the card's sender and subject words against a real statement e-mail; the amount and due date must appear in the e-mail text or the PDF. Otherwise import a CSV. |
| "Access blocked" | The consent screen is in Testing: add your address as a test user, or use an app password |

Details of how statements are read, and how to add a reader for an unusual format, are in the [docs](https://abxhr.github.io/aurum/banks.html).
