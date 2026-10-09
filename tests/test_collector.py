import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import Mock, patch

from local_osint.collector import collect, domain_name, fetch_public, PinnedHTTPS, public_address, research, target
from local_osint.engine import Engine, InvalidInput, MAX_BYTES


def records(*addresses):
    return [(socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)) for address in addresses]


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.engine = Engine(Path(self.temp.name) / "private")
        self.case = self.engine.create_case("Public signals")["id"]

    def tearDown(self):
        self.temp.cleanup()

    def test_offline_denies_every_signal_before_dns_or_socket(self):
        with patch('local_osint.collector.socket.getaddrinfo') as dns, patch('local_osint.collector.PinnedHTTPS') as connection:
            with self.assertRaises(InvalidInput):
                collect(self.engine, self.case, "https://example.org/")
            with self.assertRaises(InvalidInput):
                research(self.engine, self.case, "example.org", ["dns"])
            dns.assert_not_called()
            connection.assert_not_called()

    def test_accepted_long_identifiers_keep_complete_sources(self):
        url = "https://example.org/" + "a" * 1000
        with patch('local_osint.collector.fetch_public', return_value=(b"example.org", "text", url)):
            saved = collect(self.engine, self.case, url, True)
        evidence = self.engine.evidence(self.case, saved["id"])
        self.assertEqual(evidence["source"], url)
        self.assertLessEqual(len(evidence["title"]), 200)
        domain = ".".join(["a" * 63] * 3 + ["b" * 57, "org"])
        self.assertEqual(len(domain), 253)
        with patch('local_osint.collector.target', return_value=(domain, '8.8.8.8', '/', 'https://' + domain)), patch('local_osint.collector.socket.getaddrinfo', return_value=records('8.8.8.8')):
            result = research(self.engine, self.case, domain, ['dns'], True)
        self.assertTrue(result['results'][0]['ok'])
        saved = self.engine.evidence(self.case, result['results'][0]['id'])
        self.assertIn(domain, saved['source'])
        self.assertIn(domain, json.loads(saved['text'])['domain'])
        self.assertLessEqual(len(saved['title']), 200)

    def test_invalid_case_never_collects(self):
        with patch('local_osint.collector.socket.getaddrinfo') as dns:
            with self.assertRaises(InvalidInput):
                collect(self.engine, "missing", "https://example.org/", True)
            with self.assertRaises(InvalidInput):
                research(self.engine, "missing", "example.org", ["dns"], True)
            dns.assert_not_called()

    def test_url_controls_block_before_dns(self):
        urls = ['http://example.org', 'https://example.org:8443/', 'https://user:pass@example.org/', 'https://example.org/?secret=x', 'https://example.org/#secret', 'https://localhost/', 'https://foo.local/', 'https://127.0.0.1/', 'https://169.254.169.254/', 'https://[::1]/', 'https://example.org./', 'https://example.org\\@evil.test', 'https://example.org/\nheader', 'https://example.org/%path?']
        # A bare ? has no query data and is harmless; remove it from prohibited cases.
        urls.pop()
        with patch('local_osint.collector.socket.getaddrinfo') as dns:
            for url in urls:
                with self.subTest(url=url), self.assertRaises(InvalidInput):
                    target(url)
            dns.assert_not_called()

    def test_public_address_policy_and_mixed_dns_rejection(self):
        for address in ['127.0.0.1', '10.1.2.3', '169.254.169.254', '192.0.2.1', '224.0.0.1', '::1', 'fc00::1', 'fe80::1', '::ffff:8.8.8.8', '2002:0808:0808::', '64:ff9b::0808:0808']:
            self.assertFalse(public_address(address), address)
        self.assertTrue(public_address('8.8.8.8'))
        self.assertTrue(public_address('2606:4700:4700::1111'))
        with patch('local_osint.collector.socket.getaddrinfo', return_value=records('8.8.8.8', '10.0.0.1')):
            with self.assertRaises(InvalidInput):
                target('https://example.org/')

    def test_dns_rebinding_cannot_change_connection_destination(self):
        with patch('local_osint.collector.socket.getaddrinfo', return_value=records('8.8.8.8')) as dns:
            host, address, path, url = target('https://example.org/page')
        connection = PinnedHTTPS(host, address)
        with patch('local_osint.collector.socket.socket') as socket_factory, patch.object(connection._context, 'wrap_socket') as wrap:
            connection.connect()
            socket_factory.return_value.connect.assert_called_once_with(('8.8.8.8', 443))
            wrap.assert_called_once_with(socket_factory.return_value, server_hostname='example.org')
        dns.assert_called_once()
        self.assertEqual(path, '/page')
        self.assertEqual(url, 'https://example.org/page')
        self.assertTrue(connection._context.check_hostname)
        self.assertIsNone(connection._context.keylog_filename)

    def response(self, body=b'hello@example.org', status=200, headers=None):
        headers = headers or {'Content-Type': 'text/plain', 'Content-Length': str(len(body))}
        result = Mock(status=status)
        result.getheader.side_effect = lambda key, default=None: headers.get(key, default)
        result.read1.side_effect = [body, b'']
        return result

    def test_fetch_preserves_original_bytes_and_ignores_proxy_and_cookies(self):
        with patch('local_osint.collector.target', return_value=('example.org', '8.8.8.8', '/', 'https://example.org/')), patch('local_osint.collector.PinnedHTTPS') as factory:
            factory.return_value.getresponse.return_value = self.response()
            saved = collect(self.engine, self.case, 'https://example.org/', True)
            evidence = self.engine.evidence(self.case, saved['id'])
            self.assertEqual(evidence['text'], 'hello@example.org')
            request_headers = factory.return_value.request.call_args.kwargs['headers']
            self.assertNotIn('Cookie', request_headers)
            self.assertEqual(request_headers['Accept-Encoding'], 'identity')
            factory.return_value.close.assert_called_once()

    def test_redirects_compression_binary_and_oversize_fail_without_evidence(self):
        cases = [self.response(status=302), self.response(headers={'Content-Type':'text/plain', 'Content-Encoding':'gzip'}), self.response(headers={'Content-Type':'image/png'}), self.response(headers={'Content-Type':'text/plain', 'Content-Length':str(MAX_BYTES+1)}), self.response(body=b'x' * (MAX_BYTES+1), headers={'Content-Type':'text/plain'})]
        for response in cases:
            with patch('local_osint.collector.target', return_value=('example.org', '8.8.8.8', '/', 'https://example.org/')), patch('local_osint.collector.PinnedHTTPS') as factory:
                factory.return_value.getresponse.return_value = response
                with self.assertRaises(InvalidInput):
                    collect(self.engine, self.case, 'https://example.org/', True)
                factory.return_value.close.assert_called_once()
        self.assertEqual(self.engine.view(self.case)['counts']['evidence'], 0)

    def test_collection_errors_hide_private_details(self):
        with patch('local_osint.collector.target', return_value=('example.org', '8.8.8.8', '/', 'https://example.org/')), patch('local_osint.collector.PinnedHTTPS') as factory:
            factory.return_value.request.side_effect = OSError('private-secret')
            with self.assertRaises(InvalidInput) as caught:
                collect(self.engine, self.case, 'https://example.org/', True)
            self.assertNotIn('private-secret', str(caught.exception))

    def test_research_has_per_source_results_and_preserves_successes(self):
        with patch('local_osint.collector.target', return_value=('example.org', '8.8.8.8', '/', 'https://example.org/')), patch('local_osint.collector.socket.getaddrinfo', return_value=records('8.8.8.8')), patch('local_osint.collector.collect', side_effect=InvalidInput('Website refused')):
            result = research(self.engine, self.case, 'EXAMPLE.org', ['dns', 'website'], True)
        self.assertEqual(result['domain'], 'example.org')
        self.assertTrue(result['results'][0]['ok'])
        self.assertFalse(result['results'][1]['ok'])
        self.assertEqual(self.engine.view(self.case)['counts']['evidence'], 1)

    def test_registration_bootstrap_uses_registry_https_endpoint(self):
        bootstrap = json.dumps({'services': [[['org'], ['http://bad.example/', 'https://registry.example/rdap/']]]}).encode()
        registration = b'{"ldhName":"example.org","status":["active"]}'
        with patch('local_osint.collector.fetch_public', side_effect=[(bootstrap, 'json', 'https://data.iana.org/rdap/dns.json'), (registration, 'json', 'https://registry.example/rdap/domain/example.org')]) as fetch:
            result = research(self.engine, self.case, 'example.org', ['registration'], True)
        self.assertTrue(result['results'][0]['ok'])
        self.assertEqual(fetch.call_args_list[1].args[0], 'https://registry.example/rdap/domain/example.org')
        self.assertEqual(self.engine.export(self.case)['evidence'][0]['source'], 'https://registry.example/rdap/domain/example.org')

    def test_research_validation_and_unavailable_registry(self):
        for name in ['localhost', 'https://example.org/', 'example.org/path', '127.0.0.1', 'a' * 64 + '.org']:
            with self.assertRaises(InvalidInput):
                domain_name(name)
        for signals in [[], ['dns','dns'], ['unknown'], [123], 'dns']:
            with self.assertRaises(InvalidInput):
                research(self.engine, self.case, 'example.org', signals, True)
        with patch('local_osint.collector.fetch_public', return_value=(b'{"services":[]}', 'json', 'https://data.iana.org/rdap/dns.json')):
            result = research(self.engine, self.case, 'example.org', ['registration'], True)
        self.assertFalse(result['results'][0]['ok'])


if __name__ == '__main__':
    unittest.main()
