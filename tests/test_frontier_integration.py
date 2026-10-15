"""Source-frontier behavior across collection, persistence and worker restart.

All people, pages and responses are synthetic. External transports are replaced,
while the real evidence store, connector parsers, queue and workers are exercised.
"""

import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from local_osint.engine import Engine, InvalidInput
from local_osint.frontier import Frontier
from local_osint.investigation import Investigation, Jobs
from local_osint.workspace import Workspace


class FrontierIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.directory = Path(self.temp.name) / "private"
        self.engine = Engine(self.directory)
        self.case = self.engine.create_case("Ada Public", "person", identifiers=["secret-alias"])['id']
        self.workspace = Workspace(self.engine)
        self.jobs = []

    def tearDown(self):
        for jobs in self.jobs:
            jobs.close()
        self.temp.cleanup()

    def runner(self, **options):
        values = {'tools': [], 'packs': ['website_sources'], 'max_requests': 20, 'page_limit': 10,
                  **options}
        return Investigation(self.engine, self.case, enabled=True, options=values)

    @staticmethod
    def response(raw=b'{"message":{"items":[]}}', format='json', status=200, url='https://api.crossref.org/works'):
        return {'raw': raw, 'format': format, 'status': status, 'canonical': url,
                'requests': 1, 'retry_after': 60 if status == 429 else None}

    def make_jobs(self, enabled=True):
        jobs = Jobs(Engine(self.directory), enabled=enabled)
        self.jobs.append(jobs)
        return jobs

    def finished(self, jobs):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = jobs.get(self.case)
            if result and result['status'] != 'running':
                return result
            time.sleep(0.01)
        self.fail('Synthetic source worker did not finish within five seconds.')

    def test_ordinary_person_seed_discovers_first_party_page_without_wikipedia(self):
        first = 'https://community.example.org/ada'
        second = 'https://community.example.org/volunteers'
        originals = {
            first: b'<article>Ada Public volunteers at a community repair workshop every Saturday. Public programme information and organizer details are available here. <a href="/volunteers">Volunteer programme</a></article>',
            second: b'<article>Ada Public coordinates the community volunteer programme. The public annual bulletin describes workshops, repair events and local public services.</article>',
        }
        runner = self.runner(seed_urls=[first])
        def page_fetch(url, **kwargs):
            return originals[url], 'html', url
        def source_fetch(url, **kwargs):
            return self.response(originals[url], 'html', url=url)
        with patch('local_osint.collector.fetch_public', side_effect=page_fetch) as pages, \
                patch('local_osint.connectors.fetch_public_response', side_effect=source_fetch), \
                patch('local_osint.collector.discover') as encyclopedia:
            report = runner.run()
        encyclopedia.assert_not_called()
        self.assertEqual({call.args[0] for call in pages.call_args_list}, {first, second})
        self.assertEqual(report['quality_count'], 2)
        snapshot = self.workspace.snapshot(self.case)
        self.assertEqual(snapshot['counts']['readable_documents'], 2)
        self.assertEqual(snapshot['counts']['corroborated_claims'], 0)
        self.assertEqual(len(snapshot['candidates']), 2)
        self.assertTrue(all(item['status'] == 'unconfirmed' for item in snapshot['candidates']))
        self.assertTrue(all(claim['candidate_id'] in {item['id'] for item in snapshot['candidates']} for claim in snapshot['claims']))
        with self.engine.connection() as db:
            for url, raw in originals.items():
                rows = db.execute('SELECT raw,sha256 FROM evidence WHERE case_id=? AND source=?', (self.case, url)).fetchall()
                self.assertTrue(rows)
                self.assertTrue(all(row['raw'] == raw and row['sha256'] == hashlib.sha256(raw).hexdigest() for row in rows))

    def test_manual_local_email_analysis_works_offline_without_network_units(self):
        original = self.engine.ingest(self.case, b'From: public@example.org\nSubject: Public workshop\nAuthentication-Results: example.org; dkim=pass\n\nBody', 'Synthetic saved email')['id']
        jobs = self.make_jobs(enabled=False)
        with patch('local_osint.connectors.fetch_public_response') as outbound:
            jobs.start(self.case, action={'tool': 'email_headers', 'arguments': {'evidence_id': original}})
            report = self.finished(jobs)
        outbound.assert_not_called()
        self.assertEqual(report['request_units'], 0)
        self.assertIn(original, report['source_ids'])
        task = next(item for item in self.workspace.tasks(self.case) if item['connector'] == 'email_headers')
        self.assertEqual(task['state'], 'complete')
        self.assertEqual(task['result']['requests'], 0)

    def test_approved_public_username_is_checked_without_sending_private_alias(self):
        runner = self.runner(packs=['public_profile'], public_usernames=['ada-public'])
        frontier = Frontier(runner)
        frontier.seed()
        with self.assertRaises(InvalidInput):
            frontier.enqueue('public_profile', {'username': 'secret-alias', 'site': 'gitlab'}, 'Unapproved private clue', explicit=True)
        raw = b'<meta property="og:title" content="ada-public GitLab">'
        with patch('local_osint.connectors.fetch_public_response', return_value=self.response(raw, 'html', url='https://gitlab.com/ada-public')) as outbound, \
                patch('local_osint.collector.fetch_public', return_value=(raw, 'html', 'https://gitlab.com/ada-public')):
            frontier.run_queue()
        self.assertEqual(outbound.call_count, 1)
        self.assertEqual(outbound.call_args.args[0], 'https://gitlab.com/ada-public')
        self.assertNotIn('secret-alias', json.dumps([call.args for call in outbound.call_args_list]))
        self.assertEqual(runner.report['source_outcomes'][0]['observations'][0]['ownership'], 'unconfirmed')

    def test_encoded_private_contacts_and_unapproved_private_urls_never_dispatch(self):
        case = self.engine.create_case('Ada Public', 'person',
            identifiers=['hidden-contact@example.org', 'Hidden Contact', 'https://private.example.org/profile'])['id']
        runner = Investigation(self.engine, case, True, options={'tools': [], 'packs': ['website_sources']})
        frontier = Frontier(runner)
        urls = ['https://community.example.org/people?contact=hidden-contact%40example.org',
                'https://community.example.org/people?contact=hidden-contact%2540example.org',
                'https://community.example.org/people?lookup=Hidden+Contact',
                'https://private.example.org/profile']
        with patch('local_osint.collector.fetch_public') as outbound:
            for url in urls:
                runner.allowed_urls.add(url)
                with self.subTest(url=url), self.assertRaises(InvalidInput):
                    frontier.enqueue('collect_page', {'url': url}, 'Discovered public link')
        outbound.assert_not_called()
        self.assertEqual(self.workspace.tasks(case), [])
        approved = Investigation(self.engine, case, True, options={'tools': [],
            'packs': ['website_sources'], 'seed_urls': [urls[-1]]})
        Frontier(approved).seed()
        self.assertEqual(len(self.workspace.tasks(case)), 1)

    def test_invalid_model_choice_falls_back_to_real_queued_work(self):
        runner = self.runner(packs=['crossref'])
        runner.ai = object()
        frontier = Frontier(runner)
        for suffix in ['profile', 'news']:
            frontier.enqueue('crossref', {'query': '"Ada Public" ' + suffix}, 'Public source discovery')
        with patch('local_osint.ai.choose_action', side_effect=InvalidInput('Invalid action')), \
                patch('local_osint.connectors.fetch_public_response', return_value=self.response()) as outbound:
            frontier.run_queue()
        self.assertEqual(outbound.call_count, 2)
        self.assertTrue(all(task['state'] == 'complete' for task in self.workspace.tasks(self.case)))

    def test_pack_only_empty_discovery_is_partial_but_single_tool_can_complete(self):
        with patch('local_osint.connectors.fetch_public_response', return_value=self.response()):
            automatic = self.runner(packs=['crossref']).run()
            manual = self.runner(packs=['crossref']).run(action={'tool': 'crossref',
                'arguments': {'query': '"Ada Public" research'}})
        self.assertEqual(automatic['status'], 'partial')
        self.assertEqual(automatic['quality_count'], 0)
        self.assertEqual(manual['status'], 'complete')

    def test_repeated_completed_model_choice_does_not_block_remaining_tasks(self):
        runner = self.runner(packs=['crossref'])
        runner.ai = object()
        frontier = Frontier(runner)
        tasks = [frontier.enqueue('crossref', {'query': '"Ada Public" ' + suffix}, 'Public source discovery')
                 for suffix in ['profile', 'news', 'research']]
        with patch('local_osint.ai.choose_action', return_value={'id': tasks[0]['id'], 'reason': 'Repeated model suggestion'}) as choose, \
                patch('local_osint.connectors.fetch_public_response', return_value=self.response()) as outbound:
            frontier.run_queue()
        self.assertGreaterEqual(choose.call_count, 2)
        self.assertEqual(outbound.call_count, 3)
        self.assertTrue(all(task['state'] == 'complete' for task in self.workspace.tasks(self.case)))

    def test_rate_limit_cooldown_and_original_survive_worker_restart(self):
        runner = self.runner(packs=['crossref'])
        frontier = Frontier(runner)
        frontier.seed()
        with patch('local_osint.connectors.fetch_public_response', return_value=self.response(b'{"error":"slow down"}', status=429)):
            frontier.run_queue()
        task = self.workspace.tasks(self.case)[0]
        self.assertEqual(task['state'], 'cooldown')
        retained = task['evidence_ids']
        self.assertEqual(len(retained), 1)
        self.assertTrue(task['retry_at'])
        jobs = self.make_jobs()
        with patch('local_osint.connectors.fetch_public_response') as outbound:
            jobs.start(self.case, options={'packs': ['crossref'], 'tools': []}, action={'tool': 'resume_queue', 'arguments': {}})
            report = self.finished(jobs)
        outbound.assert_not_called()
        after = next(item for item in self.workspace.tasks(self.case) if item['id'] == task['id'])
        self.assertEqual(after['state'], 'cooldown')
        self.assertEqual(after['evidence_ids'], retained)
        self.assertEqual(report['request_units'], 0)
        self.assertEqual(self.workspace.health(self.case)[0]['state'], 'rate_limited')

    def test_interrupted_action_needs_resume_and_retains_existing_original(self):
        raw = b'From: public@example.org\nSubject: Existing evidence\n\nPublic body'
        original = self.engine.ingest(self.case, raw, 'Existing original')['id']
        initial = self.runner(packs=['crossref'])
        initial.run({'tool': 'email_headers', 'arguments': {'evidence_id': original}})
        task = self.workspace.enqueue(self.case, 'crossref', {'query': '"Ada Public" research'}, reason='Read public metadata')
        self.workspace.start_action(self.case, task['id'], 'prior-run')
        with patch('local_osint.connectors.fetch_public_response') as no_implicit_collection:
            jobs = self.make_jobs()
        no_implicit_collection.assert_not_called()
        self.assertEqual(next(item for item in self.workspace.tasks(self.case) if item['id'] == task['id'])['state'], 'interrupted_after_dispatch')
        with patch('local_osint.connectors.fetch_public_response', return_value=self.response()) as outbound:
            jobs.start(self.case, options={'packs': ['crossref'], 'tools': []}, action={'tool': 'resume_queue', 'arguments': {}})
            report = self.finished(jobs)
        self.assertEqual(outbound.call_count, 1)
        self.assertIn(original, report['source_ids'])
        self.assertEqual(self.engine.evidence(self.case, original)['sha256'], hashlib.sha256(raw).hexdigest())
        attempts = self.workspace.attempts(self.case, task['id'])
        self.assertEqual({item['state'] for item in attempts}, {'interrupted_after_dispatch', 'complete'})

    def test_disabled_source_pack_keeps_queued_work_without_dispatch(self):
        task = self.workspace.enqueue(self.case, 'crossref', {'query': '"Ada Public" projects'}, reason='Previously enabled source')
        runner = self.runner(packs=['website_sources'])
        with patch('local_osint.connectors.fetch_public_response') as outbound:
            Frontier(runner).run_queue()
        outbound.assert_not_called()
        self.assertEqual(self.workspace.tasks(self.case)[0]['id'], task['id'])
        self.assertEqual(self.workspace.tasks(self.case)[0]['state'], 'pending')
        self.assertEqual(runner.used, 0)

    def test_search_snippet_remains_discovery_without_readable_document_credit(self):
        raw = b'<a class="result-link" href="https://community.example.org/ada">Ada Public</a><td class="result-snippet">Ada Public organizes community repair workshops.</td>'
        runner = self.runner(tools=['web_search'])
        with patch('local_osint.collector.fetch_public', return_value=(raw, 'html', 'https://lite.duckduckgo.com/lite/?q=Ada+Public')):
            result = runner.web_search('"Ada Public"')
        self.assertEqual(len(result['results']), 1)
        self.assertIn(result['id'], runner.report['source_ids'])
        self.assertEqual(runner.page_count(), 0)
        self.assertEqual(self.workspace.snapshot(self.case)['counts']['readable_documents'], 0)

    def test_profile_login_shell_preserves_response_without_readable_document_credit(self):
        runner = self.runner(packs=['public_profile'], public_usernames=['ada-public'])
        frontier = Frontier(runner)
        frontier.seed()
        raw = b'<html><title>Sign in to continue</title><p>Sign in to continue</p></html>'
        with patch('local_osint.connectors.fetch_public_response', return_value=self.response(raw, 'html', url='https://gitlab.com/ada-public')):
            frontier.run_queue()
        task = self.workspace.tasks(self.case)[0]
        self.assertEqual(task['state'], 'blocked')
        self.assertEqual(len(task['evidence_ids']), 1)
        self.assertEqual(runner.page_count(), 0)
        self.assertEqual(self.workspace.snapshot(self.case)['counts']['readable_documents'], 0)
        self.assertEqual(runner.report['source_outcomes'][0]['observations'], [])

    def test_explicit_public_username_approval_is_scoped_to_profile_input(self):
        self.case = self.engine.create_case('Ada Public', 'person', identifiers=['ada-public', 'secret-alias'])['id']
        runner = self.runner(packs=['public_profile'], public_usernames=['ada-public'])
        frontier = Frontier(runner)
        frontier.seed()
        self.assertEqual(self.workspace.tasks(self.case)[0]['arguments']['username'], 'ada-public')
        with self.assertRaises(InvalidInput):
            frontier.enqueue('web_search', {'query': '"Ada Public" ada-public'}, 'Not a public search approval')
        with self.assertRaises(InvalidInput):
            frontier.enqueue('public_profile', {'username': 'secret-alias', 'site': 'gitlab'}, 'Unapproved private alias', explicit=True)
        raw = b'<meta property="og:title" content="ada-public GitLab">'
        with patch('local_osint.connectors.fetch_public_response', return_value=self.response(raw, 'html', url='https://gitlab.com/ada-public')) as outbound, \
                patch('local_osint.collector.fetch_public', return_value=(raw, 'html', 'https://gitlab.com/ada-public')):
            frontier.run_queue()
        self.assertEqual(outbound.call_args.args[0], 'https://gitlab.com/ada-public')
        self.assertEqual(self.workspace.tasks(self.case)[0]['state'], 'complete')

    def test_approved_seed_homepage_discovers_target_without_homepage_name_match(self):
        home, target = 'https://community.example.org/', 'https://community.example.org/volunteers'
        originals = {
            home: b'<article>The community repair workshop offers public events, demonstrations and volunteer programmes throughout the year.</article><a href="/volunteers">Volunteer programme</a>',
            target: b'<article>Ada Public organizes community repair demonstrations and public volunteer events. The annual bulletin lists the workshops and public programme.</article>',
        }
        runner = self.runner(seed_urls=[home])
        with patch('local_osint.collector.fetch_public', side_effect=lambda url, **kwargs: (originals[url], 'html', url)), \
                patch('local_osint.connectors.fetch_public_response', side_effect=lambda url, **kwargs: self.response(originals[url], 'html', url=url)):
            report = runner.run()
        self.assertTrue(any(task['connector'] == 'website_sources' and task['arguments']['url'] == home for task in self.workspace.tasks(self.case)))
        self.assertTrue(any(page['url'] == target and page['useful'] for page in report['pages']))
        home_page = next(page for page in report['pages'] if page['url'] == home)
        self.assertFalse(home_page['useful'])
        self.assertEqual(report['quality_count'], 1)

    def test_invalid_model_choices_have_bounded_attempts_before_deterministic_queue(self):
        runner = self.runner(packs=['crossref'])
        runner.ai = object()
        frontier = Frontier(runner)
        for count in range(9):
            frontier.enqueue('crossref', {'query': '"Ada Public"' + ' news' * count}, 'Approved public source search')
        with patch('local_osint.ai.choose_action', side_effect=InvalidInput('Invalid model choice')) as choose, \
                patch('local_osint.connectors.fetch_public_response', return_value=self.response()) as outbound:
            frontier.run_queue()
        self.assertLessEqual(choose.call_count, 2)
        self.assertEqual(outbound.call_count, 9)
        self.assertTrue(all(task['state'] == 'complete' for task in self.workspace.tasks(self.case)))

    def test_page_goal_does_not_starve_other_enabled_source_packs(self):
        first, second = 'https://community.example.org/ada', 'https://community.example.org/about'
        raw = b'<article>Ada Public organizes a public community workshop with repair demonstrations, volunteer activities and a programme of local events.</article>'
        runner = self.runner(packs=['website_sources', 'crossref'], seed_urls=[first, second], page_limit=1)
        def response(url, **kwargs):
            return self.response() if url.startswith('https://api.crossref.org/') else self.response(raw, 'html', url=url)
        with patch('local_osint.collector.fetch_public', return_value=(raw, 'html', first)) as pages, \
                patch('local_osint.connectors.fetch_public_response', side_effect=response):
            runner.run()
        tasks = self.workspace.tasks(self.case)
        self.assertEqual(next(task for task in tasks if task['connector'] == 'crossref')['state'], 'complete')
        self.assertEqual(next(task for task in tasks if task['connector'] == 'website_sources')['state'], 'complete')
        self.assertEqual(next(task for task in tasks if task['connector'] == 'collect_page' and task['arguments']['url'] == second)['state'], 'pending')
        self.assertEqual(pages.call_count, 1)

    def test_new_query_cannot_bypass_persisted_connector_cooldown(self):
        runner = self.runner(packs=['crossref'])
        frontier = Frontier(runner)
        frontier.seed()
        with patch('local_osint.connectors.fetch_public_response', return_value=self.response(b'{"error":"slow down"}', status=429)):
            frontier.run_queue()
        retry_at = self.workspace.health(self.case)[0]['retry_at']
        fresh = self.workspace.enqueue(self.case, 'crossref', {'query': '"Ada Public" research'}, reason='Different public query')
        jobs = self.make_jobs()
        with patch('local_osint.connectors.fetch_public_response') as outbound:
            jobs.start(self.case, options={'packs': ['crossref'], 'tools': []}, action={'tool': 'resume_queue', 'arguments': {}})
            self.finished(jobs)
        outbound.assert_not_called()
        deferred = next(task for task in self.workspace.tasks(self.case) if task['id'] == fresh['id'])
        self.assertEqual(deferred['state'], 'cooldown')
        self.assertEqual(deferred['retry_at'], retry_at)
        self.assertEqual(deferred['attempts'], 0)

    def test_connector_observations_retain_exact_claim_anchors_without_attribution(self):
        runner = self.runner(packs=['crossref'])
        frontier = Frontier(runner)
        frontier.seed()
        raw = json.dumps({'message': {'items': [{'title': ['Ada Public community repair study'], 'URL': 'https://doi.org/10.0000/public', 'author': [{'given': 'Ada', 'family': 'Public', 'affiliation': [{'name': 'Community Research Workshop'}]}]}]}}).encode()
        with patch('local_osint.connectors.fetch_public_response', return_value=self.response(raw)), \
                patch('local_osint.collector.fetch_public', return_value=(b'Public document unavailable.', 'text', 'https://doi.org/10.0000/public')):
            frontier.run_queue()
        snapshot = self.workspace.snapshot(self.case)
        claims = [claim for claim in snapshot['claims'] if claim['predicate'].startswith('publication_')]
        self.assertTrue(claims)
        self.assertEqual(snapshot['counts']['corroborated_claims'], 0)
        self.assertTrue(all(candidate['status'] == 'unconfirmed' for candidate in snapshot['candidates']))
        for claim in claims:
            self.assertEqual(claim['status'], 'unconfirmed')
            self.assertTrue(claim['references'])
            for ref in claim['references']:
                evidence = self.engine.evidence(self.case, ref['evidence_id'])
                self.assertEqual(evidence['text'][ref['start']:ref['end']], ref['quote'])
                self.assertEqual(ref['evidence_sha256'], hashlib.sha256(raw).hexdigest())
                self.assertEqual(ref['extraction_sha256'], hashlib.sha256(evidence['text'].encode()).hexdigest())


if __name__ == '__main__':
    unittest.main()
