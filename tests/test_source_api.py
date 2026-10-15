"""Native authenticated HTTP journeys; public providers are replaced at transport."""
import base64
from contextlib import closing
import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from local_osint.engine import Engine
from local_osint.server import LocalServer


class SourceAPIJourneyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = Engine(Path(self.temp.name).resolve() / 'private')
        self.server = LocalServer(self.engine, allow_network=True)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        self.thread.start()
        self.case = self.engine.create_case('Bea Public', 'person')['id']

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.temp.cleanup()

    def request(self, path, data=None, auth=True, origin=None):
        headers = {'X-OSINT-Local': '1', 'Origin': origin or self.server.origin}
        if auth:
            headers['Cookie'] = 'osint_session=' + self.server.token
        if data is not None:
            headers['Content-Type'] = 'application/json'
        with closing(http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)) as connection:
            connection.request('GET' if data is None else 'POST', path,
                json.dumps(data) if data is not None else None, headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())

    def wait(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status, report = self.request('/api/investigation?case=' + self.case)
            if report and report['status'] != 'running':
                self.assertEqual(status, 200)
                return report
            time.sleep(.01)
        self.fail('The source worker did not finish.')

    def test_configuration_source_run_resume_and_export_preserve_originals(self):
        home = b'<h1>Community club</h1><p>Public information about our community activities, volunteer programmes and upcoming events.</p><a href="/volunteers">Volunteers</a>'
        profile = b'<h1>Bea Public</h1><p>Bea Public organizes community events and volunteer training at this local organization. This is a public staff introduction.</p>'
        def response(url, **kwargs):
            return {'raw': home if url.endswith('/') else profile, 'format': 'html', 'canonical': url, 'status': 200, 'requests': 1}
        with patch('local_osint.connectors.fetch_public_response', side_effect=response) as discovery, patch('local_osint.collector.fetch_public', return_value=(profile, 'html', 'https://club.example.org/volunteers')) as pages:
            settings = {'packs': ['website_sources'], 'seed_urls': [], 'public_usernames': [], 'runtime_seconds': 600, 'keep_working': False}
            self.assertEqual(self.request('/api/workspace/settings', {'case': self.case, 'settings': settings})[0], 200)
            self.assertEqual(self.request('/api/action', {'case': self.case, 'connector': 'website_sources', 'arguments': {'url': 'https://club.example.org/'}})[0], 202)
            self.wait()
            pages.assert_not_called()  # A manual source action runs that tool, retaining follow-ups in the queue.
            state = self.request('/api/workspace?case=' + self.case)[1]
            self.assertTrue(any(task['state'] == 'pending' and task['connector'] == 'collect_page' for task in state['tasks']))
            saved = [x for x in self.engine.export(self.case)['evidence'] if x['source'] == 'https://club.example.org/'][0]
            self.assertEqual(saved['sha256'], hashlib.sha256(home).hexdigest())
            self.assertEqual(self.request('/api/resume', {'case': self.case})[0], 202)
            self.wait()
            state = self.request('/api/workspace?case=' + self.case)[1]
            self.assertEqual(state['counts']['readable_documents'], 1)
            self.assertTrue(state['claims'])
            self.assertTrue(all(c['status'] == 'unconfirmed' for c in state['claims']))
            exported = self.request('/api/export?case=' + self.case)[1]
            original = next(x for x in exported['evidence'] if x['id'] == saved['id'])
            self.assertEqual(base64.b64decode(original['original_base64']), home)
            self.assertIn('workspace', exported)

    def test_offline_email_headers_use_saved_original_and_zero_network_units(self):
        raw = b'From: Public Sender <sender@example.org>\nTo: Recipient <recipient@example.org>\nSubject: Community programme\n\nPublic body.'
        saved = self.engine.ingest(self.case, raw, 'Local message', 'Imported message')
        self.server.jobs.enabled = False
        self.server.allow_network = False
        with patch('local_osint.collector.fetch_public') as network, patch('local_osint.connectors.fetch_public_response') as source:
            code, _ = self.request('/api/action', {'case': self.case, 'connector': 'email_headers', 'arguments': {'evidence_id': saved['id']}})
            self.assertEqual(code, 202)
            report = self.wait()
            self.assertEqual(report['request_units'], 0)
            self.assertEqual(report['status'], 'complete')
            network.assert_not_called()
            source.assert_not_called()
            self.assertEqual(self.engine.evidence(self.case, saved['id'])['sha256'], hashlib.sha256(raw).hexdigest())
            self.assertTrue(self.request('/api/workspace?case=' + self.case)[1]['claims'])

    def test_new_routes_keep_authentication_origin_and_case_boundaries(self):
        for path in ['/api/connectors', '/api/workspace?case=' + self.case, '/api/search-service']:
            self.assertEqual(self.request(path, auth=False)[0], 401)
        self.assertEqual(self.request('/api/workspace/settings', {'case': self.case, 'settings': {}}, origin='https://evil.example')[0], 403)
        self.assertEqual(self.request('/api/workspace?case=missing')[0], 400)
        self.assertEqual(self.request('/api/action', {'case': self.case, 'connector': 'shell', 'arguments': {'command': 'anything'}})[0], 400)
        self.assertEqual(self.request('/api/workspace/settings', {'case': self.case, 'settings': {'shell': True}})[0], 400)

    def test_explicit_task_retry_runs_only_that_action_and_retains_attempt_history(self):
        payload = {'message': {'items': []}}
        raw = json.dumps(payload).encode()
        def response(url, **kwargs):
            return {'raw': raw, 'format': 'json', 'canonical': url, 'status': 200, 'requests': 1}
        with patch('local_osint.connectors.fetch_public_response', side_effect=response) as network:
            self.request('/api/action', {'case': self.case, 'connector': 'crossref', 'arguments': {'query': 'Bea Public'}})
            self.wait()
            task = self.request('/api/workspace?case=' + self.case)[1]['tasks'][0]
            self.request('/api/task', {'case': self.case, 'id': task['id'], 'operation': 'retry'})
            self.wait()
            retried = next(x for x in self.request('/api/workspace?case=' + self.case)[1]['tasks'] if x['id'] == task['id'])
            self.assertEqual(retried['attempts'], 2)
            self.assertEqual(len(retried['attempt_history']), 2)
            self.assertEqual(network.call_count, 2)


if __name__ == '__main__':
    unittest.main()
