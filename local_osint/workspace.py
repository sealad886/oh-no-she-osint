"""Durable, case-scoped investigation state. This module never uses the network.

Evidence originals are owned by Engine. Extracted passages are versioned copies;
claims cite exact character spans in those copies, never offsets in mutable AI prose.
"""

import hashlib
import json
import os
import re
import sqlite3
import uuid
import unicodedata
from datetime import datetime, timezone

from .engine import Engine, InvalidInput, field, now, private_directory

SCHEMA_VERSION = 3
EXTRACTOR_VERSION = "plain-v1"
AUTOMATIC_ATTEMPT_LIMIT = 3
TRANSIENT_RETRY_SECONDS = 60
RATE_LIMIT_RETRY_SECONDS = 300
RECOVERABLE_SEARCH_CONNECTORS = frozenset({'web_search', 'search_subject', 'news_search', 'crossref', 'searxng'})
TASK_STATES = frozenset({"pending", "running", "cooldown", "blocked", "complete", "failed", "skipped", "interrupted_after_dispatch"})
FINISH_STATES = TASK_STATES - {"pending", "running"}
CLAIM_STATES = frozenset({"unconfirmed", "supported", "disputed", "rejected"})
QUESTION_STATES = frozenset({"supported", "disputed", "unresolved", "unavailable"})
IDENTITY_STATES = frozenset({"same", "different", "uncertain"})
ORIGIN_KINDS = frozenset({"unknown", "original", "copy", "syndicated", "archive"})


def transient_source_failure(error, http_status=None):
    """Recognize recoverable transport failures, never policy or access blocks."""
    if http_status is None:
        match = re.search(r'\bHTTP\s+(\d{3})\b', error or '')
        http_status = int(match[1]) if match else None
    if http_status is not None:
        return http_status in {408, 425, 429, 500, 502, 503, 504}
    return any(message in (error or '') for message in (
        'Collection timed out.', 'HTTPS collection failed.'))


def retry_delay_seconds(error=None, *, rate_limited=False):
    """Use the same delay for new failures and saved failure recovery."""
    return RATE_LIMIT_RETRY_SECONDS if rate_limited or re.search(r'\bHTTP\s+429\b', error or '') else TRANSIENT_RETRY_SECONDS


TABLES = [
    """CREATE TABLE workspace_settings (case_id TEXT PRIMARY KEY REFERENCES cases(id),
        settings TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE workspace_candidates (id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id),
        label TEXT NOT NULL, context TEXT NOT NULL, source_id TEXT REFERENCES evidence(id),
        status TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(case_id,label,context,source_id))""",
    """CREATE TABLE workspace_tasks (id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id),
        connector TEXT NOT NULL, arguments TEXT NOT NULL, dedup_key TEXT NOT NULL,
        reason TEXT NOT NULL, candidate_id TEXT REFERENCES workspace_candidates(id), source_id TEXT REFERENCES evidence(id),
        priority INTEGER NOT NULL, state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
        run_id TEXT, result TEXT, evidence_ids TEXT NOT NULL, error TEXT, retry_at TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, supersedes TEXT REFERENCES workspace_tasks(id),
        UNIQUE(case_id,dedup_key))""",
    """CREATE TABLE workspace_events (id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL REFERENCES cases(id),
        task_id TEXT REFERENCES workspace_tasks(id), event TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE workspace_attempts (id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES workspace_tasks(id),
        case_id TEXT NOT NULL REFERENCES cases(id), run_id TEXT, started_at TEXT NOT NULL,
        finished_at TEXT, state TEXT NOT NULL, result TEXT, error TEXT)""",
    """CREATE TABLE workspace_extractions (id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id),
        evidence_id TEXT NOT NULL REFERENCES evidence(id), extractor_version TEXT NOT NULL, text TEXT NOT NULL,
        text_sha256 TEXT NOT NULL, evidence_sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
        UNIQUE(evidence_id,extractor_version,text_sha256))""",
    """CREATE TABLE workspace_claims (id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id),
        candidate_id TEXT REFERENCES workspace_candidates(id), predicate TEXT NOT NULL, value TEXT NOT NULL,
        status TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE workspace_claim_refs (claim_id TEXT NOT NULL REFERENCES workspace_claims(id),
        extraction_id TEXT NOT NULL REFERENCES workspace_extractions(id), evidence_id TEXT NOT NULL REFERENCES evidence(id),
        relation TEXT NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL, quote TEXT NOT NULL,
        PRIMARY KEY(claim_id,extraction_id,relation,start,end))""",
    """CREATE TABLE workspace_health (case_id TEXT NOT NULL REFERENCES cases(id), connector TEXT NOT NULL,
        state TEXT NOT NULL, retry_at TEXT, error TEXT, updated_at TEXT NOT NULL, PRIMARY KEY(case_id,connector))""",
    "CREATE INDEX workspace_tasks_case ON workspace_tasks(case_id,state,priority)",
    "CREATE INDEX workspace_claims_case ON workspace_claims(case_id,candidate_id)",
    "CREATE INDEX workspace_events_case ON workspace_events(case_id,id)",
]


