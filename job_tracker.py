import hashlib
import json
import os
import random
import re
import smtplib
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.getenv("JOB_SOURCE_CONFIG", ROOT / "config" / "sources.json"))
STATE_PATH = Path(os.getenv("SEEN_JOBS_PATH", ROOT / "data" / "seen_jobs.json"))

ROLE_TERMS = [
    "clinical research associate",
    "cra",
    "regulatory affairs",
    "regualtory affairs",
    "clinical data management",
    "clinical data associate",
    "clinical trial",
    "clinical operations",
    "clinical coordinator",
    "pharmacovigilance",
    "drug safety",
    "regulated",
    "regualted",
    "regulatory",
]

LOCATION_TERMS = ["bangalore", "bengaluru", "hyderabad"]

EXPERIENCE_TERMS = [
    "fresher",
    "freshers",
    "entry level",
    "entry-level",
    "0-1",
    "0 - 1",
    "0 to 1",
    "0 year",
    "1 year",
    "0-2",
    "graduate",
    "trainee",
    "associate",
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
]


@dataclass(frozen=True)
class Job:
    title: str
    company: str
    location: str
    date_posted: str
    apply_link: str
    source: str

    @property
    def fingerprint(self) -> str:
        key = "|".join(
            [
                normalize(self.title),
                normalize(self.company),
                normalize(self.location),
                self.apply_link.strip().lower(),
            ]
        )
        return hashlib.sha256(key.encode("utf-8")).hexdigest()


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().lower()


def log(message: str) -> None:
    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {message}")


def env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def ensure_active() -> None:
    if not env_bool("AUTOMATION_ACTIVE", True):
        log("AUTOMATION_ACTIVE is false; exiting without scraping or emailing.")
        raise SystemExit(0)

    control_url = os.getenv("STOP_CONTROL_URL")
    if not control_url:
        return

    try:
        payload = fetch_json(control_url)
        if not payload.get("automation_active", True):
            log("Remote control file disabled automation; exiting.")
            raise SystemExit(0)
    except SystemExit:
        raise
    except Exception as exc:
        log(f"Could not read STOP_CONTROL_URL; continuing. Error: {exc}")


def human_delay() -> None:
    seconds = random.uniform(2, 5)
    time.sleep(seconds)


def request_html(url: str) -> str:
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9",
    }
    human_delay()
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.text


def fetch_json(url: str) -> dict:
    headers = {"User-Agent": random.choice(USER_AGENTS), "Accept": "application/json"}
    response = requests.get(url, headers=headers, timeout=20)
    response.raise_for_status()
    return response.json()


