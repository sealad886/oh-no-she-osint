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
    def test_cached_domain_failures_remain_visible_and_preserve_partial_sources(self):
        for mixed in [False, True]:
            with self.subTest(mixed=mixed):
                runner = Investigation(self.engine, self.case['id'], True)
                runner.allowed_domains.add('example.org')
                saved = self.engine.ingest(self.case['id'], b'Public DNS', 'DNS')
                results = [{'signal': 'dns', 'ok': True, **saved} if mixed else {'signal': 'dns', 'ok': False, 'error': 'DNS unavailable.'},
                           {'signal': 'website', 'ok': False, 'error': 'Destination returned HTTP 503.'}]
                with patch('local_osint.collector.research', side_effect=[{'results': [r]} for r in results]) as research:
                    runner.domain('example.org', ['dns', 'website'])
                    result = runner.dispatch('domain_signals', {'domain': 'example.org'})
                    self.assertEqual(research.call_count, 2)
                self.assertEqual(runner.used, 2)
                self.assertIn('Partial domain' if mixed else 'All domain', result['error'])
                self.assertIn('HTTP 503', result['results'][1]['error'])
                step = runner.report['steps'][-1]
                self.assertEqual(step['origin'], 'ai')
                self.assertEqual(step['status'], 'failed')
                self.assertIn('HTTP 503', step['error'])
                if mixed:
                    self.assertIn(saved['id'], runner.report['source_ids'])

    def test_ai_reuse_of_failed_page_remains_visible_without_automatic_retry(self):
        runner = Investigation(self.engine, self.case['id'], True)
        url = 'https://example.org/blocked'
        runner.allowed_urls.add(url)
        with patch('local_osint.collector.collect', side_effect=InvalidInput('Destination returned HTTP 999.')) as collect:
            runner.page(url)
            result = runner.dispatch('collect_page', {'url': url})
            self.assertEqual(collect.call_count, 1)
        self.assertEqual(runner.used, 1)
        self.assertIn('HTTP 999', result['error'])
        step = runner.report['steps'][-1]
        self.assertEqual(step['origin'], 'ai')
        self.assertEqual(step['arguments'], {'url': url})
        self.assertEqual(step['status'], 'failed')
        self.assertIn('HTTP 999', step['error'])

    def test_ai_reuse_of_saved_page_keeps_source_reference_and_uses_no_unit(self):
        runner = Investigation(self.engine, self.case['id'], True)
        url = 'https://example.org/public'
        runner.allowed_urls.add(url)
        saved = self.engine.ingest(self.case['id'], b'Public profile', 'Profile', url)
        with patch('local_osint.collector.collect', return_value=saved) as collect:
            runner.page(url)
            runner.dispatch('collect_page', {'url': url})
            self.assertEqual(collect.call_count, 1)
        self.assertEqual(runner.used, 1)
        self.assertEqual(runner.report['steps'][-1]['source_id'], saved['id'])
        self.assertEqual(runner.report['steps'][-1]['origin'], 'ai')

    def test_policy_blocked_ai_request_is_visible_editable_and_never_sent(self):
        runner = Investigation(self.engine, self.case['id'], True)
        arguments = {'query': '"Ada Lovelace" hospital'}
        with patch('local_osint.collector.web_search') as search:
            with self.assertRaisesRegex(InvalidInput, 'approved public context'):
                runner.dispatch('web_search', arguments)
            search.assert_not_called()
        step = runner.report['steps'][-1]
        self.assertEqual(step['tool'], 'web_search')
        self.assertEqual(step['arguments'], arguments)
        self.assertEqual(step['origin'], 'ai')
        self.assertEqual(step['status'], 'failed')
        self.assertIn('approved public context', step['error'])
        self.assertEqual(runner.used, 0)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.engine = Engine(Path(self.temp.name) / 'private')
        self.case = self.engine.create_case('Ada Lovelace', 'person')

    def tearDown(self):
        self.temp.cleanup()

    def provider(self, url, allow_query=False):
        query = parse_qs(urlsplit(url).query)
        if 'duckduckgo' in url:
            return b'<a class="result-link" href="https://example.org/ada">Ada Lovelace profile</a><td class="result-snippet">Ada Lovelace mathematician</td>', 'html', url
        elif 'Special:EntityData' in url:
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
            payload = {'articles': [{'title': 'Ada Lovelace history article', 'url': 'https://news.example.org/article', 'seendate': '20261009'}]}
        else:
            return b'<p>Ada Lovelace wrote detailed notes about the analytical engine and its potential mathematical applications.</p>', 'html', url
        return json.dumps(payload).encode(), 'json', url

    def signals(self, engine, case_id, domain, signals, enabled):
        saved = engine.ingest(case_id, b'Public domain signal', signals[0], 'signal: ' + signals[0])
        return {'domain': domain, 'results': [{'signal': signals[0], 'ok': True, **saved}]}

    def test_subject_alone_collects_dossier_and_cited_claims(self):
        with patch('local_osint.collector.fetch_public', side_effect=self.provider) as fetch, patch('local_osint.collector.research', side_effect=self.signals):
            report = Investigation(self.engine, self.case['id'], True, options={'page_limit': 3}).run()
        self.assertEqual(report['status'], 'complete')
        occupation = next(c for c in report['profiles'][0]['claims'] if c['property'] == 'P106')
        self.assertEqual(occupation['value'], 'Mathematician')
        self.assertEqual(report['articles'][0]['title'], 'Ada Lovelace history article')
        self.assertTrue(any(page['url'] == 'https://example.org/ada' for page in report['pages']))
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
        report['options']['tools'] = ['wikipedia']
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

    def test_ordinary_person_search_collects_name_matches_and_skips_mailbox_domain(self):
        case = self.engine.create_case('Eric Mulhall', 'person', identifiers=['public-lead@gmail.com'])
        def provider(url, allow_query=False):
            if 'duckduckgo' in url:
                return b'<a class="result-link" href="https://example.org/people?id=7">Eric Mulhall profile</a><td class="result-snippet">Eric Mulhall works in a public laboratory.</td><a class="result-link" href="https://example.org/unrelated">Different Mulhall</a><td class="result-snippet">Another person</td>', 'html', url
            if 'wikipedia' in url and '/w/api' in url:
                return b'{"query":{"search":[{"pageid":1,"title":"Unrelated disease","snippet":"Mulhall references"}]}}', 'json', url
            if 'wikidata' in url:
                return b'{"search":[]}', 'json', url
            return b'<p>Eric Mulhall works in a public laboratory researching molecular biology and publishing detailed scientific articles.</p><a href="/people/eric/about">About Eric Mulhall</a>', 'html', url
        with patch('local_osint.collector.fetch_public', side_effect=provider) as fetch, patch('local_osint.collector.research') as domains:
            report = Investigation(self.engine, case['id'], True, options={'tools': ['web_search', 'wikipedia', 'wikidata', 'domain'], 'page_limit': 1}).run()
        urls = [c.args[0] for c in fetch.call_args_list]
        self.assertIn('https://example.org/people?id=7', urls)
        self.assertFalse(any('Unrelated_disease' in url or '/unrelated' in url for url in urls))
        domains.assert_not_called()
        self.assertEqual(len(report['pages']), 1)
        self.assertTrue(report['web_results'][0]['page_source_id'])
        self.assertEqual(report['pages'][0]['links'][0]['url'], 'https://example.org/people/eric/about')
        self.assertIn('limit', report['stop_reason'])

    def test_model_search_is_real_but_cannot_transmit_other_case_notes(self):
        runner = Investigation(self.engine, self.case['id'], True)
        with patch('local_osint.collector.fetch_public', side_effect=self.provider) as fetch:
            result = runner.dispatch('web_search', {'query': '"Ada Lovelace" profile'})
            self.assertTrue(result['results'])
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(runner.report['steps'][-1]['origin'], 'ai')
            with self.assertRaises(InvalidInput):
                runner.dispatch('web_search', {'query': '"Ada Lovelace" private-secret@example.org'})
            self.assertEqual(fetch.call_count, 1)

    def test_activity_is_visible_during_calls_and_preserves_failure_evidence(self):
        observed = []
        runner = Investigation(self.engine, self.case['id'], True, progress=observed.append)
        saved = self.engine.ingest(self.case['id'], b'Provider challenge', 'Search challenge')
        error = InvalidInput('Search challenged this request.')
        error.evidence_id = saved['id']
        with patch('local_osint.collector.web_search', side_effect=error):
            runner.web_search('"Ada Lovelace"')
        pending = next(r for r in observed if r['steps'][-1]['status'] == 'running')
        self.assertEqual(pending['steps'][-1]['arguments']['query'], '"Ada Lovelace"')
        self.assertEqual(pending['active_tool'], pending['steps'][-1]['id'])
        final = runner.report['steps'][-1]
        self.assertEqual(final['status'], 'failed')
        self.assertIn(saved['id'], runner.report['source_ids'])
        self.assertEqual(final['source_id'], saved['id'])
        self.assertGreaterEqual(final['duration_ms'], 0)

    def test_selected_source_retry_preserves_history_and_runs_only_requested_tool(self):
        runner = Investigation(self.engine, self.case['id'], True, options={'tools': ['web_search']})
        with patch('local_osint.collector.fetch_public', side_effect=self.provider):
            previous = runner.run()
        retry = Investigation(self.engine, self.case['id'], True, previous=previous, options=previous['options'])
        with patch('local_osint.collector.fetch_public', side_effect=self.provider) as fetch:
            report = retry.run({'tool': 'web_search', 'arguments': {'query': '"Ada Lovelace" portfolio'}})
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(report['parent_run_id'], previous['run_id'])
        self.assertTrue(set(previous['source_ids']).issubset(report['source_ids']))
        self.assertEqual(len(report['steps']), len(previous['steps']) + 1)
        self.assertEqual(report['options']['query'], '"Ada Lovelace" portfolio')
        self.assertEqual(report['budget']['used'], 1)
        self.assertEqual(self.engine.latest_investigation(self.case['id'])['run_id'], report['run_id'])

    def test_search_retry_returns_results_even_when_urls_repeat(self):
        runner = Investigation(self.engine, self.case['id'], True)
        with patch('local_osint.collector.fetch_public', side_effect=self.provider):
            first = runner.web_search('"Ada Lovelace"')
            second = runner.web_search('"Ada Lovelace" profile')
        self.assertEqual(len(first['results']), len(second['results']))
        self.assertEqual(len(runner.report['web_results']), 1)

    def test_configurable_limits_and_offline_local_reads(self):
        for options in [{'max_requests': 0}, {'max_requests': True}, {'page_limit': 0}, {'page_limit': 1.5}, {'page_limit': 2 ** 53}, {'max_requests': 2 ** 53}, {'tools': ['shell']}, {'context': 'x' * 200, 'query': 'x' * 200}]:
            with self.subTest(options=options), self.assertRaises(InvalidInput):
                Investigation(self.engine, self.case['id'], True, options=options)
        runner = Investigation(self.engine, self.case['id'], True, options={'max_requests': 1, 'tools': ['web_search']})
        with patch('local_osint.collector.fetch_public', side_effect=self.provider) as fetch:
            report = runner.run()
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(report['status'], 'limited')
        self.assertIn('ceiling', report['stop_reason'])
        evidence = self.engine.ingest(self.case['id'], b'Previously saved evidence', 'Local')
        local = Investigation(self.engine, self.case['id'], local_only=True)
        with patch('local_osint.collector.fetch_public') as fetch:
            read = local.run({'tool': 'read_evidence', 'arguments': {'id': evidence['id']}})
            self.assertEqual(read['status'], 'complete')
            with self.assertRaises(InvalidInput):
                local.dispatch('web_search', {'query': '"Ada Lovelace"'})
            fetch.assert_not_called()

    def test_larger_user_limits_allow_more_than_64_units_and_eight_useful_pages(self):
        """User limits control actual work, rather than only accepting large values."""
        runner = Investigation(self.engine, self.case['id'], True,
                              options={'tools': ['web_search'], 'max_requests': 100, 'page_limit': 70})
        searches = []
        def search(engine, case_id, query, enabled):
            offset = len(searches) * 10
            searches.append(query)
            saved = engine.ingest(case_id, query.encode(), 'Public search', 'search:' + query)
            return {**saved, 'results': [{'title': 'Ada Lovelace public profile', 'snippet': 'Detailed public biography',
                'url': f'https://example.org/profile/{n}'} for n in range(offset, offset + 10)]}
        def collect(engine, case_id, url, enabled, allow_query=False):
            body = '<p>Ada Lovelace wrote detailed notes about analytical engines and mathematical applications.</p>'
            if url.rsplit('/', 1)[-1].isdigit():
                body += f'<a href="{url}/about">About Ada Lovelace</a><a href="{url}/team">Team Ada Lovelace</a>'
            return engine.ingest(case_id, body.encode(), 'Public page', url, 'html')
        with patch('local_osint.collector.web_search', side_effect=search), patch('local_osint.collector.collect', side_effect=collect):
            report = runner.run()
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['quality_count'], 70)
        self.assertEqual(report['budget'], {'used': 73, 'limit': 100})
        self.assertEqual(len(report['pages']), 70)
        self.assertEqual(len(searches), 3)

    def test_ai_request_failure_can_be_retried_without_losing_prior_tool_activity(self):
        from unittest.mock import Mock
        ai = Mock()
        ai.chat.side_effect = InvalidInput('Local AI timed out; retry analysis.')
        runner = Investigation(self.engine, self.case['id'], True, ai=ai, options={'tools': ['web_search']})
        with patch('local_osint.collector.fetch_public', side_effect=self.provider):
            report = runner.run()
        self.assertEqual(report['status'], 'partial')
        self.assertIn('timed out', report['ai']['error'])
        self.assertTrue(report['source_ids'])
        self.assertEqual(next(s for s in report['steps'] if s['tool'] == 'analyze')['status'], 'failed')

    def test_later_model_timeout_retains_completed_ai_calls_in_trace(self):
        from unittest.mock import Mock
        ai = Mock()
        ai.chat.side_effect = [{'tool_calls': [{'function': {'name': 'web_search', 'arguments': '{"query":"Ada Lovelace"}'}}]}, InvalidInput('Local AI timed out.')]
        runner = Investigation(self.engine, self.case['id'], True, ai=ai, options={'tools': ['web_search']})
        with patch('local_osint.collector.fetch_public', side_effect=self.provider):
            runner.analyze()
        self.assertEqual(runner.report['ai']['status'], 'failed')
        self.assertEqual(runner.report['ai']['tool_calls'][0]['tool'], 'web_search')
        self.assertEqual(runner.report['ai']['tool_calls'][0]['arguments']['query'], 'Ada Lovelace')
        self.assertEqual(runner.report['ai']['tool_calls'][0]['arguments']['provider'], 'duckduckgo')
        self.assertTrue(runner.report['ai']['tool_calls'][0]['ok'])

    def test_unavailable_pages_do_not_consume_successful_page_goal(self):
        runner = Investigation(self.engine, self.case['id'], True, options={'tools': ['web_search'], 'page_limit': 1})
        search = {'id': self.engine.ingest(self.case['id'], b'Search results', 'Search')['id'], 'results': [
            {'title': 'Ada Lovelace blocked profile', 'snippet': '', 'url': 'https://example.org/blocked'},
            {'title': 'Ada Lovelace public profile', 'snippet': '', 'url': 'https://example.org/public'}]}
        saved = self.engine.ingest(self.case['id'], b'Ada Lovelace public profile', 'Public', 'https://example.org/public')
        with patch('local_osint.collector.web_search', return_value=search), patch('local_osint.collector.collect', side_effect=[InvalidInput('HTTP 403'), saved]) as collect:
            report = runner.run()
        self.assertEqual(collect.call_count, 2)
        self.assertEqual(len(report['pages']), 1)
        self.assertEqual(report['pages'][0]['url'], 'https://example.org/public')


if __name__ == '__main__':
    unittest.main()
