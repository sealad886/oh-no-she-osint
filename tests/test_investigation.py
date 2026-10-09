"""Prove subject-only collection, provenance, cancellation and tool scope."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from local_osint.engine import Engine, InvalidInput
from local_osint.investigation import Investigation, Jobs, save_report


class InvestigationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.engine = Engine(Path(self.temp.name) / 'private')
        self.case = self.engine.create_case('Ada Lovelace', 'person')

    def tearDown(self):
        self.temp.cleanup()

    def provider(self, url, allow_query=False):
        query = parse_qs(urlsplit(url).query)
        if 'Special:EntityData' in url:
            payload = {'entities': {'Q7259': {'labels': {'en': {'value': 'Ada Lovelace'}}, 'descriptions': {'en': {'value': 'Mathematician'}}, 'claims': {
                'P106': [{'mainsnak': {'snaktype': 'value', 'datavalue': {'value': {'id': 'Q170790'}}}}],
                'P856': [{'mainsnak': {'snaktype': 'value', 'datavalue': {'value': 'https://example.org/'}}}]
            }}}}
        elif query.get('action') == ['wbgetentities']:
            payload = {'entities': {'Q170790': {'labels': {'en': {'value': 'Mathematician'}}}}}
        elif 'wikidata.org/w/api' in url:
            payload = {'search': [{'id': 'Q7259', 'label': 'Ada Lovelace', 'description': 'Mathematician'}]}
        elif 'wikipedia.org/w/api' in url:
            payload = {'query': {'search': [{'pageid': 973, 'title': 'Ada Lovelace', 'snippet': 'A mathematician'}]}}
        elif 'gdeltproject' in url:
            payload = {'articles': [{'title': 'History article', 'url': 'https://news.example.org/article', 'seendate': '20261009'}]}
        else:
            return b'<p>Ada Lovelace example.org</p>', 'html', url
        return json.dumps(payload).encode(), 'json', url

    def signals(self, engine, case_id, domain, signals, enabled):
        saved = engine.ingest(case_id, b'Public domain signal', signals[0], 'signal: ' + signals[0])
        return {'domain': domain, 'results': [{'signal': signals[0], 'ok': True, **saved}]}

    def test_subject_alone_collects_dossier_and_cited_claims(self):
        with patch('local_osint.collector.fetch_public', side_effect=self.provider) as fetch, patch('local_osint.collector.research', side_effect=self.signals):
            report = Investigation(self.engine, self.case['id'], True).run()
        self.assertEqual(report['status'], 'complete')
        occupation = next(c for c in report['profiles'][0]['claims'] if c['property'] == 'P106')
        self.assertEqual(occupation['value'], 'Mathematician')
        self.assertEqual(report['articles'][0]['title'], 'History article')
        self.assertIn('page_source_id', report['articles'][0])
        self.assertGreaterEqual(len(report['source_ids']), 8)
        for profile in report['profiles']:
            for claim in profile['claims']:
                self.assertEqual(claim['candidate_id'], profile['candidate_id'])
                self.assertIn(claim['source_id'], report['source_ids'])
        self.assertIn('Unconfirmed', report['domain_lead']['basis'])
        self.assertLessEqual(report['request_units'], 24)
        self.assertEqual(self.engine.latest_investigation(self.case['id'])['run_id'], report['run_id'])
        export = self.engine.export(self.case['id'])
        report_ids = {e['id'] for e in export['evidence'] if e['source'].startswith('local-osint:')}
        self.assertFalse(any(m['evidence_id'] in report_ids for m in export['mentions']))
        self.assertTrue(any('news.example.org/article' in call.args[0] for call in fetch.call_args_list))

    def test_offline_and_unknown_case_never_send_names(self):
        with patch('local_osint.collector.fetch_public') as fetch:
            with self.assertRaises(InvalidInput):
                Investigation(self.engine, self.case['id']).run()
            with self.assertRaises(InvalidInput):
                Investigation(self.engine, 'unknown', True).run()
            fetch.assert_not_called()

    def test_failure_and_cancellation_keep_original_evidence(self):
        cancel = threading.Event()
        def progress(report):
            if report['source_ids']:
                cancel.set()
        with patch('local_osint.collector.fetch_public', side_effect=self.provider) as fetch:
            report = Investigation(self.engine, self.case['id'], True, progress, cancel).run()
        self.assertEqual(report['status'], 'cancelled')
        self.assertEqual(fetch.call_count, 1)
        self.assertTrue(report['source_ids'])
        self.engine.evidence(self.case['id'], report['source_ids'][0])
        with patch('local_osint.collector.fetch_public', side_effect=InvalidInput('Service unavailable')):
            report = Investigation(self.engine, self.case['id'], True).run()
        self.assertEqual(report['status'], 'failed')
        self.assertTrue(all(not step['ok'] for step in report['steps']))

    def test_tool_scope_cannot_exfiltrate_or_read_other_cases(self):
        runner = Investigation(self.engine, self.case['id'], True)
        other = self.engine.create_case('Other')
        evidence = self.engine.ingest(other['id'], b'Private unrelated notes', 'Private')
        for name, args in [('search_subject', {'query': 'secret text'}), ('news_search', {'query': 'secret'}), ('collect_page', {'url': 'https://example.org/private-note'}), ('domain_signals', {'domain': 'example.org'}), ('read_evidence', {'id': evidence['id']}), ('exec_shell_command', {'command': 'anything'})]:
            with self.subTest(name=name), patch('local_osint.collector.fetch_public') as fetch:
                with self.assertRaises(InvalidInput):
                    runner.dispatch(name, args)
                fetch.assert_not_called()
        self.assertEqual(runner.dispatch('search_subject', {}), {'candidates': []})

    def test_deadline_and_request_budget_prevent_next_request(self):
        runner = Investigation(self.engine, self.case['id'], True, deadline_seconds=0)
        with patch('local_osint.collector.fetch_public') as fetch:
            self.assertEqual(runner.run()['status'], 'limited')
            fetch.assert_not_called()
        runner = Investigation(self.engine, self.case['id'], True)
        runner.used = 24
        with patch('local_osint.collector.fetch_public') as fetch:
            self.assertEqual(runner.run()['status'], 'limited')
            fetch.assert_not_called()

    def test_jobs_reserve_one_slot_and_restart_marks_interrupted(self):
        entered, release = threading.Event(), threading.Event()
        def paused(url, allow_query=False):
            entered.set()
            release.wait(3)
            return self.provider(url, allow_query)
        jobs = Jobs(self.engine, True)
        with patch('local_osint.collector.fetch_public', side_effect=paused):
            first = jobs.start(self.case['id'])
            self.assertTrue(entered.wait(2))
            self.assertEqual(jobs.start(self.case['id'])['run_id'], first['run_id'])
            other = self.engine.create_case('Mozilla', 'organization')
            with self.assertRaises(InvalidInput):
                jobs.start(other['id'])
            jobs.stop(self.case['id'], first['run_id'])
            release.set()
            for _ in range(100):
                if jobs.get(self.case['id'])['status'] != 'running':
                    break
                time.sleep(.01)
            self.assertEqual(jobs.get(self.case['id'])['status'], 'cancelled')
        unfinished = Investigation(self.engine, other['id'], True).report
        save_report(self.engine, unfinished)
        Jobs(self.engine)
        self.assertEqual(self.engine.latest_investigation(other['id'])['status'], 'interrupted')

    def test_known_identifier_domain_takes_priority_over_candidate_website(self):
        case = self.engine.create_case('Mozilla', 'organization', identifiers=['mozilla.org'])
        with patch('local_osint.collector.fetch_public', side_effect=self.provider), patch('local_osint.collector.research', side_effect=self.signals):
            report = Investigation(self.engine, case['id'], True).run()
        self.assertEqual(report['domain_lead']['domain'], 'mozilla.org')
        self.assertIn('User supplied', report['domain_lead']['basis'])

    def test_rank_and_qualifiers_preserve_statement_scope_and_current_website_leads(self):
        def statement(value, rank='normal', qualifiers=None):
            return {'rank': rank, 'mainsnak': {'snaktype': 'value', 'datavalue': {'value': value}}, 'qualifiers': qualifiers or {}}
        ended = {'P582': [{'snaktype': 'value', 'datavalue': {'value': {'time': '+2005-01-01T00:00:00Z'}}}]}
        payload = {'entities': {'Q1': {'claims': {
            'P856': [statement('https://aaa-old.example.org/', 'deprecated'), statement('https://ended.example.org/', qualifiers=ended), statement('https://current.example.org/', 'preferred')],
            'P127': [statement({'id': 'Q2'}, qualifiers=ended)],
            'P108': [statement({'id': 'Q3'}, 'deprecated')]
        }}}}
        candidate = {'url': 'https://www.wikidata.org/wiki/Q1', 'collection_url': 'https://www.wikidata.org/wiki/Special:EntityData/Q1.json', 'label': 'Ada', 'description': ''}
        runner = Investigation(self.engine, self.case['id'], True)
        with patch('local_osint.collector.fetch_public', return_value=(json.dumps(payload).encode(), 'json', candidate['collection_url'])):
            runner.profile(candidate)
        claims = runner.report['profiles'][0]['claims']
        self.assertFalse(any(c['property'] == 'P108' for c in claims))
        owner = next(c for c in claims if c['property'] == 'P127')
        self.assertEqual(owner['qualifiers'][0]['property'], 'P582')
        self.assertIn('2005', owner['qualifiers'][0]['value'])
        self.assertEqual(runner.allowed_domains, {'current.example.org'})
        self.assertEqual([c for c in claims if c['property'] == 'P856'][0]['rank'], 'preferred')
        runner.publish(persist=True)
        exported = self.engine.export(self.case['id'])['investigation']
        self.assertEqual(exported['profiles'][0]['claims'], claims)
        markdown = self.engine.markdown(self.case['id'])
        self.assertIn('End time', markdown)
        self.assertIn('2005', markdown)

    def test_ai_truncation_remains_visible_in_report_export(self):
        from unittest.mock import Mock
        from local_osint.ai import run_assistant
        ai = Mock()
        ai.chat.return_value = {'content': 'Incomplete draft', 'finish_reason': 'length'}
        report = Investigation(self.engine, self.case['id'], True).report
        report['ai'] = run_assistant(ai, report, Mock())
        save_report(self.engine, report)
        self.assertEqual(self.engine.export(self.case['id'])['investigation']['ai']['status'], 'truncated')
        self.assertIn('Incomplete', self.engine.markdown(self.case['id']))

    def test_temporal_precision_survives_dossier_export_and_model_input(self):
        from local_osint.investigation import statement_value
        value = {'time': '+1838-01-01T00:00:00Z', 'precision': 9, 'calendarmodel': 'http://www.wikidata.org/entity/Q1985727', 'before': 0, 'after': 0}
        year = statement_value(value)
        day = statement_value({**value, 'precision': 11})
        self.assertIn('1838', year)
        self.assertNotIn('01-01', year)
        self.assertIn('year precision', year)
        self.assertIn('1838-01-01', day)
        self.assertIn('day precision', day)
        self.assertIn('Julian calendar', statement_value({**value, 'calendarmodel': 'http://www.wikidata.org/entity/Q1985786'}))
        self.assertIn('before: 2', statement_value({**value, 'before': 2}))
        payload = {'entities': {'Q1': {'claims': {'P569': [{'mainsnak': {'snaktype': 'value', 'datavalue': {'value': value}}}]}}}}
        candidate = {'url': 'https://www.wikidata.org/wiki/Q1', 'collection_url': 'https://www.wikidata.org/wiki/Special:EntityData/Q1.json', 'label': 'Candidate', 'description': ''}
        runner = Investigation(self.engine, self.case['id'], True)
        with patch('local_osint.collector.fetch_public', return_value=(json.dumps(payload).encode(), 'json', candidate['collection_url'])):
            runner.profile(candidate)
        runner.publish(persist=True)
        projected = self.engine.export(self.case['id'])['investigation']['profiles'][0]['claims'][0]['value']
        self.assertEqual(projected, year)
        self.assertIn('year precision', self.engine.markdown(self.case['id']))
        from local_osint.ai import run_assistant
        from unittest.mock import Mock
        ai = Mock()
        ai.chat.return_value = {'content': 'Needs source review', 'finish_reason': 'stop'}
        run_assistant(ai, runner.report, Mock())
        self.assertIn('year precision', ai.chat.call_args.args[0][1]['content'])


if __name__ == '__main__':
    unittest.main()
