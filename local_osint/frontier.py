"""Persistent, source-driven actions shared by automatic and interactive tools.

The planner chooses existing action IDs. Only this dispatcher can execute them;
source text and configurable prompts cannot create new permissions.
"""

from datetime import datetime, timedelta, timezone
import json
import re
import time
from urllib.parse import urlsplit, unquote_plus

from .engine import InvalidInput, field, now
from .relevance import outbound_private_clues
from .workspace import AUTOMATIC_ATTEMPT_LIMIT


SOURCE_KEYS = {'packs', 'public_usernames', 'seed_urls', 'runtime_seconds', 'keep_working'}


def source_settings(values=None):
    from .connectors import catalogue
    from .collector import public_url
    values = values or {}
    result = {'packs': values.get('packs', []), 'public_usernames': values.get('public_usernames', []),
              'seed_urls': values.get('seed_urls', []), 'runtime_seconds': values.get('runtime_seconds', 300),
              'keep_working': values.get('keep_working', False)}
    available = {item['id'] for item in catalogue()}
    packs = result['packs']
    if not isinstance(packs, list) or any(not isinstance(x, str) or x not in available for x in packs) or len(packs) != len(set(packs)):
        raise InvalidInput('Choose supported source packs without duplicates.')
    for key in ['public_usernames', 'seed_urls']:
        items = result[key]
        if not isinstance(items, list) or len(items) > 100 or any(not isinstance(x, str) or not x.strip() for x in items):
            raise InvalidInput('Provide up to 100 public usernames or public seed URLs.')
        result[key] = list(dict.fromkeys(x.strip() for x in items))
    if any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,38}', x) for x in result['public_usernames']):
        raise InvalidInput('Public usernames must contain 1 to 39 letters, numbers, dashes or underscores.')
    for url in result['seed_urls']:
        public_url(url, allow_query=True)
    if type(result['runtime_seconds']) is not int or not 0 <= result['runtime_seconds'] <= 2**53 - 1:
        raise InvalidInput('Run duration must be a nonnegative whole number; zero means while the page remains open.')
    if type(result['keep_working']) is not bool:
        raise InvalidInput('Keep working must be true or false.')
    return result


def default_settings(case):
    return source_settings({'packs': ['website_sources'], 'public_usernames': [], 'seed_urls': []})


