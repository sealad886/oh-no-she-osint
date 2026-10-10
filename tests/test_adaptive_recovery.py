"""Continuation changes public strategy without losing evidence or bounds."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from local_osint.engine import Engine, InvalidInput
from local_osint.investigation import Investigation, save_report


class AdaptiveRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.engine = Engine(Path(self.temp.name) / 'private')
        self.case = self.engine.create_case('Maya Example', 'person', identifiers=['private@example.net', '+1 202 555 0147'])
        self.ai = Mock()
        self.ai.status.return_value = {'state': 'ready'}
        self.original = self.engine.ingest(self.case['id'], b'Original evidence retained unchanged', 'Original')
        self.original_hash = self.engine.evidence(self.case['id'], self.original['id'])['sha256']
        self.outbound = []

    def tearDown(self):
        self.temp.cleanup()

    def previous(self, **options):
        runner = Investigation(self.engine, self.case['id'], True, options={
            'tools': ['web_search'], 'page_limit': 1, 'max_requests': 6, **options})
        runner.report.update(status='partial', request_units=24, source_ids=[self.original['id']])
        save_report(self.engine, runner.report)
        return copy.deepcopy(runner.report)

    def runner(self, previous):
        return Investigation(self.engine, self.case['id'], True, ai=self.ai, previous=previous,
                            options=previous['options'], continuing=True)

    def search(self, results=None, failure=None):
        def collect(engine, case_id, query, enabled, provider='duckduckgo'):
            self.assertTrue(enabled)
            self.outbound.append((provider, query))
            if failure and failure(provider, query):
                raise InvalidInput('Public provider returned HTTP 429.')
            entries = results(provider, query) if results else []
            saved = engine.ingest(case_id, json.dumps({'provider': provider, 'query': query, 'results': entries}).encode(), 'Public search')
            return {**saved, 'results': entries}
        return collect

    def draft(self, runner):
        def analyze():
            self.assertFalse(runner.report['network_enabled'])
            runner.report['ai'] = {'status': 'draft', 'source_ids': runner.report['source_ids'][:1]}
        return patch.object(runner, 'analyze', side_effect=analyze)

    def assert_retained(self, report):
        self.assertIn(self.original['id'], report['source_ids'])
        self.assertEqual(self.engine.evidence(self.case['id'], self.original['id'])['sha256'], self.original_hash)
        for _, query in self.outbound:
            self.assertNotIn('private@example.net', query)
            self.assertNotIn('202', query)
        self.assertNotIn('private@example.net', json.dumps(report['continuation']))

    @staticmethod
    def lead(url):
        return {'url': url, 'title': 'Maya Example public profile', 'snippet': 'Public professional biography.'}

    @staticmethod
    def useful_page(url, allow_query=False):
        raw = b'<article>Maya Example publishes detailed research about local history and public archives. This public biography describes the published projects and their historical methods.</article>'
        return raw, 'html', url

    def test_failed_provider_is_retired_and_another_provider_can_recover(self):
        previous = self.previous(max_requests=4)
        runner = self.runner(previous)
        stages = []
        def plan(*args, **kwargs):
            stages.append(kwargs['stage'])
            return {'action': 'web_search', 'arguments': {'query': '"Maya Example" profile', 'provider': 'duckduckgo'}, 'reason': 'Read a public profile.'}
        def results(provider, query):
            return [self.lead('https://public.example.org/maya')] if provider == 'bing' else []
        with patch('local_osint.ai.plan_continuation', side_effect=plan), patch('local_osint.collector.web_search', side_effect=self.search(results, lambda p, q: p == 'duckduckgo')), patch('local_osint.collector.fetch_public', side_effect=self.useful_page) as pages, patch.object(runner, 'compare_locally'), self.draft(runner):
            report = runner.run()
        self.assertEqual([provider for provider, _ in self.outbound], ['duckduckgo', 'bing'])
        self.assertIn('provider_failure', stages)
        self.assertEqual(report['continuation']['unavailable_providers'], ['duckduckgo'])
        self.assertEqual(report['quality_count'], 1)
        self.assertEqual(report['request_units'], 3)
        self.assertEqual(report['status'], 'partial')
        pages.assert_called_once()
        self.assert_retained(report)

    def test_repeated_finish_uses_selected_provider_and_fresh_budget(self):
        runner = self.runner(self.previous(providers=['bing'], max_requests=5))
        finish = {'action': 'finish', 'arguments': {}, 'reason': 'No further source.'}
        with patch('local_osint.ai.plan_continuation', side_effect=lambda *a, **k: copy.deepcopy(finish)) as planner, patch('local_osint.collector.web_search', side_effect=self.search()), patch.object(runner, 'compare_locally'), self.draft(runner):
            report = runner.run()
        self.assertEqual(report['status'], 'limited')
        self.assertEqual(report['request_units'], 5)
        self.assertEqual(len(self.outbound), 5)
        self.assertTrue(all(provider == 'bing' for provider, _ in self.outbound))
        self.assertEqual(len({tuple(sorted(query.lower().split())) for _, query in self.outbound}), 5)
        self.assertLessEqual(planner.call_count, 2)
        self.assert_retained(report)

    def test_reordered_duplicate_broadens_overconstrained_public_query(self):
        previous = self.previous(providers=['bing'], context='Dublin Foundation', max_requests=1)
        previous['steps'] = [{'tool': 'web_search', 'arguments': {'query': '"Maya Example" Dublin Foundation', 'provider': 'bing'}, 'status': 'success'}]
        runner = self.runner(previous)
        duplicate = {'action': 'web_search', 'arguments': {'query': 'Foundation "Maya Example" Dublin', 'provider': 'bing'}, 'reason': 'Try reordered words.'}
        with patch('local_osint.ai.plan_continuation', return_value=duplicate), patch('local_osint.collector.web_search', side_effect=self.search()), patch.object(runner, 'compare_locally'), self.draft(runner):
            report = runner.run()
        self.assertEqual(self.outbound, [('bing', '"Maya Example"')])
        self.assertEqual(report['status'], 'limited')
        self.assertEqual(report['continuation']['decisions'][0]['status'], 'blocked')
        self.assertTrue(any(d.get('origin') == 'engine' and d.get('status') == 'success' for d in report['continuation']['decisions']))
        self.assertEqual(report['steps'][0], previous['steps'][0])
        self.assert_retained(report)

    def test_same_query_can_switch_provider_when_first_has_no_results(self):
        previous = self.previous(max_requests=1)
        previous['steps'] = [{'tool': 'web_search', 'arguments': {'query': '"Maya Example"', 'provider': 'duckduckgo'}, 'status': 'success'}]
        runner = self.runner(previous)
        duplicate = {'action': 'web_search', 'arguments': {'query': '"Maya Example"', 'provider': 'duckduckgo'}, 'reason': 'Repeat a query.'}
        with patch('local_osint.ai.plan_continuation', return_value=duplicate), patch('local_osint.collector.web_search', side_effect=self.search()), patch.object(runner, 'compare_locally'), self.draft(runner):
            report = runner.run()
        self.assertEqual(self.outbound, [('bing', '"Maya Example"')])
        self.assertEqual(report['request_units'], 1)
        self.assertEqual(report['status'], 'limited')
        self.assert_retained(report)

    def test_saved_login_shell_changes_strategy_without_filling_useful_goal(self):
        runner = self.runner(self.previous(max_requests=5))
        plans = [
            {'action': 'web_search', 'arguments': {'query': '"Maya Example" profile', 'provider': 'duckduckgo'}, 'reason': 'Try public profile.'},
            {'action': 'web_search', 'arguments': {'query': '"Maya Example" interview', 'provider': 'bing'}, 'reason': 'Use a readable alternative.'}]
        stages = []
        def plan(*args, **kwargs):
            stages.append(kwargs['stage'])
            return copy.deepcopy(plans[len(stages) - 1])
        login_url, useful_url = 'https://login.example.net/maya', 'https://public.example.org/maya'
        def results(provider, query):
            return [self.lead(login_url if provider == 'duckduckgo' else useful_url)]
        def fetch(url, allow_query=False):
            if url == login_url:
                return b'<html><title>Sign in</title><form>Password <input type="password"></form></html>', 'html', url
            return self.useful_page(url)
        with patch('local_osint.ai.plan_continuation', side_effect=plan), patch('local_osint.collector.web_search', side_effect=self.search(results)), patch('local_osint.collector.fetch_public', side_effect=fetch) as pages, patch.object(runner, 'compare_locally'), self.draft(runner):
            report = runner.run()
        self.assertEqual(stages, ['plan', 'no_useful_pages'])
        self.assertEqual([call.args[0] for call in pages.call_args_list], [login_url, useful_url])
        self.assertEqual(report['quality_count'], 1)
        self.assertEqual(report['request_units'], 4)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual([page['useful'] for page in report['pages']], [False, True])
        self.assertTrue(all(page['source_id'] in report['source_ids'] for page in report['pages']))
        self.assert_retained(report)

    def test_saved_eligible_page_is_read_before_requesting_another_model_plan(self):
        previous = self.previous(max_requests=2)
        previous['web_results'] = [{**self.lead('https://public.example.org/maya'), 'relevant': True}]
        runner = self.runner(previous)
        with patch('local_osint.ai.plan_continuation') as planner, patch('local_osint.collector.web_search') as search, patch('local_osint.collector.fetch_public', side_effect=self.useful_page) as pages, self.draft(runner):
            report = runner.run()
        planner.assert_not_called()
        search.assert_not_called()
        pages.assert_called_once()
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['request_units'], 1)
        self.assertEqual(report['quality_count'], 1)
        self.assert_retained(report)


if __name__ == '__main__':
    unittest.main()
