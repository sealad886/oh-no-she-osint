"""Authenticated HTTP recovery journeys use synthetic public transport only."""

from contextlib import closing
import base64
import hashlib
import http.client
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from local_osint.engine import Engine, InvalidInput
from local_osint.search_service import SearchService
from local_osint.server import LocalServer


class ReliabilityAPIJourneyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.engine = Engine(Path(self.temp.name) / 'private')
        self.server = LocalServer(self.engine, allow_network=True)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        self.thread.start()
        self.case = self.engine.create_case('Ada Public', 'person')['id']

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.temp.cleanup()

    def request(self, path, data=None):
        headers = {'X-OSINT-Local': '1', 'Origin': self.server.origin,
                   'Cookie': 'osint_session=' + self.server.token}
        if data is not None:
            headers['Content-Type'] = 'application/json'
        with closing(http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)) as connection:
            connection.request('GET' if data is None else 'POST', path,
                               json.dumps(data) if data is not None else None, headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())

    def finished(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status, report = self.request('/api/investigation?case=' + self.case)
            if report and report['status'] != 'running' and self.server.jobs.active is None:
                self.assertEqual(status, 200)
                return report
            time.sleep(.01)
        self.fail('Synthetic investigation did not finish within five seconds.')

    def state(self):
        status, workspace = self.request('/api/workspace?case=' + self.case)
        self.assertEqual(status, 200)
        return workspace

    def test_failed_page_retry_makes_fresh_collection_and_preserves_history_and_originals(self):
        url = 'https://community.example.org/ada'
        original_raw = b'An existing imported public bulletin. Keep its original bytes.'
        original = self.engine.ingest(self.case, original_raw, 'Saved bulletin', 'Imported public bulletin')
        search_raw = (b'<a class="result-link" href="https://community.example.org/ada">Ada Public staff introduction</a>'
                      b'<td class="result-snippet">Ada Public coordinates community workshops.</td>')
        page_raw = b'<article>Ada Public coordinates the public repair workshop volunteer programme. This substantive staff introduction describes community events, open training and regular repair activities.</article>'
        def initial_transport(destination, **kwargs):
            if 'duckduckgo.com' in destination:
                return search_raw, 'html', destination
            raise InvalidInput('Destination returned HTTP 503.')
        empty = {'status': 'empty', 'summary': 'No further public leads.', 'evidence_ids': [], 'leads': [], 'observations': []}
        with patch('local_osint.collector.fetch_public', side_effect=initial_transport), \
                patch('local_osint.connectors.execute', return_value=empty):
            status, _ = self.request('/api/investigate', {'case': self.case, 'ai': False,
                'options': {'providers': ['duckduckgo'], 'max_requests': 20}})
            self.assertEqual(status, 202)
            first = self.finished()
        task = next(t for t in self.state()['tasks'] if t['connector'] == 'collect_page' and t['arguments']['url'] == url)
        self.assertEqual(task['state'], 'failed')
        prior_attempt = task['attempt_history'][0]
        previous_sources = set(first['source_ids'])
        with patch('local_osint.collector.fetch_public', return_value=(page_raw, 'html', url)) as restored:
            status, _ = self.request('/api/task', {'case': self.case, 'id': task['id'], 'operation': 'retry'})
            self.assertEqual(status, 202)
            retried = self.finished()
        restored.assert_called_once_with(url, allow_query=True)
        after = next(t for t in self.state()['tasks'] if t['id'] == task['id'])
        self.assertEqual(after['attempts'], 2)
        self.assertEqual(len(after['attempt_history']), 2)
        self.assertIn(prior_attempt, after['attempt_history'])
        self.assertEqual(after['state'], 'complete')
        self.assertTrue(previous_sources.issubset(retried['source_ids']))
        self.assertTrue(all(step in retried['steps'] for step in first['steps']))
        status, exported = self.request('/api/export?case=' + self.case)
        self.assertEqual(status, 200)
        kept = next(e for e in exported['evidence'] if e['id'] == original['id'])
        self.assertEqual(base64.b64decode(kept['original_base64']), original_raw)
        collected = next(e for e in exported['evidence'] if e['source'] == url)
        self.assertEqual(collected['sha256'], hashlib.sha256(page_raw).hexdigest())

    def test_continue_recovers_transient_search_only_after_persisted_cooldown(self):
        with patch('local_osint.collector.fetch_public', side_effect=InvalidInput('Destination returned HTTP 503.')) as outage:
            self.assertEqual(self.request('/api/investigate', {'case': self.case, 'ai': False,
                'options': {'providers': ['duckduckgo'], 'max_requests': 20}})[0], 202)
            first = self.finished()
        self.assertEqual(outage.call_count, 1)
        task = next(t for t in self.state()['tasks'] if t['attempts'])
        self.assertEqual(task['state'], 'cooldown')
        with patch('local_osint.collector.fetch_public') as cooling:
            self.assertEqual(self.request('/api/continue', {'case': self.case, 'ai': False})[0], 202)
            self.finished()
        cooling.assert_not_called()
        # Simulate elapsed time only in this disposable workspace; no wall-clock wait.
        with self.engine.connection() as db:
            db.execute("UPDATE workspace_tasks SET retry_at='2000-01-01T00:00:00+00:00'")
            db.execute("UPDATE workspace_health SET retry_at='2000-01-01T00:00:00+00:00'")
        raw = b'<html><p>No results found</p></html>'
        def restored_transport(url, **kwargs):
            self.assertIn('duckduckgo.com', url)
            return raw, 'html', url
        with patch('local_osint.collector.fetch_public', side_effect=restored_transport) as restored:
            self.assertEqual(self.request('/api/continue', {'case': self.case, 'ai': False})[0], 202)
            continued = self.finished()
        self.assertGreater(restored.call_count, 0)
        after = next(t for t in self.state()['tasks'] if t['id'] == task['id'])
        self.assertEqual(after['state'], 'complete')
        self.assertEqual(after['attempts'], 2)
        self.assertEqual(len(after['attempt_history']), 2)
        self.assertTrue(all(step in continued['steps'] for step in first['steps']))
        with patch('local_osint.collector.fetch_public') as completed:
            self.assertEqual(self.request('/api/continue', {'case': self.case, 'ai': False})[0], 202)
            self.finished()
        completed.assert_not_called()

    def test_duplicate_server_rejects_before_container_access_and_releases_listener(self):
        with closing(socket.socket()) as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        with patch.object(SearchService, '_load') as receipt, patch.object(SearchService, '_run') as docker:
            with self.assertRaises(InvalidInput):
                LocalServer(self.engine, port=port, allow_network=True)
        receipt.assert_not_called()
        docker.assert_not_called()
        self.assertEqual(self.request('/api/cases')[0], 200)
        with closing(socket.socket()) as released:
            released.bind(('127.0.0.1', port))
        self.assertFalse(self.server.search_service.closed)


if __name__ == '__main__':
    unittest.main()
