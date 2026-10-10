"""Case clues compared locally; only match categories leave this helper."""
import re
from urllib.parse import urlsplit


def words(text):
    return re.findall(r'[^\W_]+', text.casefold(), re.UNICODE)


def local_match(case, text, url=''):
    normalized = ' ' + ' '.join(words(text)) + ' '
    name_match = any(' ' + ' '.join(words(name)) + ' ' in normalized
                     for name in [case['name'], *case.get('aliases', [])] if words(name))
    emails = set(re.findall(r'[\w.+-]+@[\w.-]+\.[a-z]{2,}', text.casefold()))
    numbers = {re.sub(r'\D', '', value) for value in re.findall(r'\+?\d[\d\s().-]{5,}\d', text)}
    signals = []
    for index, clue in enumerate(case.get('identifiers', [])):
        clue_emails = re.findall(r'[\w.+-]+@[\w.-]+\.[a-z]{2,}', clue.casefold())
        digits = re.sub(r'\D', '', clue)
        if clue_emails:
            match, kind, strength = bool(set(clue_emails) & emails), 'email', 'strong'
        elif clue.startswith('https://'):
            parsed = urlsplit(clue)
            target = urlsplit(url)
            match = (bool(parsed.hostname) and target.scheme == 'https'
                     and parsed.hostname == target.hostname
                     and parsed.netloc.casefold() == target.netloc.casefold()
                     and parsed.path.rstrip('/') == target.path.rstrip('/')
                     and parsed.query == target.query and parsed.fragment == target.fragment)
            kind, strength = 'known_url', 'strong'
        elif len(digits) >= 7 and len(words(clue)) <= 5:
            match, kind, strength = digits in numbers, 'phone', 'strong'
        else:
            tokens = words(clue)
            match = bool(tokens) and ' ' + ' '.join(tokens) + ' ' in normalized
            kind, strength = 'context', 'context'
        if match:
            signals.append({'kind': kind, 'identifier_index': index, 'strength': strength})
    strong = any(s['strength'] == 'strong' for s in signals)
    level = 'identifier_match' if strong else 'context_match' if signals else 'name_only' if name_match else 'unrelated'
    summaries = {'identifier_match': 'Saved identifier matches this public source locally; verify attribution.',
                 'context_match': 'Saved context matches locally; identity remains unconfirmed.',
                 'name_only': 'Name match only; no saved case clue corroborates this lead.',
                 'unrelated': 'No name or saved case clue matches this source.'}
    return {'level': level, 'signals': signals, 'summary': summaries[level]}


def rank_key(lead):
    match = lead.get('local_match', {})
    base = {'identifier_match': 100, 'context_match': 40, 'name_only': 10, 'unrelated': 0}.get(match.get('level'), 0)
    hint = {'context_consistent': 15, 'context_unlikely': -20}.get(match.get('model_assessment'), 0)
    return base + (0 if base == 100 else hint)
