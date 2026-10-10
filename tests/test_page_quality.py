import unittest

from local_osint.page_quality import assess_page


class PageQualityTests(unittest.TestCase):
    def test_useful_biography_preserves_body_without_metadata_or_navigation(self):
        raw = b'''<html><head><title>Ordinary Person - Profile</title>
        <meta name="description" content="Incorrect metadata"/><script>secret = "unrelated@example.org"</script></head>
        <body><header>Generic site heading</header><nav>Log in Search Privacy</nav>
        <main><h1>Ordinary Person</h1><p>Ordinary Person works at Example Ambulance Service in Dublin.
        Their public biography describes professional training and community first aid work.</p>
        <p>Read <a href="https://example.org/about?tracking=secret">About this person</a>.</p></main>
        <footer>Cookies and privacy</footer></body></html>'''
        result = assess_page(raw, 'html')
        self.assertTrue(result['useful'])
        self.assertIn('Example Ambulance Service', result['text'])
        self.assertIn('About this person', result['text'])
        for excluded in ['Incorrect metadata', 'unrelated@example.org', 'Generic site heading', 'tracking=secret', 'Cookies and privacy']:
            self.assertNotIn(excluded, result['text'])

    def test_login_form_is_not_useful_even_with_named_title_and_large_scripts(self):
        raw = b'<title>Ordinary Person profile</title><h1>Sign in to continue</h1><form><label>Email</label><input type="password"/><p>Log in to view this profile.</p></form><script>' + b'Ordinary Person ' * 1000 + b'</script>'
        result = assess_page(raw, 'html')
        self.assertFalse(result['useful'])
        self.assertIn('login', result['reason'])
        self.assertNotIn('Ordinary Person', result['text'])

    def test_empty_social_directory_shell_does_not_count_as_profile(self):
        raw = b'<h1>People named Ordinary Person</h1><p>View the profiles of people named Ordinary Person. Join Facebook to connect with Ordinary Person and others you may know.</p><nav>Pages Groups Watch Privacy Terms</nav>'
        result = assess_page(raw, 'html')
        self.assertFalse(result['useful'])
        self.assertIn('directory', result['reason'])

    def test_article_login_navigation_and_cookie_overlay_keep_substantive_body(self):
        raw = b'''<nav><a>Sign in to continue</a><form><input type="password"/></form></nav><div class="cookie-banner">Accept all cookies.
        Sign in to continue using our website.</div><article><h1>Community work by Ordinary Person</h1>
        <p>Ordinary Person organised public first aid training for local volunteers in Dublin.
        The community group described their work with schools and published details of upcoming sessions.</p></article>'''
        result = assess_page(raw, 'html')
        self.assertTrue(result['useful'])
        self.assertNotIn('Sign in to continue', result['text'])
        self.assertIn('upcoming sessions', result['text'])

    def test_short_public_contact_page_is_useful(self):
        raw = b'<main><h1>Ordinary Person</h1><address>Public office: ordinary@example.org<br>Dublin, Ireland.</address></main>'
        result = assess_page(raw, 'html')
        self.assertTrue(result['useful'])
        self.assertIn('ordinary@example.org', result['text'])

    def test_heading_only_urls_hidden_text_and_verification_fail(self):
        for raw in [b'<title>Ordinary Person public biography</title><h1>Ordinary Person</h1>',
                    b'<div>https://example.org/' + b'large-path' * 100 + b'</div>',
                    b'<div hidden>Ordinary Person has a rich public biography with many details and a named employer in Dublin.</div>',
                    b'<h1>Verify you are human</h1><p>Complete the captcha to access this page.</p>']:
            with self.subTest(raw=raw[:80]):
                self.assertFalse(assess_page(raw, 'html')['useful'])

    def test_plain_contact_and_invalid_format_encoding_size(self):
        self.assertTrue(assess_page(b'Ordinary Person public office: ordinary@example.org Dublin Ireland.', 'text')['useful'])
        for raw, format in [(b'', 'html'), (b'\xff', 'html'), (b'Ordinary Person public biography contains enough details to read.', 'binary'), (b'x' * (2 * 1024 * 1024 + 1), 'html')]:
            with self.subTest(format=format, size=len(raw)):
                result = assess_page(raw, format)
                self.assertFalse(result['useful'])
                self.assertTrue(result['reason'])

    def test_readable_output_bound_and_authentication_article(self):
        article = '<article><h1>How login works</h1><p>' + ('A public technical article explains why visitors must be logged in for private account settings. ' * 2000) + '</p></article>'
        result = assess_page(article.encode(), 'html')
        self.assertTrue(result['useful'])
        self.assertLessEqual(len(result['text']), 32000)
