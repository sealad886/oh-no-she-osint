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

REPORT_SOURCE = 'local-osint:investigation-report'
PROPERTIES = {'P31': 'Type', 'P856': 'Official website (candidate claim)', 'P17': 'Country',
              'P159': 'Headquarters', 'P112': 'Founder', 'P127': 'Owner', 'P355': 'Subsidiary',
              'P361': 'Part of', 'P106': 'Occupation', 'P108': 'Employer', 'P69': 'Education',
              'P800': 'Notable work', 'P569': 'Date of birth', 'P570': 'Date of death'}
QUALIFIERS = {'P580': 'Start time', 'P582': 'End time', 'P585': 'Point in time',
              'P518': 'Applies to part', 'P407': 'Language', 'P459': 'Method',
              'P1480': 'Sourcing circumstances', 'P7452': 'Reason for preferred rank'}
SOURCES = {'web_search', 'wikipedia', 'wikidata', 'news', 'domain'}


def options_for(case, values=None):
    values = {} if values is None else values
    if not isinstance(values, dict) or set(values) - {'query', 'context', 'tools', 'max_requests', 'page_limit'}:
        raise InvalidInput('Invalid investigation settings.')
    result = {'query': field(values.get('query', ''), 'Public search query', 200, False) or '"' + case['name'] + '"',
              'context': field(values.get('context', ''), 'Public search context', 200, False),
              'tools': values.get('tools', ['web_search', 'wikipedia', 'wikidata', 'news', 'domain']),
              'max_requests': values.get('max_requests', 24), 'page_limit': values.get('page_limit', 4)}
    if not isinstance(result['tools'], list) or not result['tools'] or any(not isinstance(t, str) or t not in SOURCES for t in result['tools']) or len(set(result['tools'])) != len(result['tools']):
        raise InvalidInput('Select at least one supported source, without duplicates.')
    for name, maximum in [('max_requests', 64), ('page_limit', 8)]:
        if type(result[name]) is not int or not 1 <= result[name] <= maximum:
            raise InvalidInput(f'{name} must be a whole number from 1 to {maximum}.')
    field(result['query'] + (' ' + result['context'] if result['context'] else ''), 'Combined public query and context', 200)
    return result


