"""Bounded subject-to-dossier collection; AI cannot widen the network scope."""

import copy
import http.client
import json
import re
import threading
import time
import uuid
from html.parser import HTMLParser
from urllib.parse import urlencode, urlsplit, urljoin, urldefrag

from . import collector
from .engine import InvalidInput, now, field
from .page_quality import assess_page
from .relevance import local_match, rank_key, words, outbound_private_clues
from .prompts import validate_prompts
from .workspace import (Workspace, AUTOMATIC_ATTEMPT_LIMIT, TRANSIENT_RETRY_SECONDS, RATE_LIMIT_RETRY_SECONDS,
                        RECOVERABLE_SEARCH_CONNECTORS, transient_source_failure)

REPORT_SOURCE = 'local-osint:investigation-report'
PROPERTIES = {'P31': 'Type', 'P856': 'Official website (candidate claim)', 'P17': 'Country',
              'P159': 'Headquarters', 'P112': 'Founder', 'P127': 'Owner', 'P355': 'Subsidiary',
              'P361': 'Part of', 'P106': 'Occupation', 'P108': 'Employer', 'P69': 'Education',
              'P800': 'Notable work', 'P569': 'Date of birth', 'P570': 'Date of death'}
QUALIFIERS = {'P580': 'Start time', 'P582': 'End time', 'P585': 'Point in time',
              'P518': 'Applies to part', 'P407': 'Language', 'P459': 'Method',
              'P1480': 'Sourcing circumstances', 'P7452': 'Reason for preferred rank'}
SOURCES = {'web_search', 'wikipedia', 'wikidata', 'news', 'domain'}
MAX_SAFE_INTEGER = 2 ** 53 - 1  # Preserve exact values in the browser's JSON numbers.


def options_for(case, values=None):
    values = {} if values is None else values
    if not isinstance(values, dict) or set(values) - {'query', 'context', 'tools', 'max_requests', 'page_limit', 'providers', 'prompts', 'packs', 'public_usernames', 'seed_urls', 'runtime_seconds', 'keep_working', 'automatic', 'question', 'public_country', 'public_language', 'public_organization'}:
        raise InvalidInput('Invalid investigation settings.')
    result = {'query': field(values.get('query', ''), 'Public search query', 200, False) or '"' + case['name'] + '"',
              'context': field(values.get('context', ''), 'Public search context', 200, False),
              'tools': values.get('tools', ['web_search', 'wikipedia', 'wikidata', 'news', 'domain']),
              'max_requests': values.get('max_requests', 24), 'page_limit': values.get('page_limit', 4),
              'providers': values.get('providers', list(collector.SEARCH_PROVIDERS)),
              'prompts': validate_prompts(values.get('prompts'))}
    from .frontier import source_settings
    result.update(source_settings(values))
    result['automatic'] = values.get('automatic', False)
    if type(result['automatic']) is not bool:
        raise InvalidInput('Automatic investigation must be true or false.')
    for key in ['question', 'public_country', 'public_language', 'public_organization']:
        result[key] = field(values.get(key, ''), key.replace('_', ' '), 1000 if key == 'question' else 200 if key == 'public_organization' else 100, False)
    if result['automatic']:
        from .planner import recipe
        result.update(recipe(case, result))
    if not isinstance(result['providers'], list) or not result['providers'] or any(not isinstance(p, str) or p not in collector.SEARCH_PROVIDERS for p in result['providers']) or len(set(result['providers'])) != len(result['providers']):
        raise InvalidInput('Select supported search providers without duplicates.')
    if not isinstance(result['tools'], list) or (not result['tools'] and not result['packs']) or any(not isinstance(t, str) or t not in SOURCES for t in result['tools']) or len(set(result['tools'])) != len(result['tools']):
        raise InvalidInput('Select at least one supported source, without duplicates.')
    for name in ['max_requests', 'page_limit']:
        if type(result[name]) is not int or not 1 <= result[name] <= MAX_SAFE_INTEGER:
            raise InvalidInput(f'{name} must be a positive whole number within the browser\'s exact integer range.')
    field(result['query'] + (' ' + result['context'] if result['context'] else ''), 'Combined public query and context', 200)
    return result