class Frontier:
    def __init__(self, runner):
        self.runner = runner
        self.workspace, self.case_id = runner.workspace, runner.case_id

    def guard(self, connector, arguments, explicit=False, source_id=None):
        """Validate all queued input again immediately before dispatch."""
        from .ai import private_output
        from .connectors import validate_arguments
        from .collector import public_url
        if not isinstance(arguments, dict):
            raise InvalidInput('Source arguments must be an object.')
        encoded = json.dumps(arguments, ensure_ascii=False)
        clues = outbound_private_clues(self.runner.case, self.runner.options)
        if connector in {'public_profile', 'github_public'} and arguments.get('username') in self.runner.options['public_usernames']:
            clues = [clue for clue in clues if clue.casefold() != arguments['username'].casefold()]
        if connector in {'collect_page', 'website_sources', 'wayback', 'github_repository', 'github_issue'} and arguments.get('url') in self.runner.options['seed_urls']:
            clues = [clue for clue in clues if clue.casefold() != arguments['url'].casefold()]
        # Public links can encode private contacts in their path or query. Check
        # the transport representation and bounded decoded forms before dispatch.
        for _ in range(4):
            if private_output(encoded, clues) != encoded:
                raise InvalidInput('Saved private clues cannot be sent to a source tool. Use approved public inputs.')
            decoded = unquote_plus(encoded)
            if decoded == encoded:
                break
            encoded = decoded
        if connector == 'collect_page':
            if set(arguments) != {'url'}:
                raise InvalidInput('A page action needs one public URL.')
            public_url(arguments['url'], allow_query=True)
            if not explicit and arguments['url'] not in self.runner.allowed_urls and arguments['url'] not in self.runner.options['seed_urls']:
                # Persisted discovery must remain tied to a saved source, not a model-invented URL.
                raise InvalidInput('Only approved or discovered public URLs may be followed.')
            return arguments
        if connector == 'web_search':
            if set(arguments) not in ({'query'}, {'query', 'provider'}):
                raise InvalidInput('A search action needs a query and optional provider.')
            if self.runner.options.get('automatic') and source_id:
                from .planner import Controller
                query = Controller(self.runner).provenance_query(arguments['query'], source_id)
            else:
                query = self.runner.model_query(arguments['query'])
            if 'provider' in arguments and arguments['provider'] not in self.runner.options['providers']:
                raise InvalidInput('Choose a configured public search provider.')
            return {**arguments, 'query': query}
        if connector == 'domain_signals':
            from .collector import domain_name, SIGNALS
            if set(arguments) != {'domain', 'signals'} or not isinstance(arguments['signals'], list) or not arguments['signals'] or any(x not in SIGNALS for x in arguments['signals']):
                raise InvalidInput('Select a public domain and supported domain signals.')
            return {'domain': domain_name(arguments['domain']), 'signals': arguments['signals']}
        if connector == 'search_subject':
            if set(arguments) != {'query', 'provider'} or arguments['provider'] not in {'wikipedia', 'wikidata'}:
                raise InvalidInput('Choose a supported knowledge source.')
            return {**arguments, 'query': self.runner.model_query(arguments['query'])}
        if connector == 'news_search':
            if set(arguments) != {'query'}:
                raise InvalidInput('Choose a public news query.')
            return {'query': self.runner.model_query(arguments['query'])}
        checked = validate_arguments(connector, arguments)
        automatic_derived = self.runner.options.get('automatic') and source_id and connector in {'webfinger', 'public_actor'}
        if automatic_derived:
            evidence = self.runner.engine.evidence(self.case_id, source_id)
            literal = checked.get('account') or checked.get('url')
            if evidence['source'].startswith('local-osint:') or not literal or literal not in evidence['text']:
                raise InvalidInput('Federated account follow-ups require a literal public source reference.')
        if connector != 'email_headers' and connector not in self.runner.options['packs'] and not explicit and not automatic_derived:
            raise InvalidInput('This source pack is not enabled for this case.')
        if connector in {'crossref', 'searxng'} and not explicit:
            checked['query'] = self.runner.model_query(checked['query'])
        if connector in {'github_public', 'public_profile'} and not explicit:
            if checked.get('username') not in self.runner.options['public_usernames']:
                raise InvalidInput('Approve this public username in source settings before checking other sites.')
        if connector in {'github_repository', 'github_issue'} and not explicit:
            from .connectors import github_url
            seeds = set()
            for seed in self.runner.options['seed_urls']:
                try:
                    seeds.add(github_url(connector, seed)[0])
                except InvalidInput:
                    pass
            if checked['url'] not in seeds and not self._github_source(source_id, checked['url']):
                raise InvalidInput('GitHub collection requires an exact public starting URL or a recorded public source reference.')
        return checked

    def _github_source(self, source_id, url):
        """Local imports and generated reports cannot grant new collection scope."""
        if not source_id:
            return False
        from .collector import public_url
        from .connectors import REGISTRY
        evidence = self.runner.engine.evidence(self.case_id, source_id)
        try:
            public_url(evidence['source'], allow_query=True)
        except InvalidInput:
            return False
        remote = {'collect_page', 'web_search', 'news_search', 'search_subject', 'domain_signals'} | {
            key for key, item in REGISTRY.items() if item['max_requests'] > 0}
        receipts = [task for task in self.workspace.tasks(self.case_id) if source_id in task['evidence_ids'] and
                    task['connector'] in remote and not (task.get('result') or {}).get('historical_report_id')]
        recorded = bool(receipts)
        recorded = recorded or any(page.get('source_id') == source_id for page in self.runner.report.get('pages', []))
        text = (evidence.get('extraction') or {}).get('text', evidence['text'])
        saved_lead = any(lead.get('url') == url for task in receipts for lead in (task.get('result') or {}).get('leads', []) if isinstance(lead, dict))
        return recorded and (url in text or url in self.runner.allowed_urls or saved_lead)

    def _github_follow(self, url, source_id=None, *, priority=10):
        from .connectors import github_connector, github_url
        connector = github_connector(url)
        if connector not in self.runner.options['packs']:
            return False
        canonical = github_url(connector, url)[0]
        # A new discovery origin does not authorize automatic refetching of the
        # same resource. Explicit Retry still creates another attempt normally.
        if any(task['connector'] == connector and task['arguments'].get('url') == canonical
               for task in self.workspace.tasks(self.case_id)):
            return True
        try:
            self.enqueue(connector, {'url': canonical}, 'Read an exact approved or source-backed public GitHub resource.',
                         source_id=source_id, priority=priority)
        except InvalidInput:
            return False
        return True

    def enqueue(self, connector, arguments, reason, explicit=False, source_id=None, candidate_id=None, priority=0):
        if connector == 'collect_page' and not explicit:
            from .connectors import github_connector
            github = github_connector(arguments.get('url')) if isinstance(arguments, dict) else None
            if github in self.runner.options['packs']:
                try:
                    checked = self.guard(github, arguments, source_id=source_id)
                except InvalidInput:
                    pass
                else:
                    existing = next((task for task in self.workspace.tasks(self.case_id)
                                     if task['connector'] == github and task['arguments'] == checked), None)
                    if existing:
                        return existing
                    connector = github
        checked = self.guard(connector, arguments, explicit, source_id)
        task = self.workspace.enqueue(self.case_id, connector, checked, reason=reason,
            source_id=source_id, candidate_id=candidate_id, priority=priority)
        if explicit:
            # This is a user-selected source action. Its permission is persisted for resumption.
            settings = self.workspace.settings(self.case_id)
            approvals = settings.get('approved_actions', [])
            if task['id'] not in approvals:
                self.workspace.configure(self.case_id, {'approved_actions': [*approvals, task['id']]})
        return task

    def seed(self):
        r = self.runner
        for url in r.options['seed_urls']:
            r.allowed_urls.add(url)
            if not self._github_follow(url, priority=20):
                self.enqueue('collect_page', {'url': url}, 'Read the public starting URL supplied for this case.', priority=20)
        for connector in r.options['packs']:
            if connector in {'crossref', 'searxng'}:
                query = r.options['query'] + (' ' + r.options['context'] if r.options['context'] else '')
                self.enqueue(connector, {'query': query}, 'Discover structured public sources for this subject.', priority=5)
            elif connector == 'public_place' and r.case['focus_type'] in {'organization', 'topic', 'other'}:
                self.enqueue(connector, {'query': r.options['context'] or r.case['name']}, 'Find public place context, without private-address lookup.')
            elif connector in {'github_public', 'public_profile'}:
                for username in r.options['public_usernames']:
                    args = {'username': username}
                    if connector == 'public_profile':
                        args['site'] = 'gitlab'
                    self.enqueue(connector, args, 'Check a public username explicitly approved in case settings.')

    def record_page(self, page, evidence):
        """Readability is separate from attribution; no observation confirms identity."""
        r = self.runner
        with r.engine.connection() as db:
            origin = db.execute('SELECT kind FROM workspace_origins WHERE evidence_id=?', (evidence['id'],)).fetchone()
            duplicate = db.execute("SELECT id FROM evidence WHERE case_id=? AND sha256=? AND id<>? AND source NOT LIKE 'local-osint:%' ORDER BY collected_at,id LIMIT 1",
                (self.case_id, evidence['sha256'], evidence['id'])).fetchone()
        if duplicate and not origin:
            self.workspace.origin(self.case_id, evidence['id'], 'copy', duplicate['id'],
                                  'Binary original is identical to an already saved source; it is not independent corroboration.')
        if page.get('url') in r.options['seed_urls'] and page.get('readable') and 'website_sources' in r.options['packs']:
            self.enqueue('website_sources', {'url': evidence['source']},
                'Discover sources on an explicitly approved starting website, even when its homepage lacks the target name.', source_id=evidence['id'])
        if not page.get('content_relevant'):
            return
        candidate = self.workspace.candidate(self.case_id, r.case['name'],
            context='Public source candidate: ' + evidence['source'], source_id=evidence['id'])
        text = evidence['text']
        names = [r.case['name'], *r.case.get('aliases', [])]
        positions = [text.casefold().find(name.casefold()) for name in names if name]
        position = min((x for x in positions if x >= 0), default=0)
        start = max(0, position - 80)
        quote = text[start:start + 700].strip()
        start = text.find(quote, start)
        if quote:
            previous = self.workspace.snapshot(self.case_id)['claims']
            if not any(x.get('predicate') == 'readable_document' and any(ref['evidence_id'] == evidence['id'] for ref in x.get('references', [])) for x in previous):
                self.workspace.observe(self.case_id, evidence['id'], candidate['id'], quote,
                    predicate='readable_document', value='Readable candidate source; attribution remains unconfirmed.', start=start)
        if r.ai and r.options['packs'] and page.get('useful'):
            def extract():
                from .ai import extract_observations
                observations = extract_observations(r.ai, r.case, r.options, text)
                for observation in observations:
                    self.workspace.observe(self.case_id, evidence['id'], candidate['id'], **observation)
                return {'summary': str(len(observations)) + ' exact-passage observations saved locally; attribution remains unconfirmed.'}
            r.step('Local AI: extract source observations with exact passages', extract, 0, 'analyze', {})
        if page.get('context_excluded') or not page.get('useful'):
            return
        for link in page.get('links', []):
            try:
                self._github_follow(link['url'], evidence['id'])
            except InvalidInput:
                continue
        if 'website_sources' in r.options['packs']:
            self.enqueue('website_sources', {'url': evidence['source']},
                'Discover first-party feeds, documents and relevant links from this public source.', source_id=evidence['id'])

    def discover_report(self):
        r = self.runner
        if not r.options['packs']:
            return
        attempted = {step.get('arguments', {}).get('url') for step in r.report['steps'] if step.get('tool') == 'collect_page'}
        for lead in r.report.get('web_results', []):
            if not lead.get('relevant') or lead.get('local_match', {}).get('model_assessment') == 'context_unlikely':
                continue
            url = lead['url']
            if url not in attempted:
                r.allowed_urls.add(url)
                if not self._github_follow(url, lead.get('source_id')):
                    self.enqueue('collect_page', {'url': url}, 'Read a relevant public search result.', source_id=lead.get('source_id'), priority=10)
            elif 'wayback' in r.options['packs'] and not lead.get('page_source_id'):
                self.enqueue('wayback', {'url': url}, 'Look for an existing public archive after an unsuccessful live-page attempt.', source_id=lead.get('source_id'))

    def _choose(self, tasks):
        if self.runner.options.get('automatic') or not self.runner.ai or len(tasks) < 2 or self.runner.report.get('action_plan_attempts', 0) >= 4 or self.runner.report.get('action_plan_failures', 0) >= 2:
            return tasks[0]
        # One bounded choice over existing IDs; repeated or invalid model output uses queue order.
        from .ai import choose_action
        self.runner.report['action_plan_attempts'] = self.runner.report.get('action_plan_attempts', 0) + 1
        try:
            choice = choose_action(self.runner.ai, self.runner.case, self.runner.options, tasks[:12])
            selected = next((task for task in tasks if task['id'] == choice['id']), None)
            if selected:
                self.runner.report.setdefault('action_plan', []).append(choice)
                return selected
        except InvalidInput:
            pass
        self.runner.report['action_plan_failures'] = self.runner.report.get('action_plan_failures', 0) + 1
        self.runner.report.setdefault('action_plan', []).append({'origin': 'engine', 'id': tasks[0]['id'],
            'reason': 'Local AI did not select fresh permitted work; use the next queued source action.'})
        return tasks[0]

    def run_queue(self, recover=False, task_ids=None, max_actions=None):
        r = self.runner
        if recover:
            if not getattr(r, '_recovered_transient_actions', False):
                self.workspace.recover_transient_actions(self.case_id)
                r._recovered_transient_actions = True
            for task in self.workspace.tasks(self.case_id):
                historical = isinstance(task.get('result'), dict) and task['result'].get('historical_report_id')
                if task['state'] == 'interrupted_after_dispatch' and not historical and (task_ids is None or task['id'] in task_ids):
                    # All supported remote connectors are read-only GET operations.
                    self.workspace.edit_action(self.case_id, task['id'], state='pending')
        count = 0
        while True:
            r.check()
            tasks = [x for x in self.workspace.tasks(self.case_id) if x['state'] == 'pending' or
                     (x['state'] == 'cooldown' and x.get('retry_at') and x['retry_at'] <= now() and
                      (x['attempts'] < AUTOMATIC_ATTEMPT_LIMIT or task_ids and x['id'] in task_ids))]
            if task_ids is not None:
                tasks = [task for task in tasks if task['id'] in task_ids]
            cooling = {item['connector']: item for item in self.workspace.health(self.case_id)
                       if item.get('retry_at') and item['retry_at'] > now()}
            for task in tasks:
                health_key = 'web_search:' + task['arguments'].get('provider', r.active_provider) if task['connector'] == 'web_search' else task['connector']
                if health_key in cooling:
                    health = cooling[health_key]
                    self.workspace.defer_action(self.case_id, task['id'], health['retry_at'], health.get('error'))
            tasks = [task for task in tasks if ('web_search:' + task['arguments'].get('provider', r.active_provider) if task['connector'] == 'web_search' else task['connector']) not in cooling]
            approvals = self.workspace.settings(self.case_id).get('approved_actions', [])
            if r.page_count() >= r.options['page_limit']:
                tasks = [task for task in tasks if task['connector'] != 'collect_page' or task['id'] in approvals]
            legacy = {'web_search': 'web_search', 'search_subject': 'wikidata', 'news_search': 'news', 'domain_signals': 'domain'}
            permitted = set(r.options['packs']) | {'collect_page'} | {tool for tool, source in legacy.items() if source in r.options['tools']}
            if r.options.get('automatic'):
                permitted.update({'webfinger', 'public_actor'})
            tasks = [x for x in tasks if (x['connector'] in permitted or x['id'] in approvals) and not x['connector'].startswith('historical:')]
            if not tasks:
                break
            task = self._choose(tasks)
            explicit = task['id'] in approvals
            if task['connector'] == 'collect_page' and task.get('source_id'):
                # Evidence-scoped persisted discovery was validated before being queued.
                r.engine.evidence(self.case_id, task['source_id'])
                r.allowed_urls.add(task['arguments']['url'])
            try:
                args = self.guard(task['connector'], task['arguments'], explicit, task.get('source_id'))
            except InvalidInput as exc:
                self.workspace.start_action(self.case_id, task['id'], r.report['run_id'])
                self.workspace.finish_action(self.case_id, task['id'], 'blocked', error=str(exc))
                continue
            r._queue_task = task
            try:
                if task['connector'] == 'collect_page':
                    r.page(args['url'], explicit=explicit)
                elif task['connector'] == 'web_search':
                    r.web_search(args['query'], args.get('provider'))
                elif task['connector'] == 'search_subject':
                    r.search_subject(args['query'], args['provider'])
                elif task['connector'] == 'news_search':
                    r.news_search(args['query'])
                elif task['connector'] == 'domain_signals':
                    r.domain(args['domain'], args['signals'])
                else:
                    self.execute(task, args)
            finally:
                r._queue_task = None
            count += 1
            if max_actions is not None and count >= max_actions:
                break
        r.report['workspace_counts'] = self.workspace.snapshot(self.case_id)['counts']
        return {'summary': str(count) + ' source actions processed; saved evidence and remaining actions retained.'}

    def execute(self, task, arguments):
        from .connectors import REGISTRY, execute
        r = self.runner
        connector = task['connector']
        outcome = None
        def collect():
            nonlocal outcome
            outcome = execute(r.engine, self.case_id, connector, arguments, enabled=r.enabled,
                              settings={'search_service': r.search_service,
                                        'can_request': lambda: not r.cancel.is_set() and time.monotonic() < r.deadline})
            for source_id in outcome.get('evidence_ids', []):
                r.remember({'id': source_id})
            return {'summary': outcome.get('summary', ''), '_action_status': outcome['status'],
                    '_outcome': outcome, '_evidence_ids': outcome.get('evidence_ids', []),
                    **({'id': outcome['evidence_ids'][0]} if outcome.get('evidence_ids') else {})}
        result = r.step('Source tool: ' + connector, collect, tool=connector, arguments=arguments,
                        cost=REGISTRY[connector]['max_requests'])
        if outcome and outcome['status'] in {'rate_limited', 'blocked', 'uninterpretable', 'error'}:
            saved = next(x for x in self.workspace.tasks(self.case_id) if x['id'] == task['id'])
            self.workspace.update_health(self.case_id, connector, outcome['status'],
                retry_at=saved.get('retry_at'), error=outcome.get('summary'))
        elif outcome:
            self.workspace.update_health(self.case_id, connector, 'ready')
        if not outcome:
            return result
        r.report.setdefault('source_outcomes', []).append({'task_id': task['id'], 'connector': connector, **outcome})
        for observation in outcome.get('observations', [])[:40]:
            source_id, quote = observation.get('evidence_id'), observation.get('quote')
            if not source_id or not isinstance(quote, str) or not quote:
                continue
            evidence = r.engine.evidence(self.case_id, source_id)
            text = (evidence.get('extraction') or {}).get('text', evidence['text'])
            start = observation.get('start')
            if start is not None and (type(start) is not int or start < 0 or text[start:start + len(quote)] != quote):
                continue
            if start is None and text.count(quote) != 1:
                continue
            candidate = self.workspace.candidate(self.case_id,
                field(arguments.get('username') or r.case['name'], 'Candidate', 200),
                context=connector + ': ' + evidence['source'], source_id=source_id)
            self.workspace.observe(self.case_id, source_id, candidate['id'], quote,
                predicate=str(observation.get('kind', 'observation'))[:100],
                value=str(observation.get('value', ''))[:2000], start=start,
                **({'extractor_version': observation['extractor_version']} if observation.get('extractor_version') else {}))
        from .planner import rank_links
        for item in rank_links(outcome.get('leads', []), r.case, r.options)[:10]:
            url = item.get('url')
            eligible = (r.relevant(item.get('title', ''), item.get('snippet', '')) or connector in {'website_sources', 'wayback', 'commoncrawl', 'webfinger'} or
                        (connector in {'github_public', 'public_profile'} and arguments.get('username') in r.options['public_usernames']))
            if not url or not eligible:
                continue
            try:
                r.allowed_urls.add(url)
                if self._github_follow(url, (outcome.get('evidence_ids') or [None])[0]):
                    continue
                follow = 'public_actor' if connector == 'webfinger' and item.get('connector') == 'public_actor' else 'collect_page'
                self.enqueue(follow, {'url': url}, 'Follow a public lead returned by ' + connector + '.',
                    source_id=(outcome.get('evidence_ids') or [None])[0], candidate_id=task.get('candidate_id'), priority=10)
            except InvalidInput:
                continue
        return outcome