def words(value):
    return re.findall(r'[^\W_]+', value.casefold(), re.UNICODE)


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
    def __init__(self, engine, case_id, enabled=False, progress=None, cancel=None, ai=None, run_id=None, deadline_seconds=300, options=None, previous=None, local_only=False):
        if not enabled and not local_only:
            raise InvalidInput('Investigation collection is off. Restart with --allow-network.')
        with engine.connection() as db:
            self.case = engine.require_case(db, case_id)
        self.engine, self.case_id, self.progress, self.ai, self.enabled = engine, case_id, progress, ai, enabled
        self.cancel = cancel or threading.Event()
        self.options = options_for(self.case, options)
        self.deadline = time.monotonic() + deadline_seconds
        self.used = 0
        self.allowed_urls, self.allowed_domains = set(), set()
        self.pages, self.domains = {}, {}
        self.report = {'report_type': 'investigation', 'run_id': run_id or uuid.uuid4().hex,
                       'case_id': case_id, 'subject': self.case['name'], 'aliases': self.case['aliases'], 'focus_type': self.case['focus_type'],
                       'started_at': now(), 'status': 'running', 'stage': 'Starting', 'steps': [],
                       'profiles': [], 'candidates': [], 'articles': [], 'source_ids': [],
                       'interpretation': 'Name matches are unconfirmed candidates. Claims below belong to each candidate, not automatically to your subject.'}
        self.report.update(options=self.options, web_results=[], pages=[], stop_reason=None,
                           network_enabled=enabled,
                           budget={'used': 0, 'limit': self.options['max_requests']})
        if previous:
            self.report['parent_run_id'] = previous['run_id']
            for key in ['profiles', 'candidates', 'articles', 'source_ids', 'steps', 'web_results', 'pages', 'domain_lead', 'ai']:
                if key in previous:
                    self.report[key] = copy.deepcopy(previous[key])
            self.allowed_urls.update(x['url'] for x in self.report['web_results'] if x.get('relevant'))
            self.allowed_urls.update(x['collection_url'] for x in self.report['candidates'] if x.get('collection_url') and x.get('relevant'))
            for page in self.report['pages']:
                self.allowed_urls.update(x['url'] for x in page.get('links', []))
                self.allowed_urls.add(page['url'])
            if self.report.get('domain_lead'):
                self.allowed_domains.add(self.report['domain_lead']['domain'])
        self.start_step = len(self.report['steps'])
        self.origin = 'engine'

    def check(self, cost=0):
        if self.cancel.is_set():
            self.report['status'] = 'cancelled'
            self.report['stop_reason'] = 'Stopped by you. Sources already collected are retained.'
            raise Halt()
        if time.monotonic() >= self.deadline or self.used + cost > self.options['max_requests']:
            self.report['status'] = 'limited'
            self.report['stop_reason'] = 'Time limit reached; retry a tool to continue.' if time.monotonic() >= self.deadline else 'Request ceiling reached; raise it or retry a tool to continue.'
            raise Halt()
        self.used += cost

    def publish(self, stage=None, persist=False):
        if stage:
            self.report['stage'] = stage
        self.report['request_units'] = self.used
        self.report['budget']['used'] = self.used
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
        entry = {'id': uuid.uuid4().hex, 'run_id': self.report['run_id'], 'label': label, 'ok': False,
                 'tool': tool, 'arguments': arguments or {}, 'status': 'running', 'origin': self.origin, 'started_at': now()}
        self.report['steps'].append(entry)
        started = time.monotonic()
        self.report['active_tool'] = entry['id']
        self.publish(label)
        try:
            result = callback()
            self.remember(result)
            entry.update(ok=True, status='success', result_summary=str(result.get('summary', 'Evidence saved.' if result.get('id') else 'Tool finished.'))[:300])
            if result.get('status') in {'failed', 'truncated', 'limited'}:
                entry.update(ok=False, status='failed', error=result.get('warning') or 'Local AI did not complete its draft. Retry analysis.')
            if result.get('id'):
                entry['source_id'] = result['id']
            return result
        except (InvalidInput, OSError, http.client.HTTPException, ValueError, KeyError, TypeError, RecursionError) as exc:
            entry['error'] = str(exc) if isinstance(exc, InvalidInput) else 'Source unavailable or response could not be interpreted.'
            entry['status'] = 'failed'
            if getattr(exc, 'evidence_id', None):
                self.remember({'id': exc.evidence_id})
                entry['source_id'] = exc.evidence_id
            return None
        finally:
            if entry['status'] == 'running':
                entry['status'] = 'cancelled'
            entry.update(finished_at=now(), duration_ms=round((time.monotonic() - started) * 1000))
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
        query = field(query, 'Web search query', 200)
        allowed = set(words(self.case['name'] + ' ' + ' '.join(self.case['aliases']) + ' ' + self.options['query'] + ' ' + self.options['context']))
        allowed.update('profile linkedin github company news interview about contact research portfolio official website biography'.split())
        for domain in self.allowed_domains:
            allowed.update(words(domain))
        for url in self.allowed_urls:
            allowed.update(words(urlsplit(url).hostname or ''))
        allowed.add('site')
        if not self.relevant(query) or set(words(query)) - allowed or any(c in query for c in '@\\\n\r'):
            raise InvalidInput('AI searches must use the subject name and approved public context or source domains.')
        return query

    def web_search(self, query):
        if not self.enabled:
            raise InvalidInput('Web collection is off for this app session.')
        result = self.step('Web search: ' + query, lambda: collector.web_search(self.engine, self.case_id, query, True),
                           tool='web_search', arguments={'query': query})
        if result:
            current_results = []
            for item in result['results']:
                match = self.relevant(item['title'], item['snippet'])
                entry = {**item, 'source_id': result['id'], 'relevant': match,
                         'reason': 'Name or alias appears in title/snippet; identity unconfirmed.' if match else 'No complete name/alias match; not followed automatically.'}
                current_results.append(entry)
                existing = next((x for x in self.report['web_results'] if x['url'] == item['url']), None)
                if existing:
                    existing.update(entry)
                else:
                    self.report['web_results'].append(entry)
                if match:
                    self.allowed_urls.add(item['url'])
            self.report['web_results'] = self.report['web_results'][:40]
            self.publish()
            return {**result, 'results': current_results}
        return {'error': 'Web search unavailable. See its tool activity and retry.', 'results': []}

    def fetch(self, url, title, allow_query=False):
        raw, format, canonical = collector.fetch_public(url, allow_query=allow_query)
        result = self.engine.ingest(self.case_id, raw, collector.generated_title(title), canonical, format)
        self.remember(result)
        return {**result, 'raw': raw, 'format': format}

    def page(self, url):
        if not self.enabled:
            raise InvalidInput('Web collection is off for this app session.')
        if url not in self.allowed_urls:
            raise InvalidInput('Only URLs discovered for this investigation may be collected.')
        if url in self.pages:
            return self.cached_tool('Reuse page result: ' + url, self.pages[url], 'collect_page', {'url': url})
        if self.page_count() >= self.options['page_limit']:
            raise InvalidInput('Page limit reached. Increase it for the next investigation or run this page tool yourself.')
        def collect_page():
            saved = collector.collect(self.engine, self.case_id, url, True, allow_query=True)
            self.remember(saved)
            evidence = self.engine.evidence(self.case_id, saved['id'])
            links = []
            if evidence['format'] == 'html':
                with self.engine.connection() as db:
                    raw = db.execute('SELECT raw FROM evidence WHERE id=? AND case_id=?', (saved['id'], self.case_id)).fetchone()['raw']
                parser = PageLinks(evidence['source'])
                parser.feed(raw.decode('utf-8-sig'))
                for link in parser.links:
                    if not self.relevant(link['title']) and not any(w in words(link['title']) for w in ['about', 'profile', 'biography', 'team']):
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
                    'excerpt': evidence['text'][:1800], 'links': links}
            self.report['pages'] = [p for p in self.report['pages'] if p['url'] != url] + [page]
            for lead in self.report['web_results']:
                if lead['url'] == url:
                    lead['page_source_id'] = saved['id']
            return {**saved, 'url': evidence['source'], 'links': links, 'summary': 'Public page saved; ' + str(len(links)) + ' follow-up links found.'}
        result = self.step('Collect page: ' + url, collect_page, tool='collect_page', arguments={'url': url})
        self.pages[url] = result or {'error': self.report['steps'][-1].get('error', 'Page collection failed.')}
        return self.pages[url]

    def cached_tool(self, label, result, tool, arguments):
        def reuse():
            if result.get('error'):
                raise InvalidInput(result['error'])
            return {**result, 'summary': 'Reused this run\'s saved tool result; no outbound request.'}
        return self.step(label, reuse, 0, tool, arguments) or result

    def page_count(self):
        return sum(bool(result.get('id')) for result in self.pages.values())

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

    def dispatch(self, name, arguments):
        self.check()
        if not isinstance(arguments, dict):
            raise InvalidInput('Tool arguments must be an object.')
        old_origin = self.origin
        self.origin = 'ai'
        try:
            if name == 'web_search' and set(arguments) == {'query'} and 'web_search' in self.options['tools']:
                return self.web_search(self.model_query(arguments['query']))
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
                self.step('Blocked AI tool: ' + name, blocked, 0, name, arguments)
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
            result = run_assistant(self.ai, self.report, self.dispatch, lambda: self.check(),
                                   activity=lambda message: self.publish(message))
            self.report['ai'] = result
            return {'summary': 'Local AI ' + result['status'] + '.', **result}
        result = self.step('Local AI: planning searches and following public sources', analyze, 0, 'analyze', {})
        if result is None:
            entry = next(s for s in reversed(self.report['steps']) if s.get('tool') == 'analyze')
            trace = [{'tool': s['tool'], 'arguments': s['arguments'], 'ok': s['ok'],
                      'result': s.get('error') or s.get('result_summary', '')}
                     for s in self.report['steps'][self.start_step:] if s.get('origin') == 'ai']
            self.report['ai'] = {'status': 'failed', 'error': entry.get('error', 'Local AI unavailable. Retry AI analysis.'), 'source_ids': [], 'tool_calls': trace}
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
        if 'web_search' in selected:
            query = self.options['query'] + (' ' + self.options['context'] if self.options['context'] else '')
            self.web_search(query)
            matching = [x for x in self.report['web_results'] if x['relevant']]
            if not matching and self.case['aliases']:
                self.web_search('"' + self.case['aliases'][0] + '"')
                matching = [x for x in self.report['web_results'] if x['relevant']]
            automatic_pages = max(1, self.options['page_limit'] - (1 if self.ai else 0))
            for lead in matching:
                if self.page_count() >= automatic_pages:
                    break
                self.page(lead['url'])
            if not matching:
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
        for value in ([self.case['name']] if self.case['focus_type'] == 'domain' else []) + self.case['identifiers']:
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
        for url in [v for v in self.case['identifiers'] if v.startswith('https://')][:2]:
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
        if self.ai:
            self.analyze()

    def user_tool(self, name, args):
        if not isinstance(args, dict):
            raise InvalidInput('Tool inputs must be an object.')
        self.origin = 'user'
        if name == 'web_search' and set(args) == {'query'}:
            query = field(args['query'], 'Public search query', 200)
            self.options.update(query=query, context='')
            return self.web_search(query)
        if name == 'search_subject' and set(args) == {'query', 'provider'} and args['provider'] in {'wikipedia', 'wikidata'}:
            return self.search_subject(field(args['query'], 'Public search query', 200), args['provider'])
        if name == 'news_search' and set(args) == {'query'}:
            return self.news_search(field(args['query'], 'Public news query', 200))
        if name == 'collect_page' and set(args) == {'url'}:
            url = field(args['url'], 'Public page URL', 2000)
            collector.public_url(url, allow_query=True)
            self.allowed_urls.add(url)
            return self.page(url)
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
            if action:
                self.user_tool(action['tool'], action['arguments'])
            else:
                self.pipeline()
            self.check()
            current = self.report['steps'][self.start_step:]
            failures = sum(step.get('status') == 'failed' for step in current)
            ai_failed = self.report.get('ai', {}).get('status') in {'failed', 'truncated', 'limited'} if self.ai else False
            self.report['status'] = ('partial' if failures or ai_failed else 'complete') if self.report['source_ids'] else 'failed'
            self.report['stop_reason'] = ('Selected tool finished; prior evidence retained.' if action else 'Selected sources and relevant follow-ups finished.') + (' Some tools were unavailable; edit or retry them below.' if failures or ai_failed else '') + ' Request ceiling is a limit, not a target.'
        except Halt:
            pass
        except InvalidInput as exc:
            self.report['status'] = 'partial' if self.report['source_ids'] else 'failed'
            self.report['error'] = str(exc)
            self.report['stop_reason'] = str(exc)
        except Exception:
            self.report['status'] = 'partial' if self.report['source_ids'] else 'failed'
            self.report['error'] = 'Investigation stopped unexpectedly. Collected evidence is retained.'
            self.report['stop_reason'] = self.report['error']
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

    def start(self, case_id, use_ai=False, options=None, action=None):
        if type(use_ai) is not bool:
            raise InvalidInput('Local AI selection must be true or false.')
        if action and (not isinstance(action, dict) or set(action) != {'tool', 'arguments'} or action['tool'] not in {'web_search', 'search_subject', 'news_search', 'collect_page', 'domain_signals', 'read_evidence', 'analyze'} or not isinstance(action['arguments'], dict)):
            raise InvalidInput('Unsupported tool or inputs.')
        if action and action['tool'] == 'analyze':
            use_ai = True
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
            previous = self.engine.latest_investigation(case_id) if action else None
            selected_options = options if options is not None else previous.get('options') if previous else None
            runner = Investigation(self.engine, case_id, self.enabled, progress, self.cancel, selected_ai, run_id,
                                   options=selected_options, previous=previous,
                                   local_only=bool(action and action['tool'] in {'read_evidence', 'analyze'}))
            runner.publish(persist=True)
            self.active = run_id
            def work():
                try:
                    runner.run(action)
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
