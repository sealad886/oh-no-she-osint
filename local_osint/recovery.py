"""Finite public-query alternatives for deterministic investigation recovery."""

from itertools import combinations
import re

from .relevance import words


FOCUS_KEYWORDS = {
    'person': ('profile', 'interview', 'portfolio', 'official website', 'news'),
    'organization': ('official website', 'company', 'news', 'research'),
    'domain': ('official website', 'news', 'company'),
    'topic': ('research', 'news', 'about'),
    'other': ('official website', 'about', 'news', 'research'),
}
SOURCE_KEYWORDS = ('official website', 'about', 'news', 'research', 'profile', 'interview', 'company', 'portfolio')
SEARCH_WORDS = frozenset(word for keyword in SOURCE_KEYWORDS for word in words(keyword))
MAX_RECOVERY_QUERIES = 256


def recovery_queries(case, options):
    """Return deduplicated public queries; saved private identifiers are never read."""
    names = list(dict.fromkeys(name.strip() for name in [case['name'], *case.get('aliases', [])]
                              if isinstance(name, str) and name.strip()))[:11]
    result, seen = [], set()

    def add(name, suffix, strategy):
        query = '"' + name + '"' + (' ' + suffix if suffix else '')
        key = ' '.join(query.casefold().split())
        if len(query) <= 200 and key not in seen and len(result) < MAX_RECOVERY_QUERIES:
            seen.add(key)
            result.append({'query': query, 'strategy': strategy})

    def fragments(value):
        if not isinstance(value, str) or not value.strip():
            return []
        full = ' '.join(value.split())
        tokens = list(dict.fromkeys(token for token in words(full) if len(token) >= 2))[:10]
        # Keep the supplied context, then progressively drop optional constraints.
        return list(dict.fromkeys([full, *(' '.join(pair) for pair in zip(tokens, tokens[1:])), *tokens]))

    public_query = options.get('query', '')
    if not isinstance(public_query, str):
        public_query = ''
    # Avoid asking for the same name twice when the configured public query already contains it.
    for name in sorted(names, key=len, reverse=True):
        public_query = re.sub(r'"' + re.escape(name) + r'"', ' ', public_query, flags=re.IGNORECASE)
        public_query = re.sub(r'(?<!\w)' + re.escape(name) + r'(?!\w)', ' ', public_query, flags=re.IGNORECASE)
    context = fragments(options.get('context', ''))
    query_context = fragments(public_query)
    preferred = FOCUS_KEYWORDS.get(case.get('focus_type'), FOCUS_KEYWORDS['other'])
    keywords = tuple(dict.fromkeys([*preferred, *SOURCE_KEYWORDS]))

    for name in names:
        add(name, '', 'broad_exact_name')
    for source, alternatives in [('public_context', context), ('public_query', query_context)]:
        for alternative in alternatives:
            for name in names:
                add(name, alternative, source)
    for keyword in keywords:
        for name in names:
            add(name, keyword, 'focus_source' if keyword in preferred else 'alternative_public_source')
    for left, right in combinations(keywords, 2):
        for name in names:
            add(name, left + ' ' + right, 'combine_public_sources')
    for alternative in list(dict.fromkeys([*context, *query_context]))[:8]:
        for keyword in preferred:
            for name in names:
                add(name, alternative + ' ' + keyword, 'public_context_source')
    return result
