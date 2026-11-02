"""Public GitHub collection preserves originals, exact passages and disclosure scope."""

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from local_osint.connectors import execute, validate_arguments
from local_osint.engine import Engine, InvalidInput
from local_osint.frontier import Frontier
from local_osint.investigation import Investigation
from local_osint.workspace import Workspace


REPOSITORY = 'https://github.com/public-fixture/research'
ISSUE = REPOSITORY + '/issues/7'
API = 'https://api.github.com/repos/public-fixture/research'


class GitHubTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = Engine(Path(self.temp.name).resolve() / 'private')
        self.case = self.engine.create_case('Public fixture', 'organization', identifiers=['private-handle'])['id']
        self.workspace = Workspace(self.engine)

    def tearDown(self):
        self.temp.cleanup()

    def response(self, data, url=API, status=200, **extra):
        raw = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        return {'raw': raw, 'format': 'json', 'canonical': url, 'status': status, 'requests': 1, **extra}

    def repository(self, **extra):
        return {'id': 1, 'private': False, 'full_name': 'public-fixture/research',
                'html_url': REPOSITORY, 'description': 'Public fixture research project.', **extra}

    def readme(self, body='A multiline README.\nReadmeuniquetoken supports public research.\n'):
        raw = body.encode()
        return {'type': 'file', 'encoding': 'base64', 'content': base64.b64encode(raw).decode(),
                'size': len(raw), 'sha': 'a' * 40, 'path': 'README.md',
                'download_url': 'https://private.example.net/never-follow'}

    def issue(self, **extra):
        return {'id': 7, 'number': 7, 'repository_url': API, 'html_url': ISSUE,
                'title': 'Public fixture discussion', 'body': 'First issue line.\nSecond issue line.',
                'user': {'login': 'author-one'}, 'created_at': '2026-10-01T12:00:00Z', 'comments': 2, **extra}

    def comment(self, identifier=11, author='author-two', body='Repeated comment.\nSecond comment line.'):
        return {'id': identifier, 'issue_url': API + '/issues/7', 'user': {'login': author},
                'created_at': '2026-10-02T12:00:00Z', 'body': body}

    def runner(self, **options):
        return Investigation(self.engine, self.case, enabled=True,
                             options={'tools': [], 'packs': ['website_sources'], 'max_requests': 2, 'page_limit': 4, **options})

    def assert_originals(self, result, responses):
        with self.engine.connection() as db:
            for evidence_id, response in zip(result['evidence_ids'], responses, strict=True):
                row = db.execute('SELECT raw,sha256 FROM evidence WHERE id=?', (evidence_id,)).fetchone()
                self.assertEqual(row['raw'], response['raw'])
                self.assertEqual(row['sha256'], hashlib.sha256(response['raw']).hexdigest())

    def test_repository_readme_keep_raw_json_and_searchable_versioned_text(self):
        responses = [self.response(self.repository()), self.response(self.readme(), API + '/readme')]
        with patch('local_osint.connectors.fetch_public_response', side_effect=responses) as fetch:
            result = execute(self.engine, self.case, 'github_repository', {'url': REPOSITORY}, True)
        self.assertEqual(result['status'], 'success')
        self.assertTrue(result['complete'])
        self.assertEqual(result['requests'], 2)
        self.assert_originals(result, responses)
        self.assertEqual([call.args[0] for call in fetch.call_args_list], [API, API + '/readme'])
        self.assertTrue(all(call.kwargs == {'allow_query': True, 'allowed_hosts': {'api.github.com'}, 'accept': 'github'} for call in fetch.call_args_list))
        evidence = self.engine.evidence(self.case, result['evidence_ids'][1])
        self.assertIn('content', evidence['text'])
        self.assertNotIn('Readmeuniquetoken', evidence['text'])
        extraction = evidence['extraction']
        self.assertIn('README.\nReadmeuniquetoken', extraction['text'])
        self.assertEqual(extraction['evidence_sha256'], evidence['sha256'])
        self.assertEqual(extraction['text_sha256'], hashlib.sha256(extraction['text'].encode()).hexdigest())
        self.assertEqual(self.engine.view(self.case, 'Readmeuniquetoken')['evidence'][0]['id'], evidence['id'])
        self.assertEqual(self.workspace.retrieve(self.case, 'Readmeuniquetoken')[0]['evidence_id'], evidence['id'])
        other = self.engine.create_case('Other fixture')['id']
        self.assertFalse(self.engine.view(other, 'Readmeuniquetoken')['evidence'])
        with self.assertRaises(InvalidInput):
            self.engine.evidence(other, evidence['id'])
        exported = self.engine.export(self.case)
        item = next(item for item in exported['evidence'] if item['id'] == evidence['id'])
        self.assertEqual(base64.b64decode(item['original_base64']), responses[1]['raw'])
        self.assertTrue(any(item['evidence_id'] == evidence['id'] and 'Readmeuniquetoken' in item['text'] for item in exported['extractions']))

    def test_issue_comments_preserve_authors_and_duplicate_multiline_spans(self):
        comments = [self.comment(), self.comment(12, 'dependabot[bot]')]
        responses = [self.response(self.issue(), API + '/issues/7'),
                     self.response(comments, API + '/issues/7/comments?per_page=30&page=1')]
        with patch('local_osint.connectors.fetch_public_response', side_effect=responses):
            runner = self.runner()
            runner.run(action={'tool': 'github_issue', 'arguments': {'url': ISSUE}})
        result = runner.report['source_outcomes'][0]
        self.assertEqual(result['status'], 'success')
        self.assertTrue(result['complete'])
        self.assertEqual(result['comments']['captured'], 2)
        self.assertEqual([item['author'] for item in result['comments']['authors']], ['author-two', 'dependabot[bot]'])
        self.assert_originals(result, responses)
        claims = self.workspace.snapshot(self.case)['claims']
        self.assertEqual(len(claims), 3)
        self.assertTrue(all(claim['status'] == 'unconfirmed' and not claim['corroborated'] for claim in claims))
        references = [ref for claim in claims for ref in claim['references']]
        self.assertTrue(any('\n' in ref['quote'] for ref in references))
        for ref in references:
            evidence = self.engine.evidence(self.case, ref['evidence_id'])
            self.assertEqual(evidence['extraction']['text'][ref['start']:ref['end']], ref['quote'])
            self.assertEqual(ref['evidence_sha256'], evidence['sha256'])
        repeated = [ref for ref in references if ref['quote'].startswith('Repeated comment.')]
        self.assertEqual(len(repeated), 2)
        self.assertNotEqual(repeated[0]['start'], repeated[1]['start'])

    def test_comments_first_page_is_bounded_and_discussion_not_claimed_complete(self):
        records = [self.comment(number, 'author-two', 'Comment ' + str(number)) for number in range(1, 31)]
        responses = [self.response(self.issue(comments=300), API + '/issues/7'), self.response(records)]
        with patch('local_osint.connectors.fetch_public_response', side_effect=responses) as fetch:
            result = execute(self.engine, self.case, 'github_issue', {'url': ISSUE}, True)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(result['comments']['captured'], 30)
        self.assertFalse(result['comments']['complete'])
        self.assertFalse(result['complete'])
        self.assertTrue(result['partial'])
        self.assertEqual(self.engine.evidence(self.case, result['evidence_ids'][1])['extraction']['status'], 'limited')

    def test_valid_empty_comment_set_skips_second_operation(self):
        with patch('local_osint.connectors.fetch_public_response', return_value=self.response(self.issue(comments=0))) as fetch:
            result = execute(self.engine, self.case, 'github_issue', {'url': ISSUE}, True)
        self.assertEqual(fetch.call_count, 1)
        self.assertTrue(result['complete'])
        self.assertEqual(result['comments']['captured'], 0)

    def test_invalid_urls_offline_invalid_case_and_extra_fields_never_dispatch(self):
        bad = ['http://github.com/a/b', 'https://github.com/a/b?token=private', 'https://github.com/a/b#x',
               'https://github.com/a/b.git', 'https://github.com/a/..', 'https://github.com/a/b/../c',
               'https://github.com/a/b%2fc', 'https://github.com/a%2fb/c', 'https://api.github.com/a/b',
               'https://github.com.evil.example/a/b', 'https://user@github.com/a/b', 'https://github.com/a/b/issues/0']
        with patch('local_osint.connectors.fetch_public_response') as fetch, patch('socket.getaddrinfo') as dns:
            for value in bad:
                with self.subTest(url=value), self.assertRaises(InvalidInput):
                    validate_arguments('github_repository', {'url': value})
            with self.assertRaises(InvalidInput):
                execute(self.engine, self.case, 'github_repository', {'url': REPOSITORY})
            with self.assertRaises(InvalidInput):
                execute(self.engine, 'missing', 'github_repository', {'url': REPOSITORY}, True)
            with self.assertRaises(InvalidInput):
                validate_arguments('github_issue', {'url': ISSUE, 'token': 'not-supported'})
            fetch.assert_not_called()
            dns.assert_not_called()

    def test_unexpected_private_mismatched_and_pr_payloads_keep_raw_without_passages(self):
        cases = [('github_repository', REPOSITORY, self.repository(private=True)),
                 ('github_repository', REPOSITORY, self.repository(full_name='other/repo')),
                 ('github_issue', ISSUE, self.issue(number=8)),
                 ('github_issue', ISSUE, self.issue(pull_request={'url': API + '/pulls/7'}))]
        for connector, url, payload in cases:
            response = self.response(payload)
            with self.subTest(payload=payload), patch('local_osint.connectors.fetch_public_response', return_value=response) as fetch:
                result = execute(self.engine, self.case, connector, {'url': url}, True)
                self.assertEqual(fetch.call_count, 1)
                self.assertEqual(result['status'], 'uninterpretable')
                self.assertFalse(result['observations'])
                self.assert_originals(result, [response])

    def test_malformed_and_failed_secondary_originals_leave_primary_available(self):
        for secondary, expected in [(self.response(b'{broken'), 'uninterpretable'),
                                    (self.response({'message': 'Not Found'}, status=404), 'empty'),
                                    (self.response({'message': 'Forbidden'}, status=403), 'blocked')]:
            responses = [self.response(self.repository()), secondary]
            with self.subTest(status=expected), patch('local_osint.connectors.fetch_public_response', side_effect=responses):
                result = execute(self.engine, self.case, 'github_repository', {'url': REPOSITORY}, True)
            self.assertEqual(result['status'], expected)
            self.assertFalse(result['complete'])
            self.assertTrue(result['partial'])
            self.assertTrue(result['observations'])
            self.assert_originals(result, responses)

    def test_invalid_readme_encoding_or_size_never_creates_decoded_passages(self):
        for changed in ({'content': 'not-base64!'}, {'size': 999999}, {'encoding': 'none'}, {'content': base64.b64encode(b'bad\x00text').decode(), 'size': 8}):
            responses = [self.response(self.repository()), self.response({**self.readme(), **changed})]
            with self.subTest(changed=changed), patch('local_osint.connectors.fetch_public_response', side_effect=responses):
                result = execute(self.engine, self.case, 'github_repository', {'url': REPOSITORY}, True)
            self.assertEqual(result['status'], 'uninterpretable')
            self.assertFalse(any(item['kind'] == 'github_readme_passage' for item in result['observations']))
            self.assertNotIn('extraction', self.engine.evidence(self.case, result['evidence_ids'][1]))
            self.assert_originals(result, responses)

    def test_cancellation_between_parts_retains_first_original_and_stops_second_fetch(self):
        allowed = iter([True, False])
        with patch('local_osint.connectors.fetch_public_response', return_value=self.response(self.repository())) as fetch:
            result = execute(self.engine, self.case, 'github_repository', {'url': REPOSITORY}, True,
                             settings={'can_request': lambda: next(allowed)})
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(len(result['evidence_ids']), 1)
        self.assertTrue(result['partial'])
        self.assertFalse(result['complete'])

    def test_dispatch_reserves_both_operations_before_any_outbound_request(self):
        with patch('local_osint.connectors.fetch_public_response') as fetch:
            runner = self.runner(max_requests=1)
            report = runner.run(action={'tool': 'github_repository', 'arguments': {'url': REPOSITORY}})
        fetch.assert_not_called()
        self.assertEqual(report['status'], 'limited')

    def test_seeded_github_resources_run_one_bounded_action_without_self_followups(self):
        for connector, url, responses in [
            ('github_repository', REPOSITORY, [self.response(self.repository()), self.response(self.readme())]),
            ('github_issue', ISSUE, [self.response(self.issue(comments=0))]),
        ]:
            with self.subTest(connector=connector), patch('local_osint.connectors.fetch_public_response', side_effect=responses) as fetch:
                runner = self.runner(packs=[connector], seed_urls=[url], max_requests=8)
                runner.run()
                self.assertEqual(fetch.call_count, len(responses))
                outcomes = [item for item in runner.report['source_outcomes'] if item['connector'] == connector]
                self.assertEqual(len(outcomes), 1)
                self.assertEqual(runner.report['request_units'], 2)
                self.assertEqual(len([task for task in self.workspace.tasks(self.case) if task['connector'] == connector]), 1)

    def test_scope_requires_enabled_pack_and_seed_or_recorded_public_reference(self):
        runner = self.runner(packs=['github_repository'], seed_urls=[REPOSITORY])
        Frontier(runner).seed()
        self.assertTrue(any(task['connector'] == 'github_repository' for task in self.workspace.tasks(self.case)))
        other = 'https://github.com/public-fixture/other'
        frontier = Frontier(runner)
        with self.assertRaises(InvalidInput):
            frontier.enqueue('github_repository', {'url': other}, 'Invented source')
        imported = self.engine.ingest(self.case, other.encode(), 'Imported text', 'https://public.example.org/')
        with self.assertRaises(InvalidInput):
            frontier.enqueue('github_repository', {'url': other}, 'Import is not collection authority', source_id=imported['id'])
        with self.assertRaises(InvalidInput):
            frontier.enqueue('github_repository', {'url': 'https://github.com/private-handle/research'}, 'Private clue', explicit=True)
        with self.assertRaises(InvalidInput):
            Frontier(self.runner()).enqueue('github_repository', {'url': REPOSITORY}, 'Disabled pack')
        with patch('local_osint.connectors.fetch_public_response') as fetch:
            with self.assertRaises(InvalidInput):
                Investigation(self.engine, self.case, enabled=False, options={'tools': [], 'packs': ['github_repository'], 'max_requests': 2})
        fetch.assert_not_called()

    def test_search_lead_without_receipt_falls_back_and_recorded_lead_routes_github(self):
        runner = self.runner(packs=['github_repository'])
        runner.report['web_results'] = [{'url': REPOSITORY, 'title': 'Public fixture repository', 'relevant': True}]
        Frontier(runner).discover_report()
        self.assertEqual(self.workspace.tasks(self.case)[0]['connector'], 'collect_page')
        saved = self.engine.ingest(self.case, json.dumps({'url': REPOSITORY}).encode(), 'Public search', 'https://search.example.org/', 'json')
        task = self.workspace.enqueue(self.case, 'web_search', {'query': 'Public fixture'})
        self.workspace.start_action(self.case, task['id'], runner.report['run_id'])
        self.workspace.finish_action(self.case, task['id'], 'complete', evidence_ids=[saved['id']])
        runner.report['web_results'][0]['source_id'] = saved['id']
        Frontier(runner).discover_report()
        self.assertTrue(any(task['connector'] == 'github_repository' for task in self.workspace.tasks(self.case)))

    def test_persisted_source_lead_remains_valid_without_in_memory_allowlist(self):
        runner = self.runner(packs=['github_repository'])
        original = self.engine.ingest(self.case, b'{"url":"https:\\/\\/github.com\\/public-fixture\\/research"}',
                                      'Public source links', 'https://api.crossref.org/works', 'json')
        task = self.workspace.enqueue(self.case, 'crossref', {'query': 'Public fixture'})
        self.workspace.start_action(self.case, task['id'], runner.report['run_id'])
        self.workspace.finish_action(self.case, task['id'], 'complete',
                                     result={'leads': [{'url': REPOSITORY}]}, evidence_ids=[original['id']])
        self.assertNotIn(REPOSITORY, self.engine.evidence(self.case, original['id'])['text'])
        queued = Frontier(runner).enqueue('github_repository', {'url': REPOSITORY},
                                         'Persisted public lead', source_id=original['id'])
        resumed = self.runner(packs=['github_repository'])
        self.assertFalse(resumed.allowed_urls)
        self.assertEqual(Frontier(resumed).guard(queued['connector'], queued['arguments'], source_id=queued['source_id'])['url'], REPOSITORY)
        self.assertEqual(validate_arguments('github_repository', {'url': REPOSITORY + '/'})['url'], REPOSITORY)

    def test_rate_limit_reset_and_retry_hint_survive_third_attempt_and_block_auto_retry(self):
        runner = self.runner()
        task = Frontier(runner).enqueue('github_issue', {'url': ISSUE}, 'Exact public issue', explicit=True)
        with self.engine.connection() as db:
            db.execute('UPDATE workspace_tasks SET attempts=2 WHERE id=?', (task['id'],))
        response = self.response({'message': 'API rate limit exceeded'}, status=403,
                                 rate_limit_remaining=0, rate_limit_reset=int(time.time()) + 7200, retry_after=900)
        with patch('local_osint.connectors.fetch_public_response', return_value=response):
            runner.run(action={'tool': 'github_issue', 'arguments': {'url': ISSUE}})
        task_id = task['id']
        task = next(item for item in self.workspace.tasks(self.case) if item['id'] == task_id)
        self.assertEqual(task['attempts'], 3)
        self.assertEqual(task['state'], 'cooldown')
        self.assertGreater((datetime.fromisoformat(task['retry_at']) - datetime.now(timezone.utc)).total_seconds(), 7100)
        with self.assertRaises(InvalidInput):
            self.workspace.start_action(self.case, task['id'], 'retry-now')
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with self.engine.connection() as db:
            db.execute('UPDATE workspace_tasks SET retry_at=? WHERE id=?', (past, task['id']))
            db.execute('UPDATE workspace_health SET retry_at=? WHERE case_id=?', (past, self.case))
        with patch('local_osint.connectors.fetch_public_response') as fetch:
            Frontier(self.runner()).run_queue()
        fetch.assert_not_called()

    def test_github_transport_uses_fixed_headers_without_ambient_auth(self):
        from local_osint.collector import fetch_public_response
        payload = b'{"message":"API rate limit exceeded"}'
        headers = {'Content-Type': 'application/json', 'Content-Length': str(len(payload)),
                   'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset': str(int(time.time()) + 3600), 'Retry-After': '600'}
        response = Mock(status=403)
        response.getheader.side_effect = lambda name, default=None: headers.get(name, default)
        response.read1.side_effect = [payload, b'']
        with patch.dict(os.environ, {'GH_TOKEN': 'ambient-fixture', 'GITHUB_TOKEN': 'ambient-fixture', 'HTTPS_PROXY': 'http://private.invalid/'}), \
             patch('local_osint.collector.target', return_value=('api.github.com', '8.8.8.8', '/repos/public-fixture/research', API)), \
             patch('local_osint.collector.PinnedHTTPS') as connection:
            connection.return_value.getresponse.return_value = response
            result = fetch_public_response(API, allowed_hosts={'api.github.com'}, accept='github')
        actual = connection.return_value.request.call_args.kwargs['headers']
        self.assertEqual(actual['Accept'], 'application/vnd.github+json')
        self.assertEqual(actual['X-GitHub-Api-Version'], '2026-03-10')
        self.assertNotIn('Authorization', actual)
        self.assertNotIn('Cookie', actual)
        self.assertNotIn('ambient-fixture', str(actual))
        self.assertEqual(result['rate_limit_remaining'], 0)
        self.assertEqual(result['retry_after'], 600)


if __name__ == '__main__':
    unittest.main()
