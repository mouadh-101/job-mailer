"""Configuration loading and validation (everything comes from .env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
COMPANIES_CSV = BASE_DIR / "data" / "companies_it.csv"

# Values copied from .env.example that must be replaced by the user.
PLACEHOLDERS = {
    "your-email@gmail.com",
    "your-app-password",
    "/absolute/path/to/your_cv.pdf",
    "your-test-email@example.com",
    "your-full-name",
    "your-job-title",
    "your-phone-number",
    "your-linkedin-url",
}


class ConfigError(Exception):
    """Raised when configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    gmail_address: str
    gmail_app_password: str = field(repr=False)  # never shown in repr/logs
    cv_path: Path
    send_delay_seconds: int
    applicant_name: str
    applicant_title: str
    applicant_phone: str
    applicant_linkedin_url: str
    test_email: str | None = None


def _read(name: str) -> str:
    value = os.getenv(name, "").strip()
    return "" if value in PLACEHOLDERS else value


def _parse_delay(raw: str) -> int:
    raw = raw.strip()
    if not raw:
        raise ConfigError("Missing configuration: SEND_DELAY_SECONDS")
    try:
        delay = int(raw)
    except ValueError:
        raise ConfigError(f"SEND_DELAY_SECONDS must be an integer, got: {raw!r}") from None
    if delay < 0:
        raise ConfigError("SEND_DELAY_SECONDS must be >= 0")
    return delay


def load_config(require_test_email: bool = False) -> Config:
    load_dotenv(ENV_PATH)
    required = [
        "GMAIL_ADDRESS",
        "GMAIL_APP_PASSWORD",
        "CV_PATH",
        "SEND_DELAY_SECONDS",
        "APPLICANT_NAME",
        "APPLICANT_TITLE",
        "APPLICANT_PHONE",
        "APPLICANT_LINKEDIN_URL",
    ]
    if require_test_email:
        required.append("TEST_EMAIL")

    missing = [name for name in required if not _read(name)]
    if missing:
        hint = "" if ENV_PATH.exists() else " (no .env file found: copy .env.example to .env and fill it in)"
        raise ConfigError("Missing or placeholder configuration: " + ", ".join(missing) + hint)

    return Config(
        gmail_address=_read("GMAIL_ADDRESS"),
        gmail_app_password=_read("GMAIL_APP_PASSWORD").replace(" ", ""),
        cv_path=Path(_read("CV_PATH")).expanduser(),
        send_delay_seconds=_parse_delay(_read("SEND_DELAY_SECONDS")),
        applicant_name=_read("APPLICANT_NAME"),
        applicant_title=_read("APPLICANT_TITLE"),
        applicant_phone=_read("APPLICANT_PHONE"),
        applicant_linkedin_url=_read("APPLICANT_LINKEDIN_URL"),
        test_email=_read("TEST_EMAIL") or None,
    )


def validate_cv_path(path: Path) -> None:
    if not path.is_file():
        raise ConfigError(f"CV file not found: {path}")
