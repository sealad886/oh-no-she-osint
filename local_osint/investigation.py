"""Bounded subject-to-dossier collection; AI cannot widen the network scope."""

import copy
import http.client
import json
import re
import threading
import time
import uuid
from urllib.parse import urlencode, urlsplit

from . import collector
from .engine import InvalidInput, now

REPORT_SOURCE = 'local-osint:investigation-report'
PROPERTIES = {'P31': 'Type', 'P856': 'Official website (candidate claim)', 'P17': 'Country',
              'P159': 'Headquarters', 'P112': 'Founder', 'P127': 'Owner', 'P355': 'Subsidiary',
              'P361': 'Part of', 'P106': 'Occupation', 'P108': 'Employer', 'P69': 'Education',
              'P800': 'Notable work', 'P569': 'Date of birth', 'P570': 'Date of death'}
QUALIFIERS = {'P580': 'Start time', 'P582': 'End time', 'P585': 'Point in time',
              'P518': 'Applies to part', 'P407': 'Language', 'P459': 'Method',
              'P1480': 'Sourcing circumstances', 'P7452': 'Reason for preferred rank'}


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
    def __init__(self, engine, case_id, enabled=False, progress=None, cancel=None, ai=None, run_id=None, deadline_seconds=180):
        if not enabled:
            raise InvalidInput('Investigation collection is off. Restart with --allow-network.')
        with engine.connection() as db:
            self.case = engine.require_case(db, case_id)
        self.engine, self.case_id, self.progress, self.ai = engine, case_id, progress, ai
        self.cancel = cancel or threading.Event()
        self.deadline = time.monotonic() + deadline_seconds
        self.used = 0
        self.allowed_urls, self.allowed_domains = set(), set()
        self.pages, self.domains = {}, {}
        self.report = {'report_type': 'investigation', 'run_id': run_id or uuid.uuid4().hex,
                       'case_id': case_id, 'subject': self.case['name'], 'focus_type': self.case['focus_type'],
                       'started_at': now(), 'status': 'running', 'stage': 'Starting', 'steps': [],
                       'profiles': [], 'candidates': [], 'articles': [], 'source_ids': [],
                       'interpretation': 'Name matches are unconfirmed candidates. Claims below belong to each candidate, not automatically to your subject.'}

    def check(self, cost=0):
        if self.cancel.is_set():
            self.report['status'] = 'cancelled'
            raise Halt()
        if time.monotonic() >= self.deadline or self.used + cost > 24:
            self.report['status'] = 'limited'
            raise Halt()
        self.used += cost

    def publish(self, stage=None, persist=False):
        if stage:
            self.report['stage'] = stage
        self.report['request_units'] = self.used
        if persist:
            save_report(self.engine, self.report)
        if self.progress:
            self.progress(copy.deepcopy(self.report))

    def remember(self, result):
        if result.get('id') and result['id'] not in self.report['source_ids']:
            self.report['source_ids'].append(result['id'])
        return result

    def step(self, label, callback, cost=1):
        self.check(cost)
        self.publish(label)
        entry = {'label': label, 'ok': False}
        try:
            result = callback()
            self.remember(result)
            entry.update(ok=True)
            if result.get('id'):
                entry['source_id'] = result['id']
            self.report['steps'].append(entry)
            self.publish()
            return result
        except (InvalidInput, OSError, http.client.HTTPException, ValueError, KeyError, TypeError, RecursionError) as exc:
            entry['error'] = str(exc) if isinstance(exc, InvalidInput) else 'Source unavailable or response could not be interpreted.'
            self.report['steps'].append(entry)
            self.publish()
            return None

    def fetch(self, url, title, allow_query=False):
        raw, format, canonical = collector.fetch_public(url, allow_query=allow_query)
        result = self.engine.ingest(self.case_id, raw, collector.generated_title(title), canonical, format)
        self.remember(result)
        return {**result, 'raw': raw, 'format': format}

    def page(self, url):
        if url not in self.allowed_urls:
            raise InvalidInput('Only URLs discovered for this investigation may be collected.')
        if url in self.pages:
            return self.pages[url]
        result = self.step('Collect page: ' + url, lambda: collector.collect(self.engine, self.case_id, url, True))
        self.pages[url] = result or {'error': 'Page collection failed.'}
        return self.pages[url]

    def domain(self, domain):
        if domain not in self.allowed_domains:
            raise InvalidInput('Only domains identified for this investigation may be researched.')
        if domain in self.domains:
            return self.domains[domain]
        results = []
        for signal in ['dns', 'registration', 'certificate', 'website']:
            def collect_signal(signal=signal):
                result = collector.research(self.engine, self.case_id, domain, [signal], True)['results'][0]
                if not result['ok']:
                    raise InvalidInput(result['error'])
                return result
            result = self.step(f'{signal.capitalize()}: {domain}', collect_signal, 2 if signal == 'registration' else 1)
            results.append(result or {'signal': signal, 'ok': False})
        self.domains[domain] = {'domain': domain, 'results': results}
        return self.domains[domain]

    def dispatch(self, name, arguments):
        self.check()
        if not isinstance(arguments, dict):
            raise InvalidInput('Tool arguments must be an object.')
        if name in {'search_subject', 'news_search'} and not arguments:
            return {'candidates': self.report['candidates']} if name == 'search_subject' else {'articles': self.report['articles']}
        if name == 'collect_page' and set(arguments) == {'url'} and isinstance(arguments['url'], str):
            return self.page(arguments['url'])
        if name == 'domain_signals' and set(arguments) == {'domain'} and isinstance(arguments['domain'], str):
            return self.domain(arguments['domain'])
        if name == 'read_evidence' and set(arguments) == {'id'} and arguments['id'] in self.report['source_ids']:
            evidence = self.engine.evidence(self.case_id, arguments['id'])
            return {key: evidence[key][:6000] for key in ['id', 'title', 'source', 'text']}
        raise InvalidInput('Tool or arguments are outside this investigation scope.')

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

    def run(self):
        self.publish(persist=True)
        try:
            for query in [self.case['name'], *self.case['aliases'][:1]]:
                for source in ['wikipedia', 'wikidata']:
                    def search(source=source, query=query):
                        result = collector.discover(self.engine, self.case_id, [source], True, query)['results'][0]
                        if not result['ok']:
                            raise InvalidInput(result['error'])
                        for candidate in result['candidates']:
                            entry = {**candidate, 'provider': source, 'discovery_source_id': result['id']}
                            if not any(c['url'] == entry['url'] for c in self.report['candidates']):
                                self.report['candidates'].append(entry)
                            self.allowed_urls.add(candidate['collection_url'])
                        return result
                    self.step(f'Search {source}: {query}', search)
            names = {s.casefold() for s in [self.case['name'], *self.case['aliases']]}
            candidates = self.report['candidates']
            ranked = sorted(candidates, key=lambda c: c['label'].casefold() not in names)
            for candidate in [c for c in ranked if c['provider'] == 'wikidata'][:2]:
                self.step('Read profile: ' + candidate['label'], lambda c=candidate: self.profile(c))
            linked = list(dict.fromkeys(claim['entity_id'] for profile in self.report['profiles'] for claim in profile['claims'] if claim['entity_id']))[:20]
            if linked:
                def label_entities():
                    url = 'https://www.wikidata.org/w/api.php?' + urlencode({'action': 'wbgetentities', 'ids': '|'.join(linked), 'props': 'labels', 'languages': 'en', 'format': 'json'})
                    fetched = self.fetch(url, 'Labels for candidate relationships', True)
                    entities = json.loads(fetched['raw'])['entities']
                    for profile in self.report['profiles']:
                        for claim in profile['claims']:
                            entity = entities.get(claim['entity_id'], {})
                            label = entity.get('labels', {}).get('en', {}).get('value')
                            if isinstance(label, str):
                                claim.update(value=label[:300], label_source_id=fetched['id'])
                    return {'id': fetched['id']}
                self.step('Resolve relationship labels', label_entities)
            wiki = next((c for c in ranked if c['provider'] == 'wikipedia'), None)
            if wiki:
                self.page(wiki['collection_url'])
            self.publish('Profiles collected', persist=True)
            news = self.step('Search recent news', lambda: collector.news(self.engine, self.case_id, self.case['name'], True))
            if news:
                self.report['articles'] = [{**a, 'source_id': news['id']} for a in news['articles']]
                for article in self.report['articles']:
                    self.allowed_urls.add(article['url'])
                for article in self.report['articles'][:2]:
                    page = self.page(article['url'])
                    if page.get('id'):
                        article['page_source_id'] = page['id']
            known_domains = []
            for value in ([self.case['name']] if self.case['focus_type'] == 'domain' else []) + self.case['identifiers']:
                if value.startswith('https://'):
                    self.allowed_urls.add(value)
                try:
                    domain = collector.domain_name(urlsplit(value).hostname if value.startswith('https://') else value.rsplit('@', 1)[-1])
                    known_domains.append(domain)
                    self.allowed_domains.add(domain)
                except (InvalidInput, ValueError):
                    continue
            for url in [v for v in self.case['identifiers'] if v.startswith('https://')][:2]:
                self.page(url)
            domain = next(iter(known_domains), None) or next(iter(sorted(self.allowed_domains)), None)
            if domain:
                self.report['domain_lead'] = {'domain': domain, 'basis': 'User supplied identifier' if known_domains else 'Unconfirmed candidate website claim'}
                if not known_domains and self.report['profiles']:
                    profile = self.report['profiles'][0]
                    self.report['domain_lead'].update(candidate_id=profile['candidate_id'], source_id=profile['source_id'])
                self.domain(domain)
            self.publish('Public collection finished', persist=True)
            if self.ai:
                self.check()
                from .ai import run_assistant
                self.publish('Local AI: reading sources and using tools')
                try:
                    self.report['ai'] = run_assistant(self.ai, self.report, self.dispatch, lambda: self.check())
                except InvalidInput as exc:
                    self.report['ai'] = {'status': 'failed', 'error': str(exc), 'source_ids': [], 'tool_calls': []}
            self.check()
            failures = sum(not step['ok'] for step in self.report['steps'])
            ai_failed = self.report.get('ai', {}).get('status') in {'failed', 'truncated', 'limited'}
            self.report['status'] = ('partial' if failures or ai_failed else 'complete') if self.report['source_ids'] else 'failed'
        except Halt:
            pass
        except Exception:
            # Preserve successful evidence and do not leak private tracebacks.
            self.report['status'] = 'partial' if self.report['source_ids'] else 'failed'
            self.report['error'] = 'Investigation stopped unexpectedly. Collected evidence is retained.'
        finally:
            self.report['finished_at'] = now()
            self.publish('Investigation ' + self.report['status'], persist=True)
        return copy.deepcopy(self.report)