QUESTION_TABLES = [
    """CREATE TABLE IF NOT EXISTS workspace_questions (id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id),
        question_key TEXT NOT NULL, text TEXT NOT NULL, status TEXT NOT NULL, candidate_id TEXT REFERENCES workspace_candidates(id),
        refs TEXT NOT NULL, gaps TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(case_id,question_key))""",
    """CREATE TABLE IF NOT EXISTS workspace_identity_decisions (id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id),
        candidate_id TEXT NOT NULL REFERENCES workspace_candidates(id), status TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS workspace_origins (evidence_id TEXT PRIMARY KEY REFERENCES evidence(id),
        case_id TEXT NOT NULL REFERENCES cases(id), kind TEXT NOT NULL, source_id TEXT REFERENCES evidence(id),
        reason TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS workspace_document_pages (extraction_id TEXT PRIMARY KEY REFERENCES workspace_extractions(id),
        pages TEXT NOT NULL, status TEXT NOT NULL, warnings TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS workspace_questions_case ON workspace_questions(case_id,status)",
    "CREATE INDEX IF NOT EXISTS workspace_decisions_candidate ON workspace_identity_decisions(case_id,candidate_id,created_at)",
]


def normalized(value):
    """Normalize proposition text without erasing punctuation, negation or dates."""
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def canonical(value):
    try:
        result = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise InvalidInput("Use a finite JSON value.") from exc
    if len(result) > 100000 or "\u0000" in result:
        raise InvalidInput("Configuration or tool arguments are too large.")
    return result


def object_value(value, label):
    if not isinstance(value, dict):
        raise InvalidInput(label + " must be an object.")
    canonical(value)
    return value


def task_key(connector, arguments, candidate_id=None, source_id=None):
    # JSON keys are normalized; query text, quoted phrases, operators, lists and
    # pagination stay intact. Candidate/source scope is part of an action's identity.
    return hashlib.sha256(canonical([connector, arguments, candidate_id, source_id]).encode()).hexdigest()


def migration_backup(engine, version=1):
    directory = private_directory(engine.directory / "backups")
    destination = directory / ("schema" + str(version) + "-" + uuid.uuid4().hex + ".sqlite3")
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    os.close(fd)
    backup = sqlite3.connect(destination)
    try:
        with engine.connection() as source:
            source.backup(backup)
            if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise InvalidInput("Migration backup failed integrity verification; database was not migrated.")
    finally:
        backup.close()
    with destination.open("rb") as handle:
        os.fsync(handle.fileno())
    return destination


def initialize_storage(engine, version):
    """Upgrade offline once, preserving a consistent owner-only pre-migration backup."""
    if version == SCHEMA_VERSION:
        return
    if version not in {0, 1, 2}:
        raise InvalidInput("Unsupported database version. Restore a compatible backup.")
    if version in {1, 2}:
        migration_backup(engine, version)
    with engine.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        current = db.execute("PRAGMA user_version").fetchone()[0]
        if current == SCHEMA_VERSION:
            return
        if current not in {0, 1, 2}:
            raise InvalidInput("Database version changed; reopen a compatible database.")
        if current in {0, 1}:
            for statement in TABLES:
                db.execute(statement)
            try:
                db.execute("CREATE VIRTUAL TABLE workspace_search USING fts5(evidence_id UNINDEXED, case_id UNINDEXED, title, text)")
                db.execute("""INSERT INTO workspace_search(evidence_id,case_id,title,text)
                    SELECT id,case_id,title,text FROM evidence WHERE source NOT LIKE 'local-osint:%'""")
                db.execute("""CREATE TRIGGER workspace_search_insert AFTER INSERT ON evidence
                    WHEN new.source NOT LIKE 'local-osint:%' BEGIN
                    INSERT INTO workspace_search(evidence_id,case_id,title,text) VALUES(new.id,new.case_id,new.title,new.text); END""")
            except sqlite3.OperationalError as exc:
                if "no such module" not in str(exc).lower():
                    raise
            if current == 1:
                _import_reports(db)
        for statement in QUESTION_TABLES:
            db.execute(statement)
        columns = {row[1] for row in db.execute("PRAGMA table_info(workspace_claims)")}
        if "normalized_key" not in columns:
            db.execute("ALTER TABLE workspace_claims ADD COLUMN normalized_key TEXT")
        if "observed_at" not in columns:
            db.execute("ALTER TABLE workspace_claims ADD COLUMN observed_at TEXT NOT NULL DEFAULT ''")
        # Existing claims remain separate: historical observations never establish
        # identity or independence merely because their wording happens to match.
        db.execute("PRAGMA user_version=3")


def _event(db, case_id, task_id, event, detail=None):
    db.execute("INSERT INTO workspace_events(case_id,task_id,event,detail,created_at) VALUES(?,?,?,?,?)",
               (case_id, task_id, event, canonical(detail or {}), now()))


def _import_reports(db):
    """Import old execution history without promoting snippets to identity facts."""
    for row in db.execute("SELECT id,case_id,text FROM evidence WHERE source='local-osint:investigation-report' ORDER BY collected_at,id").fetchall():
        try:
            report = json.loads(row["text"])
        except (ValueError, TypeError):
            continue
        if not isinstance(report, dict) or report.get("case_id") != row["case_id"]:
            continue
        case_id = row["case_id"]
        for profile in report.get("profiles", []) if isinstance(report.get("profiles"), list) else []:
            if not isinstance(profile, dict) or not isinstance(profile.get("label"), str):
                continue
            label = profile["label"][:200]
            context = str(profile.get("description", ""))[:2000]
            source_id = profile.get("source_id")
            if not isinstance(source_id, str):
                source_id = None
            if source_id and not db.execute("SELECT 1 FROM evidence WHERE id=? AND case_id=?", (source_id, case_id)).fetchone():
                source_id = None
            existing = db.execute("SELECT id FROM workspace_candidates WHERE case_id=? AND label=? AND context=? AND source_id IS ?", (case_id, label, context, source_id)).fetchone()
            if not existing:
                db.execute("INSERT INTO workspace_candidates VALUES(?,?,?,?,?,?,?)", (uuid.uuid4().hex, case_id, label, context, source_id, "unconfirmed", now()))
        for index, step in enumerate(report.get("steps", []) if isinstance(report.get("steps"), list) else []):
            if not isinstance(step, dict) or not isinstance(step.get("arguments", {}), dict):
                continue
            connector = str(step.get("tool") or "historical")[:100]
            arguments = step.get("arguments", {})
            # A report already contains its local audit data; importing it never
            # queues those old arguments for outbound execution.
            key = hashlib.sha256(canonical(["historical", step.get("run_id", report.get("run_id", row["id"])), step.get("id", index), connector, arguments]).encode()).hexdigest()
            state = step.get("status")
            state = ("interrupted_after_dispatch" if state == "running" else "skipped" if state == "skipped" else "complete" if step.get("ok") else "failed")
            existing = db.execute("SELECT id FROM workspace_tasks WHERE case_id=? AND dedup_key=?", (case_id, key)).fetchone()
            if existing:
                # Checkpoints repeat the same step. The latest observed outcome wins,
                # rather than turning an eventually completed call into interrupted work.
                db.execute('UPDATE workspace_tasks SET state=?,result=?,error=?,updated_at=? WHERE id=?',
                    (state, canonical({'historical_report_id': row['id']}), str(step.get('error', ''))[:2000] or None,
                     str(step.get('finished_at') or step.get('started_at') or now()), existing['id']))
                continue
            task_id = uuid.uuid4().hex
            stamp = str(step.get("started_at") or now())
            db.execute("""INSERT INTO workspace_tasks(id,case_id,connector,arguments,dedup_key,reason,priority,state,
                attempts,run_id,result,evidence_ids,error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (task_id, case_id, connector, canonical(arguments), key, "Imported historical execution; identity remains unconfirmed.",
                 0, state, 1, str(step.get("run_id") or report.get("run_id") or "historical"),
                 canonical({"historical_report_id": row["id"]}), "[]", str(step.get("error", ""))[:2000] or None, stamp, stamp))
            _event(db, case_id, task_id, "imported_history", {"report_id": row["id"], "state": state})
        _event(db, case_id, None, "migrated_report", {"report_id": row["id"], "identity_status": "unconfirmed"})


class Workspace:
    def __init__(self, engine):
        self.engine = engine

    @staticmethod
    def _task(row):
        result = dict(row)
        for key in ("arguments", "result", "evidence_ids"):
            result[key] = json.loads(result[key]) if result[key] is not None else None
        return result

    @staticmethod
    def _require(db, table, case_id, identifier, label):
        # table names are fixed internal callers, never an API argument.
        row = db.execute(f"SELECT * FROM {table} WHERE id=? AND case_id=?", (identifier, case_id)).fetchone()
        if row is None:
            raise InvalidInput(label + " not found in this case.")
        return row

    def settings(self, case_id):
        with self.engine.connection() as db:
            self.engine.require_case(db, case_id)
            row = db.execute("SELECT settings FROM workspace_settings WHERE case_id=?", (case_id,)).fetchone()
            return json.loads(row[0]) if row else {}

    def configure(self, case_id, settings):
        object_value(settings, "Settings")
        with self.engine.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self.engine.require_case(db, case_id)
            previous = db.execute("SELECT settings FROM workspace_settings WHERE case_id=?", (case_id,)).fetchone()
            merged = {**(json.loads(previous[0]) if previous else {}), **settings}
            db.execute("INSERT OR REPLACE INTO workspace_settings VALUES(?,?,?)", (case_id, canonical(merged), now()))
            _event(db, case_id, None, "configuration_changed", {"keys": sorted(settings)})
        return merged

    def enqueue(self, case_id, connector, arguments, reason="", candidate_id=None, source_id=None, priority=0):
        connector = field(connector, "Connector", 100)
        object_value(arguments, "Tool arguments")
        reason = field(reason, "Action reason", 2000, required=False)
        priority = self._priority(priority)
        key = task_key(connector, arguments, candidate_id, source_id)
        with self.engine.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self.engine.require_case(db, case_id)
            if candidate_id:
                self._require(db, "workspace_candidates", case_id, candidate_id, "Candidate")
            if source_id:
                self._require(db, "evidence", case_id, source_id, "Evidence")
            previous = db.execute("SELECT * FROM workspace_tasks WHERE case_id=? AND dedup_key=?", (case_id, key)).fetchone()
            if previous:
                return self._task(previous)
            task_id, stamp = uuid.uuid4().hex, now()
            db.execute("""INSERT INTO workspace_tasks(id,case_id,connector,arguments,dedup_key,reason,candidate_id,
                source_id,priority,state,evidence_ids,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (task_id, case_id, connector, canonical(arguments), key, reason, candidate_id, source_id, priority, "pending", "[]", stamp, stamp))
            _event(db, case_id, task_id, "queued", {"reason": reason})
            return self._task(db.execute("SELECT * FROM workspace_tasks WHERE id=?", (task_id,)).fetchone())

    @staticmethod
    def _priority(value):
        if isinstance(value, bool) or not isinstance(value, int) or not -1000000 <= value <= 1000000:
            raise InvalidInput("Priority must be an integer from -1000000 to 1000000.")
        return value

    def tasks(self, case_id):
        with self.engine.connection() as db:
            self.engine.require_case(db, case_id)
            return [self._task(row) for row in db.execute("SELECT * FROM workspace_tasks WHERE case_id=? ORDER BY priority DESC,created_at,id", (case_id,))]

    actions = tasks

    def recover_transient_actions(self, case_id):
        """Requeue eligible saved failures once their cooldown expires.

        Attempts stay attached to the original action. Pages require explicit
        Retry, and imported history never grants fresh network permission.
        """
        from datetime import timedelta
        recovered = []
        with self.engine.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self.engine.require_case(db, case_id)
            for row in db.execute("SELECT * FROM workspace_tasks WHERE case_id=? AND state='failed' AND attempts<?",
                                  (case_id, AUTOMATIC_ATTEMPT_LIMIT)).fetchall():
                task = self._task(row)
                result = task['result'] if isinstance(task['result'], dict) else {}
                if task['connector'] not in RECOVERABLE_SEARCH_CONNECTORS or result.get('historical_report_id'):
                    continue
                retryable = result.get('retryable', transient_source_failure(task['error']))
                if not retryable:
                    continue
                retry_at = task['retry_at']
                if not retry_at:
                    retry_at = (datetime.fromisoformat(task['updated_at']) + timedelta(seconds=retry_delay_seconds(task['error']))).isoformat()
                if retry_at > now():
                    continue
                db.execute("UPDATE workspace_tasks SET state='pending',retry_at=NULL,updated_at=? WHERE id=?", (now(), task['id']))
                _event(db, case_id, task['id'], 'recovered_transient_failure', {'attempts': task['attempts']})
                recovered.append(task['id'])
        return recovered

    def start_action(self, case_id, task_id, run_id):
        run_id = field(run_id, "Run ID", 200)
        with self.engine.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            task = self._require(db, "workspace_tasks", case_id, task_id, "Action")
            if task["state"] not in {"pending", "cooldown"}:
                raise InvalidInput("This action is not queued. Retry it explicitly before dispatch.")
            if task["retry_at"] and task["retry_at"] > now():
                raise InvalidInput("This action is still cooling down.")
            stamp = now()
            db.execute("UPDATE workspace_tasks SET state='running',attempts=attempts+1,run_id=?,updated_at=?,error=NULL WHERE id=?", (run_id, stamp, task_id))
            db.execute("INSERT INTO workspace_attempts VALUES(?,?,?,?,?,?,?,?,?)", (uuid.uuid4().hex, task_id, case_id, run_id, stamp, None, "running", None, None))
            _event(db, case_id, task_id, "dispatched", {"run_id": run_id})
            return self._task(db.execute("SELECT * FROM workspace_tasks WHERE id=?", (task_id,)).fetchone())

    def finish_action(self, case_id, task_id, status, result=None, evidence_ids=None, error=None, retry_at=None):
        if status not in FINISH_STATES:
            raise InvalidInput("Choose a valid finished action state.")
        if error is not None:
            error = field(error, "Action error", 4000, required=False)
        if retry_at is not None:
            try:
                parsed = datetime.fromisoformat(retry_at)
                if parsed.tzinfo is None:
                    raise ValueError()
                retry_at = parsed.astimezone(timezone.utc).isoformat()
            except (ValueError, TypeError):
                raise InvalidInput("Retry time must include an ISO timestamp and timezone.") from None
        if status == "cooldown" and retry_at is None:
            raise InvalidInput("A cooldown needs a retry time.")
        if evidence_ids is not None and (not isinstance(evidence_ids, list) or not all(isinstance(item, str) for item in evidence_ids)):
            raise InvalidInput("Evidence IDs must be a list.")
        encoded = canonical(result) if result is not None else None
        with self.engine.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            task = self._require(db, "workspace_tasks", case_id, task_id, "Action")
            if task["state"] != "running":
                raise InvalidInput("Only a dispatched action can finish.")
            ids = list(dict.fromkeys(evidence_ids or []))
            for identifier in ids:
                self._require(db, "evidence", case_id, identifier, "Evidence")
            stamp = now()
            db.execute("UPDATE workspace_tasks SET state=?,result=?,evidence_ids=?,error=?,retry_at=?,updated_at=? WHERE id=?",
                       (status, encoded, canonical(ids), error, retry_at, stamp, task_id))
            db.execute("UPDATE workspace_attempts SET finished_at=?,state=?,result=?,error=? WHERE task_id=? AND state='running'", (stamp, status, encoded, error, task_id))
            _event(db, case_id, task_id, status, {"evidence_ids": ids, "error": error, "retry_at": retry_at})
            return self._task(db.execute("SELECT * FROM workspace_tasks WHERE id=?", (task_id,)).fetchone())

    def edit_action(self, case_id, task_id, arguments=None, priority=None, state=None):
        if priority is not None:
            priority = self._priority(priority)
        if state is not None and state not in {"pending", "skipped"}:
            raise InvalidInput("An edited action can be queued or skipped.")
        if arguments is not None:
            object_value(arguments, "Tool arguments")
        with self.engine.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            task = self._require(db, "workspace_tasks", case_id, task_id, "Action")
            if task["state"] == "running":
                raise InvalidInput("Wait for this running action before editing it.")
            if arguments is not None and canonical(arguments) != task["arguments"]:
                key = task_key(task["connector"], arguments, task["candidate_id"], task["source_id"])
                duplicate = db.execute("SELECT * FROM workspace_tasks WHERE case_id=? AND dedup_key=?", (case_id, key)).fetchone()
                if duplicate:
                    raise InvalidInput("That edited action already exists. Retry the existing action.")
                replacement = uuid.uuid4().hex
                stamp = now()
                db.execute("""INSERT INTO workspace_tasks(id,case_id,connector,arguments,dedup_key,reason,candidate_id,
                    source_id,priority,state,evidence_ids,created_at,updated_at,supersedes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (replacement, case_id, task["connector"], canonical(arguments), key, task["reason"], task["candidate_id"], task["source_id"],
                     priority if priority is not None else task["priority"], state or "pending", "[]", stamp, stamp, task_id))
                db.execute("UPDATE workspace_tasks SET state='skipped',updated_at=? WHERE id=?", (stamp, task_id))
                _event(db, case_id, task_id, "superseded", {"replacement_id": replacement, "previous_state": task["state"]})
                _event(db, case_id, replacement, "queued_edit", {"supersedes": task_id})
                return self._task(db.execute("SELECT * FROM workspace_tasks WHERE id=?", (replacement,)).fetchone())
            new_state = state or task["state"]
            if state == "pending" and task["retry_at"] and task["retry_at"] > now():
                new_state = "cooldown"
            db.execute("UPDATE workspace_tasks SET state=?,priority=?,updated_at=? WHERE id=?", (new_state, priority if priority is not None else task["priority"], now(), task_id))
            _event(db, case_id, task_id, "edited", {"state": new_state, "priority": priority, "previous_state": task["state"]})
            return self._task(db.execute("SELECT * FROM workspace_tasks WHERE id=?", (task_id,)).fetchone())

    def defer_action(self, case_id, task_id, retry_at, error=None):
        """Honor a connector cooldown without inventing a dispatched attempt."""
        try:
            stamp = datetime.fromisoformat(retry_at)
            if stamp.tzinfo is None:
                raise ValueError()
            retry_at = stamp.astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError):
            raise InvalidInput('A connector cooldown needs a timezone-aware retry time.') from None
        with self.engine.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            task = self._require(db, 'workspace_tasks', case_id, task_id, 'Action')
            if task['state'] not in {'pending', 'cooldown'}:
                raise InvalidInput('Only queued actions can wait for a connector cooldown.')
            db.execute("UPDATE workspace_tasks SET state='cooldown',retry_at=?,error=?,updated_at=? WHERE id=?", (retry_at, error, now(), task_id))
            _event(db, case_id, task_id, 'deferred', {'retry_at': retry_at, 'error': error})
            return self._task(db.execute('SELECT * FROM workspace_tasks WHERE id=?', (task_id,)).fetchone())

    def recover_running(self, case_id=None):
        """Call once after proving no previous worker is still alive, never per request."""
        with self.engine.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if case_id is not None:
                self.engine.require_case(db, case_id)
            rows = db.execute("SELECT id,case_id FROM workspace_tasks WHERE state='running' AND (? IS NULL OR case_id=?)", (case_id, case_id)).fetchall()
            stamp = now()
            for row in rows:
                db.execute("UPDATE workspace_tasks SET state='interrupted_after_dispatch',error=?,updated_at=? WHERE id=?", ("Process ended after dispatch; remote outcome is unknown. Retry explicitly.", stamp, row["id"]))
                db.execute("UPDATE workspace_attempts SET state='interrupted_after_dispatch',finished_at=?,error=? WHERE task_id=? AND state='running'", (stamp, "Remote outcome is unknown.", row["id"]))
                _event(db, row["case_id"], row["id"], "interrupted_after_dispatch")
            return len(rows)

    def events(self, case_id, task_id=None):
        with self.engine.connection() as db:
            self.engine.require_case(db, case_id)
            if task_id:
                self._require(db, "workspace_tasks", case_id, task_id, "Action")
            rows = db.execute("SELECT * FROM workspace_events WHERE case_id=? AND (? IS NULL OR task_id=?) ORDER BY id", (case_id, task_id, task_id))
            return [{**dict(row), "detail": json.loads(row["detail"])} for row in rows]

    def attempts(self, case_id, task_id):
        with self.engine.connection() as db:
            self._require(db, "workspace_tasks", case_id, task_id, "Action")
            return [{**dict(row), "result": json.loads(row["result"]) if row["result"] is not None else None} for row in db.execute("SELECT * FROM workspace_attempts WHERE task_id=? AND case_id=? ORDER BY started_at,id", (task_id, case_id))]

    @staticmethod
    def _question(row):
        result = dict(row)
        result["key"] = result["question_key"]
        result["refs"] = json.loads(result["refs"])
        result["gaps"] = json.loads(result["gaps"])
        result["evidence_ids"] = [ref if isinstance(ref, str) else ref["evidence_id"] for ref in result["refs"]]
        result["gap"] = " ".join(item for item in result["gaps"] if isinstance(item, str))
        return result

    def questions(self, case_id):
        with self.engine.connection() as db:
            self.engine.require_case(db, case_id)
            return [self._question(row) for row in db.execute("SELECT * FROM workspace_questions WHERE case_id=? ORDER BY created_at,id", (case_id,))]

    def _question_values(self, db, case_id, status, candidate_id, refs, gaps):
        if status not in QUESTION_STATES:
            raise InvalidInput("Choose supported, disputed, unresolved or unavailable question status.")
        if candidate_id:
            self._require(db, "workspace_candidates", case_id, candidate_id, "Candidate")
        if not isinstance(refs, list) or len(refs) > 200 or not isinstance(gaps, list) or len(gaps) > 100:
            raise InvalidInput("Question references and gaps must be bounded lists.")
        for ref in refs:
            identifier = ref if isinstance(ref, str) else ref.get("evidence_id") if isinstance(ref, dict) else None
            if not isinstance(identifier, str):
                raise InvalidInput("Question references must identify evidence in this case.")
            self._require(db, "evidence", case_id, identifier, "Evidence")
        canonical(refs)
        canonical(gaps)

    def question(self, case_id, text, status="unresolved", candidate_id=None, refs=None, gaps=None, *, key=None, evidence_ids=None, gap=None):
        text = field(text, "Investigation question", 2000)
        refs = evidence_ids if evidence_ids is not None else refs or []
        gaps = [gap] if gap is not None else gaps or []
        key = field(key, "Question key", 500) if key is not None else hashlib.sha256(canonical([normalized(text), candidate_id]).encode()).hexdigest()
        with self.engine.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self.engine.require_case(db, case_id)
            self._question_values(db, case_id, status, candidate_id, refs, gaps)
            existing = db.execute("SELECT * FROM workspace_questions WHERE case_id=? AND question_key=?", (case_id, key)).fetchone()
            if existing:
                return self._question(existing)
            identifier, stamp = uuid.uuid4().hex, now()
            db.execute("INSERT INTO workspace_questions VALUES(?,?,?,?,?,?,?,?,?,?)", (identifier, case_id, key, text, status, candidate_id, canonical(refs), canonical(gaps), stamp, stamp))
            _event(db, case_id, None, "question_created", {"question_id": identifier})
            return self._question(db.execute("SELECT * FROM workspace_questions WHERE id=?", (identifier,)).fetchone())

    def update_question(self, case_id, question_id, *, text=None, status=None, candidate_id=None, refs=None, gaps=None, evidence_ids=None, gap=None):
        with self.engine.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._question(self._require(db, "workspace_questions", case_id, question_id, "Question"))
            text = row["text"] if text is None else field(text, "Investigation question", 2000)
            status = row["status"] if status is None else status
            candidate_id = row["candidate_id"] if candidate_id is None else candidate_id
            refs = evidence_ids if evidence_ids is not None else row["refs"] if refs is None else refs
            gaps = [gap] if gap is not None else row["gaps"] if gaps is None else gaps
            self._question_values(db, case_id, status, candidate_id, refs, gaps)
            db.execute("UPDATE workspace_questions SET text=?,status=?,candidate_id=?,refs=?,gaps=?,updated_at=? WHERE id=?", (text, status, candidate_id, canonical(refs), canonical(gaps), now(), question_id))
            _event(db, case_id, None, "question_updated", {"question_id": question_id, "status": status})
            return self._question(db.execute("SELECT * FROM workspace_questions WHERE id=?", (question_id,)).fetchone())

    def decide_candidate(self, case_id, candidate_id, status, reason=""):
        if status not in IDENTITY_STATES:
            raise InvalidInput("Choose same, different or uncertain identity.")
        reason = field(reason, "Identity decision reason", 2000, required=False)
        with self.engine.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require(db, "workspace_candidates", case_id, candidate_id, "Candidate")
            identifier = uuid.uuid4().hex
            db.execute("INSERT INTO workspace_identity_decisions VALUES(?,?,?,?,?,?)", (identifier, case_id, candidate_id, status, reason, now()))
            # This classification changes no original, candidate ID or claim link.
            db.execute("UPDATE workspace_candidates SET status=? WHERE id=?", (status, candidate_id))
            _event(db, case_id, None, "identity_decided", {"candidate_id": candidate_id, "status": status, "decision_id": identifier})
            return dict(db.execute("SELECT * FROM workspace_candidates WHERE id=?", (candidate_id,)).fetchone())

    def origin(self, case_id, evidence_id, kind="unknown", source_id=None, reason=""):
        if not isinstance(kind, str) or kind not in ORIGIN_KINDS:
            raise InvalidInput("Choose a known evidence origin relationship.")
        if (kind in {"copy", "syndicated", "archive"}) != bool(source_id):
            raise InvalidInput("Only copies, syndication and archives identify source evidence.")
        if source_id == evidence_id:
            raise InvalidInput("An evidence original cannot derive from itself.")
        reason = field(reason, "Origin reason", 2000, required=False)
        with self.engine.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require(db, "evidence", case_id, evidence_id, "Evidence")
            if source_id:
                self._require(db, "evidence", case_id, source_id, "Source evidence")
                seen = {evidence_id}
                cursor = source_id
                while cursor:
                    if cursor in seen:
                        raise InvalidInput("Evidence origin relationships cannot form a cycle.")
                    seen.add(cursor)
                    row = db.execute("SELECT source_id FROM workspace_origins WHERE evidence_id=?", (cursor,)).fetchone()
                    cursor = row[0] if row else None
            db.execute("INSERT OR REPLACE INTO workspace_origins VALUES(?,?,?,?,?,?)", (evidence_id, case_id, kind, source_id, reason, now()))
            _event(db, case_id, None, "origin_recorded", {"evidence_id": evidence_id, "kind": kind, "source_id": source_id})
            return self._origin(db, evidence_id)

    @staticmethod
    def _origin(db, evidence_id):
        initial = db.execute("SELECT * FROM workspace_origins WHERE evidence_id=?", (evidence_id,)).fetchone()
        result = dict(initial) if initial else {"evidence_id": evidence_id, "kind": "unknown", "source_id": None, "reason": "Origin not established."}
        cursor, seen, established = evidence_id, set(), False
        while cursor and cursor not in seen:
            seen.add(cursor)
            row = db.execute("SELECT kind,source_id FROM workspace_origins WHERE evidence_id=?", (cursor,)).fetchone()
            if not row:
                break
            if row["kind"] == "original" and not row["source_id"]:
                established = True
                break
            if not row["source_id"]:
                break
            cursor = row["source_id"]
        result["origin_id"] = cursor
        result["established"] = established
        # Equal binary originals are always one origin, including mislabeled copies.
        digest = db.execute("SELECT sha256 FROM evidence WHERE id=?", (cursor,)).fetchone()
        result["independence_key"] = digest[0] if digest and established else None
        return result

    def record_extraction(self, case_id, evidence_id, text, extractor_version, pages=None, status="ready", warnings=None):
        extractor_version = field(extractor_version, "Extractor version", 100)
        if not isinstance(text, str) or "\x00" in text or len(text) > 8 * 1024 * 1024:
            raise InvalidInput("Extracted text must be bounded text.")
        if status not in {"ready", "needs_ocr", "unavailable", "limited"}:
            raise InvalidInput("Unknown document extraction status.")
        pages = [] if pages is None else pages
        warnings = [] if warnings is None else warnings
        if not isinstance(pages, list) or not isinstance(warnings, list):
            raise InvalidInput("Document pages and warnings must be lists.")
        previous_end = 0
        for page in pages:
            if not isinstance(page, dict) or any(isinstance(page.get(name), bool) or not isinstance(page.get(name), int) for name in ("page", "start", "end")):
                raise InvalidInput("Document pages must have exact numbered text spans.")
            if page["page"] < 1 or page["start"] < previous_end or page["end"] < page["start"] or page["end"] > len(text):
                raise InvalidInput("Document page span is outside the extraction.")
            previous_end = page["end"]
        with self.engine.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            evidence = self._require(db, "evidence", case_id, evidence_id, "Evidence")
            digest = hashlib.sha256(text.encode()).hexdigest()
            existing = db.execute("SELECT id FROM workspace_extractions WHERE evidence_id=? AND extractor_version=? AND text_sha256=?", (evidence_id, extractor_version, digest)).fetchone()
            identifier = existing[0] if existing else uuid.uuid4().hex
            if not existing:
                db.execute("INSERT INTO workspace_extractions VALUES(?,?,?,?,?,?,?,?)", (identifier, case_id, evidence_id, extractor_version, text, digest, evidence["sha256"], now()))
            metadata = db.execute("SELECT * FROM workspace_document_pages WHERE extraction_id=?", (identifier,)).fetchone()
            if metadata and (metadata["pages"] != canonical(pages) or metadata["status"] != status or metadata["warnings"] != canonical(warnings)):
                raise InvalidInput("An extraction version's page provenance cannot be overwritten.")
            if not metadata:
                db.execute("INSERT INTO workspace_document_pages VALUES(?,?,?,?)", (identifier, canonical(pages), status, canonical(warnings)))
            return {"id": identifier, "evidence_id": evidence_id, "extractor_version": extractor_version, "text_sha256": digest, "pages": pages, "status": status, "warnings": warnings}

    def candidate(self, case_id, label, context="", source_id=None):
        label = field(label, "Candidate label", 200)
        context = field(context, "Candidate context", 2000, required=False)
        with self.engine.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self.engine.require_case(db, case_id)
            if source_id:
                self._require(db, "evidence", case_id, source_id, "Evidence")
            row = db.execute("SELECT * FROM workspace_candidates WHERE case_id=? AND label=? AND context=? AND source_id IS ?", (case_id, label, context, source_id)).fetchone()
            if row:
                return dict(row)
            identifier = uuid.uuid4().hex
            db.execute("INSERT INTO workspace_candidates VALUES(?,?,?,?,?,?,?)", (identifier, case_id, label, context, source_id, "unconfirmed", now()))
            _event(db, case_id, None, "candidate_created", {"candidate_id": identifier})
            return dict(db.execute("SELECT * FROM workspace_candidates WHERE id=?", (identifier,)).fetchone())

    def observe(self, case_id, evidence_id, candidate_id, quote, predicate="observation", value="", status="unconfirmed", *, start=None, end=None, extractor_version=EXTRACTOR_VERSION, relation="support", claim_id=None, observed_at=""):
        if not isinstance(quote, str) or not quote.strip() or len(quote) > 10000 or "\x00" in quote:
            raise InvalidInput("Quote must be a nonempty exact text passage, up to 10000 characters.")
        predicate = field(predicate, "Predicate", 100)
        value = field(value, "Observation value", 2000, required=False)
        if status not in CLAIM_STATES or relation not in {"support", "contradict"}:
            raise InvalidInput("Choose a valid claim state and evidence relationship.")
        extractor_version = field(extractor_version, "Extractor version", 100)
        observed_at = field(observed_at, "Observation date", 100, required=False)
        with self.engine.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            evidence = self._require(db, "evidence", case_id, evidence_id, "Evidence")
            if candidate_id:
                self._require(db, "workspace_candidates", case_id, candidate_id, "Candidate")
            if evidence["source"].startswith("local-osint:"):
                raise InvalidInput("Generated local reports cannot support source observations.")
            text = evidence["text"]
            registered = db.execute("SELECT x.* FROM workspace_extractions x JOIN workspace_document_pages p ON p.extraction_id=x.id WHERE x.evidence_id=? ORDER BY x.created_at DESC LIMIT 1", (evidence_id,)).fetchone()
            if extractor_version == EXTRACTOR_VERSION and registered:
                extractor_version = registered["extractor_version"]
                text = registered["text"]
            elif extractor_version != EXTRACTOR_VERSION:
                selected = db.execute("SELECT * FROM workspace_extractions WHERE evidence_id=? AND extractor_version=? ORDER BY created_at DESC LIMIT 1", (evidence_id, extractor_version)).fetchone()
                if not selected:
                    raise InvalidInput("Unknown text extractor version.")
                text = selected["text"]
            if start is None:
                start = text.find(quote)
                if start < 0 or text.find(quote, start + 1) >= 0:
                    raise InvalidInput("Quote must match a unique source passage, or provide its exact span.")
            if isinstance(start, bool) or not isinstance(start, int):
                raise InvalidInput("Use an exact text passage span.")
            end = start + len(quote) if end is None else end
            if isinstance(end, bool) or not isinstance(end, int) or start < 0 or end <= start or end > len(text) or text[start:end] != quote:
                raise InvalidInput("Quote does not match that exact evidence span.")
            digest = hashlib.sha256(text.encode()).hexdigest()
            extraction = db.execute("SELECT id FROM workspace_extractions WHERE evidence_id=? AND extractor_version=? AND text_sha256=?", (evidence_id, extractor_version, digest)).fetchone()
            extraction_id = extraction[0] if extraction else uuid.uuid4().hex
            if extraction is None:
                db.execute("INSERT INTO workspace_extractions VALUES(?,?,?,?,?,?,?,?)", (extraction_id, case_id, evidence_id, extractor_version, text, digest, evidence["sha256"], now()))
            if claim_id:
                claim = self._require(db, "workspace_claims", case_id, claim_id, "Claim")
                if claim["candidate_id"] != candidate_id:
                    raise InvalidInput("Contradicting/supporting evidence must cite the same candidate.")
                if relation == "contradict":
                    db.execute("UPDATE workspace_claims SET status='disputed' WHERE id=?", (claim_id,))
            else:
                # Source mentions stay separate candidates. Only propositions already
                # assigned to the exact same candidate can reconcile automatically.
                key = hashlib.sha256(canonical([candidate_id, normalized(predicate), normalized(value), observed_at]).encode()).hexdigest() if candidate_id and value else None
                existing = db.execute("SELECT id,status FROM workspace_claims WHERE case_id=? AND normalized_key=? ORDER BY created_at,id LIMIT 1", (case_id, key)).fetchone() if key else None
                if existing:
                    claim_id = existing["id"]
                    if relation == "contradict":
                        db.execute("UPDATE workspace_claims SET status='disputed' WHERE id=?", (claim_id,))
                else:
                    claim_id = uuid.uuid4().hex
                    db.execute("INSERT INTO workspace_claims(id,case_id,candidate_id,predicate,value,status,created_at,normalized_key,observed_at) VALUES(?,?,?,?,?,?,?,?,?)", (claim_id, case_id, candidate_id, predicate, value, "disputed" if relation == "contradict" else status, now(), key, observed_at))
            db.execute("INSERT OR IGNORE INTO workspace_claim_refs VALUES(?,?,?,?,?,?,?)", (claim_id, extraction_id, evidence_id, relation, start, end, quote))
            _event(db, case_id, None, "observation_recorded", {"claim_id": claim_id, "evidence_id": evidence_id, "relation": relation})
            return self._claim(db, case_id, claim_id)

    def _claim(self, db, case_id, claim_id):
        claim = dict(self._require(db, "workspace_claims", case_id, claim_id, "Claim"))
        claim["references"] = []
        for row in db.execute("""SELECT r.*,x.extractor_version,x.text_sha256 AS extraction_sha256,x.evidence_sha256,e.collected_at
            FROM workspace_claim_refs r JOIN workspace_extractions x ON x.id=r.extraction_id
            JOIN evidence e ON e.id=r.evidence_id WHERE claim_id=? ORDER BY e.collected_at,r.evidence_id,r.start""", (claim_id,)):
            ref = dict(row)
            ref["origin"] = self._origin(db, ref["evidence_id"])
            document = db.execute("SELECT pages FROM workspace_document_pages WHERE extraction_id=?", (ref["extraction_id"],)).fetchone()
            ref["pages"] = [page for page in json.loads(document[0]) if page["start"] < ref["end"] and page["end"] > ref["start"]] if document else []
            claim["references"].append(ref)
        return self._claim_independence(claim)

    @staticmethod
    def _claim_independence(claim):
        independent = {ref["origin"]["independence_key"] for ref in claim["references"] if ref["relation"] == "support" and ref["origin"]["independence_key"]}
        claim["independent_origins"] = len(independent)
        claim["independent_sources"] = len(independent)
        claim["corroborated"] = claim["status"] == "supported" and len(independent) >= 2
        return claim

    def _project_claims(self, claims, candidates):
        """Reconcile target propositions without changing stored identity or claims.

        Only explicit Same target decisions provide the common identity scope.
        Reversing that decision immediately separates the original propositions.
        """
        same_target = {candidate["id"] for candidate in candidates if candidate["status"] == "same"}
        groups = {}
        for claim in claims:
            key = ("target", normalized(claim["predicate"]), normalized(claim["value"]), claim.get("observed_at", "")) if claim["candidate_id"] in same_target and claim["value"] else ("claim", claim["id"])
            groups.setdefault(key, []).append(claim)
        projected = []
        for key, members in groups.items():
            result = dict(members[0])
            result["member_claim_ids"] = [member["id"] for member in members]
            result["candidate_ids"] = list(dict.fromkeys(member["candidate_id"] for member in members if member["candidate_id"]))
            result["identity_group"] = "target" if key[0] == "target" else "candidate"
            references = {}
            for member in members:
                for reference in member["references"]:
                    reference_key = tuple(reference[name] for name in ("evidence_id", "extraction_id", "relation", "start", "end"))
                    if reference_key not in references:
                        references[reference_key] = {**reference, "member_claim_ids": []}
                    references[reference_key]["member_claim_ids"].append(member["id"])
            result["references"] = list(references.values())
            statuses = {member["status"] for member in members}
            if "disputed" in statuses or any(reference["relation"] == "contradict" for reference in result["references"]) or {"supported", "rejected"} <= statuses:
                result["status"] = "disputed"
            elif "supported" in statuses:
                result["status"] = "supported"
            elif statuses == {"rejected"}:
                result["status"] = "rejected"
            else:
                result["status"] = "unconfirmed"
            projected.append(self._claim_independence(result))
        return projected

    def retrieve(self, case_id, query="", candidate_id=None, limit=8):
        query = field(query, "Evidence search", 1000, required=False)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise InvalidInput("Retrieve between 1 and 100 passages.")
        with self.engine.connection() as db:
            self.engine.require_case(db, case_id)
            if candidate_id:
                self._require(db, "workspace_candidates", case_id, candidate_id, "Candidate")
            params = [case_id, candidate_id, candidate_id]
            scope = """e.case_id=? AND e.source NOT LIKE 'local-osint:%' AND (? IS NULL OR EXISTS(
                SELECT 1 FROM workspace_claim_refs r JOIN workspace_claims c ON c.id=r.claim_id
                WHERE r.evidence_id=e.id AND c.candidate_id=? AND c.case_id=e.case_id))"""
            tokens = re.findall(r"\w+", query, re.UNICODE)
            rows = []
            if tokens:
                match = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens[:30])
                try:
                    rows = db.execute("SELECT e.* FROM workspace_search JOIN evidence e ON e.id=workspace_search.evidence_id WHERE " + scope + " AND workspace_search MATCH ? ORDER BY bm25(workspace_search),e.collected_at DESC LIMIT ?", (*params, match, limit)).fetchall()
                except sqlite3.OperationalError:
                    pass
            if tokens:
                # Newly OCRed text is a separate immutable extraction; originals
                # and their indicator offsets remain unchanged during reprocessing.
                conditions = " OR ".join("instr(lower(x.text),lower(?))>0" for _ in tokens[:30])
                extracted_rows = db.execute("SELECT DISTINCT e.* FROM evidence e JOIN workspace_extractions x ON x.evidence_id=e.id WHERE " + scope + " AND (" + conditions + ") ORDER BY e.collected_at DESC LIMIT ?", (*params, *tokens[:30], limit)).fetchall()
                seen = {row["id"] for row in rows}
                rows = [*rows, *(row for row in extracted_rows if row["id"] not in seen)]
            if not rows:
                clauses = " AND (" + " OR ".join("instr(lower(e.text),lower(?))>0 OR instr(lower(e.title),lower(?))>0" for _ in tokens[:30]) + ")" if tokens else ""
                token_params = [part for token in tokens[:30] for part in (token, token)]
                rows = db.execute("SELECT e.* FROM evidence e WHERE " + scope + clauses + " ORDER BY e.collected_at DESC LIMIT ?", (*params, *token_params, limit)).fetchall()
            results = []
            for row in rows:
                extraction = db.execute("SELECT x.extractor_version,x.text,p.pages FROM workspace_extractions x JOIN workspace_document_pages p ON p.extraction_id=x.id WHERE x.evidence_id=? ORDER BY x.created_at DESC LIMIT 1", (row["id"],)).fetchone()
                text = extraction["text"] if extraction else row["text"]
                lowered = text.lower()
                occurrences = []
                for token in dict.fromkeys(token.lower() for token in tokens):
                    cursor = 0
                    for _ in range(100):
                        position = lowered.find(token, cursor)
                        if position < 0:
                            break
                        occurrences.append((position, token))
                        cursor = position + len(token)
                # Prefer the passage answering the full question, rather than an
                # early name mention that can hide a relevant late appendix.
                best = max(occurrences, key=lambda occurrence: len({token for position, token in occurrences if abs(position - occurrence[0]) <= 650}), default=None)
                start = max(0, best[0] - 200) if best else 0
                end = min(len(text), start + 1800)
                excerpt = text[start:end]
                results.append({"id": row["id"], "evidence_id": row["id"], "title": row["title"], "source": row["source"],
                    "text": excerpt, "text_excerpt": excerpt, "start": start, "end": end, "extractor_version": EXTRACTOR_VERSION,
                    "extraction_sha256": hashlib.sha256(text.encode()).hexdigest(), "evidence_sha256": row["sha256"]})
                if extraction:
                    results[-1]["extractor_version"] = extraction["extractor_version"]
                    results[-1]["pages"] = [page for page in json.loads(extraction["pages"]) if page["start"] < end and page["end"] > start]
                else:
                    results[-1]["pages"] = []
                results[-1]["origin"] = self._origin(db, row["id"])
            if tokens:
                query_tokens = set(token.lower() for token in tokens)
                results.sort(key=lambda result: len({token for token in query_tokens if token in result["text"].lower()}), reverse=True)
            return results[:limit]

    def health(self, case_id):
        with self.engine.connection() as db:
            self.engine.require_case(db, case_id)
            return [dict(row) for row in db.execute("SELECT * FROM workspace_health WHERE case_id=? ORDER BY connector", (case_id,))]

    def update_health(self, case_id, connector, state, retry_at=None, error=None):
        connector = field(connector, "Connector", 100)
        state = field(state, "Connector state", 100)
        error = field(error, "Connector error", 2000, required=False) if error is not None else None
        if retry_at is not None:
            try:
                parsed = datetime.fromisoformat(retry_at)
                if parsed.tzinfo is None:
                    raise ValueError()
                retry_at = parsed.astimezone(timezone.utc).isoformat()
            except (ValueError, TypeError):
                raise InvalidInput("Retry time must include an ISO timestamp and timezone.") from None
        with self.engine.connection() as db:
            self.engine.require_case(db, case_id)
            db.execute("INSERT OR REPLACE INTO workspace_health VALUES(?,?,?,?,?,?)", (case_id, connector, state, retry_at, error, now()))
        return next(item for item in self.health(case_id) if item["connector"] == connector)

    def snapshot(self, case_id, connection=None):
        if connection is not None:
            return self._snapshot(connection, case_id)
        with self.engine.connection() as db:
            db.execute("BEGIN")
            return self._snapshot(db, case_id)

    def _snapshot(self, db, case_id):
        self.engine.require_case(db, case_id)
        tasks = [self._task(row) for row in db.execute("SELECT * FROM workspace_tasks WHERE case_id=? ORDER BY priority DESC,created_at,id", (case_id,))]
        candidates = [dict(row) for row in db.execute("SELECT * FROM workspace_candidates WHERE case_id=? ORDER BY created_at,id", (case_id,))]
        claims = [self._claim(db, case_id, row["id"]) for row in db.execute("SELECT id FROM workspace_claims WHERE case_id=? ORDER BY created_at,id", (case_id,)).fetchall()]
        claims = self._project_claims(claims, candidates)
        row = db.execute("SELECT settings FROM workspace_settings WHERE case_id=?", (case_id,)).fetchone()
        health = [dict(item) for item in db.execute("SELECT * FROM workspace_health WHERE case_id=? ORDER BY connector", (case_id,))]
        events = [{**dict(item), "detail": json.loads(item["detail"])} for item in db.execute("SELECT * FROM workspace_events WHERE case_id=? ORDER BY id", (case_id,))]
        questions = [self._question(item) for item in db.execute("SELECT * FROM workspace_questions WHERE case_id=? ORDER BY created_at,id", (case_id,))]
        decisions = [dict(item) for item in db.execute("SELECT * FROM workspace_identity_decisions WHERE case_id=? ORDER BY created_at,id", (case_id,))]
        origins = [self._origin(db, item[0]) for item in db.execute("SELECT id FROM evidence WHERE case_id=? AND source NOT LIKE 'local-osint:%' ORDER BY collected_at,id", (case_id,))]
        return {"tasks": tasks, "candidates": candidates, "claims": claims, "questions": questions, "identity_decisions": decisions, "origins": origins, "settings": json.loads(row[0]) if row else {},
            "health": health, "events": events, "counts": {"tasks": len(tasks), "candidates": len(candidates), "claims": len(claims),
                "leads": len(candidates),
                "readable_documents": len({ref["evidence_id"] for claim in claims if claim["predicate"] == "readable_document" for ref in claim["references"]}),
                "corroborated_claims": sum(claim["corroborated"] for claim in claims),
                "questions": len(questions),
                "unresolved_questions": sum(question["status"] == "unresolved" for question in questions),
                "established_origins": len({origin["independence_key"] for origin in origins if origin["independence_key"]}),
                "unknown_origins": sum(not origin["established"] for origin in origins),
                **{state: sum(task["state"] == state for task in tasks) for state in sorted(TASK_STATES)}}}
