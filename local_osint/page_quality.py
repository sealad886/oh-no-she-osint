"""Assess readable public content without executing HTML or contacting a site."""

from html.parser import HTMLParser
import re


SKIP_TAGS = {'head', 'title', 'script', 'style', 'noscript', 'template', 'svg',
             'nav', 'header', 'footer', 'form', 'button', 'select', 'textarea'}
VOID_TAGS = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
             'meta', 'param', 'source', 'track', 'wbr'}
BLOCK_TAGS = {'article', 'section', 'main', 'div', 'p', 'li', 'dd', 'dt', 'td',
              'tr', 'address', 'h1', 'h2', 'h3', 'h4', 'blockquote', 'br'}
MAX_TEXT = 32000


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.parts = []
        self.size = 0
        self.password = False
        self.article = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        hidden = 'hidden' in attrs or attrs.get('aria-hidden', '').lower() == 'true'
        style = attrs.get('style', '').lower().replace(' ', '')
        hidden = hidden or 'display:none' in style or 'visibility:hidden' in style
        roles = attrs.get('role', '').lower()
        identity = attrs.get('id', '') + ' ' + attrs.get('class', '')
        overlay = bool(re.search(r'(?:^|[\s_-])(?:cookie|consent)[\w-]*(?:[\s_-]|$)', identity, re.I))
        chrome = tag in SKIP_TAGS - {'form'} or hidden or overlay or roles in {'navigation', 'banner', 'contentinfo'}
        chrome = chrome or (bool(self.stack) and self.stack[-1][2])
        if tag == 'input' and attrs.get('type', '').lower() == 'password' and not chrome:
            self.password = True
        skipped = chrome or tag == 'form'
        skipped = skipped or (bool(self.stack) and self.stack[-1][1])
        if tag == 'article' and not skipped:
            self.article = True
        if not skipped and tag in BLOCK_TAGS:
            self.append('\n')
        if tag not in VOID_TAGS:
            self.stack.append((tag, skipped, chrome))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                skipped = self.stack[index][1]
                del self.stack[index:]
                if not skipped and tag in BLOCK_TAGS:
                    self.append('\n')
                break

    def append(self, data):
        if self.size < MAX_TEXT:
            part = data[:MAX_TEXT - self.size]
            self.parts.append(part)
            self.size += len(part)

    def handle_data(self, data):
        if not self.stack or not self.stack[-1][1]:
            self.append(data)


def readable_text(value):
    """Exclude URL-only boilerplate without discarding link labels or emails."""
    value = re.sub(r'https?://\S+|www\.\S+', '', value, flags=re.I)
    lines = [' '.join(line.split()) for line in value.splitlines()]
    return '\n'.join(line for line in lines if line)[:MAX_TEXT]


def assess_page(raw, format):
    """Return a conservative usefulness decision and locally readable text.

    This is an HTML/text heuristic, not a rendered-browser or identity check.
    A failed decision does not authorize deleting the original source evidence.
    """
    if not isinstance(raw, bytes) or len(raw) > 2 * 1024 * 1024:
        return {'useful': False, 'reason': 'Page is empty, unsupported or exceeds the evidence size limit.', 'text': ''}
    try:
        decoded = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        return {'useful': False, 'reason': 'Page does not contain supported UTF-8 text.', 'text': ''}
    password, article = False, False
    if format == 'html':
        parser = VisibleText()
        parser.feed(decoded)
        parser.close()
        text = readable_text(''.join(parser.parts))
        password, article = parser.password, parser.article
    elif format in {'text', 'csv', 'json'}:
        text = readable_text(decoded)
    else:
        return {'useful': False, 'reason': 'This source format has no supported readable public content.', 'text': ''}
    lower = text.casefold()
    words = re.findall(r'[^\W_]+', text, re.UNICODE)
    challenge = any(phrase in lower for phrase in ('verify you are human', 'checking your browser',
        'complete the captcha', 'enable javascript and cookies to continue', 'security verification',
        'access denied', 'just a moment'))
    login = any(phrase in lower for phrase in ('log in to continue', 'login to continue',
        'sign in to continue', 'log in to view', 'login to view', 'sign in to view',
        'must be logged in', 'join linkedin', 'sign in to linkedin', 'log into facebook',
        'log in or sign up', 'sign up for facebook'))
    directory = any(phrase in lower for phrase in ('people named', 'profiles of people named',
        'join facebook to connect', 'facebook helps you connect'))
    # A real article can discuss authentication; a login control in its chrome
    # must not outweigh a substantial article body.
    substantial_article = article and len(words) >= 100
    if challenge and not substantial_article:
        reason = 'Source returned a verification or access barrier, not useful public content.'
    elif (login or password) and not substantial_article:
        reason = 'Source returned a login or authentication wall; no useful public profile was collected.'
    elif directory and not substantial_article:
        reason = 'Source returned a generic people directory or empty profile shell, not substantive profile content.'
    elif len(words) >= 8 and len(text) >= 60:
        return {'useful': True, 'reason': 'Readable public content collected; identity and relevance still require checking.', 'text': text}
    elif len(words) >= 4 and len(text) >= 25 and (re.search(r'\b[^\s@]+@[^\s@]+\.[^\s@]+', text) or re.search(r'\+?\d[\d\s().-]{7,}\d', text)):
        return {'useful': True, 'reason': 'Short public contact content collected; identity and relevance still require checking.', 'text': text}
    else:
        reason = 'Source contains too little readable public content; headings, navigation and URL metadata do not count as a useful page.'
    return {'useful': False, 'reason': reason, 'text': text}
