"""Local-only append-only revisions and delivery state, accessed through this process."""
import os
from contextlib import contextmanager, closing
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .catalog import UNITS, canonical, data_path, iso_date, number
from .intake import validate_provided


def timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        raise ValueError("invalid_timestamp") from None
    if result.tzinfo is None:
        raise ValueError("timezone_required")
    return result


def validated(payload):
    allowed = {"channel", "actor", "message_id", "received_at", "kind", "text", "occurred_on", "occurred_at", "components", "kg"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError("invalid_event_fields")
    for key in ("channel", "actor", "message_id", "text"):
        if not isinstance(payload.get(key), str) or not payload[key].strip():
            raise ValueError("event_field_required")
    if len(payload["text"]) > 16000:
        raise ValueError("text_too_large")
    timestamp(payload.get("received_at"))
    if payload.get("occurred_on") is not None:
        try:
            iso_date(payload["occurred_on"])
        except (ValueError, TypeError):
            raise ValueError("invalid_event_date") from None
    if payload.get("occurred_at") is not None:
        at = timestamp(payload["occurred_at"])
        if payload.get("occurred_on") and at.astimezone(timezone(timedelta(hours=9))).date().isoformat() != payload["occurred_on"]:
            raise ValueError("event_date_conflict")
    if payload.get("kind") == "meal":
        if "kg" in payload:
            raise ValueError("weight_in_meal_event")
        components = payload.get("components", [])
        if not isinstance(components, list):
            raise ValueError("invalid_components")
        for component in components:
            if (not isinstance(component, dict)
                    or not {"nutrition_ref", "amount", "unit", "certainty"} <= set(component)
                    or set(component) - {"nutrition_ref", "amount", "unit", "certainty", "provided_nutrients", "provided_basis"}):
                raise ValueError("invalid_component")
            if component["unit"] not in UNITS or component["certainty"] not in {"confirmed", "estimated"}:
                raise ValueError("invalid_component")
            number(component["amount"], positive=True)
            validate_provided(component)
            ref = component["nutrition_ref"]
            if not isinstance(ref, str) or len(ref) != 64 or any(x not in "0123456789abcdef" for x in ref):
                raise ValueError("invalid_nutrition_ref")
    elif payload.get("kind") == "weight":
        number(payload.get("kg"), positive=True)
        if "components" in payload:
            raise ValueError("components_in_weight_event")
    else:
        raise ValueError("unsupported_event_kind")
    return json.loads(canonical(payload))


class Journal:
    def __init__(self, path):
        self.path = data_path(path)

    @contextmanager
    def connection(self):
        if not self.path.is_file():
            raise ValueError("journal_not_initialized")
        db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=10)
        try:
            if db.execute("SELECT application,version FROM meta").fetchall() != [("agent-platform-meal", 1)]:
                raise ValueError("unsupported_journal_version")
        except (sqlite3.Error, ValueError):
            db.close()
            raise ValueError("unsupported_journal_version") from None
        try:
            with db:
                yield db
        finally:
            db.close()

    def init(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            with self.connection():
                pass
            return {"initialized": True, "created": False, "schema_version": 1}
        os.close(descriptor)
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript("""
                CREATE TABLE meta (application TEXT NOT NULL, version INTEGER NOT NULL);
                INSERT INTO meta VALUES ('agent-platform-meal', 1);
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY, source_key TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS revisions (
                    event_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    revision_key TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(event_id, revision), UNIQUE(event_id, revision_key)
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                    id TEXT PRIMARY KEY, report_hash TEXT NOT NULL, recipient TEXT NOT NULL,
                    status TEXT NOT NULL, attempts INTEGER NOT NULL, message_id TEXT,
                    UNIQUE(report_hash, recipient)
                );
            """)
            if db.execute("SELECT application,version FROM meta").fetchall() != [("agent-platform-meal", 1)]:
                raise ValueError("unsupported_journal_version")
        self.path.chmod(0o600)
        return {"initialized": True, "created": True, "schema_version": 1}

    def record(self, payload):
        payload = validated(payload)
        text = canonical(payload)
        key = canonical([payload[k] for k in ("channel", "actor", "message_id")])
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT id FROM events WHERE source_key=?", (key,)).fetchone()
            if existing:
                original = db.execute("SELECT payload FROM revisions WHERE event_id=? AND revision=1", existing).fetchone()[0]
                if original != text:
                    raise ValueError("source_message_changed_use_revision")
                return {"event_id": existing[0], "created": False}
            event_id = str(uuid.uuid4())
            db.execute("INSERT INTO events VALUES (?,?)", (event_id, key))
            db.execute("INSERT INTO revisions VALUES (?,1,'original',?)", (event_id, text))
        return {"event_id": event_id, "created": True, "revision": 1}

    def revise(self, event_id, expected_revision, revision_key, payload):
        payload = validated(payload)
        if not revision_key or revision_key == "original":
            raise ValueError("revision_key_required")
        text = canonical(payload)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            original = db.execute("SELECT source_key FROM events WHERE id=?", (event_id,)).fetchone()
            if not original:
                raise ValueError("event_not_found")
            if original[0] != canonical([payload[k] for k in ("channel", "actor", "message_id")]):
                raise ValueError("source_identity_immutable")
            prior = db.execute("SELECT revision,payload FROM revisions WHERE event_id=? AND revision_key=?", (event_id, revision_key)).fetchone()
            if prior:
                if prior[1] != text:
                    raise ValueError("revision_key_conflict")
                return {"event_id": event_id, "revision": prior[0], "created": False}
            current = db.execute("SELECT max(revision) FROM revisions WHERE event_id=?", (event_id,)).fetchone()[0]
            if current != expected_revision:
                raise ValueError("stale_revision")
            db.execute("INSERT INTO revisions VALUES (?,?,?,?)", (event_id, current + 1, revision_key, text))
        return {"event_id": event_id, "revision": current + 1, "created": True}

    def current(self):
        with self.connection() as db:
            rows = db.execute("""
                SELECT r.event_id,r.revision,r.payload FROM revisions r
                WHERE r.revision=(SELECT max(x.revision) FROM revisions x WHERE x.event_id=r.event_id)
                ORDER BY r.event_id
            """).fetchall()
        return [{"event_id": row[0], "revision": row[1], **json.loads(row[2])} for row in rows]

    def show(self, event_id, history=False):
        with self.connection() as db:
            rows = db.execute("SELECT revision,payload FROM revisions WHERE event_id=? ORDER BY revision", (event_id,)).fetchall()
        if not rows:
            raise ValueError("event_not_found")
        values = [{"event_id": event_id, "revision": row[0], **json.loads(row[1])} for row in rows]
        return values if history else values[-1]

    def list_period(self, start, end):
        first, last = iso_date(start), iso_date(end)
        if first > last:
            raise ValueError("invalid_report_range")
        rows = []
        for event in self.current():
            day = event.get("occurred_on")
            if not day and event.get("occurred_at"):
                day = timestamp(event["occurred_at"]).astimezone(timezone(timedelta(hours=9))).date().isoformat()
            basis = "occurred" if day else "recorded_only_not_meal_date"
            day = day or timestamp(event["received_at"]).astimezone(timezone(timedelta(hours=9))).date().isoformat()
            if start <= day <= end:
                rows.append({**event, "selection_date": day, "selection_date_basis": basis})
        return sorted(rows, key=lambda row: (row["selection_date"], row["received_at"], row["event_id"]))

    def claim_delivery(self, report_hash, recipient):
        if not recipient or len(report_hash) != 64 or any(x not in "0123456789abcdef" for x in report_hash):
            raise ValueError("invalid_delivery_binding")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id,status,attempts FROM deliveries WHERE report_hash=? AND recipient=?", (report_hash, recipient)).fetchone()
            if row:
                if row[1] != "failed" or row[2] >= 3:
                    return {"delivery_id": row[0], "claimed": False, "status": row[1]}
                db.execute("UPDATE deliveries SET status='in_progress',attempts=attempts+1 WHERE id=?", (row[0],))
                return {"delivery_id": row[0], "claimed": True, "attempt": row[2] + 1}
            delivery_id = str(uuid.uuid4())
            db.execute("INSERT INTO deliveries VALUES (?,?,?,'in_progress',1,NULL)", (delivery_id, report_hash, recipient))
        return {"delivery_id": delivery_id, "claimed": True, "attempt": 1}

    def finish_delivery(self, delivery_id, status, message_id=None):
        if status not in {"sent", "failed", "unknown"} or (status == "sent" and not message_id) or (status == "failed" and message_id):
            raise ValueError("invalid_delivery_result")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status,message_id FROM deliveries WHERE id=?", (delivery_id,)).fetchone()
            if not row:
                raise ValueError("delivery_not_found")
            if row == (status, message_id):
                return {"delivery_id": delivery_id, "status": status}
            if row[0] != "in_progress":
                raise ValueError("delivery_not_in_progress")
            db.execute("UPDATE deliveries SET status=?,message_id=? WHERE id=?", (status, message_id, delivery_id))
        return {"delivery_id": delivery_id, "status": status}
