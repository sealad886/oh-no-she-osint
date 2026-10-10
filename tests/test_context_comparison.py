import json
import unittest
from unittest.mock import Mock

from local_osint.ai import compare_candidates
from local_osint.engine import InvalidInput


class PublicContextComparisonTests(unittest.TestCase):
    def setUp(self):
        self.ai = Mock()
        self.case = {'name': 'Example Person', 'focus_type': 'person', 'public_context': 'Dublin Kildare Straffan Paramedic',
                     'identifiers': ['private@example.net']}
        self.candidates = [{'title': 'Example Person biography', 'snippet': 'Example Person is a scientist in another country.',
                            'url': 'https://example.org/person', 'local_match': {'level': 'name_only', 'summary': 'Name match only.'}}]
        self.ai.chat.return_value = {'content': '{"consistent":[],"unlikely":[0]}', 'finish_reason': 'stop'}

    def test_public_role_location_and_focus_are_supplied_for_local_comparison(self):
        result = compare_candidates(self.ai, self.case, self.candidates)
        self.assertEqual(result, {0: 'context_unlikely'})
        call = self.ai.chat.call_args
        self.assertEqual(len(call.args), 1)
        messages = call.args[0]
        payload = json.loads(messages[1]['content'])
        self.assertEqual(payload['public_context'], 'Dublin Kildare Straffan Paramedic')
        self.assertEqual(payload['focus_type'], 'person')
        self.assertEqual(payload['private_case_details_local_only'], ['private@example.net'])
        self.assertIn('professional role', messages[0]['content'])
        self.assertIn('location alone is not evidence', messages[0]['content'])
        self.assertIn('No tools or outbound requests', messages[0]['content'])
        self.assertNotIn('private@example.net', json.dumps(result))

    def test_longer_excerpts_preserve_late_role_context(self):
        self.candidates[0]['snippet'] = 'Introductory public biography. ' * 30 + 'Research scientist working overseas.'
        compare_candidates(self.ai, self.case, self.candidates)
        candidate = json.loads(self.ai.chat.call_args.args[0][1]['content'])['candidates'][0]
        self.assertIn('Research scientist', candidate['snippet'])
        self.assertLessEqual(len(candidate['snippet']), 1800)

    def test_ten_large_candidates_keep_complete_bounded_json_and_indices(self):
        candidates = [{**self.candidates[0], 'title': 'title' * 100, 'snippet': 'content ' * 1000,
                       'url': 'https://example.org/' + 'path' * 1000} for _ in range(15)]
        compare_candidates(self.ai, self.case, candidates)
        encoded = self.ai.chat.call_args.args[0][1]['content']
        payload = json.loads(encoded)
        self.assertLessEqual(len(encoded), 22000)
        self.assertEqual([candidate['index'] for candidate in payload['candidates']], list(range(10)))
        self.assertTrue(all(len(candidate['snippet']) <= 1800 for candidate in payload['candidates']))
        self.assertTrue(all(candidate['snippet'] for candidate in payload['candidates']))

    def test_response_protocol_rejects_private_prose_and_invalid_categories(self):
        for content in ['{"consistent":[],"unlikely":[true]}', '{"consistent":[0],"unlikely":[0]}',
                        '{"consistent":[],"unlikely":[0],"reason":"private@example.net"}', 'private@example.net']:
            self.ai.chat.return_value = {'content': content, 'finish_reason': 'stop'}
            with self.subTest(content=content), self.assertRaises(InvalidInput) as caught:
                compare_candidates(self.ai, self.case, self.candidates)
            self.assertNotIn('private@example.net', str(caught.exception))

    def test_missing_public_context_remains_optional(self):
        self.case.pop('public_context')
        compare_candidates(self.ai, self.case, self.candidates)
        payload = json.loads(self.ai.chat.call_args.args[0][1]['content'])
        self.assertEqual(payload['public_context'], '')


if __name__ == '__main__':
    unittest.main()
