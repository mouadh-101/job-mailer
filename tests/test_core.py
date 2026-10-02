"""Basic tests. No real email is ever sent: SMTP is mocked."""
from __future__ import annotations

import hashlib
import logging
import os
import smtplib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402
from config import Config, ConfigError, load_config  # noqa: E402
from csv_manager import FIELDNAMES, load_rows, pending_rows  # noqa: E402
from email_generator import build_email, build_email_from_config  # noqa: E402
from mailer import GmailMailer, MailerError, is_valid_email  # noqa: E402

logging.getLogger("job_mailer").addHandler(logging.NullHandler())
logging.getLogger("job_mailer").propagate = False


class FakeMailer:
    """Records recipients; `errors` is a per-call list of None / exception."""

    def __init__(self, errors: list[Exception | None] | None = None) -> None:
        self.calls: list[str] = []
        self.errors = list(errors or [])

    def send_email(self, recipient, subject, html_body, attachment_path) -> None:
        self.calls.append(recipient)
        error = self.errors.pop(0) if self.errors else None
        if error:
            raise error


class BaseCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.cv = self.dir / "cv.pdf"
        self.cv.write_bytes(b"%PDF-1.4 fake")
        self.csv_path = self.dir / "companies.csv"
        self.config = Config(
            gmail_address="me@gmail.com",
            gmail_app_password="secretpassword1234",
            cv_path=self.cv,
            send_delay_seconds=7,
            applicant_name="Jane Doe",
            applicant_title="Software Engineer",
            applicant_phone="+1 555 123 456",
            applicant_linkedin_url="https://linkedin.com/in/janedoe",
            test_email="test@example.com",
        )

    def write_csv(self, rows: list[tuple[str, str, str]]) -> None:
        lines = [",".join(FIELDNAMES)] + [f"{n},{e},{s},," for n, e, s in rows]
        self.csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def bulk(self, mailer: FakeMailer, limit: int, sleeps: list | None = None) -> main.CampaignResult:
        sleeps = [] if sleeps is None else sleeps
        return main.run_bulk(self.config, mailer, self.csv_path, limit, sleep=sleeps.append)


class EmailGenerationTests(unittest.TestCase):
    def test_empty_company_gives_generic_email(self) -> None:
        subject, html = build_email("", "Jane Doe", "Software Engineer", "+1 555 123 456", "https://linkedin.com/in/janedoe")
        self.assertIn("any current or future opportunities", html)
        self.assertIn("within your company", html)
        self.assertNotIn("to <strong>", html)
        self.assertIn("Spontaneous Application", subject)
        # signature block
        self.assertIn("Jane Doe", html)
        self.assertIn("+1 555 123 456", html)
        self.assertIn("linkedin.com/in/janedoe", html)
        self.assertIn("Dear Hiring Team", html)

    def test_company_name_is_personalized_and_escaped(self) -> None:
        _, html = build_email("ACME & Co", "Jane Doe", "Software Engineer", "+1 555 123 456", "https://linkedin.com/in/janedoe")
        self.assertIn("at <strong>ACME &amp; Co</strong>", html)
        self.assertIn("179 REST APIs", html)
        self.assertIn("ESPRIT", html)

    def test_custom_signature_from_config(self) -> None:
        _, html = build_email(
            "ACME",
            "John Smith",
            "Backend Developer",
            "+1 555 123 456",
            "https://linkedin.com/in/johnsmith",
        )
        self.assertIn("John Smith", html)
        self.assertIn("Backend Developer", html)
        self.assertIn("+1 555 123 456", html)
        self.assertIn("tel:+1555123456", html)
        self.assertIn("linkedin.com/in/johnsmith", html)

    def test_from_config_uses_signature(self) -> None:
        config = Config(
            gmail_address="me@gmail.com",
            gmail_app_password="secretpassword1234",
            cv_path=Path("cv.pdf"),
            send_delay_seconds=7,
            applicant_name="John Smith",
            applicant_title="Backend Developer",
            applicant_phone="+1 555 123 456",
            applicant_linkedin_url="https://linkedin.com/in/johnsmith/",
        )
        _, html = build_email_from_config("ACME", config)
        self.assertIn("Backend Developer", html)
        self.assertIn(">linkedin.com/in/johnsmith</a>", html)


