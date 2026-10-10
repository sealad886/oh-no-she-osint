"""Prove local clue matching, model privacy, and useful-page continuation."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from local_osint.ai import compare_candidates, run_assistant, private_output
from local_osint.engine import Engine, InvalidInput
from local_osint.investigation import Investigation
from local_osint.relevance import local_match, rank_key


class LocalRelevanceTests(unittest.TestCase):
    def setUp(self):
        self.case = {'name': 'Ordinary Person', 'aliases': [], 'identifiers': [
            'private@example.net', '+353 87 123 4567', 'Harbor Clinic']}

    def test_exact_local_clue_match_ranks_above_context_and_namesakes_without_echoing_clues(self):
        direct = local_match(self.case, 'Ordinary Person: private@example.net')
        context = local_match(self.case, 'Ordinary Person works at Harbor Clinic')
        namesake = local_match(self.case, 'Ordinary Person researches physics overseas')
        self.assertGreater(rank_key({'local_match': direct}), rank_key({'local_match': context}))
        self.assertGreater(rank_key({'local_match': context}), rank_key({'local_match': namesake}))
        self.assertEqual(direct['level'], 'identifier_match')
        self.assertNotIn('private@example.net', json.dumps(direct))
        self.assertNotIn('Harbor Clinic', json.dumps(context))
        self.assertEqual(local_match(self.case, 'other-private@example.net')['level'], 'unrelated')

    def test_formatted_phone_matches_locally_but_partial_numbers_do_not(self):
        self.assertEqual(local_match(self.case, 'Ordinary Person +353 (87) 123-4567')['level'], 'identifier_match')
        self.assertEqual(local_match(self.case, 'Ordinary Person 87 123 4567')['level'], 'name_only')

    def test_model_draft_and_rejected_tool_trace_do_not_copy_private_clues(self):
        value = {'summary': 'PRIVATE@example.net +353 (87) 123-4567 Harbor Clinic',
                 'tool_calls': [{'arguments': {'query': 'Ordinary Person private@example.net'}}]}
        rendered = json.dumps(private_output(value, self.case['identifiers']))
        for clue in ['example.net', '123-4567', 'Harbor Clinic']:
            self.assertNotIn(clue, rendered)
        self.assertIn('Ordinary Person', rendered)

    def test_query_selected_profiles_and_fragments_do_not_corroborate_a_different_person(self):
        case = {**self.case, 'identifiers': ['https://example.org/profile?id=1#person']}
        self.assertEqual(local_match(case, 'Ordinary Person', 'https://example.org/profile?id=1#person')['level'], 'identifier_match')
        for url in ['https://example.org/profile?id=2#person', 'https://example.org/profile?id=1#other']:
            self.assertEqual(local_match(case, 'Ordinary Person', url)['level'], 'name_only')

    def test_local_model_comparison_has_private_context_and_no_tools_or_unbounded_output(self):
        candidates = [{'title': 'Ordinary Person profile', 'url': 'https://example.org/profile'}]
        ai = Mock()
        ai.chat.return_value = {'content': '{"consistent":[0],"unlikely":[]}', 'finish_reason': 'stop'}
        self.assertEqual(compare_candidates(ai, self.case, candidates), {0: 'context_consistent'})
        self.assertEqual(len(ai.chat.call_args.args), 1)
        self.assertIn('private@example.net', ai.chat.call_args.args[0][1]['content'])
        for value in ['{"consistent":[true],"unlikely":[]}', '{"consistent":[11],"unlikely":[]}',
                      '{"consistent":[0],"unlikely":[0]}', '{"consistent":[],"unlikely":[],"private":"secret"}']:
            ai.chat.return_value = {'content': value}
            with self.assertRaises(InvalidInput):
                compare_candidates(ai, self.case, candidates)

    def test_private_draft_context_does_not_expand_outbound_query_scope(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as folder:
            engine = Engine(Path(folder) / 'private')
            case = engine.create_case(self.case['name'], 'person', identifiers=self.case['identifiers'])
            runner = Investigation(engine, case['id'], True, options={'tools': ['web_search']})
            ai = Mock()
            ai.chat.return_value = {'content': 'Uncertain'}
            run_assistant(ai, runner.report, Mock(), private_context=self.case['identifiers'])
            self.assertIn('private@example.net', ai.chat.call_args.args[0][1]['content'])
            with patch('local_osint.collector.web_search') as search:
                for clue in ['private@example.net', 'Harbor Clinic', '+353 87 123 4567']:
                    with self.assertRaises(InvalidInput):
                        runner.dispatch('web_search', {'query': '"Ordinary Person" ' + clue})
                search.assert_not_called()
            rendered = json.dumps(runner.report)
            for clue in self.case['identifiers']:
                self.assertNotIn(clue, rendered)

    def test_model_can_refine_from_public_search_context_but_not_saved_private_clues(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as folder:
            engine = Engine(Path(folder) / 'private')
            case = engine.create_case(self.case['name'], 'person', identifiers=self.case['identifiers'])
            runner = Investigation(engine, case['id'], True, options={'tools': ['web_search']})
            runner.report['web_results'] = [{'title': 'Ordinary Person — Dublin Medicall', 'snippet': 'Harbor Clinic private example net', 'url': 'https://example.org/bio'}]
            self.assertEqual(runner.model_query('"Ordinary Person" Dublin Medicall'), '"Ordinary Person" Dublin Medicall')
            with self.assertRaises(InvalidInput):
                runner.model_query('"Ordinary Person" Harbor Clinic')
            with self.assertRaises(InvalidInput):
                runner.model_query('"Ordinary Person" private example net')

    def test_shell_does_not_fill_goal_and_next_public_refinement_collects_useful_page(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as folder:
            engine = Engine(Path(folder) / 'private')
            case = engine.create_case('Ordinary Person', 'person', identifiers=self.case['identifiers'])
            runner = Investigation(engine, case['id'], True, options={'tools': ['web_search'], 'page_limit': 1})
            queries = []
            def search(engine, case_id, query, enabled):
                queries.append(query)
                url = 'https://example.org/shell' if len(queries) == 1 else 'https://example.org/bio'
                saved = engine.ingest(case_id, query.encode(), 'Public search', 'search:' + query)
                return {**saved, 'results': [{'title': 'Ordinary Person profile', 'snippet': 'Public profile', 'url': url}]}
            def collect(engine, case_id, url, enabled, allow_query=False):
                body = b'<h1>Ordinary Person</h1><p>Log in to continue</p>' if url.endswith('shell') else b'<p>Ordinary Person works at Harbor Clinic, providing public services and detailed community information.</p>'
                return engine.ingest(case_id, body, 'Public page', url, 'html')
            with patch('local_osint.collector.web_search', side_effect=search), patch('local_osint.collector.collect', side_effect=collect):
                report = runner.run()
            self.assertEqual(report['quality_count'], 1)
            self.assertEqual(len(report['pages']), 2)
            self.assertFalse(report['pages'][0]['useful'])
            self.assertTrue(report['pages'][1]['useful'])
            self.assertEqual(report['status'], 'complete')
            self.assertEqual(len(queries), 2)
            for clue in self.case['identifiers']:
                self.assertFalse(any(clue in query for query in queries))
            self.assertEqual(len([e for e in engine.export(case['id'])['evidence'] if e['source'].startswith('https://')]), 2)

    def test_failed_or_unlikely_context_does_not_override_direct_identifier_match(self):
        direct = local_match(self.case, 'Ordinary Person private@example.net')
        direct['model_assessment'] = 'context_unlikely'
        self.assertEqual(rank_key({'local_match': direct}), 100)
