"""Independent behavioral regressions from the question-controller review.

Use synthetic cases and transports only; no external network or local model is
started. Assertions cover disclosure, answers, origin handling and recovery.
"""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from local_osint.engine import Engine, InvalidInput
from local_osint.investigation import Halt, Investigation
from local_osint.planner import Controller
from local_osint.workspace import Workspace


class QuestionReviewRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.root = Path(self.temp.name) / 'private'
        self.engine = Engine(self.root)
        self.case = self.engine.create_case('Morgan Public', 'person', identifiers=['secret-local-clue'])['id']
        self.workspace = Workspace(self.engine)

    def tearDown(self):
        self.temp.cleanup()

    def runner(self, **options):
        return Investigation(self.engine, self.case, True, options={'automatic': True, 'max_requests': 60, 'page_limit': 20, **options})

    def source(self, text, url='https://civic.example.org/team'):
        return self.engine.ingest(self.case, text.encode(), 'Synthetic public document', url)

    def answer_fixture(self, response, text='Morgan Public is the public programme coordinator.', **options):
        runner = self.runner(question='What role does the public source state?', **options)
        source = self.source(text)
        runner.report['pages'] = [{'source_id': source['id'], 'url': 'https://civic.example.org/team', 'useful': True}]
        controller = Controller(runner)
        controller.questions()
        payload = response(source) if callable(response) else response
        runner.ai = Mock()
        runner.ai.chat.return_value = {'content': json.dumps(payload), 'finish_reason': 'stop'}
        controller.answer_objective()
        controller.evaluate()
        question = next(q for q in self.workspace.questions(self.case) if q['key'] == 'objective')
        return question, runner, source

    def test_model_preparation_does_not_spend_short_collection_clock(self):
        runner = self.runner(runtime_seconds=1)
        controller = Controller(runner)
        clock = {'now': 0.0}
        runner.deadline = 1.0
        runner.ai = Mock()
        runner.ai.status.side_effect = [{'state': 'loading'}, {'state': 'ready'}]
        runner.ai.start.return_value = {'state': 'loading'}
        def cold_launch_wait(_seconds):
            clock['now'] += 20
        with patch('local_osint.planner.time.monotonic', side_effect=lambda: clock['now']), \
                patch.object(runner.cancel, 'wait', side_effect=cold_launch_wait):
            controller.start_ai()
            result = runner.step('First public operation after warmup', lambda: {'summary': 'Permitted'}, cost=1)
            self.assertEqual(result['summary'], 'Permitted')
            self.assertEqual(runner.used, 1)
            clock['now'] += 2
            with self.assertRaises(Halt):
                runner.check()
        self.assertEqual(runner.report['status'], 'limited')

    def test_rate_limited_provider_cools_down_without_suppressing_other_provider(self):
        calls = []
        def search(engine, case_id, query, enabled, provider='duckduckgo'):
            calls.append((provider, query))
            if provider == 'duckduckgo':
                raise InvalidInput('Source rate limited this request (HTTP 429). Try later.')
            saved = engine.ingest(case_id, b'{"results":[]}', 'Empty public search', 'https://search.example.org/bing', 'json')
            return {**saved, 'results': []}
        runner = self.runner()
        with patch('local_osint.collector.web_search', side_effect=search), \
                patch('local_osint.connectors.execute', return_value={'status': 'empty', 'summary': 'Empty', 'evidence_ids': [], 'leads': [], 'observations': []}):
            report = runner.run()
            before = list(calls)
            resumed = Investigation(Engine(self.root), self.case, True, options=report['options'], previous=report, continuing=True)
            resumed.run()
        self.assertEqual(sum(provider == 'duckduckgo' for provider, _ in calls), 1)
        self.assertGreater(sum(provider == 'bing' for provider, _ in calls), 0)
        self.assertEqual(calls, before)
        health = {item['connector']: item for item in self.workspace.health(self.case)}
        self.assertTrue(health['web_search:duckduckgo']['retry_at'])
        self.assertEqual(health['web_search:bing']['state'], 'ready')
        deferred = [task for task in self.workspace.tasks(self.case) if task['connector'] == 'web_search' and task['arguments']['provider'] == 'duckduckgo' and task['state'] == 'cooldown']
        self.assertTrue(deferred)

    def test_context_excluded_namesake_does_not_expand_crawl_or_archives(self):
        source = self.source('Morgan Public is an unrelated namesake with a different professional role.')
        runner = self.runner()
        runner.report['pages'] = [{'source_id': source['id'], 'url': 'https://namesake.example.org/bio',
                                  'readable': True, 'useful': False, 'context_excluded': True,
                                  'links': [{'url': 'https://namesake.example.org/team', 'title': 'Other team'}]}]
        Controller(runner).discover()
        self.assertEqual(self.workspace.tasks(self.case), [])

    def test_disputed_claim_does_not_mark_corroboration_question_supported(self):
        runner = self.runner()
        controller = Controller(runner)
        controller.questions()
        candidate = self.workspace.candidate(self.case, 'Morgan Public')
        first = self.source('Morgan Public works with Harbour Workshop.', 'https://first.example.org/')
        second = self.source('The public bulletin says Morgan Public works with Harbour Workshop.', 'https://second.example.org/')
        for source in (first, second):
            self.workspace.origin(self.case, source['id'], 'original')
        claim = self.workspace.observe(self.case, first['id'], candidate['id'], 'Morgan Public works with Harbour Workshop.', predicate='affiliation', value='Harbour Workshop', status='disputed')
        self.workspace.observe(self.case, second['id'], candidate['id'], 'Morgan Public works with Harbour Workshop.', predicate='affiliation', value='Harbour Workshop', claim_id=claim['id'])
        controller.evaluate()
        snapshot = self.workspace.snapshot(self.case)
        self.assertFalse(snapshot['claims'][0]['corroborated'])
        self.assertNotEqual(next(q['status'] for q in snapshot['questions'] if q['key'] == 'corroboration'), 'supported')

    def test_objective_answer_retains_literal_reference_without_resolving_identity(self):
        quote = 'Morgan Public is the public programme coordinator.'
        question, runner, source = self.answer_fixture(lambda saved: {'status': 'supported', 'references': [{'evidence_id': saved['id'], 'quote': quote, 'relation': 'support'}]})
        self.assertEqual(question['status'], 'supported')
        self.assertEqual(question['refs'][0]['quote'], quote)
        self.assertEqual(question['refs'][0]['evidence_id'], source['id'])
        self.assertEqual(next(q['status'] for q in runner.report['questions'] if q['key'] == 'identity'), 'unresolved')

    def test_nonliteral_or_foreign_answer_reference_cannot_support_question(self):
        other_case = self.engine.create_case('Other fixture', 'person')['id']
        foreign = self.engine.ingest(other_case, b'Morgan Public is a director.', 'Unrelated case source')['id']
        for ref in ({'evidence_id': foreign, 'quote': 'Morgan Public is a director.', 'relation': 'support'},
                    {'evidence_id': None, 'quote': 'Morgan Public is a director.', 'relation': 'support'}):
            with self.subTest(reference=ref):
                question, _, _ = self.answer_fixture(lambda saved: {'status': 'supported', 'references': [{**ref, 'evidence_id': ref['evidence_id'] or saved['id']}]})
                self.assertEqual(question['status'], 'unresolved')

    def test_literal_private_clue_is_not_persisted_as_public_answer(self):
        quote = 'Morgan Public lists secret-local-clue as a local reference.'
        question, runner, _ = self.answer_fixture(lambda saved: {'status': 'supported', 'references': [{'evidence_id': saved['id'], 'quote': quote, 'relation': 'support'}]}, text=quote)
        self.assertEqual(question['status'], 'unresolved')
        self.assertNotIn('secret-local-clue', json.dumps(question))
        self.assertNotIn('secret-local-clue', json.dumps(runner.report.get('research_decisions', [])))

    def test_opposing_answer_passages_cannot_be_presented_as_supported(self):
        first = 'Morgan Public is the public programme coordinator.'
        opposing = 'Morgan Public is not the public programme coordinator.'
        def response(saved):
            return {'status': 'supported', 'references': [
                {'evidence_id': saved['id'], 'quote': first, 'relation': 'support'},
                {'evidence_id': saved['id'], 'quote': opposing, 'relation': 'contradict'}]}
        question, _, _ = self.answer_fixture(response, text=first + ' ' + opposing)
        self.assertNotEqual(question['status'], 'supported')

    def test_changed_question_clears_prior_supported_objective(self):
        first = Controller(self.runner(question='Which role is stated?'))
        first.questions()
        self.workspace.update_question(self.case, first.main['id'], status='supported', gaps=[])
        second = Controller(self.runner(question='Which organization is stated?'))
        second.questions()
        question = next(q for q in self.workspace.questions(self.case) if q['key'] == 'objective')
        self.assertEqual(question['text'], 'Which organization is stated?')
        self.assertEqual(question['status'], 'unresolved')

    def test_same_target_projection_is_reversible_and_preserves_original_claims(self):
        sources, candidate_ids, claim_ids = [], [], []
        quote = 'Morgan Public works with Harbour Workshop.'
        for index, text in enumerate((quote, 'Independent bulletin: ' + quote)):
            source = self.source(text, 'https://source' + str(index) + '.example.org/')
            sources.append({**source, 'original_sha256': hashlib.sha256(text.encode()).hexdigest()})
            self.workspace.origin(self.case, source['id'], 'original')
            candidate = self.workspace.candidate(self.case, 'Morgan Public', 'Document ' + str(index), source['id'])
            candidate_ids.append(candidate['id'])
            claim_ids.append(self.workspace.observe(self.case, source['id'], candidate['id'], quote, predicate='affiliation', value='Harbour Workshop', status='supported')['id'])
            self.workspace.decide_candidate(self.case, candidate['id'], 'same')
        together = self.workspace.snapshot(self.case)['claims']
        self.assertEqual(len(together), 1)
        self.assertEqual(set(together[0]['member_claim_ids']), set(claim_ids))
        self.assertEqual(together[0]['independent_sources'], 2)
        self.assertTrue(together[0]['corroborated'])
        self.workspace.decide_candidate(self.case, candidate_ids[1], 'uncertain')
        self.assertEqual(len(self.workspace.snapshot(self.case)['claims']), 2)
        with self.engine.connection() as db:
            self.assertEqual({row[0] for row in db.execute('SELECT id FROM workspace_claims WHERE case_id=?', (self.case,))}, set(claim_ids))
        for source in sources:
            evidence = self.engine.evidence(self.case, source['id'])
            self.assertEqual(evidence['sha256'], source['original_sha256'])

    def test_archive_or_copy_cannot_create_independent_same_target_corroboration(self):
        quote = 'Morgan Public works with Harbour Workshop.'
        first = self.source(quote, 'https://original.example.org/')
        copied = self.source('Archived page: ' + quote, 'https://archive.example.org/')
        self.workspace.origin(self.case, first['id'], 'original')
        self.workspace.origin(self.case, copied['id'], 'archive', first['id'])
        for index, source in enumerate((first, copied)):
            candidate = self.workspace.candidate(self.case, 'Morgan Public', 'Capture ' + str(index), source['id'])
            self.workspace.observe(self.case, source['id'], candidate['id'], quote, predicate='affiliation', value='Harbour Workshop', status='supported')
            self.workspace.decide_candidate(self.case, candidate['id'], 'same')
        claim = self.workspace.snapshot(self.case)['claims'][0]
        self.assertEqual(claim['independent_sources'], 1)
        self.assertFalse(claim['corroborated'])


if __name__ == '__main__':
    unittest.main()
