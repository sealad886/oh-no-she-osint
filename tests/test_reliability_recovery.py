"""Recovery makes fresh permitted requests while preserving durable history."""

from pathlib import Path
import hashlib
from datetime import datetime, timedelta, timezone
import tempfile
import unittest
from unittest.mock import patch

from local_osint.engine import Engine, InvalidInput
from local_osint.frontier import Frontier
from local_osint.investigation import Investigation
from local_osint.workspace import Workspace, AUTOMATIC_ATTEMPT_LIMIT


class ReliabilityRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.directory = Path(self.temp.name) / 'private'
        self.engine = Engine(self.directory)
        self.case = self.engine.create_case('Ada Public', 'person', identifiers=['private-secret'])['id']
        self.workspace = Workspace(self.engine)
        self.options = {'tools': ['web_search'], 'providers': ['duckduckgo'], 'max_requests': 30, 'page_limit': 20}

    def tearDown(self):
        self.temp.cleanup()

    def runner(self, previous=None, **options):
        return Investigation(self.engine, self.case, True, options={**self.options, **options}, previous=previous)

    def elapse_cooldowns(self):
        with self.engine.connection() as db:
            db.execute("UPDATE workspace_tasks SET retry_at='2000-01-01T00:00:00+00:00',updated_at='2000-01-01T00:00:00+00:00'")
            db.execute("UPDATE workspace_health SET retry_at='2000-01-01T00:00:00+00:00'")

    def empty_search(self, engine, case_id, query, enabled, **kwargs):
        original = engine.ingest(case_id, b'<html>No public results</html>', 'Search original', 'https://search.example.org/', 'html')
        return {**original, 'results': []}

    def test_explicit_queue_retry_collects_once_and_retains_failed_attempt(self):
        url = 'https://public.example.org/ada'
        initial = self.runner()
        initial.allowed_urls.add(url)
        with patch('local_osint.collector.collect', side_effect=InvalidInput('Destination returned HTTP 503.')):
            initial.page(url)
        task = self.workspace.tasks(self.case)[0]
        old_attempt = self.workspace.attempts(self.case, task['id'])[0]
        self.workspace.edit_action(self.case, task['id'], state='pending')
        self.workspace.configure(self.case, {'approved_actions': [task['id']]})
        retry = self.runner(initial.report, automatic=True)
        # Reproduce the approval boundary with no discovery permission in memory.
        retry.allowed_urls.clear()
        raw = b'<article>Ada Public coordinates a public volunteer programme. This substantive staff introduction explains the repair workshops, open events and community training activities.</article>'
        with patch('local_osint.collector.fetch_public', return_value=(raw, 'html', url)) as transport:
            Frontier(retry).run_queue(task_ids={task['id']})
        self.assertEqual(transport.call_count, 1)
        attempts = self.workspace.attempts(self.case, task['id'])
        self.assertEqual(len(attempts), 2)
        self.assertIn(old_attempt, attempts)
        self.assertEqual({a['state'] for a in attempts}, {'failed', 'complete'})
        self.assertEqual(retry.report['steps'][0], initial.report['steps'][0])
        collected = next(p for p in retry.report['pages'] if p['url'] == url)
        self.assertEqual(self.engine.evidence(self.case, collected['source_id'])['sha256'], hashlib.sha256(raw).hexdigest())

    def test_manual_page_retry_bypasses_cache_and_retains_completed_original(self):
        url = 'https://public.example.org/ada'
        initial = self.runner(automatic=True, page_limit=1)
        initial.allowed_urls.add(url)
        raw = b'Ada Public volunteers at the public repair workshop and coordinates its regular training sessions and community events.'
        with patch('local_osint.collector.fetch_public', return_value=(raw, 'text', url)):
            first = initial.page(url)
        original = self.engine.evidence(self.case, first['id'])
        retry = self.runner(initial.report, automatic=True, page_limit=1)
        with patch('local_osint.collector.fetch_public', return_value=(raw + b' Updated public programme.', 'text', url)) as transport:
            retry.user_tool('collect_page', {'url': url})
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(self.engine.evidence(self.case, first['id'])['sha256'], original['sha256'])
        self.assertIn(first['id'], retry.report['source_ids'])
        self.assertEqual(len(self.workspace.attempts(self.case, self.workspace.tasks(self.case)[0]['id'])), 2)

    def test_approved_retry_does_not_bypass_private_or_destination_guards(self):
        for url in ['https://public.example.org/private-secret', 'https://127.0.0.1/ada', 'http://public.example.org/ada']:
            with self.subTest(url=url), patch('local_osint.collector.collect') as transport:
                with self.assertRaises(InvalidInput):
                    self.runner().user_tool('collect_page', {'url': url})
                transport.assert_not_called()

    def test_automatic_continue_retries_recovered_search_after_cooldown(self):
        initial = self.runner(automatic=True)
        with patch('local_osint.collector.web_search', side_effect=InvalidInput('Destination returned HTTP 503.')) as failed:
            initial.run()
        self.assertGreater(failed.call_count, 0)
        attempted = next(t for t in self.workspace.tasks(self.case) if t['attempts'])
        with patch('local_osint.collector.web_search') as cooling:
            self.runner(initial.report, automatic=True).run()
        cooling.assert_not_called()
        self.elapse_cooldowns()
        # Reopen the store to prove recovery does not depend on process memory.
        self.engine = Engine(self.directory)
        with patch('local_osint.collector.web_search', side_effect=self.empty_search) as restored:
            continued = self.runner(initial.report, automatic=True).run()
        self.assertGreater(restored.call_count, 0)
        task = next(t for t in self.workspace.tasks(self.case) if t['id'] == attempted['id'])
        self.assertEqual(task['state'], 'complete')
        self.assertEqual(task['attempts'], 2)
        self.assertEqual(len(self.workspace.attempts(self.case, task['id'])), 2)
        with patch('local_osint.collector.web_search') as completed:
            self.runner(continued, automatic=True).run()
        completed.assert_not_called()

    def test_automatic_attempt_limit_persists_but_user_retry_remains_available(self):
        task = self.workspace.enqueue(self.case, 'web_search', {'query': '"Ada Public"', 'provider': 'duckduckgo'})
        for _ in range(AUTOMATIC_ATTEMPT_LIMIT + 2):
            self.elapse_cooldowns()
            with patch('local_osint.collector.web_search', side_effect=InvalidInput('Destination returned HTTP 503.')):
                Frontier(self.runner()).run_queue(recover=True)
        exhausted = self.workspace.tasks(self.case)[0]
        self.assertEqual(exhausted['attempts'], AUTOMATIC_ATTEMPT_LIMIT)
        self.assertEqual(exhausted['state'], 'failed')
        self.workspace.edit_action(self.case, task['id'], state='pending')
        self.workspace.configure(self.case, {'approved_actions': [task['id']]})
        with patch('local_osint.collector.web_search', side_effect=self.empty_search) as explicit:
            Frontier(self.runner()).run_queue(task_ids={task['id']})
        explicit.assert_called_once()
        self.assertEqual(self.workspace.tasks(self.case)[0]['attempts'], AUTOMATIC_ATTEMPT_LIMIT + 1)

    def test_legacy_transient_failure_recovers_but_permanent_and_history_do_not(self):
        tasks = []
        for index, (error, result) in enumerate([
            ('Destination returned HTTP 503.', {}), ('Destination returned HTTP 403.', {}),
            ('Destination returned HTTP 999.', {}), ('Destination returned HTTP 503.', {'historical_report_id': 'report'}),
            ('Saved private clues cannot be sent to a source tool.', {})]):
            task = self.workspace.enqueue(self.case, 'web_search', {'query': '"Ada Public" ' + str(index), 'provider': 'duckduckgo'})
            self.workspace.start_action(self.case, task['id'], 'old-run')
            self.workspace.finish_action(self.case, task['id'], 'failed', error=error, result=result)
            tasks.append(task)
        self.elapse_cooldowns()
        self.assertEqual(self.workspace.recover_transient_actions(self.case), [tasks[0]['id']])
        self.assertEqual(self.workspace.recover_transient_actions(self.case), [])

    def test_rate_limit_cooldown_applies_to_new_queries_and_explicit_retry(self):
        runner = self.runner()
        task = Frontier(runner).enqueue('web_search', {'query': '"Ada Public"', 'provider': 'duckduckgo'}, 'Public search')
        with patch('local_osint.collector.web_search', side_effect=InvalidInput('Destination returned HTTP 429.')):
            Frontier(runner).run_queue()
        cooling = self.workspace.tasks(self.case)[0]
        health = self.workspace.health(self.case)[0]
        self.assertEqual(cooling['retry_at'], health['retry_at'])
        self.workspace.edit_action(self.case, task['id'], state='pending')
        Frontier(runner).enqueue('web_search', {'query': '"Ada Public" biography', 'provider': 'duckduckgo'}, 'Public alternative')
        with patch('local_osint.collector.web_search') as transport:
            Frontier(self.runner()).run_queue(recover=True)
        transport.assert_not_called()
        self.assertTrue(all(t['state'] == 'cooldown' for t in self.workspace.tasks(self.case)))
        self.assertEqual(self.workspace.tasks(self.case)[0]['attempts'], 1)

    def test_legacy_rate_limit_without_deadline_keeps_five_minute_cooldown(self):
        task = self.workspace.enqueue(self.case, 'web_search', {'query': '"Ada Public"', 'provider': 'duckduckgo'})
        self.workspace.start_action(self.case, task['id'], 'old-run')
        self.workspace.finish_action(self.case, task['id'], 'failed', error='Destination returned HTTP 429.')
        with self.engine.connection() as db:
            db.execute('UPDATE workspace_tasks SET updated_at=? WHERE id=?',
                       ((datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat(), task['id']))
        self.assertEqual(self.workspace.recover_transient_actions(self.case), [])
        with self.engine.connection() as db:
            db.execute('UPDATE workspace_tasks SET updated_at=? WHERE id=?',
                       ((datetime.now(timezone.utc) - timedelta(seconds=301)).isoformat(), task['id']))
        self.assertEqual(self.workspace.recover_transient_actions(self.case), [task['id']])

    def test_automatic_continuation_keeps_failed_pages_cached(self):
        url = 'https://public.example.org/blocked'
        initial = self.runner()
        initial.allowed_urls.add(url)
        with patch('local_osint.collector.collect', side_effect=InvalidInput('Destination returned HTTP 503.')):
            initial.page(url)
        self.elapse_cooldowns()
        continued = self.runner(initial.report, automatic=True)
        continued.allowed_urls.add(url)
        with patch('local_osint.collector.collect') as transport:
            Frontier(continued).run_queue(recover=True)
            continued.dispatch('collect_page', {'url': url})
        transport.assert_not_called()


if __name__ == '__main__':
    unittest.main()
