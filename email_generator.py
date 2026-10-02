"""Builds the subject and HTML body of the application email."""
from __future__ import annotations

import re
from functools import lru_cache
from html import escape
from pathlib import Path
from string import Template
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config import Config

TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "application.html"
ROLE = "Software Engineer / Full Stack Developer"
SUBJECT = f"Spontaneous Application – {ROLE}"


@lru_cache(maxsize=1)
def _load_template() -> Template:
    return Template(TEMPLATE_PATH.read_text(encoding="utf-8"))


def build_intro(company_name: str) -> str:
    company = company_name.strip()
    if company:
        return (
            "I am writing to express my interest in any current or future opportunities "
            f"as a {ROLE} at <strong>{escape(company)}</strong>."
        )
    return (
        "I am writing to express my interest in any current or future opportunities "
        f"as a {ROLE} within your company."
    )


def _phone_link(phone: str) -> str:
    cleaned = re.sub(r"[^\d+]", "", phone.strip())
    return f"tel:{cleaned}" if cleaned else ""


def _linkedin_label(url: str) -> str:
    return re.sub(r"^https?://", "", url.strip()).rstrip("/")


def build_email(
    company_name: str,
    applicant_name: str,
    applicant_title: str,
    applicant_phone: str,
    applicant_linkedin_url: str,
) -> tuple[str, str]:
    """Return (subject, html_body). All signature fields are required (see Config)."""
    html_body = _load_template().substitute(
        intro=build_intro(company_name),
        applicant_name=escape(applicant_name),
        applicant_title=escape(applicant_title.strip()),
        applicant_phone=escape(applicant_phone.strip()),
        applicant_phone_link=escape(_phone_link(applicant_phone)),
        applicant_linkedin_url=escape(applicant_linkedin_url.strip()),
        applicant_linkedin_label=escape(_linkedin_label(applicant_linkedin_url)),
    )
    return SUBJECT, html_body


def build_email_from_config(company_name: str, config: Config) -> tuple[str, str]:
    """Convenience wrapper so callers don't repeat the 5 signature arguments."""
    return build_email(
        company_name,
        config.applicant_name,
        config.applicant_title,
        config.applicant_phone,
        config.applicant_linkedin_url,
    )
