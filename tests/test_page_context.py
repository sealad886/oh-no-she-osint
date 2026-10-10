"""Public target context filters saved namesakes without altering originals."""
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from local_osint.engine import Engine
from local_osint.investigation import Investigation, save_report


class PageContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.engine = Engine(Path(self.temp.name) / 'private')
        self.case = self.engine.create_case('Maya Example', 'person', identifiers=['private@example.net'])
        self.ai = Mock()
        self.ai.status.return_value = {'state': 'ready'}
        self.url = 'https://public.example.org/maya'
        self.context = 'Dublin paramedic'
        self.raw = b'<article>Maya Example is a research scientist working overseas. This detailed public biography describes published scientific research and university projects spanning many years.</article>'

    def tearDown(self):
        self.temp.cleanup()

    def runner(self, ai=None, previous=None, **options):
        return Investigation(self.engine, self.case['id'], True, ai=ai, previous=previous,
                             continuing=previous is not None, options={
                                 'tools': ['web_search'], 'context': self.context, 'page_limit': 1,
                                 'max_requests': 4, **options})

    def collect(self, runner, raw=None):
        runner.allowed_urls.add(self.url)
        raw = self.raw if raw is None else raw
        def save(engine, case_id, url, enabled, allow_query=False):
            self.assertEqual(url, self.url)
            self.assertTrue(enabled)
            self.assertTrue(allow_query)
            return engine.ingest(case_id, raw, 'Public biography', url, 'html')
        return patch('local_osint.collector.collect', side_effect=save)

    def assert_original(self, runner, raw=None):
        page = runner.report['pages'][0]
        self.assertIn(page['source_id'], runner.report['source_ids'])
        saved = self.engine.evidence(self.case['id'], page['source_id'])
        self.assertEqual(saved['sha256'], hashlib.sha256(self.raw if raw is None else raw).hexdigest())
        self.assertIn('Maya Example', saved['text'])

    def test_chosen_role_and_location_are_compared_locally_without_collection(self):
        runner = self.runner()
        with self.collect(runner):
            runner.page(self.url)
        runner.ai = self.ai
        runner.report['network_enabled'] = False
        used = runner.used
        with patch('local_osint.ai.compare_candidates', return_value={0: 'context_unlikely'}) as compare, patch('local_osint.collector.collect') as collect, patch('local_osint.collector.fetch_public') as fetch, patch('local_osint.collector.research') as domains, patch('socket.getaddrinfo') as dns:
            runner.compare_pages_locally(runner.report['pages'])
        compare.assert_called_once()
        local_case, candidates = compare.call_args.args[1:]
        self.assertEqual(local_case['public_context'], self.context)
        self.assertEqual(local_case['identifiers'], self.case['identifiers'])
        self.assertIn('research scientist', candidates[0]['snippet'])
        self.assertEqual(candidates[0]['url'], self.url)
        collect.assert_not_called()
        fetch.assert_not_called()
        domains.assert_not_called()
        dns.assert_not_called()
        self.assertEqual(runner.used, used)
        self.assertEqual(runner.page_count(), 0)
        self.assert_original(runner)

    def test_newly_collected_likely_namesake_does_not_fill_page_goal(self):
        runner = self.runner(ai=self.ai)
        with self.collect(runner) as collect, patch('local_osint.ai.compare_candidates', return_value={0: 'context_unlikely'}):
            result = runner.page(self.url)
        collect.assert_called_once()
        self.assertFalse(result['useful'])
        self.assertFalse(runner.report['pages'][0]['useful'])
        self.assertEqual(runner.report['quality_count'], 0)
        self.assertEqual(runner.report['pages'][0]['local_match']['model_assessment'], 'context_unlikely')
        self.assertEqual(runner.used, 1)
        self.assert_original(runner)

    def test_corrected_context_reconsiders_saved_namesake_without_refetch(self):
        runner = self.runner(ai=self.ai)
        with self.collect(runner), patch('local_osint.ai.compare_candidates', return_value={0: 'context_unlikely'}):
            runner.page(self.url)
        self.assertEqual(runner.page_count(), 0)
        runner.options['context'] = 'research scientist'
        with patch('local_osint.ai.compare_candidates', return_value={0: 'context_consistent'}) as compare, patch('local_osint.collector.collect') as collect:
            runner.compare_pages_locally(runner.report['pages'])
            result = runner.page(self.url)
        compare.assert_called_once()
        collect.assert_not_called()
        self.assertTrue(result['useful'])
        self.assertEqual(runner.page_count(), 1)
        self.assert_original(runner)

    def test_context_edits_do_not_turn_login_shells_into_useful_pages(self):
        runner = self.runner(ai=self.ai)
        raw = b'<html><title>Sign in</title><form>Password <input type="password"></form></html>'
        with self.collect(runner, raw), patch('local_osint.ai.compare_candidates') as compare:
            runner.page(self.url)
            runner.options['context'] = 'different public context'
            runner.compare_pages_locally(runner.report['pages'])
        compare.assert_not_called()
        self.assertEqual(runner.page_count(), 0)

    def test_direct_identifier_match_overrides_unlikely_model_hint_without_more_ai(self):
        runner = self.runner(ai=self.ai)
        runner.report['web_results'] = [{'url': self.url, 'local_match': {'model_assessment': 'context_unlikely'}}]
        raw = self.raw.replace(b'</article>', b' Public contact: private@example.net.</article>')
        with self.collect(runner, raw), patch('local_osint.ai.compare_candidates', return_value={0: 'context_unlikely'}) as compare:
            result = runner.page(self.url)
        compare.assert_not_called()
        self.assertTrue(result['useful'])
        self.assertEqual(runner.page_count(), 1)
        self.assertEqual(runner.report['pages'][0]['local_match']['level'], 'identifier_match')
        self.assert_original(runner, raw)

    def test_uncertain_local_hint_preserves_content_and_does_not_confirm_identity(self):
        runner = self.runner(ai=self.ai)
        with self.collect(runner), patch('local_osint.ai.compare_candidates', return_value={0: 'uncertain'}):
            result = runner.page(self.url)
        page = runner.report['pages'][0]
        self.assertTrue(result['useful'])
        self.assertEqual(runner.page_count(), 1)
        self.assertEqual(page['local_match']['level'], 'name_only')
        self.assertEqual(page['local_match']['model_assessment'], 'uncertain')
        self.assertFalse(page['local_match']['signals'])
        self.assertIn('research scientist', page['excerpt'])
        self.assert_original(runner)

    def test_continuation_reassessment_changes_copy_not_saved_parent_report(self):
        parent = self.runner()
        with self.collect(parent):
            parent.page(self.url)
        parent.report['status'] = 'partial'
        save_report(self.engine, parent.report)
        previous = copy.deepcopy(parent.report)
        original_previous = copy.deepcopy(previous)
        runner = self.runner(ai=self.ai, previous=previous)
        with patch('local_osint.ai.compare_candidates', return_value={0: 'context_unlikely'}), patch('local_osint.collector.collect') as collect:
            runner.compare_pages_locally(runner.report['pages'])
        collect.assert_not_called()
        self.assertEqual(runner.page_count(), 0)
        self.assertEqual(previous, original_previous)
        self.assertTrue(previous['pages'][0]['useful'])
        self.assertTrue(self.engine.latest_investigation(self.case['id'])['pages'][0]['useful'])
        self.assertEqual(runner.report['source_ids'], previous['source_ids'])
        self.assert_original(runner)

    def test_continuation_rechecks_old_goal_before_finishing_and_seeks_public_work(self):
        parent = self.runner()
        with self.collect(parent):
            parent.page(self.url)
        previous = copy.deepcopy(parent.report)
        runner = self.runner(ai=self.ai, previous=previous, providers=['bing'], max_requests=1)
        def search(engine, case_id, query, enabled, provider):
            self.assertEqual(provider, 'bing')
            self.assertNotIn('private@example.net', query)
            saved = engine.ingest(case_id, b'Empty public search', 'Search')
            return {**saved, 'results': []}
        with patch('local_osint.ai.compare_candidates', return_value={0: 'context_unlikely'}), patch('local_osint.ai.plan_continuation', return_value={'action': 'finish', 'arguments': {}, 'reason': 'No further source.'}), patch('local_osint.collector.web_search', side_effect=search) as collect:
            report = runner.run()
        collect.assert_called_once()
        self.assertEqual(report['status'], 'limited')
        self.assertEqual(report['quality_count'], 0)
        self.assertEqual(report['request_units'], 1)
        self.assertTrue(previous['pages'][0]['useful'])
        self.assert_original(runner)

    def test_all_retained_pages_are_reassessed_beyond_one_model_batch(self):
        parent = self.runner(page_limit=11, max_requests=11)
        hashes = {}
        def save(engine, case_id, url, enabled, allow_query=False):
            raw = self.raw + ('<p>Public profile section ' + url.rsplit('/', 1)[-1] + '</p>').encode()
            saved = engine.ingest(case_id, raw, 'Public biography', url, 'html')
            hashes[saved['id']] = hashlib.sha256(raw).hexdigest()
            return saved
        with patch('local_osint.collector.collect', side_effect=save):
            for index in range(11):
                url = self.url + '/' + str(index)
                parent.allowed_urls.add(url)
                parent.page(url)
        previous = copy.deepcopy(parent.report)
        runner = self.runner(ai=self.ai, previous=previous, page_limit=11)
        def compare(ai, case, candidates):
            self.assertLessEqual(len(candidates), 10)
            return {index: 'context_unlikely' for index in range(len(candidates))}
        with patch('local_osint.ai.compare_candidates', side_effect=compare), patch('local_osint.collector.collect') as collect, patch('socket.getaddrinfo') as dns:
            runner.compare_pages_locally(runner.report['pages'])
        collect.assert_not_called()
        dns.assert_not_called()
        self.assertEqual(len(runner.report['pages']), 11)
        self.assertEqual(runner.page_count(), 0)
        self.assertTrue(all(not page['useful'] for page in runner.report['pages']))
        self.assertTrue(all(page['useful'] for page in previous['pages']))
        self.assertEqual(runner.report['source_ids'], previous['source_ids'])
        self.assertEqual(len(hashes), 11)
        for evidence_id, expected_hash in hashes.items():
            self.assertEqual(self.engine.evidence(self.case['id'], evidence_id)['sha256'], expected_hash)


if __name__ == '__main__':
    unittest.main()