class PageLinks(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.links, self.href, self.text = base, [], None, []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.href, self.text = dict(attrs).get('href'), []

    def handle_data(self, text):
        if self.href:
            self.text.append(text)

    def handle_endtag(self, tag):
        if tag == 'a' and self.href:
            url = urldefrag(urljoin(self.base, self.href))[0]
            if url.startswith('https://') and len(url) <= 2000 and urlsplit(url).hostname == urlsplit(self.base).hostname:
                title = ' '.join(' '.join(self.text).split())[:200]
                if title and not any(x['url'] == url for x in self.links):
                    self.links.append({'url': url, 'title': title})
            self.href = None


def statement_value(value):
    if isinstance(value, dict):
        if 'time' in value:
            timestamp = str(value['time'])[:100]
            precision = value.get('precision')
            names = {9: 'year', 10: 'month', 11: 'day', 12: 'hour', 13: 'minute', 14: 'second'}
            parts = re.fullmatch(r'([+-][0-9]+)-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})Z', timestamp)
            display = timestamp
            if parts and type(precision) is int:
                year, month, day, hour, minute, second = parts.groups()
                display = year
                if precision >= 10:
                    display += '-' + month
                if precision >= 11:
                    display += '-' + day
                if precision >= 12:
                    display += ' ' + hour
                if precision >= 13:
                    display += ':' + minute
                if precision >= 14:
                    display += ':' + second + ' UTC'
            context = [(names[precision] + ' precision') if type(precision) is int and precision in names else 'precision ' + str(precision)[:30]]
            calendar = value.get('calendarmodel')
            if calendar:
                calendar_id = str(calendar).rsplit('/', 1)[-1]
                context.append({'Q1985727': 'Gregorian calendar', 'Q1985786': 'Julian calendar'}.get(calendar_id, 'Calendar: ' + str(calendar)[:200]))
            for key in ['before', 'after']:
                if value.get(key):
                    context.append(key + ': ' + str(value[key])[:30] + ' precision units')
            return display + ' (' + '; '.join(context) + ')'
        value = value.get('id') or value.get('time') or value.get('text') or json.dumps(value, ensure_ascii=True)
    return str(value)[:1000] if value is not None else None


class Halt(Exception):
    pass


class Investigation:
    def __init__(self, engine, case_id, enabled=False, progress=None, cancel=None, ai=None, run_id=None, deadline_seconds=300, options=None, previous=None, local_only=False, continuing=False, search_service=None, diagnostics=None):
        if not enabled and not local_only:
            raise InvalidInput('Investigation collection is off. Restart with --allow-network.')
        with engine.connection() as db:
            self.case = engine.require_case(db, case_id)
        self.engine, self.case_id, self.progress, self.ai, self.enabled = engine, case_id, progress, ai, enabled
        self.cancel = cancel or threading.Event()
        self.options = options_for(self.case, options)
        duration = self.options['runtime_seconds'] if deadline_seconds == 300 else deadline_seconds
        unbounded = deadline_seconds == 300 and (self.options['keep_working'] or self.options['runtime_seconds'] == 0)
        self.deadline = float('inf') if unbounded else time.monotonic() + duration
        self.used = 0
        self.allowed_urls, self.allowed_domains = set(), set()
        self.pages, self.domains = {}, {}
        self.failed_providers = set()
        self.workspace = Workspace(engine)
        from .diagnostics import Diagnostics
        self.diagnostics = diagnostics if diagnostics is not None else Diagnostics(engine.directory)
        self.search_service = search_service
        self._queue_task = None
        self.active_provider = self.options['providers'][0]
        self.continuing = continuing
        self.report = {'report_type': 'investigation', 'run_id': run_id or uuid.uuid4().hex,
                       'case_id': case_id, 'subject': self.case['name'], 'aliases': self.case['aliases'], 'focus_type': self.case['focus_type'],
                       'started_at': now(), 'status': 'running', 'stage': 'Starting', 'steps': [],
                       'profiles': [], 'candidates': [], 'articles': [], 'source_ids': [],
                       'interpretation': 'Name matches are unconfirmed candidates. Claims below belong to each candidate, not automatically to your subject.'}
        self.report.update(options=self.options, web_results=[], pages=[], stop_reason=None,
                           network_enabled=enabled, quality_count=0,
                           budget={'used': 0, 'limit': self.options['max_requests']})
        if previous:
            self.report['parent_run_id'] = previous['run_id']
            for key in ['profiles', 'candidates', 'articles', 'source_ids', 'steps', 'web_results', 'pages', 'domain_lead', 'ai']:
                if key in previous:
                    self.report[key] = copy.deepcopy(previous[key])
            self.allowed_urls.update(x['url'] for x in self.report['web_results'] if x.get('relevant'))
            self.allowed_urls.update(x['collection_url'] for x in self.report['candidates'] if x.get('collection_url') and x.get('relevant'))
            self.allowed_urls.update(x['url'] for x in self.report['articles'] if self.relevant(x['title']))
            for page in self.report['pages']:
                self.allowed_urls.update(x['url'] for x in page.get('links', []))
                self.allowed_urls.add(page['url'])
            if self.report.get('domain_lead'):
                self.allowed_domains.add(self.report['domain_lead']['domain'])
        self.start_step = len(self.report['steps'])
        self.origin = 'engine'
        if continuing or previous and self.options['automatic']:
            if not previous:
                raise InvalidInput('Run an investigation before continuing it.')
            self.report.pop('ai', None)
            self.report['continuation'] = {'parent_run_id': previous['run_id'], 'decisions': [],
                'excluded_hosts': [], 'stop_reason': None}
            # Never refetch an attempted URL automatically. Individual edited
            # retries remain available when the user wants another attempt.
            for page in self.report['pages']:
                self.pages[page['url']] = {'id': page['source_id'], 'useful': page.get('useful'),
                    'summary': 'Previously collected evidence retained.'}
            for step in self.report['steps']:
                if step.get('tool') == 'collect_page' and step.get('arguments', {}).get('url'):
                    self.pages.setdefault(step['arguments']['url'], {'error': 'Previously attempted URL; choose another public source.'})
            blocked = []
            for step in self.report['steps']:
                if step.get('tool') == 'collect_page' and step.get('status') == 'failed':
                    if any(code in step.get('error', '') for code in ['403', '429', '999']):
                        blocked.append(step.get('arguments', {}).get('url', ''))
            blocked.extend(p['url'] for p in self.report['pages'] if not p.get('useful') and
                           any(word in p.get('quality_reason', '').lower() for word in ['login', 'challenge', 'sign in', 'access barrier']))
            self.report['continuation']['excluded_hosts'] = sorted({urlsplit(url).hostname for url in blocked if urlsplit(url).hostname})

    def check(self, cost=0):
        if self.cancel.is_set():
            self.report['status'] = 'cancelled'
            self.report['stop_reason'] = 'Stopped by you. Sources already collected are retained.'
            raise Halt()
        if time.monotonic() >= self.deadline or self.used + cost > self.options['max_requests']:
            self.report['status'] = 'limited'
            self.report['stop_reason'] = 'Time limit reached; Continue investigation keeps saved evidence.' if time.monotonic() >= self.deadline else 'Request ceiling reached; Continue investigation gives a fresh budget and keeps saved evidence.'
            raise Halt()
        self.used += cost

    def publish(self, stage=None, persist=False):
        if stage:
            self.report['stage'] = stage
        self.report['request_units'] = self.used
        self.report['budget']['used'] = self.used
        self.report['quality_count'] = self.page_count()
        if persist:
            save_report(self.engine, self.report)
        if self.progress:
            self.progress(copy.deepcopy(self.report))

    def remember(self, result):
        if result.get('id') and result['id'] not in self.report['source_ids']:
            self.report['source_ids'].append(result['id'])
        return result

    def step(self, label, callback, cost=1, tool=None, arguments=None):
        self.check(cost)
        task = None
        queued = self._queue_task if self._queue_task and self._queue_task['connector'] == tool else None
        if (cost and tool) or queued:
            task = queued or self.workspace.enqueue(self.case_id, tool, arguments or {}, reason=label)
            if task['state'] not in {'pending', 'queued', 'running'}:
                task = self.workspace.edit_action(self.case_id, task['id'], state='pending')
            if task['state'] != 'running':
                task = self.workspace.start_action(self.case_id, task['id'], self.report['run_id'])
        entry = {'id': uuid.uuid4().hex, 'run_id': self.report['run_id'], 'label': label, 'ok': False,
                 'tool': tool, 'arguments': arguments or {}, 'status': 'running', 'origin': self.origin, 'started_at': now()}
        self.report['steps'].append(entry)
        if task:
            entry['task_id'] = task['id']
        started = time.monotonic()
        self.report['active_tool'] = entry['id']
        self.publish(label)
        result = None
        retryable = False
        try:
            result = callback()
            self.remember(result)
            entry.update(ok=True, status='success', result_summary=str(result.get('summary', 'Evidence saved.' if result.get('id') else 'Tool finished.'))[:300])
            if result.get('status') in {'failed', 'truncated', 'limited'}:
                entry.update(ok=False, status='failed', error=result.get('warning') or 'Local AI did not complete its draft. Retry analysis.')
            if result.get('id'):
                entry['source_id'] = result['id']
            if result.get('_action_status') in {'blocked', 'rate_limited', 'uninterpretable', 'error'}:
                entry.update(ok=False, status='failed', error=result.get('summary', 'Source tool unavailable.'))
                outcome = result.get('_outcome') or {}
                retryable = result['_action_status'] == 'rate_limited' or (
                    result['_action_status'] == 'error' and transient_source_failure(entry['error'], outcome.get('http_status')))
            return result
        except (InvalidInput, OSError, http.client.HTTPException, ValueError, KeyError, TypeError, RecursionError) as exc:
            entry['error'] = str(exc) if isinstance(exc, InvalidInput) else 'Source unavailable or response could not be interpreted.'
            entry['status'] = 'failed'
            retryable = isinstance(exc, (OSError, http.client.HTTPException)) or transient_source_failure(entry['error'])
            if getattr(exc, 'evidence_id', None):
                self.remember({'id': exc.evidence_id})
                entry['source_id'] = exc.evidence_id
            return None
        finally:
            if entry['status'] == 'running':
                entry['status'] = 'cancelled'
            entry.update(finished_at=now(), duration_ms=round((time.monotonic() - started) * 1000))
            if task:
                action_status = (result or {}).get('_action_status')
                retry = None
                if retryable and (tool in RECOVERABLE_SEARCH_CONNECTORS or action_status == 'rate_limited') and task['attempts'] < AUTOMATIC_ATTEMPT_LIMIT:
                    from datetime import datetime, timedelta, timezone
                    retry = (datetime.now(timezone.utc) + timedelta(seconds=RATE_LIMIT_RETRY_SECONDS if action_status == 'rate_limited' or 'HTTP 429' in entry.get('error', '') else TRANSIENT_RETRY_SECONDS)).isoformat()
                finished_state = ('cooldown' if retry else 'blocked' if action_status in {'blocked', 'uninterpretable'} else
                    'complete' if entry.get('ok') else 'interrupted_after_dispatch' if entry['status'] == 'cancelled' else 'failed')
                self.workspace.finish_action(self.case_id, task['id'],
                    finished_state,
                    result={**((result or {}).get('_outcome') or {'summary': entry.get('result_summary', ''), 'duration_ms': entry['duration_ms']}), 'retryable': retryable},
                    evidence_ids=(result or {}).get('_evidence_ids') or ([entry['source_id']] if entry.get('source_id') else []),
                    error=entry.get('error'), retry_at=retry)
            self.report['active_tool'] = None
            self.publish()

    def skip(self, label, reason, tool=None, arguments=None):
        self.report['steps'].append({'id': uuid.uuid4().hex, 'run_id': self.report['run_id'], 'label': label,
            'tool': tool, 'arguments': arguments or {}, 'origin': self.origin, 'status': 'skipped', 'ok': False, 'result_summary': reason})
        self.publish()

    def relevant(self, title, snippet=''):
        text = ' '.join(words(title + ' ' + snippet))
        return any(' ' + ' '.join(words(name)) + ' ' in ' ' + text + ' ' for name in [self.case['name'], *self.case['aliases']] if words(name))

    def model_query(self, query):
        from .ai import private_output
        query = field(query, 'Web search query', 200)
        allowed = set(words(self.case['name'] + ' ' + ' '.join(self.case['aliases']) + ' ' + self.options['query'] + ' ' + self.options['context']))
        from .recovery import SEARCH_WORDS
        allowed.update(SEARCH_WORDS)
        allowed.update('linkedin github contact biography'.split())
        if self.options.get('automatic'):
            from .planner import RECIPE_WORDS
            allowed.update(RECIPE_WORDS | {'filetype', 'pdf'})
            for key in ['public_country', 'public_language', 'public_organization']:
                allowed.update(words(self.options.get(key, '')))
        for domain in self.allowed_domains:
            allowed.update(words(domain))
        for url in self.allowed_urls:
            allowed.update(words(urlsplit(url).hostname or ''))
        for host in self.report.get('continuation', {}).get('excluded_hosts', []):
            allowed.update(words(host))
        # Follow public employer/location/topic words already visible in search
        # results. Saved private context never supplies this vocabulary.
        for lead in self.report['web_results']:
            allowed.update(token for token in words(lead['title'] + ' ' + lead.get('snippet', ''))
                           if len(token) >= 2 and not any(c.isdigit() for c in token))
        allowed.add('site')
        private_clues = outbound_private_clues(self.case, self.options)
        normalized_query = ' ' + ' '.join(words(query)) + ' '
        private_phrase = any(' ' + ' '.join(words(clue)) + ' ' in normalized_query
                             for clue in private_clues if words(clue))
        if private_phrase or private_output(query, private_clues) != query or not self.relevant(query) or set(words(query)) - allowed or any(c in query for c in '@\\\n\r'):
            raise InvalidInput('AI searches must use the subject name and approved public context or source domains.')
        return query

    def web_search(self, query, provider=None):
        if not self.enabled:
            raise InvalidInput('Web collection is off for this app session.')
        provider = provider or self.active_provider
        if provider not in self.options['providers']:
            raise InvalidInput('Search provider is not selected.')
        self.active_provider = provider
        collect = (lambda: collector.web_search(self.engine, self.case_id, query, True)) if provider == 'duckduckgo' else (lambda: collector.web_search(self.engine, self.case_id, query, True, provider=provider))
        result = self.step('Web search (' + collector.SEARCH_PROVIDERS[provider] + '): ' + query, collect,
                           tool='web_search', arguments={'query': query, 'provider': provider})
        if result:
            self.workspace.update_health(self.case_id, 'web_search:' + provider, 'ready')
            current_results = []
            for item in result['results']:
                match = self.relevant(item['title'], item['snippet'])
                entry = {**item, 'source_id': result['id'], 'relevant': match,
                         'local_match': local_match(self.case, item['title'] + ' ' + item['snippet'], item['url']),
                         'reason': 'Name or alias appears in title/snippet; identity unconfirmed.' if match else 'No complete name/alias match; not followed automatically.'}
                current_results.append(entry)
                existing = next((x for x in self.report['web_results'] if x['url'] == item['url']), None)
                if existing:
                    hint = existing.get('local_match', {}).get('model_assessment')
                    if hint:
                        entry['local_match']['model_assessment'] = hint
                    existing.update(entry)
                else:
                    self.report['web_results'].append(entry)
                if match:
                    self.allowed_urls.add(item['url'])
            # Keep newly discovered alternatives when the bounded lead window
            # is already full; original search evidence remains immutable.
            current_urls = {x['url'] for x in current_results}
            self.report['web_results'] = (current_results + [x for x in self.report['web_results'] if x['url'] not in current_urls])[:40]
            self.report['web_results'].sort(key=rank_key, reverse=True)
            self.publish()
            return {**result, 'results': current_results}
        self.failed_providers.add(provider)
        from datetime import datetime, timedelta, timezone
        failed_task = next((task for task in self.workspace.tasks(self.case_id)
                            if task['id'] == self.report['steps'][-1].get('task_id')), {})
        retry_at = failed_task.get('retry_at') or (datetime.now(timezone.utc) + timedelta(seconds=TRANSIENT_RETRY_SECONDS)).isoformat()
        self.workspace.update_health(self.case_id, 'web_search:' + provider, 'unavailable',
            retry_at=retry_at,
            error='Public search provider unavailable; use another provider while this one cools down.')
        return {'error': 'Web search unavailable. See its tool activity and retry.', 'results': []}

    def compare_locally(self):
        if not self.ai or not self.report['web_results']:
            return
        candidates = self.report['web_results'][:10]
        def compare():
            from .ai import compare_candidates
            hints = compare_candidates(self.ai, {**self.case, 'public_context': self.options['context']}, candidates)
            for index, lead in enumerate(candidates):
                lead['local_match']['model_assessment'] = hints.get(index, 'uncertain')
            self.report['web_results'].sort(key=rank_key, reverse=True)
            self.report['local_comparison'] = {'status': 'complete', 'compared': len(candidates),
                'consistent': sum(v == 'context_consistent' for v in hints.values()),
                'unlikely': sum(v == 'context_unlikely' for v in hints.values())}
            return {'summary': 'Saved case details compared locally. Context hints are unverified; private values stay local.'}
        result = self.step('Local AI: compare saved case details with public leads', compare, 0, 'analyze', {})
        if result is None:
            self.report['local_comparison'] = {'status': 'failed', 'compared': 0}

    def fetch(self, url, title, allow_query=False):
        raw, format, canonical = collector.fetch_public(url, allow_query=allow_query)
        result = self.engine.ingest(self.case_id, raw, collector.generated_title(title), canonical, format)
        self.remember(result)
        return {**result, 'raw': raw, 'format': format}

    def compare_pages_locally(self, pages):
        """Reassess readable namesakes locally; never change saved originals."""
        eligible = [p for p in pages if (p.get('useful') or p.get('context_excluded') or p.get('content_relevant'))
                    and p.get('local_match', {}).get('level') != 'identifier_match']
        if not self.ai or not self.options['context'] or not eligible:
            return
        def compare(candidates):
            from .ai import compare_candidates
            hints = compare_candidates(self.ai, {**self.case, 'public_context': self.options['context']}, [
                {'url': p['url'], 'title': self.case['name'], 'snippet': p.get('excerpt', ''),
                 'local_match': p.get('local_match', {})} for p in candidates])
            for index, page in enumerate(candidates):
                hint = hints.get(index, 'uncertain')
                page.setdefault('local_match', {})['model_assessment'] = hint
                page.setdefault('content_relevant', bool(page.get('useful') or page.get('context_excluded')))
                page.setdefault('content_quality_reason', page['quality_reason'])
                if hint == 'context_unlikely':
                    page.update(useful=False, context_excluded=True, quality_reason='Substantive public content, but local AI flags a likely namesake against chosen context. Identity remains unconfirmed.')
                else:
                    page.update(useful=page['content_relevant'], context_excluded=False,
                                quality_reason=page['content_quality_reason'])
                summary = ('Useful candidate page saved. ' if page['useful'] else 'Original saved; does not fill useful-page goal. ') + page['quality_reason']
                if page['url'] in self.pages:
                    self.pages[page['url']].update(useful=page['useful'], summary=summary)
                for entry in self.report['steps']:
                    if entry.get('tool') == 'collect_page' and entry.get('run_id') == self.report['run_id'] and entry.get('arguments', {}).get('url') == page['url'] and entry.get('ok'):
                        entry['result_summary'] = summary
            return {'summary': 'Saved page content compared with chosen public context and private clues locally; likely namesakes do not fill the useful-page goal.'}
        for offset in range(0, len(eligible), 10):
            batch = eligible[offset:offset + 10]
            self.step('Local AI: assess collected pages against target context', lambda batch=batch: compare(batch), 0, 'analyze', {})

    def page(self, url, explicit=False):
        if not self.enabled:
            raise InvalidInput('Web collection is off for this app session.')
        from .frontier import Frontier
        Frontier(self).guard('collect_page', {'url': url}, explicit=explicit)
        if not explicit and url not in self.allowed_urls:
            raise InvalidInput('Only URLs discovered for this investigation may be collected.')
        if url in self.pages and not explicit:
            return self.cached_tool('Reuse page result: ' + url, self.pages[url], 'collect_page', {'url': url})
        if not explicit and self.page_count() >= self.options['page_limit']:
            raise InvalidInput('Page limit reached. Increase it for the next investigation or run this page tool yourself.')
        def collect_page():
            saved = collector.collect(self.engine, self.case_id, url, True, allow_query=True)
            self.remember(saved)
            evidence = self.engine.evidence(self.case_id, saved['id'])
            with self.engine.connection() as db:
                raw = db.execute('SELECT raw FROM evidence WHERE id=? AND case_id=?', (saved['id'], self.case_id)).fetchone()['raw']
            quality = assess_page(raw, evidence['format'])
            if evidence['format'] in {'pdf', 'docx', 'odt', 'png', 'jpeg', 'tiff'}:
                text = evidence['text']
                position = text.casefold().find(self.case['name'].casefold())
                window = text[max(0, position - 1200):max(0, position - 1200) + 32000]
                quality = assess_page(window.encode('utf-8'), 'text')
            match = local_match(self.case, quality['text'], evidence['source'])
            lead = next((x for x in self.report['web_results'] if x['url'] == url), None)
            hint = (lead or {}).get('local_match', {}).get('model_assessment')
            if hint:
                match['model_assessment'] = hint
            content_relevant = quality['useful'] and (self.relevant(quality['text']) or match['level'] in {'identifier_match', 'context_match'})
            useful = content_relevant
            if hint == 'context_unlikely' and match['level'] != 'identifier_match':
                useful = False
            reason = quality['reason'] if not quality['useful'] else 'Substantive public content, but this is an unverified likely namesake.' if hint == 'context_unlikely' and not useful else 'Substantive content lacks subject or saved-clue corroboration.' if not useful else quality['reason']
            links = []
            if evidence['format'] == 'html' and quality['useful']:
                parser = PageLinks(evidence['source'])
                parser.feed(raw.decode('utf-8-sig'))
                from .planner import rank_links
                for link in rank_links(parser.links, self.case, self.options):
                    if not self.options.get('automatic') and not self.relevant(link['title']) and not any(w in words(link['title']) for w in ['about', 'profile', 'biography', 'team']):
                        continue
                    try:
                        collector.public_url(link['url'], allow_query=True)
                    except InvalidInput:
                        continue
                    links.append(link)
                    if len(links) == 10:
                        break
            self.allowed_urls.update(x['url'] for x in links)
            self.allowed_urls.add(evidence['source'])
            page = {'url': url, 'final_url': evidence['source'], 'source_id': saved['id'], 'run_id': self.report['run_id'],
                    'excerpt': quality['text'][:1800], 'links': links, 'useful': useful,
                    'readable': quality['useful'],
                    'quality_reason': reason, 'local_match': match, 'content_relevant': content_relevant,
                    'content_quality_reason': quality['reason'], 'context_excluded': content_relevant and not useful}
            self.report['pages'] = [p for p in self.report['pages'] if p['url'] != url] + [page]
            from .frontier import Frontier
            Frontier(self).record_page(page, evidence)
            for lead in self.report['web_results']:
                if lead['url'] == url:
                    lead['page_source_id'] = saved['id']
            return {**saved, 'url': evidence['source'], 'links': links, 'useful': useful,
                    'summary': ('Useful candidate page saved. ' if useful else 'Original saved; does not fill useful-page goal. ') + reason}
        result = self.step('Collect page: ' + url, collect_page, tool='collect_page', arguments={'url': url})
        self.pages[url] = result or {'error': self.report['steps'][-1].get('error', 'Page collection failed.')}
        if result:
            page = next(p for p in self.report['pages'] if p['url'] == url)
            self.compare_pages_locally([page])
            self.pages[url]['useful'] = page['useful']
        return self.pages[url]

    def cached_tool(self, label, result, tool, arguments):
        def reuse():
            if result.get('error'):
                raise InvalidInput(result['error'])
            return {**result, 'summary': 'Reused this run\'s saved tool result; no outbound request.'}
        return self.step(label, reuse, 0, tool, arguments) or result

    def page_count(self):
        return sum(p.get('useful') is True and (self.continuing or self.options.get('automatic') or p.get('run_id') == self.report['run_id']) for p in self.report['pages'])

    def domain(self, domain, signals=None):
        if not self.enabled:
            raise InvalidInput('Domain collection is off for this app session.')
        if domain not in self.allowed_domains:
            raise InvalidInput('Only domains identified for this investigation may be researched.')
        if domain in self.domains and signals is None:
            return self.cached_tool('Reuse domain results: ' + domain, self.domains[domain], 'domain_signals', {'domain': domain})
        results = []
        for signal in signals or ['dns', 'registration', 'certificate', 'website']:
            def collect_signal(signal=signal):
                result = collector.research(self.engine, self.case_id, domain, [signal], True)['results'][0]
                if not result['ok']:
                    raise InvalidInput(result['error'])
                return result
            result = self.step(f'{signal.capitalize()}: {domain}', collect_signal, 2 if signal == 'registration' else 1,
                               tool='domain_signals', arguments={'domain': domain, 'signals': [signal]})
            results.append(result or {'signal': signal, 'ok': False,
                                     'error': self.report['steps'][-1].get('error', 'Signal unavailable.')})
        self.domains[domain] = {'domain': domain, 'results': results}
        failures = [r for r in results if not r.get('ok')]
        if failures:
            prefix = 'All domain signals unavailable.' if len(failures) == len(results) else 'Partial domain results; some signals unavailable.'
            self.domains[domain]['error'] = prefix + ' ' + '; '.join(r['signal'] + ': ' + r['error'] for r in failures)
        return self.domains[domain]

    def dispatch(self, name, arguments, origin='ai'):
        self.check()
        if not isinstance(arguments, dict):
            raise InvalidInput('Tool arguments must be an object.')
        old_origin = self.origin
        self.origin = origin
        try:
            if name in {'web_search', 'collect_page', 'domain_signals'} and not self.report['network_enabled']:
                raise InvalidInput('This local analysis phase cannot call outbound tools.')
            if name == 'web_search' and set(arguments) in ({'query'}, {'query', 'provider'}) and 'web_search' in self.options['tools']:
                return self.web_search(self.model_query(arguments['query']), arguments.get('provider'))
            if name in {'search_subject', 'news_search'} and not arguments:
                return self.step('Read collected ' + name, lambda: {'candidates': self.report['candidates']} if name == 'search_subject' else {'articles': self.report['articles']}, 0, name, {})
            if name == 'collect_page' and set(arguments) == {'url'} and isinstance(arguments['url'], str):
                return self.page(arguments['url'])
            if name == 'domain_signals' and set(arguments) == {'domain'} and isinstance(arguments['domain'], str) and 'domain' in self.options['tools']:
                return self.domain(arguments['domain'])
            if name == 'read_evidence' and set(arguments) == {'id'} and arguments['id'] in self.report['source_ids']:
                return self.read_evidence(arguments['id'])
            raise InvalidInput('Tool or arguments are outside this investigation scope.')
        except InvalidInput as exc:
            # Scope failures precede collection, but still belong in visible tool history.
            fields = {'web_search': {'query'}, 'collect_page': {'url'}, 'domain_signals': {'domain'},
                      'read_evidence': {'id'}, 'search_subject': set(), 'news_search': set()}
            if name in fields and set(arguments) == fields[name] and all(isinstance(v, str) and len(v) <= 2000 for v in arguments.values()):
                def blocked():
                    raise exc
                from .ai import private_output
                self.step('Blocked AI tool: ' + name, blocked, 0, name, private_output(arguments, self.case['identifiers']))
            raise
        finally:
            self.origin = old_origin

    def read_evidence(self, source_id):
        def read():
            evidence = self.engine.evidence(self.case_id, source_id)
            return {key: evidence[key][:6000] for key in ['id', 'title', 'source', 'text']}
        return self.step('Read evidence: ' + source_id, read, 0, 'read_evidence', {'id': source_id})

    def search_subject(self, query, provider):
        if not self.enabled:
            raise InvalidInput('Web collection is off for this app session.')
        def search():
            result = collector.discover(self.engine, self.case_id, [provider], True, query)['results'][0]
            if not result['ok']:
                raise InvalidInput(result['error'])
            relevant = 0
            for candidate in result['candidates']:
                match = self.relevant(candidate['label'], candidate['description'])
                entry = {**candidate, 'provider': provider, 'discovery_source_id': result['id'], 'relevant': match}
                if not any(c['url'] == entry['url'] for c in self.report['candidates']):
                    self.report['candidates'].append(entry)
                if match:
                    relevant += 1
                    self.allowed_urls.add(candidate['collection_url'])
            return {**result, 'summary': f'{len(result["candidates"])} candidates; {relevant} name/alias matches.'}
        return self.step(f'Search {provider}: {query}', search, tool='search_subject', arguments={'query': query, 'provider': provider})

    def news_search(self, query):
        if not self.enabled:
            raise InvalidInput('Web collection is off for this app session.')
        result = self.step('Search recent news: ' + query, lambda: collector.news(self.engine, self.case_id, query, True),
                           tool='news_search', arguments={'query': query})
        if result:
            self.report['articles'] = [{**a, 'source_id': result['id']} for a in result['articles']]
            for item in self.report['articles']:
                if self.relevant(item['title']):
                    self.allowed_urls.add(item['url'])
        return result

    def analyze(self):
        if not self.ai:
            raise InvalidInput('Start local AI and wait for Ready, then retry AI analysis.')
        from .ai import run_assistant
        def analyze():
            if not self.report.get('local_comparison'):
                self.compare_locally()
            self.report['retrieved_evidence'] = self.workspace.retrieve(self.case_id,
                self.case['name'] + ' ' + self.options['context'], limit=8)
            for passage in self.report['retrieved_evidence']:
                self.remember({'id': passage['id']})
            result = run_assistant(self.ai, self.report, self.dispatch, lambda: self.check(),
                                   activity=lambda message: self.publish(message), private_context=self.case['identifiers'])
            self.report['ai'] = result
            return {'summary': 'Local AI ' + result['status'] + '.', **result}
        result = self.step('Local AI: planning searches and following public sources', analyze, 0, 'analyze', {})
        if result is None:
            from .ai import private_output
            entry = next(s for s in reversed(self.report['steps']) if s.get('tool') == 'analyze')
            trace = [{'tool': s['tool'], 'arguments': s['arguments'], 'ok': s['ok'],
                      'result': s.get('error') or s.get('result_summary', '')}
                     for s in self.report['steps'][self.start_step:] if s.get('origin') == 'ai']
            self.report['ai'] = private_output({'status': 'failed', 'error': entry.get('error', 'Local AI unavailable. Retry AI analysis.'), 'source_ids': [], 'tool_calls': trace}, self.case['identifiers'])
        return result

    def profile(self, candidate):
        identifier = candidate['url'].rsplit('/', 1)[-1]
        fetched = self.fetch(candidate['collection_url'], 'Candidate profile: ' + candidate['label'])
        if fetched['format'] != 'json':
            raise InvalidInput('Candidate profile did not return JSON.')
        item = json.loads(fetched['raw'])['entities'][identifier]
        labels, descriptions = item.get('labels', {}), item.get('descriptions', {})
        profile = {'candidate_id': identifier, 'label': str(labels.get('en', {}).get('value', candidate['label']))[:300],
                   'description': str(descriptions.get('en', {}).get('value', candidate['description']))[:1000],
                   'source_id': fetched['id'], 'url': candidate['url'], 'claims': []}
        for prop, label in PROPERTIES.items():
            statements = [c for c in item.get('claims', {}).get(prop, []) if c.get('rank', 'normal') != 'deprecated']
            statements.sort(key=lambda c: c.get('rank') != 'preferred')
            website_rank = 'preferred' if any(c.get('rank') == 'preferred' and 'P582' not in c.get('qualifiers', {}) for c in statements) else 'normal'
            for claim in statements[:3]:
                snak = claim.get('mainsnak', {})
                if snak.get('snaktype') != 'value':
                    continue
                value = snak.get('datavalue', {}).get('value')
                linked_id = value.get('id') if isinstance(value, dict) else None
                value = statement_value(value)
                if value is None:
                    continue
                qualifiers = []
                qualifier_map = claim.get('qualifiers', {})
                for key, snaks in list(qualifier_map.items())[:8]:
                    for qualifier in snaks[:2]:
                        qvalue = statement_value(qualifier.get('datavalue', {}).get('value'))
                        qualifiers.append({'property': key, 'label': QUALIFIERS.get(key, key),
                                           'value': qvalue or qualifier.get('snaktype', 'unknown')})
                profile['claims'].append({'property': prop, 'label': label, 'value': value,
                                          'entity_id': linked_id, 'candidate_id': identifier, 'source_id': fetched['id'],
                                          'rank': claim.get('rank', 'normal'), 'qualifiers': qualifiers,
                                          'qualifiers_truncated': len(qualifier_map) > 8 or any(len(v) > 2 for v in qualifier_map.values())})
                if prop == 'P856' and not self.report['profiles'] and 'P582' not in qualifier_map and claim.get('rank', 'normal') == website_rank:
                    # HTTPS upgrade is an attempted lead, never a claim that HTTPS exists.
                    url = 'https://' + value[7:] if value.startswith('http://') else value
                    if url.startswith('https://'):
                        self.allowed_urls.add(url)
                        try:
                            self.allowed_domains.add(collector.domain_name(urlsplit(url).hostname or ''))
                        except (InvalidInput, ValueError):
                            pass
                if len(profile['claims']) >= 24:
                    break
            if len(profile['claims']) >= 24:
                break
        self.report['profiles'].append(profile)
        return {'id': fetched['id']}

    def pipeline(self):
        selected = self.options['tools']
        from .frontier import Frontier
        frontier = Frontier(self)
        frontier.seed()
        frontier.run_queue()
        def search(query):
            providers = [p for p in self.options['providers'] if p not in self.failed_providers]
            providers.sort(key=lambda p: p != self.active_provider)
            for provider in providers:
                result = self.web_search(query, provider)
                if not result.get('error'):
                    return result
            return {'error': 'Selected search providers unavailable.', 'results': []}
        if 'web_search' in selected:
            query = self.options['query'] + (' ' + self.options['context'] if self.options['context'] else '')
            search(query)
            matching = [x for x in self.report['web_results'] if x['relevant']]
            if not matching and self.case['aliases']:
                search('"' + self.case['aliases'][0] + '"')
                matching = [x for x in self.report['web_results'] if x['relevant']]
            self.compare_locally()
            automatic_pages = self.options['page_limit']
            self.follow_candidates(automatic_pages)
            # Public-only refinements never include private clues or model prose.
            for suffix in ['profile contact', 'biography website']:
                if self.page_count() >= automatic_pages:
                    break
                refined = query + ' ' + suffix
                if len(refined) <= 200:
                    search(refined)
                    self.follow_candidates(automatic_pages)
            if not any(x['relevant'] for x in self.report['web_results']):
                self.skip('Follow web results', 'No complete name/alias match. Add public context or edit the search query.', 'collect_page')
        for provider in ['wikipedia', 'wikidata']:
            if provider in selected:
                for query in [self.case['name'], *self.case['aliases'][:1]]:
                    self.search_subject(query, provider)
        ranked = sorted([c for c in self.report['candidates'] if c.get('relevant')], key=lambda c: c['label'].casefold() != self.case['name'].casefold())
        for candidate in [c for c in ranked if c['provider'] == 'wikidata'][:2]:
            self.step('Read candidate profile: ' + candidate['label'], lambda c=candidate: self.profile(c),
                      tool='collect_page', arguments={'url': candidate['collection_url']})
        linked = list(dict.fromkeys(claim['entity_id'] for profile in self.report['profiles'] for claim in profile['claims'] if claim['entity_id']))[:20]
        if linked:
            def label_entities():
                url = 'https://www.wikidata.org/w/api.php?' + urlencode({'action': 'wbgetentities', 'ids': '|'.join(linked), 'props': 'labels', 'languages': 'en', 'format': 'json'})
                fetched = self.fetch(url, 'Labels for candidate relationships', True)
                entities = json.loads(fetched['raw'])['entities']
                for profile in self.report['profiles']:
                    for claim in profile['claims']:
                        label = entities.get(claim['entity_id'], {}).get('labels', {}).get('en', {}).get('value')
                        if isinstance(label, str):
                            claim.update(value=label[:300], label_source_id=fetched['id'])
                return {'id': fetched['id']}
            self.step('Resolve candidate relationship labels', label_entities, tool='collect_page', arguments={'url': 'https://www.wikidata.org/w/api.php?' + urlencode({'action': 'wbgetentities', 'ids': '|'.join(linked), 'props': 'labels', 'languages': 'en', 'format': 'json'})})
        wiki = next((c for c in ranked if c['provider'] == 'wikipedia'), None)
        if wiki and self.page_count() < self.options['page_limit']:
            self.page(wiki['collection_url'])
        elif 'wikipedia' in selected and not wiki:
            self.skip('Follow Wikipedia result', 'No complete name/alias match; unrelated articles were not collected.', 'collect_page')
        self.publish('Profiles and web sources collected', persist=True)
        if 'news' in selected:
            self.news_search(self.case['name'])
            for article in self.report['articles'][:2]:
                if article['url'] in self.allowed_urls and self.page_count() < self.options['page_limit']:
                    page = self.page(article['url'])
                    if page.get('id'):
                        article['page_source_id'] = page['id']
        known_domains = []
        for value in ([self.case['name']] if self.case['focus_type'] == 'domain' else []) + self.options['seed_urls']:
            if '@' in value:
                continue
            if value.startswith('https://'):
                self.allowed_urls.add(value)
            try:
                domain = collector.domain_name(urlsplit(value).hostname if value.startswith('https://') else value)
                known_domains.append(domain)
                self.allowed_domains.add(domain)
            except (InvalidInput, ValueError):
                continue
        for url in self.options['seed_urls'][:2]:
            if self.page_count() < self.options['page_limit']:
                self.page(url)
        domain = next(iter(known_domains), None) or next(iter(sorted(self.allowed_domains)), None)
        if 'domain' in selected and domain:
            self.report['domain_lead'] = {'domain': domain, 'basis': 'User supplied domain/URL' if known_domains else 'Unconfirmed candidate website claim'}
            if not known_domains and self.report['profiles']:
                profile = self.report['profiles'][0]
                self.report['domain_lead'].update(candidate_id=profile['candidate_id'], source_id=profile['source_id'])
            self.domain(domain)
        elif 'domain' in selected:
            self.skip('Domain signals', 'No subject domain supplied or identified. Email-provider domains are not researched automatically.', 'domain_signals')
        self.publish('Public collection finished', persist=True)
        frontier.discover_report()
        frontier.run_queue()
        if self.ai:
            self.analyze()

    def follow_candidates(self, goal):
        for lead in sorted(self.report['web_results'], key=rank_key, reverse=True):
            if self.page_count() >= goal:
                return
            match = lead.get('local_match', {})
            if lead.get('relevant') and lead['url'] not in self.pages and (match.get('level') == 'identifier_match' or match.get('model_assessment') != 'context_unlikely'):
                self.page(lead['url'])
                for link in next((p['links'] for p in self.report['pages'] if p['url'] == lead['url'] and p.get('useful')), [])[:2]:
                    if self.page_count() >= goal:
                        return
                    if link['url'] not in self.pages:
                        self.page(link['url'])

    def continuation_hosts(self):
        """Avoid access barriers across a host, without bypassing them."""
        hosts = set(self.report['continuation']['excluded_hosts'])
        for step in self.report['steps'][self.start_step:]:
            if step.get('tool') == 'collect_page' and step.get('status') == 'failed' and any(
                    code in step.get('error', '') for code in ['403', '429', '999']):
                host = urlsplit(step.get('arguments', {}).get('url', '')).hostname
                if host:
                    hosts.add(host)
        for page in self.report['pages']:
            if not page.get('useful') and any(word in page.get('quality_reason', '').lower()
                    for word in ['login', 'authentication wall', 'verification', 'access barrier']):
                hosts.add(urlsplit(page['url']).hostname)
        self.report['continuation']['excluded_hosts'] = sorted(host for host in hosts if host)
        return hosts

    def continuation_pages(self):
        hosts = self.continuation_hosts()
        selected = self.options['tools']
        def excluded(url):
            host = (urlsplit(url).hostname or '').removeprefix('www.')
            return any(host == h.removeprefix('www.') or host.endswith('.' + h.removeprefix('www.')) for h in hosts)
        leads = sorted(self.report['web_results'], key=rank_key, reverse=True) if 'web_search' in selected else []
        leads += [{'url': c['collection_url'], 'title': c['label'], 'relevant': True}
                  for c in self.report['candidates'] if c.get('provider') in selected and c.get('relevant') and c.get('collection_url')]
        leads += [{'url': a['url'], 'title': a['title'], 'relevant': True}
                  for a in self.report['articles'] if 'news' in selected and self.relevant(a['title'])]
        leads += [{'url': link['url'], 'title': link['title'], 'relevant': True}
                  for page in self.report['pages'] if 'web_search' in selected and page.get('useful') for link in page.get('links', [])]
        result, seen = [], set()
        for lead in leads:
            url = lead['url']
            match = lead.get('local_match', {})
            if (lead.get('relevant') and url not in self.pages and url not in seen and not excluded(url)
                    and (match.get('level') == 'identifier_match' or match.get('model_assessment') != 'context_unlikely')):
                result.append(lead)
                seen.add(url)
        return result

    @staticmethod
    def query_key(query):
        # Word ordering alone does not constitute a new research strategy.
        return tuple(sorted(words(query)))

    def alternative_query(self, query):
        query = self.model_query(query)
        for host in self.report.get('continuation', {}).get('excluded_hosts', []):
            host = host.removeprefix('www.')
            if host.rsplit('.', 1)[-1] in {'com', 'org', 'net'}:
                host = '.'.join(host.split('.')[-2:])
            exclusion = ' -site:' + host
            if exclusion.strip() not in query and len(query + exclusion) <= 200:
                query += exclusion
        return self.model_query(query)

    def recovery_choices(self, attempted):
        from .recovery import recovery_queries
        if 'web_search' not in self.options['tools']:
            return []
        providers = [p for p in self.options['providers'] if p not in self.failed_providers]
        # Try another provider for a stalled query before more refinements on it.
        providers.sort(key=lambda p: p == self.active_provider)
        choices = []
        for entry in recovery_queries(self.case, self.options):
            try:
                query = self.alternative_query(entry['query'])
            except InvalidInput:
                continue
            for provider in providers:
                if (provider, self.query_key(query)) not in attempted:
                    choices.append({'action': 'web_search', 'arguments': {'query': query, 'provider': provider},
                                    'reason': 'Change public research strategy or search provider.',
                                    'strategy': entry['strategy'], 'provider': provider, 'origin': 'engine'})
        return choices

    def continue_pipeline(self):
        from .ai import plan_continuation, private_output
        self.check()
        if not self.ai:
            raise InvalidInput('Continue investigation needs the installed local AI and a cached model.')
        self.publish('Starting local AI for the continuation planner')
        state = self.ai.status()
        if state['state'] != 'ready':
            state = self.ai.start()
        while state['state'] == 'loading':
            self.check()
            self.cancel.wait(0.5)
            state = self.ai.status()
        if state['state'] != 'ready':
            raise InvalidInput(state.get('error') or 'Local AI is unavailable. Saved evidence is retained.')
        # Model preparation has its own bounded timeout and does not consume collection time.
        if not self.options['keep_working'] and self.options['runtime_seconds']:
            self.deadline = time.monotonic() + self.options['runtime_seconds']
        from .frontier import Frontier
        frontier = Frontier(self)
        frontier.seed()
        frontier.discover_report()
        frontier.run_queue()
        self.compare_pages_locally(self.report['pages'])
        attempted = {(s.get('arguments', {}).get('provider', 'duckduckgo'), self.query_key(s.get('arguments', {}).get('query', '')))
                     for s in self.report['steps'] if s.get('tool') == 'web_search'}
        stage, stalled, index = 'plan', 0, 0
        while True:
            self.check()
            if self.page_count() >= self.options['page_limit']:
                self.report['continuation']['stop_reason'] = 'Cumulative useful-page goal reached.'
                break
            if self.used >= self.options['max_requests']:
                self.check(1)
            available = self.continuation_pages()
            choices = self.recovery_choices(attempted)
            if not available and not choices:
                self.report['continuation']['stop_reason'] = 'Eligible public pages and fresh strategies on available selected providers exhausted. Retry an unavailable provider or add public context; identity remains unconfirmed.'
                break
            index += 1
            self.report['active_provider'] = self.active_provider
            self.report['continuation'].update(strategy=stage, unavailable_providers=sorted(self.failed_providers))
            # Collect eligible results promptly. Repeated model output cannot spend
            # the remaining deadline preventing real collection requests.
            if available or stalled >= 2:
                decision = ({'action': 'collect_page', 'arguments': {'url': available[0]['url']},
                             'reason': 'Read an eligible untried public source before more searching.',
                             'origin': 'engine', 'strategy': stage} if available else copy.deepcopy(choices[0]))
            else:
                self.publish(f'Local AI {stage} decision {index} (up to 60 seconds)')
                try:
                    decision = plan_continuation(self.ai, self.case, self.report, available,
                        [s.get('arguments', {}).get('query', '') for s in self.report['steps'] if s.get('tool') == 'web_search'][-30:],
                        self.report['continuation']['excluded_hosts'], stage=stage,
                        recovery_choices=[choice['arguments']['query'] for choice in choices[:15]])
                except InvalidInput:
                    decision = {'action': 'finish', 'arguments': {},
                                'reason': 'Local AI returned an unusable plan; choose fresh public work automatically.'}
                self.check()
                decision = private_output(decision, outbound_private_clues(self.case, self.options))
                decision['reason'] = private_output(decision['reason'], self.case['identifiers'])
                decision.update(origin='ai', strategy=stage)
            decision['status'] = 'planned'
            self.report['continuation']['decisions'].append(decision)
            try:
                action, args = decision['action'], decision['arguments']
                if action == 'web_search':
                    if 'web_search' not in self.options['tools']:
                        raise InvalidInput('Web search is not selected.')
                    query = self.alternative_query(args['query'])
                    provider = args.get('provider', self.active_provider)
                    if provider not in self.options['providers'] or provider in self.failed_providers:
                        raise InvalidInput('Choose an available selected search provider.')
                    if (provider, self.query_key(query)) in attempted:
                        raise InvalidInput('This provider already attempted the public query; change strategy or provider.')
                    args.update(query=query, provider=provider)
                    decision['provider'] = provider
                elif action == 'collect_page':
                    if args['url'] not in {lead['url'] for lead in available}:
                        raise InvalidInput('Choose an untried eligible public URL, outside blocked hosts.')
                else:
                    raise InvalidInput('Eligible public work remains; premature finish cannot end recovery.')
            except (InvalidInput, KeyError) as exc:
                decision.update(status='blocked', error=private_output(str(exc), self.case['identifiers']))
                stalled += 1
                stage = 'repetition'
                decision = copy.deepcopy(choices[0]) if choices else {
                    'action': 'collect_page', 'arguments': {'url': available[0]['url']},
                    'reason': 'Model repeated or rejected work; recover an eligible public page.', 'origin': 'engine'}
                decision.update(status='planned', strategy='repetition')
                self.report['continuation']['decisions'].append(decision)
                action, args = decision['action'], decision['arguments']
            self.publish(persist=True)
            if action == 'web_search':
                attempted.add((args['provider'], self.query_key(args['query'])))
            before_pages = self.page_count()
            result = self.dispatch(action, args, origin=decision.get('origin', 'ai'))
            frontier.discover_report()
            frontier.run_queue()
            decision['status'] = 'success' if result and not result.get('error') else 'failed'
            if action == 'web_search':
                if result.get('error'):
                    stage = 'provider_failure'
                else:
                    # Reassessing already attempted/login-only leads wastes the
                    # deadline without creating a possible collection action.
                    if self.continuation_pages():
                        self.compare_locally()
                    stage = 'no_useful_pages' if not self.continuation_pages() else 'identity_ambiguity'
            else:
                stage = 'plan' if self.page_count() > before_pages else 'no_useful_pages'
                if self.page_count() > before_pages:
                    stalled = 0
            self.report['continuation'].update(strategy=stage, unavailable_providers=sorted(self.failed_providers))
            self.publish(persist=True)
        self.publish('Recovery collection finished; reading saved evidence locally', persist=True)
        self.report['network_enabled'] = False
        try:
            self.analyze()
        finally:
            self.report['network_enabled'] = self.enabled

    def user_tool(self, name, args):
        if not isinstance(args, dict):
            raise InvalidInput('Tool inputs must be an object.')
        self.origin = 'user'
        if name == 'resume_queue' and set(args) in (set(), {'id'}):
            from .frontier import Frontier
            return Frontier(self).run_queue(recover=True, task_ids={args['id']} if args else None)
        from .connectors import catalogue
        if name in {item['id'] for item in catalogue()}:
            from .frontier import Frontier
            frontier = Frontier(self)
            task = frontier.enqueue(name, args, 'Requested from the source tools panel.', explicit=True)
            if task['state'] not in {'pending', 'queued'}:
                self.workspace.edit_action(self.case_id, task['id'], state='pending')
            return frontier.run_queue(task_ids={task['id']})
        if name == 'web_search' and set(args) in ({'query'}, {'query', 'provider'}):
            query = field(args['query'], 'Public search query', 200)
            self.options.update(query=query, context='')
            return self.web_search(query, args.get('provider'))
        if name == 'search_subject' and set(args) == {'query', 'provider'} and args['provider'] in {'wikipedia', 'wikidata'}:
            return self.search_subject(field(args['query'], 'Public search query', 200), args['provider'])
        if name == 'news_search' and set(args) == {'query'}:
            return self.news_search(field(args['query'], 'Public news query', 200))
        if name == 'collect_page' and set(args) == {'url'}:
            url = field(args['url'], 'Public page URL', 2000)
            collector.public_url(url, allow_query=True)
            self.allowed_urls.add(url)
            return self.page(url, explicit=True)
        if name == 'domain_signals' and set(args) == {'domain', 'signals'}:
            domain = collector.domain_name(args['domain'])
            signals = args['signals']
            if not isinstance(signals, list) or not signals or any(not isinstance(x, str) or x not in collector.SIGNALS for x in signals) or len(set(signals)) != len(signals):
                raise InvalidInput('Select supported domain signals without duplicates.')
            self.allowed_domains.add(domain)
            return self.domain(domain, signals)
        if name == 'read_evidence' and set(args) == {'id'}:
            return self.read_evidence(field(args['id'], 'Evidence ID', 100))
        if name == 'analyze' and not args:
            return self.analyze()
        raise InvalidInput('Unsupported tool or inputs.')

    def run(self, action=None):
        self.publish(persist=True)
        try:
            if self.options['automatic'] and not action:
                from .planner import Controller
                self.controller = Controller(self)
                self.controller.run()
                return self.report
            elif self.continuing:
                self.continue_pipeline()
            elif action:
                self.user_tool(action['tool'], action['arguments'])
            else:
                self.pipeline()
            self.check()
            current = self.report['steps'][self.start_step:]
            failures = sum(step.get('status') == 'failed' for step in current)
            ai_failed = self.report.get('ai', {}).get('status') in {'failed', 'truncated', 'limited'} if self.ai else False
            shortfall = (self.continuing or 'web_search' in self.options['tools'] or bool(self.options['packs'])) and not action and self.page_count() < self.options['page_limit']
            self.report['status'] = ('partial' if failures or ai_failed or shortfall else 'complete') if self.report['source_ids'] else 'failed'
            self.report['stop_reason'] = ('Selected tool finished; prior evidence retained.' if action else 'Selected sources and relevant follow-ups finished.') + (' Some tools were unavailable; edit or retry them below.' if failures or ai_failed else '') + ' Request ceiling is a limit, not a target.'
            if shortfall:
                self.report['stop_reason'] += f' Useful-page goal not reached: {self.page_count()} of {self.options["page_limit"]}. Bounded public searches and eligible leads exhausted; refine public context or retry a source.'
            if self.continuing:
                self.report['stop_reason'] = (self.report['continuation']['stop_reason'] or 'Continuation finished.') + f' {self.page_count()} of {self.options["page_limit"]} useful pages retained across runs. Existing evidence and tool history are preserved; this continuation used a fresh request budget.'
                if failures or ai_failed:
                    self.report['stop_reason'] += ' Some recovery tools or local analysis were unavailable; inspect activity and retry.'
        except Halt:
            pass
        except InvalidInput as exc:
            self.report['status'] = 'partial' if self.report['source_ids'] else 'failed'
            self.report['error'] = str(exc)
            self.report['stop_reason'] = str(exc)
        except Exception as exc:
            self.report['status'] = 'partial' if self.report['source_ids'] else 'failed'
            event = self.diagnostics.record('investigation', exc)
            self.report['error'] = 'Investigation stopped unexpectedly. Collected evidence is retained. Reference: ' + event['id'] + '.'
            self.report['stop_reason'] = self.report['error']
        finally:
            if getattr(self, 'controller', None):
                self.controller.evaluate()
            self.report['finished_at'] = now()
            self.publish('Investigation ' + self.report['status'], persist=True)
        return copy.deepcopy(self.report)


def save_report(engine, report):
    return engine.ingest(report['case_id'], json.dumps(report, ensure_ascii=True).encode(),
                         'Investigation: ' + report['subject'], REPORT_SOURCE, 'json', extract=False)


class Jobs:
    """One owned worker; persisted checkpoints survive restart without resuming traffic."""
    def __init__(self, engine, enabled=False, ai=None, search_service=None, diagnostics=None):
        self.engine, self.enabled, self.ai = engine, enabled, ai
        self.search_service = search_service
        from .diagnostics import Diagnostics
        self.diagnostics = diagnostics if diagnostics is not None else Diagnostics(engine.directory)
        self.workspace = Workspace(engine)
        from .owned_process import WorkerLease
        self.lease = WorkerLease(engine.directory)
        self.lock = threading.RLock()
        self.active = None
        self.states = {}
        self.cancel = threading.Event()
        self._worker = None
        if self.lease.acquire():
            try:
                self.workspace.recover_running()
                for case in engine.cases():
                    report = engine.latest_investigation(case['id'])
                    if report and report.get('status') == 'running':
                        report.update(status='interrupted', stage='Interrupted by app restart', finished_at=now())
                        report.pop('report_id', None)
                        save_report(engine, report)
            finally:
                self.lease.release()

    def start(self, case_id, use_ai=False, options=None, action=None, continuing=False):
        if type(use_ai) is not bool:
            raise InvalidInput('Local AI selection must be true or false.')
        from .connectors import catalogue
        allowed = {'web_search', 'search_subject', 'news_search', 'collect_page', 'domain_signals', 'read_evidence', 'analyze', 'resume_queue'} | {item['id'] for item in catalogue()}
        if action and (not isinstance(action, dict) or set(action) != {'tool', 'arguments'} or action['tool'] not in allowed or not isinstance(action['arguments'], dict)):
            raise InvalidInput('Unsupported tool or inputs.')
        if action and action['tool'] == 'analyze':
            use_ai = True
        with self.lock:
            if self.active:
                current = self.states[self.active]
                if current['case_id'] == case_id:
                    return copy.deepcopy(current)
                raise InvalidInput('Another investigation is running. Stop it or wait for completion.')
            selected_ai = self.ai if use_ai or continuing and not (options or {}).get('automatic') else None
            if use_ai and not continuing and not (options or {}).get('automatic') and (not selected_ai or selected_ai.status()['state'] != 'ready'):
                raise InvalidInput('Start local AI and wait until ready before selecting AI assistance.')
            self.cancel = threading.Event()
            run_id = uuid.uuid4().hex
            def progress(report):
                with self.lock:
                    self.states[run_id] = report
            previous = self.engine.latest_investigation(case_id) if action or continuing or (options or {}).get('automatic') else None
            with self.engine.connection() as db:
                case = self.engine.require_case(db, case_id)
            from .frontier import SOURCE_KEYS, default_settings
            saved = self.workspace.settings(case_id)
            selected_options = {**(previous.get('options', {}) if previous else {}),
                **default_settings(case), **{key: saved[key] for key in SOURCE_KEYS if key in saved}, **(options or {})}
            if not self.lease.acquire():
                raise InvalidInput('Another app or CLI worker is using this workspace. Stop it before starting collection here.')
            try:
                runner = Investigation(self.engine, case_id, self.enabled, progress, self.cancel, selected_ai, run_id,
                                       options=selected_options, previous=previous,
                                       local_only=bool(action and action['tool'] in {'read_evidence', 'analyze', 'email_headers', 'resume_queue'}),
                                       continuing=continuing, search_service=self.search_service, diagnostics=self.diagnostics)
            except BaseException:
                self.lease.release()
                raise
            runner.publish(persist=True)
            self.active = run_id
            def work():
                try:
                    runner.run(action)
                finally:
                    self.lease.release()
                    with self.lock:
                        self.active = None
            try:
                self._worker = threading.Thread(target=work, name='osint-investigation', daemon=True)
                self._worker.start()
            except BaseException:
                self.active = None
                self.lease.release()
                raise
            while len(self.states) > 50:
                self.states.pop(next(iter(self.states)))
            return copy.deepcopy(self.states[run_id])

    def get(self, case_id):
        with self.engine.connection() as db:
            self.engine.require_case(db, case_id)
        with self.lock:
            matches = [v for v in self.states.values() if v['case_id'] == case_id]
            if matches:
                return copy.deepcopy(matches[-1])
        return self.engine.latest_investigation(case_id)

    def stop(self, case_id, run_id):
        with self.lock:
            if self.active != run_id or self.states[run_id]['case_id'] != case_id:
                raise InvalidInput('This investigation is no longer running.')
            self.cancel.set()
            return {'stopping': True}

    def close(self):
        self.cancel.set()
        if self._worker and self._worker is not threading.current_thread():
            self._worker.join(timeout=1)
        if not self._worker or not self._worker.is_alive():
            self.lease.release()
