"""The maintenance record: proof that a human did the monthly review recently.

Why: Apple's API and our dependencies change, and this server holds a key that can
edit a live App Store listing. Tools stop working when the last review is too old, so
the review cannot be forgotten.

The rule is "fail closed": a missing, corrupt, naive-timestamp or future-dated record all
count as "overdue". Writes are atomic and mode 0600 so a crash or another user cannot
leave a half-written or forged file.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from app_store_connect.identity import CodeIdentity, current_identity
from app_store_connect.paths import ensure_private_dir, state_dir

logger = logging.getLogger(__name__)

SCHEMA: Final = 1
HISTORY_LIMIT: Final = 12
FUTURE_TOLERANCE: Final = timedelta(hours=1)  # small clock differences only
FILE_NAME: Final = "review.json"
FILE_MODE: Final = 0o600


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    """A past review, kept only so a maintainer can see how reviews went over time."""

    reviewed_at: str
    spec_version: str
    outcome: str


@dataclass(frozen=True, slots=True)
class MaintenanceRecord:
    """The latest review plus a short history of earlier ones."""

    reviewed_at: datetime
    spec_version: str
    outcome: str
    tools: tuple[str, ...] = ()
    history: tuple[HistoryEntry, ...] = ()
    code_version: str = ""  # version of the code that was reviewed ("" in older records)
    code_commit: str = ""  # git commit of that code, when it was installed from git

    @property
    def code(self) -> CodeIdentity | None:
        """The identity of the reviewed code, or None when the record did not store it."""
        return CodeIdentity(self.code_version, self.code_commit) if self.code_version else None


@dataclass(frozen=True, slots=True)
class RecordCheck:
    """Whether a review is fresh enough, and a sentence to show the user either way."""

    overdue: bool
    message: str
    days_ago: int | None = None
    days_left: int | None = None


def now_local() -> datetime:
    """Return the current time as a timezone-aware datetime in the local zone."""
    return datetime.now(UTC).astimezone()


def record_path() -> Path:
    """Return the full path of the review file."""
    return state_dir() / FILE_NAME


def parse_time(text: str) -> datetime:
    """Parse an ISO 8601 timestamp and insist that it carries a UTC offset."""
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp {text!r} has no UTC offset")
    return parsed


def _history_from_json(raw: object) -> tuple[HistoryEntry, ...]:
    """Convert the stored history list; a malformed entry is dropped, not fatal."""
    if not isinstance(raw, list):
        return ()
    entries: list[HistoryEntry] = []
    for item in raw:
        if isinstance(item, dict):
            entries.append(
                HistoryEntry(
                    reviewed_at=str(item.get("reviewed_at", "")),
                    spec_version=str(item.get("spec_version", "")),
                    outcome=str(item.get("outcome", "")),
                )
            )
    return tuple(entries[:HISTORY_LIMIT])


def _record_from_json(data: Any) -> MaintenanceRecord:
    """Build a `MaintenanceRecord` from parsed JSON; raises if the data is unusable."""
    if not isinstance(data, dict):
        raise TypeError("review file is not a JSON object")
    tools = data.get("tools", [])
    if not isinstance(tools, list):
        raise TypeError("tools must be a list")
    return MaintenanceRecord(
        reviewed_at=parse_time(data["reviewed_at"]),
        spec_version=str(data.get("spec_version", "")),
        outcome=str(data.get("outcome", "")),
        tools=tuple(str(name) for name in tools),
        history=_history_from_json(data.get("history")),
        code_version=str(data.get("code_version", "")),
        code_commit=str(data.get("code_commit", "")),
    )


def load_record(path: Path | None = None) -> MaintenanceRecord | None:
    """Read the review file; return None when it is missing or unusable.

    A corrupt file is treated like "no review happened" so the gate stays closed.
    """
    target = path or record_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        return _record_from_json(data)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError):
        logger.warning("review file %s is unreadable; treating the review as overdue", target)
        return None


def check_record(review: MaintenanceRecord | None, now: datetime, max_days: int) -> RecordCheck:
    """Decide whether `review` is still fresh enough at `now`."""
    todo = "Run the maintenance_report tool, read it, then confirm with maintenance_confirm_review."
    if review is None:
        return RecordCheck(True, f"The maintenance review has never been done. {todo}")
    if review.reviewed_at > now + FUTURE_TOLERANCE:
        # A future date (wrong clock, hand-edited file) would otherwise keep tools open forever.
        when = review.reviewed_at.isoformat()
        return RecordCheck(
            True, f"The review date {when} is in the future, so it cannot be trusted. {todo}"
        )
    days = (now - review.reviewed_at).days
    if days >= max_days:
        date = review.reviewed_at.date().isoformat()
        message = (
            f"The maintenance review is overdue (last review {date}, {days} days ago; "
            f"limit {max_days} days). {todo}"
        )
        return RecordCheck(True, message, days_ago=days, days_left=max_days - days)
    return RecordCheck(
        False, f"Last review {days} days ago.", days_ago=days, days_left=max_days - days
    )


def build_record(
    previous: MaintenanceRecord | None,
    now: datetime,
    spec_version: str,
    outcome: str,
    tools: list[str] | None,
    code: CodeIdentity | None = None,
) -> MaintenanceRecord:
    """Assemble the new review, pushing the previous one into the history (newest first)."""
    history: list[HistoryEntry] = []
    old_tools: tuple[str, ...] = ()
    if previous is not None:
        old_tools = previous.tools
        entry = HistoryEntry(
            previous.reviewed_at.isoformat(timespec="seconds"),
            previous.spec_version,
            previous.outcome,
        )
        history = [entry, *previous.history]
    return MaintenanceRecord(
        reviewed_at=now,
        spec_version=spec_version,
        outcome=outcome,
        tools=tuple(sorted(set(tools))) if tools is not None else old_tools,
        history=tuple(history[:HISTORY_LIMIT]),
        code_version=code.version if code else "",
        code_commit=code.commit if code else "",
    )


def _to_json(review: MaintenanceRecord) -> dict[str, Any]:
    """Convert a `MaintenanceRecord` to the on-disk JSON shape."""
    return {
        "schema": SCHEMA,
        "reviewed_at": review.reviewed_at.isoformat(timespec="seconds"),
        "spec_version": review.spec_version,
        "outcome": review.outcome,
        "tools": list(review.tools),
        "code_version": review.code_version,
        "code_commit": review.code_commit,
        "history": [
            {
                "reviewed_at": entry.reviewed_at,
                "spec_version": entry.spec_version,
                "outcome": entry.outcome,
            }
            for entry in review.history
        ],
    }


def write_record(review: MaintenanceRecord, path: Path | None = None) -> None:
    """Write the review atomically (temp file + rename) with mode 0600."""
    target = path or record_path()
    directory = ensure_private_dir(target.parent)
    fd, tmp_name = tempfile.mkstemp(dir=directory, prefix="review-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(_to_json(review), handle, indent=2)
            handle.write("\n")
        Path(tmp_name).chmod(FILE_MODE)
        Path(tmp_name).replace(target)  # atomic rename on the same filesystem
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def stamp_record(
    now: datetime,
    spec_version: str,
    outcome: str,
    tools: list[str] | None,
    code: CodeIdentity | None = None,
) -> MaintenanceRecord:
    """Record a completed review (of the running code, unless `code` is given) and return it."""
    review = build_record(
        load_record(), now, spec_version, outcome, tools, code or current_identity()
    )
    write_record(review)
    return review