class BulkTests(BaseCase):
    def test_sent_true_rows_are_skipped(self) -> None:
        self.write_csv([("A", "a@x.com", "true"), ("B", "b@x.com", "false")])
        mailer = FakeMailer()
        self.bulk(mailer, 10)
        self.assertEqual(mailer.calls, ["b@x.com"])

    def test_success_sets_sent_and_timestamp(self) -> None:
        self.write_csv([("A", "a@x.com", "false")])
        result = self.bulk(FakeMailer(), 1)
        row = load_rows(self.csv_path)[0]
        self.assertEqual((row["sent"], row["error"]), ("true", ""))
        self.assertRegex(row["sent_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d$")
        self.assertEqual(result.sent, 1)

    def test_failure_keeps_unsent_and_records_error(self) -> None:
        self.write_csv([("A", "a@x.com", "false")])
        result = self.bulk(FakeMailer([MailerError("boom")]), 1)
        row = load_rows(self.csv_path)[0]
        self.assertEqual((row["sent"], row["sent_at"], row["error"]), ("false", "", "boom"))
        self.assertEqual(result.failed, 1)

    def test_limit_caps_processed_rows(self) -> None:
        self.write_csv([(f"C{i}", f"c{i}@x.com", "false") for i in range(8)])
        mailer = FakeMailer()
        self.bulk(mailer, 5)
        self.assertEqual(len(mailer.calls), 5)
        self.assertEqual(len(pending_rows(load_rows(self.csv_path))), 3)

    def test_invalid_email_not_sent_and_error_recorded(self) -> None:
        self.write_csv([("A", "not-an-email", "false"), ("B", "b@x.com", "false")])
        mailer = FakeMailer()
        result = self.bulk(mailer, 5)
        rows = load_rows(self.csv_path)
        self.assertEqual(mailer.calls, ["b@x.com"])
        self.assertEqual((rows[0]["sent"], rows[0]["error"]), ("false", "Invalid email address"))
        self.assertEqual(result.failed, 1)

    def test_missing_cv_prevents_campaign(self) -> None:
        self.write_csv([("A", "a@x.com", "false")])
        self.cv.unlink()
        mailer = FakeMailer()
        with self.assertRaises(ConfigError):
            self.bulk(mailer, 1)
        self.assertEqual(mailer.calls, [])

    def test_delay_between_sends_but_not_after_last(self) -> None:
        self.write_csv([(f"C{i}", f"c{i}@x.com", "false") for i in range(3)])
        sleeps: list = []
        self.bulk(FakeMailer(), 3, sleeps)
        self.assertEqual(sleeps, [7, 7])

    def test_resume_never_resends(self) -> None:
        self.write_csv([(f"C{i}", f"c{i}@x.com", "false") for i in range(5)])
        mailer = FakeMailer()
        self.bulk(mailer, 2)
        self.bulk(mailer, 2)
        self.bulk(mailer, 2)
        self.assertEqual(mailer.calls, [f"c{i}@x.com" for i in range(5)])

    def test_csv_saved_after_each_attempt(self) -> None:
        self.write_csv([(f"C{i}", f"c{i}@x.com", "false") for i in range(3)])

        class CrashingMailer(FakeMailer):
            def send_email(inner, *args):  # noqa: N805
                if len(inner.calls) == 2:
                    raise KeyboardInterrupt
                super().send_email(*args)

        result = self.bulk(CrashingMailer(), 3)
        self.assertTrue(result.interrupted)
        sent = [r["sent"] for r in load_rows(self.csv_path)]
        self.assertEqual(sent, ["true", "true", "false"])

    def test_three_consecutive_failures_stop_campaign(self) -> None:
        self.write_csv([(f"C{i}", f"c{i}@x.com", "false") for i in range(5)])
        mailer = FakeMailer([MailerError("x")] * 3)
        result = self.bulk(mailer, 5)
        self.assertEqual(len(mailer.calls), 3)
        self.assertIn("consecutive", result.stopped_reason or "")

    def test_fatal_error_stops_immediately(self) -> None:
        self.write_csv([(f"C{i}", f"c{i}@x.com", "false") for i in range(3)])
        mailer = FakeMailer([MailerError("auth", fatal=True)])
        result = self.bulk(mailer, 3)
        self.assertEqual(len(mailer.calls), 1)
        self.assertIsNotNone(result.stopped_reason)

    def test_duplicate_address_not_resent(self) -> None:
        self.write_csv([("A", "a@x.com", "true"), ("A2", "A@x.com", "false"), ("B", "b@x.com", "false"), ("B2", "b@x.com", "false")])
        mailer = FakeMailer()
        result = self.bulk(mailer, 10)
        self.assertEqual(mailer.calls, ["b@x.com"])
        self.assertEqual(result.skipped, 1)


class TestCommandTests(BaseCase):
    def test_test_command_sends_one_email_and_leaves_csv_untouched(self) -> None:
        self.write_csv([("A", "a@x.com", "false")])
        before = hashlib.sha256(self.csv_path.read_bytes()).hexdigest()
        fake = FakeMailer()
        with patch.object(main, "load_config", return_value=self.config), \
             patch.object(main, "GmailMailer", return_value=fake):
            code = main.main(["test"])
        self.assertEqual(code, 0)
        self.assertEqual(fake.calls, ["test@example.com"])
        self.assertEqual(hashlib.sha256(self.csv_path.read_bytes()).hexdigest(), before)


class ConfigTests(unittest.TestCase):
    def test_missing_signature_fields_raise(self) -> None:
        env = {
            "GMAIL_ADDRESS": "me@gmail.com",
            "GMAIL_APP_PASSWORD": "secretpassword1234",
            "CV_PATH": "cv.pdf",
            "SEND_DELAY_SECONDS": "7",
        }
        with patch.dict(os.environ, env, clear=True), \
             patch("config.ENV_PATH", Path("does-not-exist.env")):
            with self.assertRaises(ConfigError) as ctx:
                load_config()
        message = str(ctx.exception)
        for name in ("APPLICANT_NAME", "APPLICANT_TITLE", "APPLICANT_PHONE", "APPLICANT_LINKEDIN_URL"):
            self.assertIn(name, message)

    def test_placeholder_values_rejected(self) -> None:
        env = {
            "GMAIL_ADDRESS": "me@gmail.com",
            "GMAIL_APP_PASSWORD": "secretpassword1234",
            "CV_PATH": "cv.pdf",
            "SEND_DELAY_SECONDS": "7",
            "APPLICANT_NAME": "your-full-name",
            "APPLICANT_TITLE": "Software Engineer",
            "APPLICANT_PHONE": "+1 555 123 456",
            "APPLICANT_LINKEDIN_URL": "https://linkedin.com/in/janedoe",
        }
        with patch.dict(os.environ, env, clear=True), \
             patch("config.ENV_PATH", Path("does-not-exist.env")):
            with self.assertRaises(ConfigError) as ctx:
                load_config()
        self.assertIn("APPLICANT_NAME", str(ctx.exception))


class MailerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cv = Path(self._tmp.name) / "My_CV.pdf"
        self.cv.write_bytes(b"%PDF-1.4 fake")
        self.mailer = GmailMailer("me@gmail.com", "hunter2hunter2")

    def test_smtp_flow_and_attachment(self) -> None:
        with patch("mailer.smtplib.SMTP") as smtp_cls:
            smtp = smtp_cls.return_value.__enter__.return_value
            self.mailer.send_email("to@x.com", "Subj", "<p>Hello</p>", str(self.cv))
        smtp_cls.assert_called_once()
        self.assertEqual(smtp_cls.call_args.args[:2], ("smtp.gmail.com", 587))
        smtp.starttls.assert_called_once()
        smtp.login.assert_called_once_with("me@gmail.com", "hunter2hunter2")
        message = smtp.send_message.call_args.args[0]
        self.assertEqual(message["To"], "to@x.com")
        self.assertEqual([p.get_filename() for p in message.iter_attachments()], ["My_CV.pdf"])

    def test_error_messages_never_contain_secret(self) -> None:
        with patch("mailer.smtplib.SMTP") as smtp_cls:
            smtp = smtp_cls.return_value.__enter__.return_value
            smtp.send_message.side_effect = smtplib.SMTPDataError(550, b"rejected hunter2hunter2 now")
            with self.assertRaises(MailerError) as ctx:
                self.mailer.send_email("to@x.com", "S", "<p>x</p>", str(self.cv))
        self.assertNotIn("hunter2hunter2", str(ctx.exception))

    def test_auth_error_is_fatal(self) -> None:
        with patch("mailer.smtplib.SMTP") as smtp_cls:
            smtp = smtp_cls.return_value.__enter__.return_value
            smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad credentials")
            with self.assertRaises(MailerError) as ctx:
                self.mailer.send_email("to@x.com", "S", "<p>x</p>", str(self.cv))
        self.assertTrue(ctx.exception.fatal)

    def test_email_validation(self) -> None:
        self.assertTrue(is_valid_email("hr@acme.tn"))
        for bad in ("", "nope", "a@b", "a b@c.com", "a@b.com,c@d.com"):
            self.assertFalse(is_valid_email(bad), bad)


if __name__ == "__main__":
    unittest.main()
