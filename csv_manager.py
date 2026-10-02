"""CSV persistence: load, update and atomically save the companies file."""
from __future__ import annotations

import csv
import os
from datetime import datetime
from pathlib import Path

FIELDNAMES = ["company_name", "email", "sent", "sent_at", "error"]
REQUIRED_COLUMNS = {"company_name", "email", "sent"}
TRUE_VALUES = {"true", "1", "yes"}
MAX_ERROR_LENGTH = 300

Row = dict[str, str]


class CsvError(Exception):
    """Raised when the CSV file is missing or malformed."""


def load_rows(path: Path) -> list[Row]:
    if not path.is_file():
        raise CsvError(f"CSV file not found: {path}")
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise CsvError(f"CSV file is empty: {path}")
            reader.fieldnames = [name.strip() for name in reader.fieldnames]
            missing = REQUIRED_COLUMNS - set(reader.fieldnames)
            if missing:
                raise CsvError(f"CSV is missing required column(s): {', '.join(sorted(missing))}")
            rows = [_clean_row(raw) for raw in reader]
    except (csv.Error, UnicodeDecodeError) as exc:
        raise CsvError(f"Invalid CSV file {path}: {exc}") from None
    return [row for row in rows if any(row.values())]


def _clean_row(raw: dict[str, str | None]) -> Row:
    return {name: (raw.get(name) or "").strip() for name in FIELDNAMES}


def save_rows(path: Path, rows: list[Row]) -> None:
    """Write to a temp file then replace, so a crash never leaves a half-written CSV."""
    tmp_path = path.with_name(path.name + ".tmp")
    with tmp_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)


def is_sent(row: Row) -> bool:
    return row.get("sent", "").strip().lower() in TRUE_VALUES


def sent_addresses(rows: list[Row]) -> set[str]:
    return {row["email"].lower() for row in rows if is_sent(row) and row["email"]}


def pending_rows(rows: list[Row]) -> list[Row]:
    """Rows still to send: not sent, and whose address was never sent to in another row."""
    done = sent_addresses(rows)
    return [row for row in rows if not is_sent(row) and row["email"].lower() not in done]


def mark_sent(row: Row) -> None:
    row["sent"] = "true"
    row["sent_at"] = datetime.now().replace(microsecond=0).isoformat()
    row["error"] = ""


def mark_failed(row: Row, error: str) -> None:
    row["sent"] = "false"
    row["sent_at"] = ""
    row["error"] = " ".join(str(error).split())[:MAX_ERROR_LENGTH]
