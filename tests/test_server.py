import base64
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
import time
from unittest.mock import patch

from local_osint.engine import Engine
from local_osint.server import LocalServer, MAX_REQUEST


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.engine = Engine(Path(self.temp.name) / 'private')
        self.server = LocalServer(self.engine)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        self.thread.start()
        self.cookie = 'osint_session=' + self.server.token

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, path, data=None, headers=None, authenticated=True):
        default = {'X-OSINT-Local': '1', 'Origin': self.server.origin}
        if authenticated:
            default['Cookie'] = self.cookie
        if data is not None:
            default['Content-Type'] = 'application/json'
        default.update(headers or {})
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.request('POST' if data is not None else 'GET', path, json.dumps(data).encode() if data is not None else None, headers=default)
            response = connection.getresponse()
            raw = response.read()
            return response.status, dict(response.getheaders()), json.loads(raw) if response.getheader('Content-Type', '').startswith('application/json') else raw
        finally:
            connection.close()

    def test_launch_exchange_and_cookie_attributes(self):
        status, headers, result = self.request('/api/session', {'token': self.server.token}, authenticated=False)
        self.assertEqual(status, 200)
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', headers['Set-Cookie'])
        status, _, _ = self.request('/api/session', {'token':'incorrect'}, authenticated=False)
        self.assertEqual(status, 401)
        status, _, _ = self.request('/api/session', {'token':123}, authenticated=False)
        self.assertEqual(status, 401)

    def test_authentication_required_for_private_data(self):
        for path in ['/api/status', '/api/prompts', '/api/cases', '/api/view?case=x', '/api/evidence?case=x&id=y', '/api/export?case=x', '/api/entity?case=x&id=y', '/api/investigation?case=x', '/api/ai']:
            with self.subTest(path=path):
                self.assertEqual(self.request(path, authenticated=False)[0], 401)
        self.assertEqual(self.request('/api/cases', headers={'X-OSINT-Local':''})[0], 401)
        self.assertEqual(self.request('/api/cases', headers={'Cookie':'osint_session=wrong'})[0], 401)

    def test_local_prompt_configuration_is_discoverable(self):
        from local_osint.prompts import DEFAULT_PROMPTS
        status, _, settings = self.request('/api/prompts')
        self.assertEqual(status, 200)
        self.assertEqual(set(settings['defaults']), set(DEFAULT_PROMPTS))
        self.assertEqual({p['id'] for p in settings['providers']}, {'duckduckgo', 'bing'})

    def test_browser_origin_and_dns_rebinding_are_blocked(self):
        for headers in [{'Origin':'https://evil.example'}, {'Host':'evil.example'}, {'Host':'localhost:' + str(self.server.server_port)}, {'Sec-Fetch-Site':'cross-site'}]:
            self.assertEqual(self.request('/api/cases', headers=headers)[0], 403)
        self.assertEqual(self.request('/api/cases', {'name':'Forged'}, headers={'Origin':''})[0], 403)
        self.assertEqual(len(self.engine.cases()), 0)

    def test_safe_static_assets_and_no_filesystem_routes(self):
        status, headers, raw = self.request('/', authenticated=False)
        self.assertEqual(status, 200)
        self.assertIn(b'Evidence workspace', raw)
        self.assertIn("default-src 'none'", headers['Content-Security-Policy'])
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(headers['Referrer-Policy'], 'no-referrer')
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertNotIn('Access-Control-Allow-Origin', headers)
        for path in ['/../pyproject.toml', '/%2e%2e/pyproject.toml', '/evidence.sqlite3', '/favicon.ico']:
            self.assertEqual(self.request(path, authenticated=False)[0], 404)

    def test_full_browser_api_workflow(self):
        status, _, case = self.request('/api/cases', {'name':'Example Foundation', 'focus_type':'organization', 'aliases':['Example'], 'identifiers':['example.org']})
        self.assertEqual(status, 201)
        status, _, saved = self.request('/api/import', {'case':case['id'], 'title':'Public note <script>alert(1)</script>', 'source':'public bulletin', 'format':'text', 'original_base64':base64.b64encode(b'hello@example.org').decode()})
        self.assertEqual(status, 201)
        status, _, view = self.request('/api/view?case=' + case['id'])
        self.assertEqual(view['counts']['evidence'], 1)
        self.assertEqual(view['case']['focus_type'], 'organization')
        self.assertEqual(view['case']['aliases'], ['Example'])
        status, headers, evidence = self.request('/api/evidence?case=' + case['id'] + '&id=' + saved['id'])
        self.assertEqual(evidence['text'], 'hello@example.org')
        self.assertIn('<script>', evidence['title'])  # returned as JSON data, never markup
        status, headers, report = self.request('/api/export?case=' + case['id'])
        self.assertIn('attachment', headers['Content-Disposition'])
        self.assertEqual(base64.b64decode(report['evidence'][0]['original_base64']), b'hello@example.org')
        status, headers, markdown = self.request('/api/export?case=' + case['id'] + '&format=markdown')
        self.assertNotIn(b'<script>', markdown)

    def test_bad_inputs_and_request_limits_do_not_write(self):
        for body in [[], {'name':None}, {'name':'x', 'focus_type':'invalid'}, {'name':'x', 'aliases':'oops'}, {'name':'x', 'identifiers':[123]}]:
            self.assertEqual(self.request('/api/cases', body)[0], 400)
        self.assertEqual(self.request('/api/import', {'original_base64':'??'})[0], 400)
        self.assertEqual(self.request('/api/cases', {'name':'x'}, headers={'Content-Type':'text/plain'})[0], 400)
        self.assertEqual(self.request('/api/cases', {'name':'x'}, headers={'Content-Length':str(MAX_REQUEST+1)})[0], 400)
        self.assertEqual(len(self.engine.cases()), 0)

    def test_offline_status_and_all_network_endpoints(self):
        self.assertFalse(self.request('/api/status')[2]['network'])
        for path, data in [('/api/collect', {'url':'https://example.org/'}), ('/api/research', {'domain':'example.org','signals':['dns']}), ('/api/discover', {'sources':['wikipedia']}), ('/api/investigate', {'case':'missing'})]:
            self.assertEqual(self.request(path, data)[0], 400)

    def test_automatic_job_api_is_authenticated_and_case_scoped(self):
        self.server.jobs.enabled = True
        case = self.request('/api/cases', {'name': 'Ada Lovelace', 'focus_type': 'person'})[2]
        def public(url, allow_query=False):
            if 'wikipedia' in url:
                return b'{"query":{"search":[]}}', 'json', url
            if 'wikidata' in url:
                return b'{"search":[]}', 'json', url
            return b'{"articles":[]}', 'json', url
        with patch('local_osint.collector.fetch_public', side_effect=public):
            self.assertEqual(self.request('/api/investigate', {'case': case['id']}, authenticated=False)[0], 401)
            response = self.request('/api/investigate', {'case': case['id'], 'options': {'tools': ['wikipedia', 'wikidata', 'news']}})
            self.assertEqual(response[0], 202)
            for _ in range(100):
                report = self.request('/api/investigation?case=' + case['id'])[2]
                if report['status'] != 'running':
                    break
                time.sleep(.01)
            self.assertEqual(report['status'], 'complete')
            self.assertEqual(report['case_id'], case['id'])
            self.assertEqual(len(report['source_ids']), 3)
        self.assertEqual(self.request('/api/stop', {'case': 'other', 'run_id': report['run_id']})[0], 400)
        exported = self.request('/api/export?case=' + case['id'])[2]
        self.assertEqual(exported['investigation']['run_id'], report['run_id'])

    def test_ai_start_uses_only_server_configuration(self):
        with patch.object(self.server.ai, 'start', return_value={'state': 'loading'}) as start:
            self.assertEqual(self.request('/api/ai/start', {}, authenticated=False)[0], 401)
            self.assertEqual(self.request('/api/ai/start', {'model': '/some/arbitrary/path', 'endpoint': 'https://remote.example'})[0], 202)
            start.assert_called_once_with()
        case = self.engine.create_case('Sample')
        self.assertEqual(self.request('/api/investigate', {'case': case['id'], 'ai': True})[0], 400)

    def test_continue_is_authenticated_and_uses_only_owned_planner(self):
        with patch.object(self.server.jobs, 'start', return_value={'status': 'running'}) as start:
            payload = {'case': 'saved-case', 'options': {'max_requests': 100, 'page_limit': 70},
                       'model': 'remote-model', 'endpoint': 'https://remote.example'}
            self.assertEqual(self.request('/api/continue', payload, authenticated=False)[0], 401)
            start.assert_not_called()
            self.assertEqual(self.request('/api/continue', payload)[0], 202)
            start.assert_called_once_with('saved-case', options=payload['options'], continuing=True)

    def test_tool_retry_has_authentication_single_job_and_retained_history(self):
        self.server.jobs.enabled = True
        case = self.engine.create_case('Eric Mulhall', 'person')
        html = b'<a class="result-link" href="https://example.org/eric">Eric Mulhall profile</a><td class="result-snippet">Public profile</td>'
        def wait():
            for _ in range(100):
                report = self.request('/api/investigation?case=' + case['id'])[2]
                if report and report['status'] != 'running':
                    return report
                time.sleep(.01)
            self.fail('Tool did not finish')
        with patch('local_osint.collector.fetch_public', return_value=(html, 'html', 'https://lite.duckduckgo.com/lite/?q=Eric')) as fetch:
            payload = {'case': case['id'], 'tool': 'web_search', 'arguments': {'query': '"Eric Mulhall"'}}
            self.assertEqual(self.request('/api/tool', payload, authenticated=False)[0], 401)
            self.assertEqual(self.request('/api/tool', payload)[0], 202)
            first = wait()
            payload['arguments']['query'] = '"Eric Mulhall" Dublin'
            self.assertEqual(self.request('/api/tool', payload)[0], 202)
            second = wait()
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(second['parent_run_id'], first['run_id'])
        self.assertEqual(second['options']['query'], '"Eric Mulhall" Dublin')
        self.assertEqual(len(second['steps']), 2)
        self.assertEqual(second['budget']['used'], 1)
        self.assertEqual(self.request('/api/tool', {'case': case['id'], 'tool': 'shell', 'arguments': {}})[0], 400)

    def test_offline_tool_reads_are_local_and_other_case_reads_fail(self):
        case = self.engine.create_case('Local')
        other = self.engine.create_case('Other')
        saved = self.engine.ingest(case['id'], b'Saved offline evidence', 'Offline')
        with patch('local_osint.collector.fetch_public') as fetch:
            self.assertEqual(self.request('/api/tool', {'case': case['id'], 'tool': 'web_search', 'arguments': {'query': 'Local'}})[0], 400)
            self.assertEqual(self.request('/api/tool', {'case': case['id'], 'tool': 'read_evidence', 'arguments': {'id': saved['id']}})[0], 202)
            for _ in range(100):
                report = self.request('/api/investigation?case=' + case['id'])[2]
                if report['status'] != 'running':
                    break
                time.sleep(.01)
            self.assertEqual(report['status'], 'complete')
            self.assertEqual(report['budget']['used'], 0)
            self.assertEqual(self.request('/api/tool', {'case': other['id'], 'tool': 'read_evidence', 'arguments': {'id': saved['id']}})[0], 202)
            for _ in range(100):
                report = self.request('/api/investigation?case=' + other['id'])[2]
                if report['status'] != 'running':
                    break
                time.sleep(.01)
            self.assertEqual(report['status'], 'failed')
            fetch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
