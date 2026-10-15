"""Behavioral storage contracts: migration, provenance, isolation and recovery."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest
from unittest import mock

from local_osint.engine import Engine, InvalidInput
from local_osint.workspace import Workspace


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.directory = Path(self.temp.name) / "private"
        self.engine = Engine(self.directory)
        self.ws = Workspace(self.engine)
        self.case = self.engine.create_case("Ordinary person", "person", identifiers=["private@example.org"])["id"]
        self.other = self.engine.create_case("Namesake", "person")["id"]
        self.source = self.engine.ingest(self.case, b"Community bulletin. Ada works at the community service. Dublin.", "Bulletin", "https://example.org/bulletin")["id"]

    def tearDown(self):
        self.temp.cleanup()

    def test_new_database_uses_schema2_without_spurious_backup(self):
        with self.engine.connection() as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
        self.assertFalse((self.directory / "backups").exists())

    def test_arguments_preserve_phrases_operators_pagination_and_candidate_scope(self):
        first = self.ws.enqueue(self.case, "web_search", {"query": '"Ada Smith" -site:example.org', "page": 1})
        same = self.ws.enqueue(self.case, "web_search", {"page": 1, "query": '"Ada Smith" -site:example.org'})
        phrase = self.ws.enqueue(self.case, "web_search", {"query": 'Ada "Smith -site:example.org"', "page": 1})
        page = self.ws.enqueue(self.case, "web_search", {"query": '"Ada Smith" -site:example.org', "page": 2})
        candidate = self.ws.candidate(self.case, "Ada", "Public community service")
        scoped = self.ws.enqueue(self.case, "web_search", first["arguments"], candidate_id=candidate["id"])
        self.assertEqual(first["id"], same["id"])
        self.assertEqual(len({first["id"], phrase["id"], page["id"], scoped["id"]}), 4)
        self.assertNotIn("private@example.org", json.dumps(self.ws.tasks(self.case)))

    def test_concurrent_enqueue_has_one_durable_action(self):
        with ThreadPoolExecutor(max_workers=4) as workers:
            actions = list(workers.map(lambda _: self.ws.enqueue(self.case, "web_search", {"query": '"Ada Smith"'}), range(8)))
        self.assertEqual(len({task["id"] for task in actions}), 1)

    def test_dispatch_finish_and_explicit_retry_keep_attempt_history(self):
        task = self.ws.enqueue(self.case, "collect_page", {"url": "https://example.org"})
        self.ws.start_action(self.case, task["id"], "run1")
        done = self.ws.finish_action(self.case, task["id"], "complete", {"readable": True}, [self.source])
        self.assertEqual(done["evidence_ids"], [self.source])
        with self.assertRaises(InvalidInput):
            self.ws.start_action(self.case, task["id"], "run2")
        self.ws.edit_action(self.case, task["id"], state="pending")
        self.ws.start_action(self.case, task["id"], "run2")
        self.ws.finish_action(self.case, task["id"], "failed", error="Provider unavailable")
        attempts = self.ws.attempts(self.case, task["id"])
        self.assertEqual([item["state"] for item in attempts], ["complete", "failed"])
        self.assertEqual(attempts[0]["result"], {"readable": True})
        self.assertEqual(self.ws.tasks(self.case)[0]["attempts"], 2)

    def test_restart_recovers_unknown_dispatch_without_automatic_retry(self):
        task = self.ws.enqueue(self.case, "web_search", {"query": "public clue"})
        self.ws.start_action(self.case, task["id"], "old_process")
        restarted = Workspace(Engine(self.directory))
        self.assertEqual(restarted.tasks(self.case)[0]["state"], "running")
        self.assertEqual(restarted.recover_running(), 1)
        self.assertEqual(restarted.tasks(self.case)[0]["state"], "interrupted_after_dispatch")
        with self.assertRaises(InvalidInput):
            restarted.start_action(self.case, task["id"], "new_process")
        self.assertEqual(restarted.recover_running(), 0)
        self.assertEqual(restarted.attempts(self.case, task["id"])[0]["state"], "interrupted_after_dispatch")

    def test_cooldown_persists_and_cannot_be_bypassed_by_retry(self):
        task = self.ws.enqueue(self.case, "web_search", {"query": "public clue"})
        self.ws.start_action(self.case, task["id"], "one")
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        self.ws.finish_action(self.case, task["id"], "cooldown", retry_at=future)
        restarted = Workspace(Engine(self.directory))
        retried = restarted.edit_action(self.case, task["id"], state="pending")
        self.assertEqual(retried["state"], "cooldown")
        with self.assertRaises(InvalidInput):
            restarted.start_action(self.case, task["id"], "two")

    def test_argument_edit_creates_new_action_and_keeps_old_audit(self):
        original = self.ws.enqueue(self.case, "web_search", {"query": "old phrase"})
        replacement = self.ws.edit_action(self.case, original["id"], arguments={"query": '"new phrase"'}, priority=30)
        self.assertNotEqual(original["id"], replacement["id"])
        self.assertEqual(replacement["supersedes"], original["id"])
        tasks = {task["id"]: task for task in self.ws.tasks(self.case)}
        self.assertEqual(tasks[original["id"]]["arguments"], {"query": "old phrase"})
        self.assertEqual(tasks[original["id"]]["state"], "skipped")
        self.assertTrue(any(event["event"] == "superseded" for event in self.ws.events(self.case, original["id"])))

    def test_candidate_separation_claim_spans_and_contradiction(self):
        candidate = self.ws.candidate(self.case, "Ada Smith", "Dublin community service", self.source)
        namesake = self.ws.candidate(self.case, "Ada Smith", "Boston researcher")
        self.assertNotEqual(candidate["id"], namesake["id"])
        claim = self.ws.observe(self.case, self.source, candidate["id"], "Ada works at the community service", "affiliation", "Community service")
        ref = claim["references"][0]
        text = self.engine.evidence(self.case, self.source)["text"]
        self.assertEqual(text[ref["start"]:ref["end"]], ref["quote"])
        self.assertEqual(ref["extraction_sha256"], hashlib.sha256(text.encode()).hexdigest())
        self.assertEqual(claim["status"], "unconfirmed")
        contrary = self.engine.ingest(self.case, b"Ada no longer works there.", "Correction", "https://example.org/correction")["id"]
        disputed = self.ws.observe(self.case, contrary, candidate["id"], "Ada no longer works there.", claim_id=claim["id"], relation="contradict")
        self.assertEqual(disputed["status"], "disputed")
        self.assertEqual({item["relation"] for item in disputed["references"]}, {"support", "contradict"})
        self.assertEqual(self.ws.retrieve(self.case, "community", candidate_id=namesake["id"]), [])

    def test_claim_rejects_false_ambiguous_out_of_bounds_and_generated_citations(self):
        raw = self.engine.ingest(self.case, b"same same", "Repeated", "https://example.org/repeated")["id"]
        candidate = self.ws.candidate(self.case, "Ada")
        for quote, kwargs in [("invented", {}), ("same", {}), ("same", {"start": -1}), ("same", {"start": 0, "end": 5})]:
            with self.subTest(quote=quote, kwargs=kwargs), self.assertRaises(InvalidInput):
                self.ws.observe(self.case, raw, candidate["id"], quote, **kwargs)
        valid = self.ws.observe(self.case, raw, candidate["id"], "same", start=5)
        self.assertEqual(valid["references"][0]["start"], 5)
        spaced = self.ws.observe(self.case, raw, candidate["id"], "same ", start=0)
        self.assertEqual(spaced["references"][0]["quote"], "same ")
        generated = self.engine.ingest(self.case, b'{"claim":"invented"}', "Draft", "local-osint:investigation-report", "json", extract=False)["id"]
        with self.assertRaises(InvalidInput):
            self.ws.observe(self.case, generated, candidate["id"], "invented")

    def test_every_reference_and_mutation_enforces_case_scope(self):
        candidate = self.ws.candidate(self.case, "Ada")
        task = self.ws.enqueue(self.case, "web_search", {"query": "Ada"})
        checks = [
            lambda: self.ws.enqueue(self.other, "web_search", {}, candidate_id=candidate["id"]),
            lambda: self.ws.enqueue(self.other, "web_search", {}, source_id=self.source),
            lambda: self.ws.start_action(self.other, task["id"], "bad"),
            lambda: self.ws.finish_action(self.other, task["id"], "complete"),
            lambda: self.ws.edit_action(self.other, task["id"], state="skipped"),
            lambda: self.ws.candidate(self.other, "Ada", source_id=self.source),
            lambda: self.ws.observe(self.other, self.source, None, "Community bulletin"),
            lambda: self.ws.observe(self.case, self.source, self.ws.candidate(self.other, "Ada")["id"], "Community bulletin"),
            lambda: self.ws.retrieve(self.other, candidate_id=candidate["id"]),
            lambda: self.ws.events(self.other, task["id"]),
            lambda: self.ws.attempts(self.other, task["id"]),
        ]
        for check in checks:
            with self.subTest(check=check), self.assertRaises(InvalidInput):
                check()
        self.ws.start_action(self.case, task["id"], "one")
        other_source = self.engine.ingest(self.other, b"Other", "Other")["id"]
        with self.assertRaises(InvalidInput):
            self.ws.finish_action(self.case, task["id"], "complete", evidence_ids=[other_source])
        self.assertEqual(self.ws.tasks(self.case)[0]["state"], "running")

    def test_retrieval_finds_late_evidence_with_safe_fts_syntax_and_fallback(self):
        for index in range(35):
            self.engine.ingest(self.case, f"Irrelevant document {index}".encode(), f"Document {index}", f"https://example.org/{index}")
        late = self.engine.ingest(self.case, b"UniqueSignal public role confirmed by this document.", "Late relevant", "https://example.org/late")["id"]
        self.assertEqual(self.ws.retrieve(self.case, "UniqueSignal")[0]["id"], late)
        self.ws.retrieve(self.case, '" ) OR * : -')
        with self.engine.connection() as db:
            db.execute("DROP TRIGGER workspace_search_insert")
            db.execute("DROP TABLE workspace_search")
        self.assertEqual(self.ws.retrieve(self.case, "UniqueSignal")[0]["id"], late)
        self.assertEqual(self.ws.retrieve(self.other, "UniqueSignal"), [])

    def test_configuration_health_and_export_preserve_originals(self):
        self.ws.configure(self.case, {"connectors": ["crossref"], "prompts": {"plan": "Find public sources"}})
        self.ws.configure(self.case, {"pause": True})
        self.ws.update_health(self.case, "crossref", "blocked", error="Remote unavailable")
        restarted = Workspace(Engine(self.directory))
        snapshot = restarted.snapshot(self.case)
        self.assertTrue(snapshot["settings"]["pause"])
        self.assertEqual(snapshot["settings"]["connectors"], ["crossref"])
        self.assertEqual(snapshot["health"][0]["state"], "blocked")
        exported = self.engine.export(self.case)
        self.assertEqual(exported["schema_version"], 2)
        self.assertIn("workspace", exported)
        self.assertEqual(exported["evidence"][0]["id"], self.source)


class MigrationTests(unittest.TestCase):
    def test_repeated_checkpoints_import_latest_completed_action_without_changing_reports(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as temporary:
            root = Path(temporary) / "private"
            engine = Engine(root)
            case_id = engine.create_case("Checkpoint recovery", "person")["id"]
            step = {"id": "same-step", "run_id": "same-run", "tool": "web_search",
                    "arguments": {"query": '"Public person" community'},
                    "status": "running", "started_at": "2026-10-15T09:00:00+00:00"}
            report = {"report_type": "investigation", "case_id": case_id,
                      "run_id": "same-run", "steps": [step]}
            running_raw = json.dumps(report).encode()
            running_id = engine.ingest(case_id, running_raw, "Running checkpoint",
                "local-osint:investigation-report", "json", extract=False)["id"]
            step.update({"status": "success", "ok": True,
                         "finished_at": "2026-10-15T09:00:02+00:00"})
            completed_raw = json.dumps(report).encode()
            completed_id = engine.ingest(case_id, completed_raw, "Completed checkpoint",
                "local-osint:investigation-report", "json", extract=False)["id"]
            with engine.connection() as db:
                db.execute("DROP TRIGGER workspace_search_insert")
                for table in ["workspace_search", "workspace_claim_refs", "workspace_claims", "workspace_extractions", "workspace_attempts", "workspace_events", "workspace_tasks", "workspace_candidates", "workspace_settings", "workspace_health"]:
                    db.execute("DROP TABLE " + table)
                db.execute("PRAGMA user_version=1")
                originals = [tuple(row) for row in db.execute("SELECT id,sha256,raw,text FROM evidence ORDER BY id")]
            migrated = Engine(root)
            tasks = Workspace(migrated).tasks(case_id)
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]["state"], "complete")
            self.assertEqual(tasks[0]["attempts"], 1)
            self.assertEqual(tasks[0]["result"]["historical_report_id"], completed_id)
            with migrated.connection() as db:
                self.assertEqual([tuple(row) for row in db.execute("SELECT id,sha256,raw,text FROM evidence ORDER BY id")], originals)
                for evidence_id, expected in [(running_id, running_raw), (completed_id, completed_raw)]:
                    row = db.execute("SELECT raw,sha256 FROM evidence WHERE id=?", (evidence_id,)).fetchone()
                    self.assertEqual(row["raw"], expected)
                    self.assertEqual(row["sha256"], hashlib.sha256(expected).hexdigest())

    def test_v1_backup_consistent_originals_unchanged_and_history_conservative(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as temporary:
            root = Path(temporary) / "private"
            engine = Engine(root)
            case_id = engine.create_case("Ordinary person", "person")["id"]
            original = engine.ingest(case_id, b"Unchanged source original", "Source", "https://example.org")["id"]
            report = {"report_type": "investigation", "case_id": case_id, "run_id": "old", "profiles": [{"label": "Namesake", "description": "Unconfirmed", "source_id": original}], "steps": [{"tool": "web_search", "arguments": {"query": "old public query"}, "status": "running", "id": "step1"}]}
            report_id = engine.ingest(case_id, json.dumps(report).encode(), "Report", "local-osint:investigation-report", "json", extract=False)["id"]
            with engine.connection() as db:
                for trigger in db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'workspace_%'").fetchall():
                    db.execute('DROP TRIGGER "' + trigger[0] + '"')
                # Drop children first; FTS shadow tables disappear with the virtual table.
                for table in ["workspace_search", "workspace_claim_refs", "workspace_claims", "workspace_extractions", "workspace_attempts", "workspace_events", "workspace_tasks", "workspace_candidates", "workspace_settings", "workspace_health"]:
                    db.execute("DROP TABLE " + table)
                db.execute("PRAGMA user_version=1")
                before = [tuple(row) for row in db.execute("SELECT id,sha256,raw,text FROM evidence ORDER BY id")]
            migrated = Engine(root)
            backups = list((root / "backups").glob("*.sqlite3"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(backups[0].parent.stat().st_mode), 0o700)
            backup = sqlite3.connect(backups[0])
            try:
                self.assertEqual(backup.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(backup.execute("PRAGMA user_version").fetchone()[0], 1)
                self.assertEqual(backup.execute("SELECT id,sha256,raw,text FROM evidence ORDER BY id").fetchall(), before)
            finally:
                backup.close()
            with migrated.connection() as db:
                self.assertEqual([tuple(row) for row in db.execute("SELECT id,sha256,raw,text FROM evidence ORDER BY id")], before)
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            snapshot = Workspace(migrated).snapshot(case_id)
            self.assertEqual(snapshot["candidates"][0]["status"], "unconfirmed")
            self.assertEqual(snapshot["claims"], [])
            self.assertEqual(snapshot["tasks"][0]["state"], "interrupted_after_dispatch")
            self.assertEqual(snapshot["tasks"][0]["result"]["historical_report_id"], report_id)
            Engine(root)
            self.assertEqual(len(list((root / "backups").glob("*.sqlite3"))), 1)

    def test_migration_failure_rolls_back_schema_and_preserves_restore_backup(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as temporary:
            root = Path(temporary) / "private"
            engine = Engine(root)
            case_id = engine.create_case("Preserve me")["id"]
            source = engine.ingest(case_id, b"Original immutable bytes", "Original")["id"]
            with engine.connection() as db:
                db.execute("DROP TRIGGER workspace_search_insert")
                for table in ["workspace_search", "workspace_claim_refs", "workspace_claims", "workspace_extractions", "workspace_attempts", "workspace_events", "workspace_tasks", "workspace_candidates", "workspace_settings", "workspace_health"]:
                    db.execute("DROP TABLE " + table)
                db.execute("PRAGMA user_version=1")
            from local_osint import workspace
            with mock.patch.object(workspace, "TABLES", workspace.TABLES + ["INVALID MIGRATION SQL"]):
                with self.assertRaises(sqlite3.OperationalError):
                    Engine(root)
            with engine.connection() as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT count(*) FROM sqlite_master WHERE name='workspace_tasks'").fetchone()[0], 0)
                self.assertEqual(db.execute("SELECT raw FROM evidence WHERE id=?", (source,)).fetchone()[0], b"Original immutable bytes")
            self.assertEqual(len(list((root / "backups").glob("*.sqlite3"))), 1)
            self.assertEqual(Workspace(Engine(root)).snapshot(case_id)["claims"], [])

    def test_unknown_version_leaves_database_bytes_unchanged(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as temporary:
            root = Path(temporary) / "private"
            engine = Engine(root)
            with engine.connection() as db:
                db.execute("PRAGMA user_version=999")
            before = engine.path.read_bytes()
            with self.assertRaises(InvalidInput):
                Engine(root)
            self.assertEqual(before, engine.path.read_bytes())
            self.assertFalse((root / "backups").exists())


if __name__ == "__main__":
    unittest.main()