def save_report(engine, report):
    return engine.ingest(report['case_id'], json.dumps(report, ensure_ascii=True).encode(),
                         'Investigation: ' + report['subject'], REPORT_SOURCE, 'json', extract=False)


class Jobs:
    """One owned worker; persisted checkpoints survive restart without resuming traffic."""
    def __init__(self, engine, enabled=False, ai=None):
        self.engine, self.enabled, self.ai = engine, enabled, ai
        self.lock = threading.RLock()
        self.active = None
        self.states = {}
        self.cancel = threading.Event()
        for case in engine.cases():
            report = engine.latest_investigation(case['id'])
            if report and report.get('status') == 'running':
                report.update(status='interrupted', stage='Interrupted by app restart', finished_at=now())
                report.pop('report_id', None)
                save_report(engine, report)

    def start(self, case_id, use_ai=False):
        if type(use_ai) is not bool:
            raise InvalidInput('Local AI selection must be true or false.')
        with self.lock:
            if self.active:
                current = self.states[self.active]
                if current['case_id'] == case_id:
                    return copy.deepcopy(current)
                raise InvalidInput('Another investigation is running. Stop it or wait for completion.')
            selected_ai = self.ai if use_ai else None
            if use_ai and (not selected_ai or selected_ai.status()['state'] != 'ready'):
                raise InvalidInput('Start local AI and wait until ready before selecting AI assistance.')
            self.cancel = threading.Event()
            run_id = uuid.uuid4().hex
            def progress(report):
                with self.lock:
                    self.states[run_id] = report
            runner = Investigation(self.engine, case_id, self.enabled, progress, self.cancel, selected_ai, run_id)
            runner.publish(persist=True)
            self.active = run_id
            def work():
                try:
                    runner.run()
                finally:
                    with self.lock:
                        self.active = None
            try:
                threading.Thread(target=work, name='osint-investigation', daemon=True).start()
            except BaseException:
                self.active = None
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
