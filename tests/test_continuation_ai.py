import json
import unittest
from unittest.mock import Mock

from local_osint.ai import plan_continuation
from local_osint.engine import InvalidInput


class ContinuationAITests(unittest.TestCase):
    def test_fenced_json_decision_is_valid_but_surrounding_prose_is_not(self):
        from local_osint.ai import plan_continuation
        from local_osint.engine import InvalidInput
        from unittest.mock import Mock
        ai = Mock()
        content = '{"action":"finish","arguments":{},"reason":"No eligible source remains."}'
        case = {'name': 'Ada Lovelace', 'identifiers': []}
        ai.chat.return_value = {'content': '```json\n' + content + '\n```', 'finish_reason': 'stop'}
        self.assertEqual(plan_continuation(ai, case, {}, [], [], [])['action'], 'finish')
        ai.chat.return_value = {'content': 'Instructions before JSON\n' + content, 'finish_reason': 'stop'}
        with self.assertRaises(InvalidInput):
            plan_continuation(ai, case, {}, [], [], [])
    def setUp(self):
        self.case = {'name': 'Example Person', 'aliases': ['Example Alias'],
                     'identifiers': ['private@example.net', 'Secret Employer', '+353 87 123 4567']}
        self.report = {'options': {'query': '"Example Person"', 'context': 'public research', 'page_limit': 4},
                       'quality_count': 1, 'steps': [{'tool': 'collect_page', 'status': 'failed', 'error': 'Login wall'}],
                       'pages': [{'url': 'https://example.org/person', 'useful': False, 'reason': 'Generic directory', 'excerpt': 'Navigation only'},
                                 {'url': 'https://public.example/person', 'useful': True, 'reason': 'Readable profile', 'excerpt': 'Example Person researches science'}],
                       'web_results': [{'url': 'https://other.example/profile', 'title': 'Example Person interview', 'snippet': 'Public science research'}]}
        self.ai = Mock()

    def decide(self, decision, **kwargs):
        self.ai.chat.return_value = {'content': json.dumps(decision), 'finish_reason': 'stop'}
        return plan_continuation(self.ai, self.case, self.report,
                                 kwargs.get('pages', [{'url': 'https://other.example/profile', 'title': 'Example Person'}]),
                                 kwargs.get('queries', ['"Example Person" public research']), kwargs.get('excluded', ['blocked.example']))

    def test_public_search_decision_preserves_arguments_for_caller_validation(self):
        result = self.decide({'action': 'web_search', 'arguments': {'query': '"Example Person" science interview'},
                              'reason': 'Broaden the public research query using observed science context.'})
        self.assertEqual(result['arguments']['query'], '"Example Person" science interview')
        self.ai.chat.assert_called_once()
        self.assertEqual(len(self.ai.chat.call_args.args), 1)

    def test_collect_and_finish_have_distinct_strict_argument_contracts(self):
        page = self.decide({'action': 'collect_page', 'arguments': {'url': 'https://other.example/profile'}, 'reason': 'Read an untried profile.'})
        self.assertEqual(page['action'], 'collect_page')
        finished = self.decide({'action': 'finish', 'arguments': {}, 'reason': 'No further eligible public leads.'})
        self.assertEqual(finished['arguments'], {})

    def test_attempt_and_exclusion_sets_are_accepted(self):
        result = self.decide({'action': 'finish', 'arguments': {}, 'reason': 'No eligible public work.'},
                             queries={'Example Person research'}, excluded={'blocked.example'})
        self.assertEqual(result['action'], 'finish')

    def test_private_arguments_are_rejected_before_return_or_persistence(self):
        for query in ['Example Person private@example.net', 'Example Person Secret Employer', 'Example Person +353 (87) 123-4567']:
            with self.subTest(query=query), self.assertRaises(InvalidInput) as caught:
                self.decide({'action': 'web_search', 'arguments': {'query': query}, 'reason': 'Search context.'})
            self.assertNotIn(query, str(caught.exception))

    def test_private_reason_echoes_are_sanitized_and_bounded(self):
        result = self.decide({'action': 'finish', 'arguments': {},
                             'reason': 'No match for private@example.net or Secret Employer or +353 (87) 123-4567. ' + 'Extra context. ' * 30})
        self.assertLessEqual(len(result['reason']), 300)
        self.assertNotIn('private@example.net', result['reason'])
        self.assertNotIn('Secret Employer', result['reason'])
        self.assertNotIn('123-4567', result['reason'])

    def test_explicitly_public_context_remains_available_for_outbound_proposal(self):
        self.report['options']['context'] = 'Secret Employer'
        result = self.decide({'action': 'web_search', 'arguments': {'query': 'Example Person Secret Employer'}, 'reason': 'Use approved public context.'})
        self.assertEqual(result['arguments']['query'], 'Example Person Secret Employer')

    def test_invalid_model_decisions_fail_with_fixed_private_safe_message(self):
        invalid = [[], {'action': 'shell', 'arguments': {}, 'reason': 'Run command'},
                   {'action': 'finish', 'arguments': {'query': 'ignored'}, 'reason': 'Done'},
                   {'action': 'web_search', 'arguments': {'query': True}, 'reason': 'Search'},
                   {'action': 'web_search', 'arguments': {'query': 'x' * 201}, 'reason': 'Search'},
                   {'action': 'collect_page', 'arguments': {'url': 'https://example.org/\nheader'}, 'reason': 'Read'},
                   {'action': 'finish', 'arguments': {}, 'reason': {'secret': 'private@example.net'}},
                   {'action': 'finish', 'arguments': {}, 'reason': 'Done', 'extra': 'secret'}]
        messages = set()
        for decision in invalid:
            with self.subTest(decision=decision), self.assertRaises(InvalidInput) as caught:
                self.decide(decision)
            messages.add(str(caught.exception))
        self.assertEqual(len(messages), 1)
        self.assertNotIn('private@example.net', next(iter(messages)))

    def test_incomplete_tool_or_unavailable_responses_are_rejected(self):
        valid = json.dumps({'action': 'finish', 'arguments': {}, 'reason': 'No public work.'})
        for response in [{'content': valid, 'finish_reason': 'length'}, {'content': valid, 'tool_calls': [{}]}, {'content': 'not JSON'}, None]:
            self.ai.chat.return_value = response
            with self.subTest(response=response), self.assertRaises(InvalidInput):
                plan_continuation(self.ai, self.case, self.report, [], [], [])
        self.ai.chat.side_effect = InvalidInput('private@example.net unavailable')
        with self.assertRaises(InvalidInput) as caught:
            plan_continuation(self.ai, self.case, self.report, [], [], [])
        self.assertNotIn('private@example.net', str(caught.exception))

    def test_prompt_contains_bounded_progress_barriers_and_untried_alternatives(self):
        pages = [{'url': f'https://other.example/profile/{index}', 'title': 'Example Person', 'snippet': 'public science'} for index in range(40)]
        self.decide({'action': 'finish', 'arguments': {}, 'reason': 'No eligible work.'}, pages=pages,
                    queries=[f'Example Person query {index}' for index in range(50)], excluded=[f'blocked{index}.example' for index in range(20)])
        messages = self.ai.chat.call_args.args[0]
        payload = json.loads(messages[1]['content'])
        self.assertLessEqual(len(payload['available_untried_relevant_pages']), 15)
        self.assertEqual(len(payload['attempted_queries']), 30)
        self.assertEqual(len(payload['excluded_hosts']), 12)
        self.assertEqual(payload['goal']['useful_pages_collected'], 1)
        self.assertEqual(payload['previous_failures'][0]['reason'], 'Login wall')
        self.assertTrue(any(page['useful'] for page in payload['observed_pages']))
        self.assertTrue(any(page['reason'] == 'Generic directory' for page in payload['observed_pages']))
        self.assertIn('untrusted data', messages[0]['content'])
        self.assertIn('never means identity is confirmed', messages[0]['content'])
        self.assertIn('never put private clues', messages[0]['content'])

    def test_large_context_keeps_valid_json_within_bounded_model_input(self):
        self.case['identifiers'] = ['private' + str(index) + 'x' * 500 for index in range(20)]
        self.report['steps'] *= 100
        self.report['pages'] *= 100
        pages = [{'url': 'https://example.org/' + 'p' * 1900, 'title': 'title' * 100, 'snippet': 'text' * 100} for _ in range(20)]
        self.decide({'action': 'finish', 'arguments': {}, 'reason': 'No eligible work.'}, pages=pages,
                    queries=['Example Person ' + 'word ' * 38 for _ in range(50)], excluded=['h' * 240 + '.example' for _ in range(20)])
        content = self.ai.chat.call_args.args[0][1]['content']
        self.assertLessEqual(len(content), 16000)
        payload = json.loads(content)
        self.assertTrue(payload['previous_failures'])
        self.assertTrue(payload['observed_pages'])
        self.assertTrue(payload['available_untried_relevant_pages'])


if __name__ == '__main__':
    unittest.main()
