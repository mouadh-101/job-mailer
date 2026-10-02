"""Gmail SMTP sender (smtp.gmail.com:587, STARTTLS)."""
from __future__ import annotations

import mimetypes
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from html import unescape
from pathlib import Path

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
DEFAULT_TIMEOUT = 30
EMAIL_PATTERN = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
RATE_LIMIT_CODES = {421, 450, 451, 452}
RATE_LIMIT_WORDS = ("5.4.5", "rate limit", "rate-limit", "quota", "too many", "limit exceeded")


class MailerError(Exception):
    """A sending failure.

    fatal: the campaign must stop (bad credentials, rate limit, ...).
    recipient_specific: only this address is the problem, not the account.
    """

    def __init__(self, message: str, fatal: bool = False, recipient_specific: bool = False) -> None:
        super().__init__(message)
        self.fatal = fatal
        self.recipient_specific = recipient_specific


def is_valid_email(address: str) -> bool:
    address = address.strip()
    return bool(address) and len(address) <= 254 and EMAIL_PATTERN.match(address) is not None


def html_to_text(html_body: str) -> str:
    """Tiny HTML -> plain text conversion for the text/plain alternative part."""
    text = re.sub(r"<head>.*?</head>", "", html_body, flags=re.S | re.I)
    text = re.sub(r"</p>", "\n\n", text, flags=re.I)
    text = re.sub(r"<br\s*/?>|</li>", "\n", text, flags=re.I)
    text = re.sub(r"<li[^>]*>", "- ", text, flags=re.I)
    text = unescape(re.sub(r"<[^>]+>", "", text))
    text = "\n".join(line.strip() for line in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


class GmailMailer:
    def __init__(self, address: str, app_password: str, timeout: int = DEFAULT_TIMEOUT) -> None:
        self._address = address
        self._app_password = app_password
        self._timeout = timeout

    def send_email(self, recipient: str, subject: str, html_body: str, attachment_path: str) -> None:
        message = self._build_message(recipient, subject, html_body, Path(attachment_path))
        self._deliver(message)

    def _build_message(self, recipient: str, subject: str, html_body: str, attachment: Path) -> EmailMessage:
        message = EmailMessage()
        message["From"] = self._address
        message["To"] = recipient
        message["Subject"] = subject
        message["Date"] = formatdate(localtime=True)
        message["Message-ID"] = make_msgid(domain=self._address.rsplit("@", 1)[-1])
        message.set_content(html_to_text(html_body))
        message.add_alternative(html_body, subtype="html")

        try:
            data = attachment.read_bytes()
        except OSError as exc:
            raise MailerError(f"Cannot read CV file {attachment}: {exc.strerror or exc}", fatal=True) from None
        mime_type, _ = mimetypes.guess_type(attachment.name)
        maintype, subtype = (mime_type or "application/octet-stream").split("/", 1)
        message.add_attachment(data, maintype=maintype, subtype=subtype, filename=attachment.name)
        return message

    def _deliver(self, message: EmailMessage) -> None:
        try:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=self._timeout) as smtp:
                smtp.ehlo()
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
                smtp.login(self._address, self._app_password)
                smtp.send_message(message)
        except smtplib.SMTPAuthenticationError:
            raise MailerError(
                "Gmail authentication failed: check GMAIL_ADDRESS and GMAIL_APP_PASSWORD "
                "(an App Password is required, not your normal password)",
                fatal=True,
            ) from None
        except smtplib.SMTPRecipientsRefused:
            raise MailerError("Recipient address refused by the server", recipient_specific=True) from None
        except smtplib.SMTPSenderRefused as exc:
            raise MailerError(self._scrub(f"Sender refused by Gmail (code {exc.smtp_code})"), fatal=True) from None
        except smtplib.SMTPResponseException as exc:
            detail = exc.smtp_error.decode(errors="replace") if isinstance(exc.smtp_error, bytes) else str(exc.smtp_error)
            raise MailerError(
                self._scrub(f"SMTP error {exc.smtp_code}: {detail}"),
                fatal=self._is_rate_limit(exc.smtp_code, detail),
            ) from None
        except (smtplib.SMTPException, OSError) as exc:
            raise MailerError(self._scrub(f"Connection/SMTP error: {type(exc).__name__}: {exc}")) from None

    @staticmethod
    def _is_rate_limit(code: int, detail: str) -> bool:
        lowered = detail.lower()
        return code in RATE_LIMIT_CODES or any(word in lowered for word in RATE_LIMIT_WORDS)

    def _scrub(self, text: str) -> str:
        """Make sure credentials can never leak through an error message."""
        for secret in (self._app_password,):
            if secret:
                text = text.replace(secret, "***")
        return text
