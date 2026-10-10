import unittest

from local_osint.engine import FOCUS_TYPES
from local_osint.recovery import MAX_RECOVERY_QUERIES, SEARCH_WORDS, recovery_queries
from local_osint.relevance import words


class RecoveryQueryTests(unittest.TestCase):
    def case(self, focus='person'):
        return {'name': 'Example Subject', 'focus_type': focus, 'aliases': ['Example Alias'],
                'identifiers': ['private@example.net', '+353 87 123 4567', 'Secret Employer']}

    def test_all_supported_focus_types_have_finite_distinct_fresh_defaults(self):
        for focus in FOCUS_TYPES:
            case = self.case(focus)
            case['aliases'] = []
            with self.subTest(focus=focus):
                results = recovery_queries(case, {'query': '"Example Subject"', 'context': ''})
                self.assertGreater(len(results), 24)
                self.assertLessEqual(len(results), MAX_RECOVERY_QUERIES)
                self.assertEqual(len(results), len({result['query'].casefold() for result in results}))
                self.assertEqual(results[0]['query'], '"Example Subject"')
                self.assertTrue(all(set(result) == {'query', 'strategy'} for result in results))

    def test_saved_private_identifiers_never_enter_recovery_queries(self):
        results = recovery_queries(self.case(), {'query': '"Example Subject"', 'context': 'Dublin public science'})
        joined = ' '.join(result['query'] for result in results)
        for identifier in self.case()['identifiers']:
            self.assertNotIn(identifier.casefold(), joined.casefold())
        self.assertNotIn('private@example.net', joined)

    def test_broadening_drops_overconstrained_public_context(self):
        results = recovery_queries(self.case(), {'query': '"Example Subject"', 'context': 'Dublin science conference publication'})
        queries = {result['query'] for result in results}
        self.assertIn('"Example Subject"', queries)
        self.assertIn('"Example Subject" Dublin science conference publication', queries)
        self.assertIn('"Example Subject" dublin', queries)
        self.assertIn('"Example Subject" science conference', queries)

    def test_exact_quoted_subject_or_alias_is_always_present(self):
        case = self.case()
        results = recovery_queries(case, {'query': 'Example Subject university research', 'context': 'public science'})
        for result in results:
            self.assertTrue(any('"' + name + '"' in result['query'] for name in [case['name'], *case['aliases']]))
        self.assertIn('"Example Alias"', {result['query'] for result in results})
        self.assertIn('"Example Subject" university research', {result['query'] for result in results})
        self.assertFalse(any('"Example Subject" Example Subject' in result['query'] for result in results))

    def test_vocabulary_comes_only_from_public_inputs_and_search_keywords(self):
        case = self.case('organization')
        options = {'query': 'Example Subject software company', 'context': 'Dublin open source research'}
        allowed = set(words(case['name'] + ' ' + ' '.join(case['aliases']) + ' ' + options['query'] + ' ' + options['context'])) | SEARCH_WORDS
        for result in recovery_queries(case, options):
            self.assertLessEqual(set(words(result['query'])), allowed)

    def test_length_bound_preserves_complete_name_and_omits_oversize_combinations(self):
        case = self.case('topic')
        case['name'] = 'N' * 100
        case['aliases'] = ['A' * 100]
        results = recovery_queries(case, {'query': 'context ' * 25, 'context': 'longcontext ' * 18})
        self.assertTrue(results)
        self.assertTrue(all(len(result['query']) <= 200 for result in results))
        self.assertTrue(all('"' + case['name'] + '"' in result['query'] or '"' + case['aliases'][0] + '"' in result['query'] for result in results))

    def test_explicit_public_context_is_available_even_when_also_a_saved_clue(self):
        queries = {result['query'] for result in recovery_queries(self.case(), {'query': '"Example Subject"', 'context': 'Secret Employer'})}
        self.assertIn('"Example Subject" Secret Employer', queries)

    def test_duplicate_aliases_and_context_do_not_duplicate_queries(self):
        case = self.case()
        case['aliases'] = ['Example Subject', 'example subject', 'Example Alias', 'Example Alias']
        results = recovery_queries(case, {'query': 'Example Subject research', 'context': 'research'})
        self.assertEqual(len(results), len({' '.join(result['query'].casefold().split()) for result in results}))


if __name__ == '__main__':
    unittest.main()
