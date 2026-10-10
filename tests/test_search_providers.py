import base64
from html import escape
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from local_osint.collector import SEARCH_PROVIDERS, fetch_public, parse_search, web_search
from local_osint.engine import Engine, InvalidInput, MAX_BYTES


def bing_result(url="https://example.org/entity", title="Ordinary Entity", snippet="Public source", wrapped=True):
    if wrapped:
        encoded = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
        url = "https://www.bing.com/ck/a?!&u=a1" + encoded + "&ntb=1"
    return ('<li class="b_algo"><h2><a href="' + escape(url, quote=True) + '">' + title +
            '</a></h2><div class="b_caption"><p>' + snippet + '</p></div></li>').encode()


class SearchProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.engine = Engine(Path(self.temp.name) / "private")
        self.case = self.engine.create_case("Ordinary Entity", focus_type="organization")["id"]

    def tearDown(self):
        self.temp.cleanup()

    def test_provider_switch_contacts_distinct_fixed_engine_and_preserves_original(self):
        fixtures = {
            "duckduckgo": b'<a class="result-link" href="https://example.org/entity">Ordinary Entity</a>',
            "bing": bing_result(),
        }
        hosts = []
        for provider, raw in fixtures.items():
            with patch("local_osint.collector.fetch_public", side_effect=lambda url, **kwargs: (raw, "html", url)) as fetch:
                result = web_search(self.engine, self.case, '"Ordinary Entity" official', True, provider)
            destination = fetch.call_args.args[0]
            hosts.append(urlsplit(destination).hostname)
            self.assertEqual(parse_qs(urlsplit(destination).query)["q"], ['"Ordinary Entity" official'])
            self.assertTrue(fetch.call_args.kwargs["allow_query"])
            self.assertEqual(result["provider"], provider)
            self.assertEqual(result["results"][0]["url"], "https://example.org/entity")
            source = self.engine.evidence(self.case, result["id"])
            self.assertEqual(source["source"], destination)
            exported = next(row for row in self.engine.export(self.case)["evidence"] if row["id"] == result["id"])
            self.assertEqual(base64.b64decode(exported["original_base64"]), raw)
        self.assertEqual(len(set(hosts)), len(SEARCH_PROVIDERS))

    def test_unknown_provider_offline_invalid_case_and_query_never_fetch(self):
        with patch("local_osint.collector.fetch_public") as fetch, patch("local_osint.collector.socket.getaddrinfo") as dns:
            for provider in ["unknown", "https://private.test/", [], None]:
                with self.assertRaises(InvalidInput):
                    web_search(self.engine, self.case, "Ordinary Entity", True, provider)
            for case, query, enabled in [(self.case, "Ordinary Entity", False), ("missing", "Ordinary Entity", True), (self.case, "", True), (self.case, "x" * 201, True)]:
                with self.assertRaises(InvalidInput):
                    web_search(self.engine, case, query, enabled, "bing")
            fetch.assert_not_called()
            dns.assert_not_called()

    def test_bing_organic_titles_snippets_and_bounds_ignore_other_links(self):
        raw = b'<li class="b_ad"><h2><a href="https://ads.example.org/">Sponsored Entity</a></h2></li>'
        raw += bing_result(title="Ordinary <strong>Entity</strong> &amp; partners", snippet="Public <em>record</em> &amp; source")
        raw += b"".join(bing_result(f"https://example.org/{number}", "t" * 1000, "s" * 2000, wrapped=False) for number in range(15))
        results = parse_search(raw, "bing")
        self.assertEqual(results[0], {"title": "Ordinary Entity & partners", "snippet": "Public record & source", "url": "https://example.org/entity"})
        self.assertEqual(len(results), 10)
        self.assertTrue(all(len(row["title"]) <= 300 and len(row["snippet"]) <= 1000 for row in results))
        self.assertFalse(any("ads.example.org" in row["url"] for row in results))

    def test_bing_wrappers_duplicates_and_unsafe_destinations_never_become_leads(self):
        raw = bing_result("https://example.org/entity#section") + bing_result()
        for url in ["http://example.org/", "https://127.0.0.1/", "https://user:password@example.org/", "https://host.internal/", "https://[::1]/", "https://example.org:8443/", "https://www.bing.com/search?q=x"]:
            raw += bing_result(url)
        for url in ["/ck/a?u=invalid", "/ck/a?u=a1%%%", "/ck/a?u=a1aHR0cHM6Ly9leGFtcGxlLm9yZy8&u=a1other", "/ck/a?u=a1_w"]:
            raw += bing_result(url, wrapped=False)
        with patch("local_osint.collector.socket.getaddrinfo") as dns:
            results = parse_search(raw, "bing")
        self.assertEqual(results, [{"title": "Ordinary Entity", "snippet": "Public source", "url": "https://example.org/entity"}])
        dns.assert_not_called()

    def test_bing_challenges_bad_format_and_changed_markup_preserve_failure_source(self):
        for raw, format in [(b'<div id="b_captcha">Human check</div>', "html"), (b"<html>Gateway message</html>", "html"), (b"not html", "text")]:
            with self.subTest(raw=raw), patch("local_osint.collector.fetch_public", return_value=(raw, format, "https://www.bing.com/search?q=Ordinary+Entity")):
                with self.assertRaises(InvalidInput) as caught:
                    web_search(self.engine, self.case, "Ordinary Entity", True, "bing")
            saved = self.engine.evidence(self.case, caught.exception.evidence_id)
            self.assertEqual(saved["source"], "https://www.bing.com/search?q=Ordinary+Entity")
        self.assertEqual(parse_search(b'<li class="b_no">There are no results for this query</li>', "bing"), [])
        for raw in [b"\xff", b"x" * (MAX_BYTES + 1)]:
            with self.assertRaises(InvalidInput):
                parse_search(raw, "bing")

    def test_bing_fetch_still_uses_validated_pinned_public_https(self):
        raw = bing_result()
        response = Mock(status=200)
        headers = {"Content-Type": "text/html; charset=utf-8", "Content-Length": str(len(raw))}
        response.getheader.side_effect = lambda key, default=None: headers.get(key, default)
        response.read1.side_effect = [raw, b""]
        with patch("local_osint.collector.target", return_value=("www.bing.com", "8.8.8.8", "/search?q=Ordinary+Entity", "https://www.bing.com/search?q=Ordinary+Entity")), patch("local_osint.collector.PinnedHTTPS") as connection:
            connection.return_value.getresponse.return_value = response
            result = web_search(self.engine, self.case, "Ordinary Entity", True, "bing")
        connection.assert_called_once_with("www.bing.com", "8.8.8.8")
        sent = connection.return_value.request.call_args.kwargs["headers"]
        self.assertNotIn("Cookie", sent)
        self.assertEqual(sent["Accept-Encoding"], "identity")
        self.assertEqual(result["results"][0]["url"], "https://example.org/entity")


if __name__ == "__main__":
    unittest.main()
