# Clinical Job Tracker Automation

This project scrapes fresh entry-level clinical and regulatory job postings for Bangalore and Hyderabad, emails an HTML digest through SMTP, and includes a stop button that disables future scheduled GitHub Actions runs.

## What It Searches

- Roles: Clinical Research Associate, Regulatory Affairs, Clinical Data Management, clinical operations, pharmacovigilance, drug safety, regulated/regulatory roles.
- Locations: Bangalore/Bengaluru and Hyderabad.
- Experience: fresher, entry-level, 0-1 years, trainee, graduate, associate.
- Sources: PharmaBharat plus configurable official company career pages in `config/sources.json`.

## Files

- `job_tracker.py` - scraper, filter, dedupe state, and SMTP email sender.
- `config/sources.json` - source URLs and optional CSS selectors.
- `.github/workflows/job-tracker.yml` - twice-daily GitHub Actions schedule.
- `api/stop.py` - Vercel-compatible serverless stop endpoint.
- `requirements.txt` - Python dependencies.

## GitHub Repository Setup

1. Create a private GitHub repository.
2. Upload these files to the repository root.
3. In GitHub, go to `Settings -> Secrets and variables -> Actions`.
4. Add these Repository Secrets:

| Secret name | Value |
| --- | --- |
| `SENDER_EMAIL` | Gmail or SMTP sender email address |
| `SENDER_APP_PASSWORD` | App Password, not your normal email password |
| `RECEIVER_EMAIL` | `punithalahari187@gmail.com` |
| `STOP_BUTTON_URL` | Your deployed stop endpoint URL, for example `https://your-app.vercel.app/api/stop?token=YOUR_RANDOM_TOKEN` |

5. Add this Repository Variable:

| Variable name | Value |
| --- | --- |
| `AUTOMATION_ACTIVE` | `true` |

The workflow runs at `08:00 IST` and `18:00 IST` using this UTC cron:

```yaml
- cron: "30 2,12 * * *"
```

## Gmail App Password

For Gmail, enable 2-Step Verification, then create an App Password:

`Google Account -> Security -> 2-Step Verification -> App passwords`

Use that generated password as `SENDER_APP_PASSWORD`.

## Stop Button Setup With Vercel

The stop button cannot securely call GitHub directly from an email client because GitHub API calls need authenticated headers. The included `api/stop.py` gives you a tiny serverless endpoint instead.

1. Create a Vercel project from the same GitHub repository.
2. Add these Vercel environment variables:

| Variable | Value |
| --- | --- |
| `STOP_TOKEN` | A long random string, for example from a password manager |
| `GITHUB_REPOSITORY` | `owner/repository-name` |
| `GITHUB_TOKEN` | A fine-grained GitHub token with Actions variables read/write access for this repo |

3. Deploy the Vercel project.
4. Set the GitHub Actions secret `STOP_BUTTON_URL` to:

```text
https://your-vercel-project.vercel.app/api/stop?token=YOUR_STOP_TOKEN
```

When clicked, the endpoint sets the GitHub Actions repository variable `AUTOMATION_ACTIVE=false`. The scheduled workflow then skips future runs.

To restart later, change `AUTOMATION_ACTIVE` back to `true` in GitHub repository variables.

## Local Test

Create a `.env` file locally if you want, or set environment variables in your shell:

```powershell
$env:SENDER_EMAIL="sender@gmail.com"
$env:SENDER_APP_PASSWORD="your-app-password"
$env:RECEIVER_EMAIL="punithalahari187@gmail.com"
$env:STOP_BUTTON_URL="https://your-vercel-project.vercel.app/api/stop?token=YOUR_STOP_TOKEN"
python -m pip install -r requirements.txt
python -m playwright install chromium
python job_tracker.py
```

## Tuning Sources

Add or edit sources in `config/sources.json`. For stable websites, add CSS selectors:

```json
{
  "name": "Example Careers",
  "url": "https://example.com/careers?query=clinical",
  "enabled": true,
  "default_company": "Example",
  "selectors": {
    "card": ".job-card",
    "title": ".job-title",
    "company": ".company",
    "location": ".location",
    "date": ".posted-date",
    "link": "a.apply"
  }
}
```

If a page loads listings through JavaScript, the script automatically attempts Playwright in headless Chromium.
