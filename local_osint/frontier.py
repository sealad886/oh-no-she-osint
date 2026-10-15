"""Persistent, source-driven actions shared by automatic and interactive tools.

The planner chooses existing action IDs. Only this dispatcher can execute them;
source text and configurable prompts cannot create new permissions.
"""

from datetime import datetime, timedelta, timezone
import json
import re
from urllib.parse import urlsplit, unquote_plus

from .engine import InvalidInput, field, now
from .relevance import outbound_private_clues


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

    def guard(self, connector, arguments, explicit=False):
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
        if connector in {'collect_page', 'website_sources', 'wayback'} and arguments.get('url') in self.runner.options['seed_urls']:
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
            return {**arguments, 'query': self.runner.model_query(arguments['query'])}
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
        if connector != 'email_headers' and connector not in self.runner.options['packs'] and not explicit:
            raise InvalidInput('This source pack is not enabled for this case.')
        if connector in {'crossref', 'searxng'} and not explicit:
            checked['query'] = self.runner.model_query(checked['query'])
        if connector in {'github_public', 'public_profile'} and not explicit:
            if checked.get('username') not in self.runner.options['public_usernames']:
                raise InvalidInput('Approve this public username in source settings before checking other sites.')
        return checked

    def enqueue(self, connector, arguments, reason, explicit=False, source_id=None, candidate_id=None, priority=0):
        checked = self.guard(connector, arguments, explicit)
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
                self.enqueue('collect_page', {'url': url}, 'Read a relevant public search result.', source_id=lead.get('source_id'), priority=10)
            elif 'wayback' in r.options['packs'] and not lead.get('page_source_id'):
                self.enqueue('wayback', {'url': url}, 'Look for an existing public archive after an unsuccessful live-page attempt.', source_id=lead.get('source_id'))

    def _choose(self, tasks):
        if not self.runner.ai or len(tasks) < 2 or self.runner.report.get('action_plan_attempts', 0) >= 4 or self.runner.report.get('action_plan_failures', 0) >= 2:
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

    def run_queue(self, recover=False, task_ids=None):
        r = self.runner
        if recover:
            for task in self.workspace.tasks(self.case_id):
                historical = isinstance(task.get('result'), dict) and task['result'].get('historical_report_id')
                if task['state'] == 'interrupted_after_dispatch' and not historical and (task_ids is None or task['id'] in task_ids):
                    # All supported remote connectors are read-only GET operations.
                    self.workspace.edit_action(self.case_id, task['id'], state='pending')
        count = 0
        while True:
            r.check()
            tasks = [x for x in self.workspace.tasks(self.case_id) if x['state'] == 'pending' or
                     (x['state'] == 'cooldown' and x.get('retry_at') and x['retry_at'] <= now())]
            if task_ids is not None:
                tasks = [task for task in tasks if task['id'] in task_ids]
            cooling = {item['connector']: item for item in self.workspace.health(self.case_id)
                       if item.get('retry_at') and item['retry_at'] > now()}
            for task in tasks:
                if task['connector'] in cooling:
                    health = cooling[task['connector']]
                    self.workspace.defer_action(self.case_id, task['id'], health['retry_at'], health.get('error'))
            tasks = [task for task in tasks if task['connector'] not in cooling]
            if r.page_count() >= r.options['page_limit']:
                tasks = [task for task in tasks if task['connector'] != 'collect_page']
            approvals = self.workspace.settings(self.case_id).get('approved_actions', [])
            legacy = {'web_search': 'web_search', 'search_subject': 'wikidata', 'news_search': 'news', 'domain_signals': 'domain'}
            permitted = set(r.options['packs']) | {'collect_page'} | {tool for tool, source in legacy.items() if source in r.options['tools']}
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
                args = self.guard(task['connector'], task['arguments'], explicit)
            except InvalidInput as exc:
                self.workspace.start_action(self.case_id, task['id'], r.report['run_id'])
                self.workspace.finish_action(self.case_id, task['id'], 'blocked', error=str(exc))
                continue
            r._queue_task = task
            try:
                if task['connector'] == 'collect_page':
                    r.page(args['url'])
                elif task['connector'] in legacy:
                    r.user_tool(task['connector'], args)
                else:
                    self.execute(task, args)
            finally:
                r._queue_task = None
            count += 1
        r.report['workspace_counts'] = self.workspace.snapshot(self.case_id)['counts']
        return {'summary': str(count) + ' source actions processed; saved evidence and remaining actions retained.'}

    def execute(self, task, arguments):
        from .connectors import execute
        r = self.runner
        connector = task['connector']
        outcome = None
        def collect():
            nonlocal outcome
            outcome = execute(r.engine, self.case_id, connector, arguments, enabled=r.enabled,
                              settings={'search_service': r.search_service})
            for source_id in outcome.get('evidence_ids', []):
                r.remember({'id': source_id})
            return {'summary': outcome.get('summary', ''), '_action_status': outcome['status'],
                    '_outcome': outcome, '_evidence_ids': outcome.get('evidence_ids', []),
                    **({'id': outcome['evidence_ids'][0]} if outcome.get('evidence_ids') else {})}
        result = r.step('Source tool: ' + connector, collect, tool=connector, arguments=arguments,
                        cost=0 if connector == 'email_headers' else 1)
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
            if evidence['text'].count(quote) != 1:
                continue
            candidate = self.workspace.candidate(self.case_id,
                field(arguments.get('username') or r.case['name'], 'Candidate', 200),
                context=connector + ': ' + evidence['source'], source_id=source_id)
            self.workspace.observe(self.case_id, source_id, candidate['id'], quote,
                predicate=str(observation.get('kind', 'observation'))[:100],
                value=str(observation.get('value', ''))[:2000])
        for item in outcome.get('leads', [])[:10]:
            url = item.get('url')
            eligible = (r.relevant(item.get('title', ''), item.get('snippet', '')) or connector in {'website_sources', 'wayback'} or
                        (connector in {'github_public', 'public_profile'} and arguments.get('username') in r.options['public_usernames']))
            if not url or not eligible:
                continue
            try:
                r.allowed_urls.add(url)
                self.enqueue('collect_page', {'url': url}, 'Follow a public lead returned by ' + connector + '.',
                    source_id=(outcome.get('evidence_ids') or [None])[0], candidate_id=task.get('candidate_id'), priority=10)
            except InvalidInput:
                continue
        return outcome
