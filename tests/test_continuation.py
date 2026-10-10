"""Continuation retains evidence, recovers public work and enforces privacy."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from local_osint.engine import Engine, InvalidInput
from local_osint.investigation import Investigation, Jobs, save_report


class ContinuationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.engine = Engine(Path(self.temp.name) / 'private')
        self.case = self.engine.create_case('Ada Lovelace', 'person', identifiers=['private@example.net'])
        self.ai = Mock()
        self.ai.status.return_value = {'state': 'ready'}

    def tearDown(self):
        self.temp.cleanup()

    def previous(self, useful=True):
        runner = Investigation(self.engine, self.case['id'], True, options={'tools': ['web_search'], 'page_limit': 2})
        saved = self.engine.ingest(self.case['id'], b'Ada Lovelace public original', 'Original', 'https://old.example.org/ada')
        runner.report.update(status='partial', source_ids=[saved['id']], pages=[{
            'url': 'https://old.example.org/ada', 'source_id': saved['id'], 'run_id': runner.report['run_id'],
            'useful': useful, 'excerpt': 'Ada Lovelace public original', 'links': [],
            'quality_reason': 'Readable content' if useful else 'Source returned a login or authentication wall.'}],
                    steps=[{'id': 'old-step', 'tool': 'collect_page', 'arguments': {'url': 'https://blocked.example.net/profile'},
                    'status': 'failed', 'error': 'Destination returned HTTP 999.', 'ok': False}],
            web_results=[{'url': 'https://blocked.example.net/another', 'title': 'Ada Lovelace', 'snippet': '', 'relevant': True}])
        save_report(self.engine, runner.report)
        return copy.deepcopy(runner.report)

    def runner(self, previous, **kwargs):
        return Investigation(self.engine, self.case['id'], True, ai=self.ai, previous=previous,
                             options=previous['options'], continuing=True, **kwargs)

    def draft(self, runner):
        def analyze():
            self.assertFalse(runner.report['network_enabled'])
            runner.report['ai'] = {'status': 'draft', 'source_ids': runner.report['source_ids'][:1]}
        return patch.object(runner, 'analyze', side_effect=analyze)

    def test_partial_recovery_uses_alternative_source_and_preserves_original_bytes(self):
        previous = self.previous()
        before = self.engine.evidence(self.case['id'], previous['source_ids'][0])['sha256']
        runner = self.runner(previous)
        search = {'action': 'web_search', 'arguments': {'query': '"Ada Lovelace" official website'}, 'reason': 'Try a public alternative.'}
        page = {'action': 'collect_page', 'arguments': {'url': 'https://new.example.org/ada'}, 'reason': 'Read this public source.'}
        def fetch(url, allow_query=False):
            if 'duckduckgo' in url:
                return b'<a class="result-link" href="https://new.example.org/ada">Ada Lovelace</a><td class="result-snippet">Official profile</td>', 'html', url
            return b'<p>Ada Lovelace wrote extensive mathematical notes on the analytical engine and studied its scientific applications.</p>', 'html', url
        with patch('local_osint.ai.plan_continuation', side_effect=[search, page]), patch('local_osint.collector.fetch_public', side_effect=fetch) as fetcher, patch.object(runner, 'compare_locally'), self.draft(runner):
            report = runner.run()
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['quality_count'], 2)
        self.assertEqual(report['request_units'], 2)
        query = report['continuation']['decisions'][0]['arguments']['query']
        self.assertIn('-site:example.net', query)
        self.assertEqual(report['parent_run_id'], previous['run_id'])
        self.assertTrue(set(previous['source_ids']) <= set(report['source_ids']))
        self.assertEqual(report['steps'][0], previous['steps'][0])
        self.assertEqual(self.engine.evidence(self.case['id'], previous['source_ids'][0])['sha256'], before)
        self.assertFalse(any('blocked.example.net' in call.args[0] or 'private' in call.args[0] for call in fetcher.call_args_list))
        self.assertTrue(report['network_enabled'])

    def test_login_and_previously_failed_urls_are_not_eligible(self):
        runner = self.runner(self.previous(False))
        runner.report['web_results'] += [{'url': 'https://old.example.org/other', 'relevant': True},
                                       {'url': 'https://good.example.org/new', 'relevant': True}]
        runner.allowed_urls.add('https://good.example.org/new')
        self.assertEqual([x['url'] for x in runner.continuation_pages()], ['https://good.example.org/new'])
        self.assertEqual(runner.page_count(), 0)

    def test_current_selected_sources_filter_saved_leads(self):
        runner = self.runner(self.previous())
        runner.report['web_results'].append({'url': 'https://web.example.org/ada', 'relevant': True})
        runner.report['candidates'].append({'collection_url': 'https://wiki.example.org/ada', 'label': 'Ada Lovelace', 'relevant': True, 'provider': 'wikipedia'})
        runner.report['articles'].append({'url': 'https://news.example.org/ada', 'title': 'Ada Lovelace news'})
        for selected, expected in [(['domain'], []), (['wikipedia'], ['https://wiki.example.org/ada']),
                                   (['news'], ['https://news.example.org/ada']),
                                   (['web_search'], ['https://web.example.org/ada'])]:
            runner.options['tools'] = selected
            self.assertEqual([lead['url'] for lead in runner.continuation_pages()], expected)

    def test_private_or_duplicate_plan_recovers_with_fresh_public_work(self):
        for query in ['"Ada Lovelace" private@example.net', '"Ada Lovelace" profile']:
            with self.subTest(query=query):
                previous = self.previous()
                previous['options']['max_requests'] = 2
                previous['steps'].append({'tool': 'web_search', 'arguments': {'query': '"Ada Lovelace" profile -site:example.net'}, 'status': 'success'})
                runner = self.runner(previous)
                decision = {'action': 'web_search', 'arguments': {'query': query}, 'reason': 'Try another query.'}
                outbound = []
                def search(engine, case_id, public_query, enabled, provider='duckduckgo'):
                    outbound.append((provider, public_query))
                    saved = engine.ingest(case_id, public_query.encode(), 'Public search')
                    return {**saved, 'results': []}
                with patch('local_osint.ai.plan_continuation', side_effect=lambda *args, **kwargs: copy.deepcopy(decision)), patch('local_osint.collector.web_search', side_effect=search), patch.object(runner, 'compare_locally'), self.draft(runner):
                    report = runner.run()
                self.assertEqual(report['status'], 'limited')
                self.assertEqual(report['request_units'], 2)
                self.assertEqual(len(outbound), 2)
                pairs = {(provider, tuple(sorted(public_query.lower().split()))) for provider, public_query in outbound}
                self.assertEqual(len(pairs), 2)
                self.assertTrue(all('private@example.net' not in public_query for _, public_query in outbound))
                self.assertTrue(any(d['origin'] == 'engine' for d in report['continuation']['decisions']))
                self.assertTrue(set(previous['source_ids']) <= set(report['source_ids']))
                self.assertNotIn('private@example.net', json.dumps(report['continuation']))

    def test_explicit_public_context_is_not_redacted_before_dispatch(self):
        self.case = self.engine.create_case('Ada Lovelace', 'person', identifiers=['Example Foundation'])
        previous = self.previous()
        previous['options']['context'] = 'Example Foundation'
        previous['options']['max_requests'] = 1
        runner = self.runner(previous)
        plans = [{'action': 'web_search', 'arguments': {'query': '"Ada Lovelace" Example Foundation'}, 'reason': 'Public employer context.'},
                 {'action': 'finish', 'arguments': {}, 'reason': 'No remaining source.'}]
        saved = self.engine.ingest(self.case['id'], b'Empty public search', 'Search')
        with patch('local_osint.ai.plan_continuation', side_effect=plans), patch('local_osint.collector.web_search', return_value={**saved, 'results': []}) as search, patch.object(runner, 'compare_locally'), self.draft(runner):
            report = runner.run()
        search.assert_called_once()
        self.assertTrue(search.call_args.args[2].startswith('"Ada Lovelace" Example Foundation'))
        self.assertEqual(report['continuation']['decisions'][0]['status'], 'success')
        self.assertEqual(report['status'], 'limited')
        self.assertEqual(report['request_units'], 1)

    def test_cancelled_and_time_limited_continuations_keep_previous_evidence(self):
        for cancelled in [True, False]:
            previous = self.previous()
            cancel = threading.Event()
            if cancelled:
                cancel.set()
            runner = self.runner(previous, cancel=cancel, deadline_seconds=300 if cancelled else -1)
            with patch('local_osint.collector.fetch_public') as fetcher:
                report = runner.run()
            fetcher.assert_not_called()
            self.assertEqual(report['status'], 'cancelled' if cancelled else 'limited')
            self.assertEqual(report['source_ids'], previous['source_ids'])

    def test_restart_interrupted_report_can_continue_with_fresh_budget(self):
        previous = self.previous()
        previous.update(status='running', request_units=24)
        save_report(self.engine, previous)
        jobs = Jobs(self.engine, True, self.ai)
        recovered = jobs.get(self.case['id'])
        self.assertEqual(recovered['status'], 'interrupted')
        runner = self.runner(recovered)
        self.assertEqual(runner.used, 0)
        self.assertEqual(runner.page_count(), 1)
        self.assertEqual(runner.report['source_ids'], previous['source_ids'])

    def test_retained_news_article_is_eligible_and_can_be_collected(self):
        previous = self.previous()
        previous['options']['tools'] = ['news']
        previous['articles'] = [{'url': 'https://news.example.org/ada', 'title': 'Ada Lovelace in public news'}]
        runner = self.runner(previous)
        plan = {'action': 'collect_page', 'arguments': {'url': previous['articles'][0]['url']}, 'reason': 'Recover the saved news lead.'}
        raw = b'<p>Ada Lovelace published detailed mathematical notes about the analytical engine and its many scientific applications.</p>'
        with patch('local_osint.ai.plan_continuation', return_value=plan), patch('local_osint.collector.fetch_public', return_value=(raw, 'html', plan['arguments']['url'])) as fetcher, self.draft(runner):
            report = runner.run()
        fetcher.assert_called_once()
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['quality_count'], 2)

    def test_unavailable_local_model_preserves_history_and_never_collects(self):
        previous = self.previous()
        self.ai.status.return_value = {'state': 'stopped'}
        self.ai.start.return_value = {'state': 'unavailable', 'error': 'Install a cached local model.'}
        with patch('local_osint.collector.fetch_public') as fetcher:
            report = self.runner(previous).run()
        fetcher.assert_not_called()
        self.ai.start.assert_called_once()
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['source_ids'], previous['source_ids'])
        self.assertIn('cached local model', report['stop_reason'])

    def test_new_search_leads_survive_full_lead_window(self):
        runner = self.runner(self.previous())
        runner.report['web_results'] = [{'url': f'https://old.example.org/{i}', 'title': 'Ada Lovelace', 'snippet': '', 'relevant': True} for i in range(40)]
        saved = self.engine.ingest(self.case['id'], b'Public search', 'Search')
        with patch('local_osint.collector.web_search', return_value={**saved, 'results': [{'url': 'https://new.example.org/ada', 'title': 'Ada Lovelace', 'snippet': 'Public alternative'}]}):
            runner.web_search('"Ada Lovelace" official website')
        self.assertIn('https://new.example.org/ada', [x['url'] for x in runner.report['web_results']])
        self.assertEqual(len(runner.report['web_results']), 40)

    def test_continue_requires_saved_case_and_network_before_model_start(self):
        with self.assertRaises(InvalidInput):
            Jobs(self.engine, True, self.ai).start(self.case['id'], continuing=True)
        self.previous()
        with self.assertRaises(InvalidInput):
            Jobs(self.engine, False, self.ai).start(self.case['id'], continuing=True)
        self.ai.start.assert_not_called()

    def test_local_drafting_cannot_dispatch_network_tools_even_if_model_ignores_schema(self):
        runner = self.runner(self.previous())
        runner.report['network_enabled'] = False
        runner.allowed_urls.add('https://good.example.org/new')
        runner.allowed_domains.add('good.example.org')
        with patch('local_osint.collector.fetch_public') as fetcher, patch('local_osint.collector.research') as domains:
            for name, args in [('web_search', {'query': '"Ada Lovelace" profile'}),
                               ('collect_page', {'url': 'https://good.example.org/new'}),
                               ('domain_signals', {'domain': 'good.example.org'})]:
                with self.assertRaisesRegex(InvalidInput, 'cannot call outbound'):
                    runner.dispatch(name, args)
            fetcher.assert_not_called()
            domains.assert_not_called()
        self.assertTrue(runner.read_evidence(runner.report['source_ids'][0]))


if __name__ == '__main__':
    unittest.main()
