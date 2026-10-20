import csv
import re
from datetime import datetime
from pathlib import Path
from .models import Member, Problem, now
from .security import restrict

MEMBER_FIELDS = ["user_id", "username", "display_name", "bot", "deleted", "source_id"]
CONSENT_FIELDS = ["user_id", "destination_id", "state", "timestamp", "evidence"]


def integer(value, field, positive=False):
    if not re.fullmatch(r"-?[0-9]+", value or ""):
        raise ValueError(f"{field} must contain decimal integer digits (no floats or scientific notation)")
    number = int(value)
    if not -(2**63) < number < 2**63 or number == 0 or (positive and number < 0):
        raise ValueError(f"{field} is outside supported Telegram ID bounds")
    return number


def boolean(value):
    if value not in {"0", "1"}:
        raise ValueError("bot/deleted must be 0 or 1")
    return value == "1"


def safe_cell(value):
    text = str(value)
    return "'" + text if text.startswith(("\t", "\r", "\n")) or text.lstrip().startswith(("=", "+", "-", "@")) else text


def unsafeguard(value):
    # Remove only the single protection prefix used by our readable exports.
    return value[1:] if value.startswith("'") and value[1:].lstrip().startswith(("=", "+", "-", "@")) else value


def read_csv(path, fields):
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != fields:
            raise Problem("csv_schema", "Required exact headers: " + ",".join(fields))
        rows = list(reader)
    for line, row in enumerate(rows, 2):
        if None in row or any(v is None for v in row.values()):
            raise Problem("csv_schema", f"Line {line}: wrong number of columns")
    return rows


def import_members(store, path):
    parsed = []
    for line, row in enumerate(read_csv(path, MEMBER_FIELDS), 2):
        try:
            user = integer(row["user_id"], "user_id", True)
            source = integer(unsafeguard(row["source_id"]), "source_id")
            parsed.append((Member(user, unsafeguard(row["username"]), unsafeguard(row["display_name"]),
                                  boolean(row["bot"]), boolean(row["deleted"])), source))
        except ValueError as exc:
            raise Problem("csv_validation", f"Line {line}: {exc}") from None
    for member, source in parsed:
        store.put_member(member, source)
    return len(parsed)


def import_consent(store, path, attest=False):
    if not attest:
        raise Problem("attestation_required", "Attest that each row accurately records permission for that specific destination")
    parsed = []
    for line, row in enumerate(read_csv(path, CONSENT_FIELDS), 2):
        try:
            user = integer(row["user_id"], "user_id", True)
            destination = integer(row["destination_id"], "destination_id")
            timestamp = int(datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")).timestamp())
            date = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
            if date.tzinfo is None or not 0 < timestamp <= now():
                raise ValueError("timestamp must have timezone and must not be in the future")
            state = row["state"]
            if state not in {"unknown", "opted_in", "declined", "revoked"}:
                raise ValueError("invalid consent state")
            if state == "opted_in" and not row["evidence"].strip():
                raise ValueError("opted_in requires a real evidence reference")
            parsed.append((user, destination, state, timestamp, row["evidence"]))
        except (ValueError, OverflowError) as exc:
            raise Problem("csv_validation", f"Line {line}: {exc}") from None
    for values in parsed:
        store.set_consent(*values)
    return len(parsed)


def list_members(store, source=None, search="", consent=None, destination=None, state=None, bot=None, deleted=None, error=None):
    if (consent or state or error) and destination is None:
        raise Problem("validation", "Consent, migration-state and error filters require --destination")
    result = []
    for row in store.rows("SELECT m.*,s.source,s.collected_at FROM members m JOIN memberships s ON s.user_id=m.id ORDER BY m.id,s.source"):
        if source is not None and row["source"] != source:
            continue
        if search.lower() not in f"{row['id']} {row['username']} {row['display_name']}".lower():
            continue
        if bot is not None and bool(row["bot"]) != bot or deleted is not None and bool(row["deleted"]) != deleted:
            continue
        c = store.consent(row["id"], destination)["state"] if destination else "unknown"
        outcomes = store.rows("SELECT o.* FROM outcomes o JOIN runs r ON o.run_id=r.id JOIN plans p ON p.id=r.plan_id "
                              "WHERE o.user_id=? AND json_extract(p.body,'$.mapping.destination')=? ORDER BY r.rowid DESC LIMIT 1",
                              (row["id"], destination)) if destination else []
        last = outcomes[0] if outcomes else {"state": "not_planned", "category": "", "explanation": ""}
        if consent and c != consent or state and last["state"] != state or error and last["category"] != error:
            continue
        result.append({**row, "consent": c, "migration_state": last["state"], "error_category": last["category"], "explanation": last["explanation"]})
    return result


def export_members(store, path, source=None):
    rows = list_members(store, source)
    with Path(path).open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(MEMBER_FIELDS)
        for row in rows:
            writer.writerow([str(row["id"]), safe_cell(row["username"]), safe_cell(row["display_name"]),
                             row["bot"], row["deleted"], safe_cell(row["source"])])
    restrict(Path(path))
    return len(rows)
