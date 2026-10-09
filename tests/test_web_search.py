import base64
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import quote

from local_osint.collector import collect, fetch_public, parse_search, web_search
from local_osint.engine import Engine, InvalidInput, MAX_BYTES


def page(url="https://example.org/profile?name=Ordinary+Person", title="Ordinary Person", snippet="Public profile"):
    wrapper = "//duckduckgo.com/l/?uddg=" + quote(url, safe="") + "&amp;rut=ignored"
    return (f'<a class="result-link" href="{wrapper}">{title}</a>'
            f'<td class="result-snippet">{snippet}</td>').encode()


def response(body=b"public", status=200, location=None):
    headers = {"Content-Type": "text/plain", "Content-Length": str(len(body))}
    if location is not None:
        headers["Location"] = location
    value = Mock(status=status)
    value.getheader.side_effect = lambda key, default=None: headers.get(key, default)
    value.read1.side_effect = [body, b""]
    return value


class WebSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.engine = Engine(Path(self.temp.name) / "private")
        self.case = self.engine.create_case("Ordinary Person", focus_type="person")["id"]

    def tearDown(self):
        self.temp.cleanup()

    def test_search_gates_before_dns_and_never_queries_invalid_case(self):
        with patch("local_osint.collector.socket.getaddrinfo") as dns, patch("local_osint.collector.fetch_public") as fetch:
            for case, enabled in [(self.case, False), ("missing", True)]:
                with self.assertRaises(InvalidInput):
                    web_search(self.engine, case, "Ordinary Person", enabled)
            for query in ["", "x" * 201, None]:
                with self.assertRaises(InvalidInput):
                    web_search(self.engine, self.case, query, True)
            dns.assert_not_called()
            fetch.assert_not_called()

    def test_search_preserves_raw_source_and_decodes_destination_without_contact(self):
        raw = page(snippet="Work: <b>public</b> &amp; readable")
        with patch("local_osint.collector.fetch_public", return_value=(raw, "html", "https://lite.duckduckgo.com/lite/?q=Ordinary+Person")) as fetch, patch("local_osint.collector.socket.getaddrinfo") as dns:
            found = web_search(self.engine, self.case, "Ordinary Person", True)
            dns.assert_not_called()
        self.assertEqual(found["results"], [{"title": "Ordinary Person", "url": "https://example.org/profile?name=Ordinary+Person", "snippet": "Work: public & readable"}])
        self.assertEqual(fetch.call_count, 1)
        self.assertTrue(fetch.call_args.kwargs["allow_query"])
        source = self.engine.evidence(self.case, found["id"])
        self.assertEqual(source["source"], "https://lite.duckduckgo.com/lite/?q=Ordinary+Person")
        self.assertEqual(base64.b64decode(self.engine.export(self.case)["evidence"][0]["original_base64"]), raw)

    def test_html_interface_nested_markup_direct_urls_and_result_bounds(self):
        raw = b'<a class="result__a" href="https://example.org/a#section"><b>First</b> person</a><a class="result__snippet">A <em>public</em> snippet</a>'
        raw += b"".join(page(f"https://example.org/{n}", "t" * 1000, "s" * 2000) for n in range(15))
        results = parse_search(raw)
        self.assertEqual(results[0], {"url": "https://example.org/a", "title": "First person", "snippet": "A public snippet"})
        self.assertEqual(len(results), 10)
        self.assertTrue(all(len(item["title"]) <= 300 and len(item["snippet"]) <= 1000 for item in results))
        relative = parse_search(b'<a class="result-link" href="//example.org/profile">Public person</a>')
        self.assertEqual(relative[0]["url"], "https://example.org/profile")

    def test_duplicates_and_unsafe_results_do_not_become_collection_leads(self):
        raw = page() + page()
        for url in ["http://example.org/", "https://127.0.0.1/", "https://user:password@example.org/", "https://host.internal/", "https://[::1]/", "https://example.org:8443/", "https://duckduckgo.com/ads/"]:
            raw += page(url)
        results = parse_search(raw)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["url"], "https://example.org/profile?name=Ordinary+Person")

    def test_challenge_and_malformed_response_are_failure_not_no_results(self):
        for raw in [b'<form id="challenge-form">Select ducks</form>', b'<script src="anomaly.js"></script>', b'<html>unexpected gateway</html>']:
            with self.subTest(raw=raw), patch("local_osint.collector.fetch_public", return_value=(raw, "html", "https://lite.duckduckgo.com/lite/")):
                with self.assertRaises(InvalidInput) as caught:
                    web_search(self.engine, self.case, "Ordinary Person", True)
                self.assertTrue(caught.exception.evidence_id)
                self.assertEqual(self.engine.evidence(self.case, caught.exception.evidence_id)["source"], "https://lite.duckduckgo.com/lite/")
        with self.assertRaisesRegex(InvalidInput, "UTF-8"):
            parse_search(b'\xff')

    def test_provider_explicit_no_results_and_limits(self):
        with patch("local_osint.collector.fetch_public", return_value=(b'<div class="no-results">No results found</div>', "html", "https://lite.duckduckgo.com/lite/")):
            self.assertEqual(web_search(self.engine, self.case, "Ordinary Person", True)["results"], [])
        with self.assertRaises(InvalidInput):
            parse_search(b"x" * (MAX_BYTES + 1))
        with patch("local_osint.collector.fetch_public", return_value=(b"{}", "json", "https://lite.duckduckgo.com/lite/")):
            with self.assertRaises(InvalidInput):
                web_search(self.engine, self.case, "Ordinary Person", True)

    def test_query_collection_requires_explicit_opt_in(self):
        with patch("local_osint.collector.socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]) as dns, patch("local_osint.collector.PinnedHTTPS") as factory:
            with self.assertRaises(InvalidInput):
                collect(self.engine, self.case, "https://example.org/profile?id=3", True)
            dns.assert_not_called()
            factory.return_value.getresponse.return_value = response()
            saved = collect(self.engine, self.case, "https://example.org/profile?id=3", True, allow_query=True)
            self.assertEqual(self.engine.evidence(self.case, saved["id"])["source"], "https://example.org/profile?id=3")
            self.assertEqual(factory.return_value.request.call_args.args[1], "/profile?id=3")

    def test_redirects_revalidate_pin_each_destination_and_preserve_final_source(self):
        dns_values = [[(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))] for ip in ("8.8.8.8", "1.1.1.1")]
        first, second = Mock(), Mock()
        first.getresponse.return_value = response(status=302, location="https://other.example.org/public?id=3")
        second.getresponse.return_value = response()
        with patch("local_osint.collector.socket.getaddrinfo", side_effect=dns_values) as dns, patch("local_osint.collector.PinnedHTTPS", side_effect=[first, second]) as factory:
            saved = collect(self.engine, self.case, "https://example.org/", True, allow_query=True)
        self.assertEqual([item.args for item in factory.call_args_list], [("example.org", "8.8.8.8"), ("other.example.org", "1.1.1.1")])
        self.assertEqual(dns.call_count, 2)
        self.assertEqual(self.engine.evidence(self.case, saved["id"])["source"], "https://other.example.org/public?id=3")
        first.close.assert_called_once()
        second.close.assert_called_once()
        self.assertNotIn("Cookie", second.request.call_args.kwargs["headers"])

    def test_redirect_private_credentials_downgrade_and_queries_block_before_contact(self):
        for location in ["https://127.0.0.1/", "https://host.internal/", "https://user:pass@example.org/", "http://example.org/", "/?secret=1", "https://example.org/\nX-Header:bad"]:
            with self.subTest(location=location), patch("local_osint.collector.socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]), patch("local_osint.collector.PinnedHTTPS") as factory:
                factory.return_value.getresponse.return_value = response(status=302, location=location)
                with self.assertRaises(InvalidInput):
                    fetch_public("https://example.org/")
                self.assertEqual(factory.call_count, 1)

    def test_redirect_host_resolving_private_is_blocked_and_previous_connection_closed(self):
        dns_values = [[(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))] for ip in ("8.8.8.8", "10.0.0.1")]
        with patch("local_osint.collector.socket.getaddrinfo", side_effect=dns_values), patch("local_osint.collector.PinnedHTTPS") as factory:
            factory.return_value.getresponse.return_value = response(status=302, location="https://other.example.org/")
            with self.assertRaises(InvalidInput):
                fetch_public("https://example.org/")
            factory.assert_called_once()
            factory.return_value.close.assert_called_once()

    def test_redirect_loop_hop_limit_and_whole_chain_deadline(self):
        with patch("local_osint.collector.socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]), patch("local_osint.collector.PinnedHTTPS") as factory:
            factory.return_value.getresponse.return_value = response(status=302, location="/")
            with self.assertRaisesRegex(InvalidInput, "loop"):
                fetch_public("https://example.org/")
            self.assertEqual(factory.call_count, 1)
        connections = [Mock() for _ in range(4)]
        for n, connection in enumerate(connections):
            connection.getresponse.return_value = response(status=302, location=f"/next{n}")
        with patch("local_osint.collector.socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]), patch("local_osint.collector.PinnedHTTPS", side_effect=connections):
            with self.assertRaisesRegex(InvalidInput, "three-redirect"):
                fetch_public("https://example.org/")
        with patch("local_osint.collector.time.monotonic", side_effect=[0, 21]), patch("local_osint.collector.target") as target:
            with self.assertRaisesRegex(InvalidInput, "timed out"):
                fetch_public("https://example.org/")
            target.assert_not_called()
        with patch("local_osint.collector.time.monotonic", side_effect=[0, 1, 2, 21]), patch("local_osint.collector.socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]), patch("local_osint.collector.PinnedHTTPS") as factory:
            factory.return_value.getresponse.return_value = response(status=302, location="/next")
            with self.assertRaisesRegex(InvalidInput, "timed out"):
                fetch_public("https://example.org/")
            self.assertEqual(factory.call_count, 1)
