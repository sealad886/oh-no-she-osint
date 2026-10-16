"""Question-driven behavior through the real queue/store with synthetic providers."""
import json
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from local_osint.engine import Engine, InvalidInput
from local_osint.investigation import Investigation, options_for
from local_osint.planner import Controller, canonical_url, rank_links
from local_osint.workspace import Workspace


class QuestionPlannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.engine = Engine(Path(self.temp.name) / "workspace")
        self.case = self.engine.create_case("Morgan Public", "person", identifiers=["secret-local-clue"])
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def runner(self, **values):
        return Investigation(self.engine, self.case['id'], enabled=True, options={
            'automatic': True, 'max_requests': 60, 'page_limit': 20, **values})

    def search(self, engine, case, query, enabled, provider='duckduckgo'):
        self.calls.append((provider, query))
        original = engine.ingest(case, json.dumps({'query': query}).encode(), 'Search fixture',
                                 'https://search.example.org/' + provider, 'json')
        return {**original, 'results': [{'url': 'https://civic.example.org/team',
            'title': 'Morgan Public community programme', 'snippet': 'Morgan Public organizes public repair events.'}]}

    def empty_connector(self, engine, case, connector, arguments, **kwargs):
        return {'status': 'empty', 'summary': 'No further links in this synthetic source.', 'evidence_ids': [], 'leads': [], 'observations': []}

    def test_one_automatic_run_finds_ordinary_person_without_encyclopedia_and_preserves_original(self):
        saved = self.engine.ingest(self.case['id'], b'Private original', 'Existing evidence')
        raw = b'<article>Morgan Public organizes the public community repair programme. This annual civic programme describes workshops, public events and volunteer support for local residents.</article>'
        runner = self.runner(context='Harbour Workshop')
        with patch('local_osint.collector.web_search', side_effect=self.search), patch('local_osint.collector.fetch_public', return_value=(raw, 'html', 'https://civic.example.org/team')), patch('local_osint.connectors.execute', side_effect=self.empty_connector), patch('local_osint.collector.discover') as encyclopedia:
            report = runner.run()
        encyclopedia.assert_not_called()
        self.assertGreater(report['quality_count'], 0)
        self.assertEqual(report['research_quality']['automatic_identity_merges'], 0)
        self.assertTrue(report['findings'])
        self.assertTrue(any(q['status'] == 'supported' for q in report['questions']))
        self.assertTrue(any(q['key'] == 'identity' and q['status'] == 'unresolved' for q in report['questions']))
        self.assertNotEqual(report['status'], 'failed')
        self.assertEqual(self.engine.evidence(self.case['id'], saved['id'])['sha256'], hashlib.sha256(b'Private original').hexdigest())
        self.assertTrue(report['finished_at'])
        self.assertEqual(report['options']['context'], 'Harbour Workshop')
        self.assertEqual(len(self.calls), len(set(self.calls)))
        self.assertTrue(all('secret-local-clue' not in query for _, query in self.calls))

    def test_provider_failure_changes_provider_and_continuation_does_not_repeat_completed_requests(self):
        first = self.runner()
        def search(*args, **kwargs):
            if kwargs.get('provider', 'duckduckgo') == 'duckduckgo':
                raise InvalidInput('Provider is unavailable.')
            return self.search(*args, **kwargs)
        raw = b'<article>Morgan Public organizes the public community repair programme. Public programme details describe volunteer workshops, events and civic activities for local residents.</article>'
        with patch('local_osint.collector.web_search', side_effect=search) as provider, patch('local_osint.collector.fetch_public', return_value=(raw, 'html', 'https://civic.example.org/team')), patch('local_osint.connectors.execute', side_effect=self.empty_connector):
            previous = first.run()
            before = provider.call_count
            resumed = Investigation(self.engine, self.case['id'], True, options=previous['options'], previous=previous, continuing=True)
            result = resumed.run()
            self.assertEqual(provider.call_count, before)
        self.assertTrue(any(p == 'bing' for p, _ in self.calls))
        self.assertEqual({p['source_id'] for p in previous['pages']}, {p['source_id'] for p in result['pages']})

    def test_empty_results_leave_explicit_unanswered_question_instead_of_false_completion(self):
        def empty(engine, case, query, enabled, **kwargs):
            saved = engine.ingest(case, b'{"results":[]}', 'Empty public search', format='json')
            return {**saved, 'results': []}
        runner = self.runner()
        with patch('local_osint.collector.web_search', side_effect=empty), patch('local_osint.connectors.execute', side_effect=self.empty_connector):
            report = runner.run()
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['research_quality']['useful_sources'], 0)
        self.assertTrue(all(q['status'] == 'unresolved' for q in report['questions']))
        self.assertIn('Insufficient public evidence', report['stop_reason'])

    def test_source_backed_query_accepts_public_pivot_and_blocks_private_or_report_derived_inputs(self):
        runner = self.runner()
        source = self.engine.ingest(self.case['id'], b'Morgan Public works with Harbour Workshop.', 'Public source')
        controller = Controller(runner)
        self.assertEqual(controller.provenance_query('"Morgan Public" "Harbour Workshop"', source['id']), '"Morgan Public" "Harbour Workshop"')
        with self.assertRaises(InvalidInput):
            controller.provenance_query('"Morgan Public" secret-local-clue', source['id'])
        generated = self.engine.ingest(self.case['id'], b'Morgan Public Harbour Workshop', 'Generated report', 'local-osint:investigation-report', extract=False)
        with self.assertRaises(InvalidInput):
            controller.provenance_query('"Morgan Public" Harbour', generated['id'])

    def test_repeated_model_actions_do_not_block_fresh_provider_strategies(self):
        class RepeatingModel:
            def chat(self, messages):
                return {'content': '{"actions":[]}'}
        runner = self.runner()
        source = self.engine.ingest(self.case['id'], b'Morgan Public organizes Harbour Workshop public events.', 'Public staff source', 'https://civic.example.org/team')
        runner.report['pages'] = [{'source_id': source['id'], 'url': 'https://civic.example.org/team', 'useful': True}]
        controller = Controller(runner)
        controller.questions()
        runner.ai = RepeatingModel()
        controller.seed()
        controller.model_plan()
        controller.model_plan()
        self.assertEqual(controller.model_failures, 2)
        tasks = Workspace(self.engine).tasks(self.case['id'])
        self.assertTrue(any(t['connector'] == 'web_search' and t['arguments']['provider'] == 'bing' for t in tasks))

    def test_link_ranking_prioritizes_late_document_and_normalizes_tracking(self):
        links = [{'url': 'https://civic.example.org/privacy', 'title': 'Privacy'} for _ in range(12)]
        links += [{'url': 'https://civic.example.org/programme.pdf', 'title': 'Morgan Public speaker programme'}]
        ranked = rank_links(links, self.case, {})
        self.assertTrue(ranked[0]['url'].endswith('programme.pdf'))
        self.assertEqual(canonical_url('https://EXAMPLE.org/path?utm_source=x&year=2026#part'), 'https://example.org/path?year=2026')

    def test_valid_local_ai_proposal_queues_new_source_backed_typed_query(self):
        runner = self.runner()
        quote = 'Morgan Public coordinates Harbour Workshop volunteer training.'
        source = self.engine.ingest(self.case['id'], quote.encode(), 'Public staff source', 'https://civic.example.org/team')
        runner.report['pages'] = [{'source_id': source['id'], 'url': 'https://civic.example.org/team', 'useful': True}]
        class Model:
            def chat(self, messages):
                return {'content': json.dumps({'actions': [{'connector': 'web_search', 'arguments': {'query': '"Morgan Public" "Harbour Workshop"', 'provider': 'bing'}, 'source_id': 'S1', 'quote': quote, 'reason': 'Find another public source for the observed workshop affiliation.'}]})}
        runner.ai = Model()
        controller = Controller(runner)
        controller.questions()
        controller.model_plan()
        tasks = runner.workspace.tasks(self.case['id'])
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]['source_id'], source['id'])
        self.assertEqual(tasks[0]['connector'], 'web_search')
        self.assertIn('Harbour Workshop', tasks[0]['arguments']['query'])

    def test_automatic_recipe_is_target_specific_and_does_not_select_academic_sources_for_every_person(self):
        plain = options_for(self.case, {'automatic': True})
        self.assertNotIn('crossref', plain['packs'])
        self.assertNotIn('github_public', plain['packs'])
        academic = options_for(self.case, {'automatic': True, 'public_organization': 'University research group'})
        self.assertIn('crossref', academic['packs'])

    def test_domain_and_organization_recipes_queue_their_relevant_builtin_signals(self):
        domain = self.engine.create_case('example.org', 'domain')
        runner = Investigation(self.engine, domain['id'], True, options={'automatic': True})
        controller = Controller(runner)
        controller.questions()
        controller.seed()
        signals = [task['arguments']['signals'] for task in runner.workspace.tasks(domain['id']) if task['connector'] == 'domain_signals']
        self.assertEqual({signal[0] for signal in signals}, {'dns', 'registration', 'certificate'})
        self.assertTrue(all(len(signal) == 1 for signal in signals))
        organization = self.engine.create_case('Community Workshop', 'organization')
        runner = Investigation(self.engine, organization['id'], True, options={'automatic': True})
        controller = Controller(runner)
        controller.questions()
        controller.seed()
        self.assertTrue(any(task['connector'] == 'news_search' for task in runner.workspace.tasks(organization['id'])))
