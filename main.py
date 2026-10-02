"""CLI: python main.py test [--email X]  |  python main.py bulk -n N"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from config import COMPANIES_CSV, Config, ConfigError, load_config, validate_cv_path
from csv_manager import CsvError, Row, load_rows, mark_failed, mark_sent, pending_rows, save_rows, sent_addresses
from email_generator import build_email_from_config
from logger import PLAIN, get_logger, setup_logger
from mailer import GmailMailer, MailerError, is_valid_email

MAX_CONSECUTIVE_FAILURES = 3
TEST_COMPANY_NAME = "Test Company"

log = get_logger()


@dataclass
class CampaignResult:
    requested: int
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    stopped_reason: str | None = None
    interrupted: bool = False


# ---------------------------------------------------------------- test command

def run_test(config: Config, mailer: GmailMailer, recipient: str) -> bool:
    """Send exactly one email to `recipient`. Never touches the CSV."""
    validate_cv_path(config.cv_path)
    if not is_valid_email(recipient):
        raise ConfigError(f"Invalid test email address: {recipient!r}")
    subject, html_body = build_email_from_config(TEST_COMPANY_NAME, config)
    try:
        mailer.send_email(recipient, subject, html_body, str(config.cv_path))
    except MailerError as exc:
        log.error("Test FAILED: %s", exc)
        return False
    log.info("Test email sent successfully to %s", recipient)
    return True


# ---------------------------------------------------------------- bulk command

def run_bulk(
    config: Config,
    mailer: GmailMailer,
    csv_path: Path,
    limit: int,
    sleep: Callable[[float], None] = time.sleep,
) -> CampaignResult:
    validate_cv_path(config.cv_path)
    rows = load_rows(csv_path)
    pending = pending_rows(rows)
    log.info("Loaded %d companies", len(rows))
    log.info("%d unsent companies available", len(pending))

    batch = pending[:limit]
    result = CampaignResult(requested=limit)
    if not batch:
        log.info("No unsent companies to process")
    else:
        log.info("Sending %d emails", len(batch))
        try:
            _process_batch(config, mailer, csv_path, rows, batch, result, sleep)
        except KeyboardInterrupt:
            result.interrupted = True
            log.warning("Interrupted by user. CSV updates already made are preserved.")
    _log_summary(result)
    return result


def _process_batch(
    config: Config,
    mailer: GmailMailer,
    csv_path: Path,
    rows: list[Row],
    batch: list[Row],
    result: CampaignResult,
    sleep: Callable[[float], None],
) -> None:
    already_sent = sent_addresses(rows)
    consecutive_failures = 0
    attempted_send = False

    for position, row in enumerate(batch, start=1):
        address = row["email"]
        label = f"[{position}/{len(batch)}] {row['company_name'] or '(no company name)'} <{address}>"

        if address.lower() in already_sent:
            log.info(label, extra=PLAIN)
            log.warning("Address already sent earlier in this file, skipping")
            result.skipped += 1
            continue

        if not is_valid_email(address):
            log.info(label, extra=PLAIN)
            log.error("Invalid email address")
            mark_failed(row, "Invalid email address")
            result.failed += 1
            if not _save(csv_path, rows, result):
                return
            log.info("CSV updated with error")
            continue

        if attempted_send:
            _wait(config.send_delay_seconds, sleep)
        attempted_send = True
        log.info(label, extra=PLAIN)

        error = _send_one(config, mailer, row)
        if error is None:
            mark_sent(row)
            already_sent.add(address.lower())
            result.sent += 1
            consecutive_failures = 0
            log.info("Email sent successfully")
        else:
            mark_failed(row, str(error))
            result.failed += 1
            log.error("SMTP error: %s", error)
            # A refused recipient says nothing about the account's health.
            consecutive_failures = 0 if error.recipient_specific else consecutive_failures + 1

        if not _save(csv_path, rows, result):
            return
        log.info("CSV updated" if error is None else "CSV updated with error")

        if error is not None:
            reason = _stop_reason(error, consecutive_failures)
            if reason:
                result.stopped_reason = reason
                log.error("Stopping campaign: %s", reason)
                return


def _send_one(config: Config, mailer: GmailMailer, row: Row) -> MailerError | None:
    subject, html_body = build_email_from_config(row["company_name"], config)
    try:
        mailer.send_email(row["email"], subject, html_body, str(config.cv_path))
    except MailerError as exc:
        return exc
    return None


def _stop_reason(error: MailerError, consecutive_failures: int) -> str | None:
    if error.fatal:
        return f"serious sending error ({error})"
    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
        return f"{MAX_CONSECUTIVE_FAILURES} consecutive SMTP failures"
    return None


def _save(csv_path: Path, rows: list[Row], result: CampaignResult) -> bool:
    """Persist after every attempt. If we cannot, stop: continuing could cause duplicate sends."""
    try:
        save_rows(csv_path, rows)
    except OSError as exc:
        result.stopped_reason = f"cannot save CSV ({exc.strerror or exc}); close it in other programs"
        log.error("Stopping campaign: %s", result.stopped_reason)
        return False
    return True


def _wait(seconds: int, sleep: Callable[[float], None]) -> None:
    if seconds > 0:
        log.info("Waiting %d seconds...", seconds)
        sleep(seconds)


def _log_summary(result: CampaignResult) -> None:
    title = "Campaign interrupted." if result.interrupted else "Campaign finished."
    lines = [
        "",
        title,
        "",
        f"Requested: {result.requested}",
        f"Sent successfully: {result.sent}",
        f"Failed: {result.failed}",
        f"Skipped: {result.skipped}",
    ]
    if result.stopped_reason:
        lines.append(f"Stopped early: {result.stopped_reason}")
    for line in lines:
        log.info(line, extra=PLAIN)


# ---------------------------------------------------------------- CLI

def positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer") from None
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Send spontaneous application emails through Gmail SMTP.")
    commands = parser.add_subparsers(dest="command", required=True)

    test = commands.add_parser("test", help="send ONE test email (CSV is not modified)")
    test.add_argument("--email", help="override TEST_EMAIL from .env")

    bulk = commands.add_parser("bulk", help="send to at most N unsent companies from data/companies.csv")
    bulk.add_argument("-n", type=positive_int, required=True, help="maximum number of emails to send")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    setup_logger()

    try:
        config = load_config(require_test_email=args.command == "test" and not args.email)
        mailer = GmailMailer(config.gmail_address, config.gmail_app_password)
        if args.command == "test":
            return 0 if run_test(config, mailer, args.email or config.test_email or "") else 1
        result = run_bulk(config, mailer, COMPANIES_CSV, args.n)
    except (ConfigError, CsvError, OSError) as exc:
        get_logger().error("%s", exc)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130

    if result.interrupted:
        return 130
    return 1 if result.stopped_reason else 0


if __name__ == "__main__":
    sys.exit(main())
