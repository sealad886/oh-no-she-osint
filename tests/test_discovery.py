import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from local_osint.collector import discover
from local_osint.engine import Engine, InvalidInput


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.engine = Engine(Path(self.temp.name) / 'private')
        self.case = self.engine.create_case('Ada Lovelace', 'person', ['Augusta Ada King'], ['ada.example.org'])['id']

    def tearDown(self):
        self.temp.cleanup()

    def test_named_person_candidates_do_not_confirm_identity(self):
        wiki = {'query':{'search':[{'title':'Ada Lovelace','pageid':973,'snippet':'<span>Ada</span> was a mathematician.'}]}}
        data = {'search':[{'id':'Q7259','label':'Ada Lovelace','description':'English mathematician'}, {'id':'javascript:evil','label':'Bad'}]}
        with patch('local_osint.collector.fetch_public', side_effect=[(json.dumps(wiki).encode(),'json','https://en.wikipedia.org/w/api.php?query'), (json.dumps(data).encode(),'json','https://www.wikidata.org/w/api.php?query')]) as fetch:
            result = discover(self.engine, self.case, ['wikipedia','wikidata'], True)
        self.assertEqual(result['query'], 'Ada Lovelace')
        self.assertEqual(result['focus_type'], 'person')
        self.assertIn('not confirmed', result['interpretation'])
        self.assertEqual(len(result['results'][1]['candidates']), 1)
        self.assertNotIn('<span>', result['results'][0]['candidates'][0]['description'])
        self.assertEqual(self.engine.view(self.case)['counts']['evidence'], 2)
        self.assertTrue(fetch.call_args_list[0].kwargs['allow_query'])

    def test_alias_query_and_source_failures_are_visible(self):
        payload = b'{"search":[]}'
        with patch('local_osint.collector.fetch_public', side_effect=[InvalidInput('Unavailable'), (payload,'json','https://www.wikidata.org/w/api.php?query')]):
            result = discover(self.engine, self.case, ['wikipedia','wikidata'], True, 'Augusta Ada King')
        self.assertEqual(result['query'], 'Augusta Ada King')
        self.assertFalse(result['results'][0]['ok'])
        self.assertTrue(result['results'][1]['ok'])
        self.assertEqual(self.engine.view(self.case)['counts']['evidence'], 1)

    def test_maximum_query_is_collected_without_title_overflow(self):
        query = "a" * 200
        canonical = 'https://www.wikidata.org/w/api.php?search=' + query
        with patch('local_osint.collector.fetch_public', return_value=(b'{"search":[]}', 'json', canonical)):
            result = discover(self.engine, self.case, ['wikidata'], True, query)
        self.assertTrue(result['results'][0]['ok'])
        self.assertEqual(result['query'], query)
        saved = self.engine.evidence(self.case, result['results'][0]['id'])
        self.assertEqual(saved['source'], canonical)
        self.assertLessEqual(len(saved['title']), 200)

    def test_offline_and_invalid_case_do_not_disclose_names(self):
        with patch('local_osint.collector.fetch_public') as fetch:
            with self.assertRaises(InvalidInput):
                discover(self.engine, self.case, ['wikipedia'])
            with self.assertRaises(InvalidInput):
                discover(self.engine, 'missing', ['wikipedia'], True)
            fetch.assert_not_called()

    def test_subject_types_and_metadata_validation(self):
        for kind in ['person','organization','domain','topic','other']:
            result = self.engine.create_case('Subject', kind, ['Alias','Alias'], ['example.org'])
            self.assertEqual(result['focus_type'], kind)
            self.assertEqual(result['aliases'], ['Alias'])
            self.assertEqual(self.engine.export(result['id'])['case']['identifiers'], ['example.org'])
        for kind in ['invalid', None]:
            with self.assertRaises(InvalidInput):
                self.engine.create_case('Subject', kind)
        with self.assertRaises(InvalidInput):
            self.engine.create_case('Subject', 'person', ['Alias'] * 21)

    def test_malformed_source_response_has_explicit_failure(self):
        with patch('local_osint.collector.fetch_public', return_value=(b'{"error":{"info":"server data"}}','json','https://en.wikipedia.org/w/api.php')):
            result = discover(self.engine, self.case, ['wikipedia'], True)
        self.assertFalse(result['results'][0]['ok'])
        self.assertEqual(self.engine.view(self.case)['counts']['evidence'], 0)


if __name__ == '__main__':
    unittest.main()
