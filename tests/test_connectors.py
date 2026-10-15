import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from local_osint.connectors import catalogue, execute, validate_arguments
from local_osint.engine import Engine, InvalidInput


class ConnectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.engine = Engine(Path(self.tmp.name) / "private")
        self.case = self.engine.create_case("Public fixture", "person")["id"]

    def tearDown(self):
        self.tmp.cleanup()

    def response(self, raw, format="json", status=200, url="https://api.crossref.org/works"):
        return {"raw": raw, "format": format, "canonical": url, "status": status, "requests": 1, "retry_after": 60 if status == 429 else None}

    def run_source(self, source, args, response):
        with patch("local_osint.connectors.fetch_public_response", return_value=response) as fetch:
            result = execute(self.engine, self.case, source, args, True)
        return result, fetch

    def test_registry_is_explicit_and_optional_service_is_unavailable(self):
        items = catalogue()
        self.assertEqual(len(items), 8)
        self.assertFalse(next(x for x in items if x["id"] == "searxng")["available"])
        local = next(x for x in items if x["id"] == "email_headers")
        self.assertEqual(local["outbound_fields"], [])
        self.assertEqual(local["max_requests"], 0)

    def test_disabled_invalid_case_and_invalid_inputs_never_fetch(self):
        with patch("local_osint.connectors.fetch_public_response") as fetch:
            with self.assertRaises(InvalidInput):
                execute(self.engine, self.case, "crossref", {"query": "public"})
            with self.assertRaises(InvalidInput):
                execute(self.engine, "missing", "crossref", {"query": "public"}, True)
            for connector, args in (("shell", {"query": "public"}), ("website_sources", {"url": "https://127.0.0.1/"}), ("public_profile", {"username": "alice", "site": "evil"}), ("github_public", {"username": "../alice"}), ("crossref", {"query": "public", "command": "id"})):
                with self.subTest(connector=connector), self.assertRaises(InvalidInput):
                    execute(self.engine, self.case, connector, args, True)
            fetch.assert_not_called()

    def test_crossref_preserves_original_and_returns_public_metadata_leads(self):
        body = json.dumps({"message": {"items": [{"title": ["Public study"], "URL": "https://doi.org/10.123/example", "publisher": "Publisher"}]}}).encode()
        result, fetch = self.run_source("crossref", {"query": "Public Person"}, self.response(body))
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["leads"][0]["url"], "https://doi.org/10.123/example")
        self.assertEqual(result["observations"][0]["status"], "unverified")
        with self.engine.connection() as db:
            self.assertEqual(db.execute("SELECT raw FROM evidence WHERE id=?", (result["evidence_ids"][0],)).fetchone()[0], body)
        self.assertIn("Public+Person", fetch.call_args.args[0])

    def test_rate_limits_and_failure_bodies_are_saved_as_failure_not_empty_success(self):
        result, _ = self.run_source("crossref", {"query": "Public Person"}, self.response(b'{"message":"rate limit"}', status=429))
        self.assertEqual(result["status"], "rate_limited")
        self.assertEqual(result["retry_after"], 60)
        self.assertEqual(len(result["evidence_ids"]), 1)
        self.assertEqual(result["leads"], [])

    def test_malformed_api_json_retains_original_as_uninterpretable(self):
        result, _ = self.run_source("crossref", {"query": "Public Person"}, self.response(b'{"broken":'))
        self.assertEqual(result["status"], "uninterpretable")
        self.assertEqual(len(result["evidence_ids"]), 1)

    def test_shared_transport_preserves_failure_body_and_accepts_feed_content(self):
        from local_osint.collector import fetch_public_response
        for status, mime, payload in ((429, "application/json", b'{"error":"rate limit"}'), (200, "application/rss+xml", b'<rss/>')):
            headers = {"Content-Type": mime, "Content-Length": str(len(payload)), "Retry-After": "120"}
            response = Mock(status=status)
            response.getheader.side_effect = lambda name, default=None: headers.get(name, default)
            response.read1.side_effect = [payload, b'']
            with patch("local_osint.collector.target", return_value=("example.org", "8.8.8.8", "/", "https://example.org/")), patch("local_osint.collector.PinnedHTTPS") as factory:
                factory.return_value.getresponse.return_value = response
                result = fetch_public_response("https://example.org/")
            self.assertEqual(result["raw"], payload)
            self.assertEqual(result["status"], status)
            self.assertEqual(result["retry_after"], 120)

    def test_declared_connector_host_cannot_redirect_to_a_different_public_host(self):
        from local_osint.collector import fetch_public_response
        response = Mock(status=302)
        response.getheader.side_effect = lambda key, default=None: "https://other.example/" if key == "Location" else default
        with patch("local_osint.collector.target", return_value=("example.org", "8.8.8.8", "/", "https://example.org/")) as target, patch("local_osint.collector.PinnedHTTPS") as factory:
            factory.return_value.getresponse.return_value = response
            with self.assertRaises(InvalidInput):
                fetch_public_response("https://example.org/", allowed_hosts={"example.org"})
            self.assertEqual(target.call_count, 1)

    def test_profile_shell_and_login_barrier_never_claim_account_existence(self):
        for text, expected in (("<html><title>Welcome</title></html>", "uninterpretable"), ("<title>Sign in to continue</title>", "blocked")):
            result, _ = self.run_source("public_profile", {"username": "alice", "site": "gitlab"}, self.response(text.encode(), "html", url="https://gitlab.com/alice"))
            self.assertEqual(result["status"], expected)
            self.assertEqual(result["observations"], [])

    def test_matching_public_metadata_is_unconfirmed_ownership(self):
        result, _ = self.run_source("public_profile", {"username": "alice", "site": "gitlab"}, self.response(b'<meta property="og:title" content="alice GitLab">', "html", url="https://gitlab.com/alice"))
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["observations"][0]["ownership"], "unconfirmed")

    def test_github_account_login_must_match(self):
        for login, expected in (("alice", "success"), ("someone-else", "uninterpretable")):
            body = json.dumps({"login": login, "id": 123, "html_url": "https://github.com/" + login, "name": "Alice Public"}).encode()
            result, _ = self.run_source("github_public", {"username": "alice"}, self.response(body))
            self.assertEqual(result["status"], expected)

    def test_website_same_host_feed_and_sitemap_followups_are_queued_not_fetched(self):
        text = b'<a href="/about">About</a><link rel="alternate" type="application/rss+xml" href="/feed.xml"><a href="https://evil.example/">External</a><a href="http://example.org/insecure">HTTP</a><script type="application/ld+json">{"name":"Public Organization"}</script>'
        result, fetch = self.run_source("website_sources", {"url": "https://example.org/"}, self.response(text, "html", url="https://example.org/"))
        self.assertEqual({x["url"] for x in result["leads"]}, {"https://example.org/about", "https://example.org/feed.xml"})
        self.assertEqual(result["observations"][0]["value"], "Public Organization")
        self.assertEqual(fetch.call_count, 1)

    def test_website_leads_use_nested_visible_anchor_text_without_losing_feed_metadata(self):
        text = b'<a href="/people"> Meet <strong>our staff</strong> &amp; volunteers </a><a href="/annual" title="Annual report">Download</a><link rel="alternate" type="application/rss+xml" href="/feed.xml" title="Organization news">'
        result, _ = self.run_source("website_sources", {"url": "https://example.org/"}, self.response(text, "html", url="https://example.org/"))
        leads = {item["url"]: item for item in result["leads"]}
        self.assertEqual(leads["https://example.org/people"]["title"], "Meet our staff & volunteers")
        self.assertEqual(leads["https://example.org/annual"]["title"], "Annual report")
        self.assertEqual(leads["https://example.org/feed.xml"]["title"], "Organization news")
        self.assertIn("alternate", leads["https://example.org/feed.xml"]["snippet"])
        self.assertIn("application/rss+xml", leads["https://example.org/feed.xml"]["snippet"])

    def test_website_anchor_labels_exclude_hidden_content_and_remain_bounded(self):
        text = '<a href="/team">Team <span hidden><span>invisible</span> still hidden</span><script>tracking</script><style>css</style><span aria-hidden="true">icon label</span> directory</a><a href="/long">' + "Staff " * 300 + '</a><a href="/unclosed">Public <strong>contacts</strong>'
        result, _ = self.run_source("website_sources", {"url": "https://example.org/"}, self.response(text.encode(), "html", url="https://example.org/"))
        leads = {item["url"]: item for item in result["leads"]}
        self.assertEqual(leads["https://example.org/team"]["title"], "Team directory")
        self.assertTrue(leads["https://example.org/long"]["title"].startswith("Staff"))
        self.assertLessEqual(len(leads["https://example.org/long"]["title"]), 300)
        self.assertEqual(leads["https://example.org/unclosed"]["title"], "Public contacts")

    def test_xml_sitemap_and_entity_rejection(self):
        for raw, expected in ((b'<urlset><url><loc>https://example.org/public</loc></url></urlset>', "success"), (b'<!DOCTYPE x [<!ENTITY y "unsafe">]><x>&y;</x>', "uninterpretable")):
            result, _ = self.run_source("website_sources", {"url": "https://example.org/sitemap.xml"}, self.response(raw, "text", url="https://example.org/sitemap.xml"))
            self.assertEqual(result["status"], expected)

    def test_wayback_retains_original_url_and_capture_without_hidden_followup(self):
        body = json.dumps({"archived_snapshots": {"closest": {"available": True, "status": "200", "url": "http://web.archive.org/web/20240101000000/https://example.org/public", "timestamp": "20240101000000"}}}).encode()
        result, fetch = self.run_source("wayback", {"url": "https://example.org/public"}, self.response(body, url="https://archive.org/wayback/available"))
        self.assertTrue(result["leads"][0]["archived"])
        self.assertEqual(result["leads"][0]["original_url"], "https://example.org/public")
        self.assertEqual(fetch.call_count, 1)

    def test_email_headers_local_and_case_scoped(self):
        saved = self.engine.ingest(self.case, b'From: alice@example.org\nAuthentication-Results: example.org; dkim=pass\nReceived: from public.example\nSubject: Public message\n\nBody', "Saved email")
        with patch("local_osint.connectors.fetch_public_response") as fetch:
            result = execute(self.engine, self.case, "email_headers", {"evidence_id": saved["id"]})
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["requests"], 0)
            self.assertTrue(all(x["status"] == "unverified" for x in result["observations"]))
            other = self.engine.create_case("Other")["id"]
            with self.assertRaises(InvalidInput):
                execute(self.engine, other, "email_headers", {"evidence_id": saved["id"]})
            fetch.assert_not_called()

    def test_searxng_saves_raw_json_and_reports_partial_upstreams(self):
        class Service:
            def search(self, query):
                return {"raw": b'{"results":[{"url":"https://example.org/public","title":"Public"}]}', "upstream_fanout": 3, "upstream_failures": [["bing", "timeout"]]}
        result = execute(self.engine, self.case, "searxng", {"query": "Public Person"}, True, {"search_service": Service()})
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["upstream_fanout"], 3)
        self.assertEqual(len(result["upstream_failures"]), 1)
        self.assertEqual(len(result["evidence_ids"]), 1)


if __name__ == "__main__":
    unittest.main()