def maybe_playwright_html(url: str) -> str | None:
    if not env_bool("ENABLE_PLAYWRIGHT_FALLBACK", True):
        return None

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        log(f"Playwright fallback unavailable: {exc}")
        return None

    log(f"Using Playwright fallback for {url}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=random.choice(USER_AGENTS))
        page.goto(url, wait_until="networkidle", timeout=60000)
        html = page.content()
        browser.close()
        return html


def soup_for(url: str) -> BeautifulSoup:
    html = request_html(url)
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    if len(text) < 250:
        rendered = maybe_playwright_html(url)
        if rendered:
            html = rendered
    return BeautifulSoup(html, "html.parser")


def text_matches(text: str, terms: Iterable[str]) -> bool:
    normalized = normalize(text)
    return any(term in normalized for term in terms)


def job_matches(job: Job) -> bool:
    searchable = " ".join([job.title, job.company, job.location, job.date_posted, job.source])
    role_ok = text_matches(searchable, ROLE_TERMS)
    location_ok = text_matches(searchable, LOCATION_TERMS)
    experience_ok = text_matches(searchable, EXPERIENCE_TERMS)
    return role_ok and location_ok and experience_ok


def clean_text(node) -> str:
    if not node:
        return ""
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()


def extract_by_selector(card, selector: str | None) -> str:
    if not selector:
        return ""
    return clean_text(card.select_one(selector))


def extract_link(card, base_url: str, selector: str | None = None) -> str:
    link_node = card.select_one(selector) if selector else card.select_one("a[href]")
    if not link_node or not link_node.get("href"):
        return base_url
    return urljoin(base_url, link_node["href"])


def infer_company(card_text: str, fallback: str) -> str:
    patterns = [
        r"company\s*[:\-]\s*([A-Za-z0-9&.,'() /-]{2,80})",
        r"organization\s*[:\-]\s*([A-Za-z0-9&.,'() /-]{2,80})",
    ]
    for pattern in patterns:
        match = re.search(pattern, card_text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return fallback


def infer_location(card_text: str) -> str:
    found = [term.title() for term in LOCATION_TERMS if term in normalize(card_text)]
    return ", ".join(dict.fromkeys(found)) or "Not specified"


def infer_date(card_text: str) -> str:
    patterns = [
        r"\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{4}\b",
        r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
        r"\b(?:today|yesterday|\d+\s+days?\s+ago|\d+\s+hours?\s+ago)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, card_text, flags=re.IGNORECASE)
        if match:
            return match.group(0)
    return "Not specified"


def generic_jobs_from_page(source: dict) -> list[Job]:
    url = source["url"]
    soup = soup_for(url)
    source_name = source.get("name", url)
    selectors = source.get("selectors", {})
    card_selector = selectors.get("card") or "article, .job, .job-listing, .post, .card, li"

    jobs: list[Job] = []
    for card in soup.select(card_selector):
        card_text = clean_text(card)
        if not card_text or len(card_text) < 20:
            continue

        title = (
            extract_by_selector(card, selectors.get("title"))
            or clean_text(card.select_one("h1, h2, h3, h4, a[href]"))
        )
        if not title:
            continue

        company = extract_by_selector(card, selectors.get("company")) or infer_company(
            card_text, source.get("default_company", source_name)
        )
        location = extract_by_selector(card, selectors.get("location")) or infer_location(card_text)
        date_posted = extract_by_selector(card, selectors.get("date")) or infer_date(card_text)
        apply_link = extract_link(card, url, selectors.get("link"))

        job = Job(
            title=title[:180],
            company=company[:120],
            location=location[:120],
            date_posted=date_posted[:80],
            apply_link=apply_link,
            source=source_name,
        )
        if job_matches(job) or text_matches(card_text, ROLE_TERMS) and text_matches(card_text, LOCATION_TERMS):
            jobs.append(job)

    return jobs


def load_sources() -> list[dict]:
    with CONFIG_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)["sources"]


def load_seen() -> set[str]:
    if not STATE_PATH.exists():
        return set()
    try:
        with STATE_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return set(data.get("seen_fingerprints", []))
    except Exception as exc:
        log(f"Could not load seen state; treating as empty. Error: {exc}")
        return set()


def save_seen(fingerprints: set[str]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seen_fingerprints": sorted(fingerprints),
    }
    with STATE_PATH.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)


def dedupe_jobs(jobs: Iterable[Job]) -> list[Job]:
    by_id: dict[str, Job] = {}
    for job in jobs:
        by_id.setdefault(job.fingerprint, job)
    return list(by_id.values())


def collect_jobs() -> list[Job]:
    all_jobs: list[Job] = []
    for source in load_sources():
        if not source.get("enabled", True):
            continue
        try:
            log(f"Scraping {source.get('name', source['url'])}")
            all_jobs.extend(generic_jobs_from_page(source))
        except Exception as exc:
            log(f"Source failed: {source.get('name', source.get('url'))}; error={exc}")
    return dedupe_jobs(all_jobs)


def render_email(jobs: list[Job]) -> str:
    stop_url = os.getenv("STOP_BUTTON_URL", "#")
    generated_at = datetime.now().strftime("%d %b %Y, %I:%M %p")
    rows = []
    for job in jobs:
        rows.append(
            f"""
            <tr>
              <td style="padding:14px;border-bottom:1px solid #e5e7eb;">
                <div style="font-size:16px;font-weight:700;color:#111827;">{escape(job.title)}</div>
                <div style="margin-top:4px;color:#374151;">{escape(job.company)} · {escape(job.location)}</div>
                <div style="margin-top:4px;color:#6b7280;font-size:13px;">Posted: {escape(job.date_posted)} · Source: {escape(job.source)}</div>
                <a href="{escape(job.apply_link)}" style="display:inline-block;margin-top:10px;color:#0f766e;font-weight:700;">Apply / View posting</a>
              </td>
            </tr>
            """
        )

    body = "".join(rows) or """
        <tr><td style="padding:18px;color:#374151;">No new matching postings were found in this run.</td></tr>
    """

    return f"""<!doctype html>
<html>
  <body style="margin:0;background:#f3f4f6;font-family:Arial,Helvetica,sans-serif;">
    <div style="max-width:760px;margin:0 auto;padding:24px;">
      <div style="background:#ffffff;border:1px solid #e5e7eb;border-radius:8px;overflow:hidden;">
        <div style="padding:22px;background:#0f172a;color:#ffffff;">
          <h1 style="margin:0;font-size:22px;">Clinical Jobs Digest</h1>
          <p style="margin:8px 0 0;color:#cbd5e1;">Fresh entry-level postings for Bangalore and Hyderabad · {escape(generated_at)}</p>
        </div>
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;">
          {body}
        </table>
        <div style="padding:18px;background:#f9fafb;border-top:1px solid #e5e7eb;">
          <a href="{escape(stop_url)}" style="display:inline-block;background:#dc2626;color:#ffffff;text-decoration:none;padding:12px 18px;border-radius:6px;font-weight:700;">
            Stop Automation
          </a>
          <p style="margin:10px 0 0;color:#6b7280;font-size:12px;">The stop button disables future scheduled runs through the configured serverless endpoint.</p>
        </div>
      </div>
    </div>
  </body>
</html>"""


def send_email(jobs: list[Job]) -> None:
    sender = os.environ["SENDER_EMAIL"]
    password = os.environ["SENDER_APP_PASSWORD"]
    receiver = os.environ["RECEIVER_EMAIL"]
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))

    subject_date = datetime.now().strftime("%d %b %Y")
    message = MIMEMultipart("alternative")
    message["Subject"] = f"Clinical Jobs Digest: {len(jobs)} new posting(s) - {subject_date}"
    message["From"] = sender
    message["To"] = receiver
    message.attach(MIMEText(render_email(jobs), "html"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(sender, password)
        server.sendmail(sender, [receiver], message.as_string())


def main() -> None:
    ensure_active()
    seen = load_seen()
    jobs = collect_jobs()
    new_jobs = [job for job in jobs if job.fingerprint not in seen]
    limit = int(os.getenv("MAX_JOBS_PER_EMAIL", "25"))
    new_jobs = new_jobs[:limit]

    log(f"Found {len(jobs)} matching job(s); {len(new_jobs)} new.")
    if new_jobs or env_bool("SEND_EMPTY_DIGEST", False):
        send_email(new_jobs)
        log("Email sent.")
    else:
        log("No new jobs; email skipped.")

    seen.update(job.fingerprint for job in jobs)
    save_seen(seen)

    output_path = ROOT / "latest_jobs.json"
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump([asdict(job) for job in new_jobs], fh, indent=2, ensure_ascii=False)
    log(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
