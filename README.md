# job-mailer

Small CLI for sending spontaneous job-application emails through Gmail SMTP.
**CSV → generate email → send → update CSV.** No database, no web server.

## Installation

```bash
python -m venv .venv
```

Activate it:

- Windows (PowerShell): `.venv\Scripts\Activate.ps1`  (cmd: `.venv\Scripts\activate.bat`)
- Linux / macOS: `source .venv/bin/activate`

Then:

```bash
pip install -r requirements.txt
```

Requires Python 3.11+.

## Gmail setup

1. Enable Google **2-Step Verification** on your account.
2. Create a Gmail **App Password** (Google Account → Security → App passwords).
3. Copy `.env.example` to `.env` and paste the generated app password into `GMAIL_APP_PASSWORD`.

Never put your normal Gmail password in `.env`.

## Configuration (`.env`)

| Variable | Meaning |
|---|---|
| `GMAIL_ADDRESS` | Gmail address used to send (also the From address). |
| `GMAIL_APP_PASSWORD` | The 16-character Gmail app password (spaces are ignored). |
| `CV_PATH` | Absolute path to your CV (PDF). Attached to every email under its real file name. |
| `SEND_DELAY_SECONDS` | Pause between two sends in bulk mode. |
| `TEST_EMAIL` | Recipient of `python main.py test`. |
| `APPLICANT_NAME` | Name in the email signature. |
| `APPLICANT_TITLE` | Job title under the name in the signature. |
| `APPLICANT_PHONE` | Phone in the signature. |
| `APPLICANT_LINKEDIN_URL` | LinkedIn URL in the signature. |

The tool refuses to start if a required variable is missing or still has its example value.

## Test

```bash
python main.py test
python main.py test --email someone@example.com   # override TEST_EMAIL
```

Sends exactly one email (company name "Test Company") with your CV attached. The CSV is never modified.

## Bulk

```bash
python main.py bulk -n 10
```

Sends to **at most 10 unsent rows**. After every single attempt the CSV is saved:

- success → `sent=true`, `sent_at=<timestamp>`, `error` cleared
- failure → `sent=false`, `error=<reason>`

The campaign stops by itself on authentication errors, Gmail rate-limit / quota errors,
or after **3 consecutive SMTP failures**. It does not retry or try to get around Gmail limits.
`Ctrl+C` exits cleanly; everything already saved is kept.

## Resuming

Successful sends are written as `sent=true` + `sent_at=...`, and rows marked `sent=true`
(or an address already sent in another row) are **never sent again**. Just run
`python main.py bulk -n 10` again to continue with the remaining rows. Rows that failed
(`sent=false` + `error`) are retried on the next run.

## Tests

```bash
python -m unittest discover -s tests -v
```

SMTP is mocked; no real email is sent. Logs go to the console and `logs/mailer.log` (no secrets).
