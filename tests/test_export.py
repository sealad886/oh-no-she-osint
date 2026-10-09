import json
import re
from pathlib import Path
import tempfile
import unittest

from local_osint.engine import Engine


class InvestigationExportTests(unittest.TestCase):
    def test_web_investigation_export_preserves_stop_explanation_source_attribution_and_tool_inputs(self):
        """An exported ordinary-person dossier must explain its leads and actual work."""
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as folder:
            engine = Engine(Path(folder) / 'private')
            case = engine.create_case('Ordinary Person', focus_type='person')['id']
            other_case = engine.create_case('Unrelated private investigation')['id']
            engine.ingest(other_case, b'Unrelated private evidence', 'Private source')
            search = engine.ingest(case, b'Public profile search results', 'Search', 'https://lite.duckduckgo.com/lite/?q=Ordinary+Person')['id']
            page = engine.ingest(case, b'Ordinary Person public biography', 'Profile', 'https://example.org/bio')['id']
            report = {'report_type': 'investigation', 'run_id': 'example-run', 'case_id': case,
                'status': 'partial', 'interpretation': 'Name matches remain unconfirmed.',
                'stop_reason': 'Selected sources finished. Search unavailable; retry this tool.',
                'budget': {'used': 9, 'limit': 24}, 'profiles': [], 'articles': [],
                'web_results': [{'title': 'Ordinary Person public profile', 'url': 'https://example.org/profile?id=3',
                    'snippet': 'Public employment information', 'reason': 'Name matched; identity unconfirmed.',
                    'source_id': search, 'page_source_id': page}],
                'pages': [{'url': 'https://example.org/profile?id=3', 'final_url': 'https://example.org/bio',
                    'source_id': page, 'excerpt': 'Ordinary Person public biography',
                    'links': [{'title': 'About this person', 'url': 'https://example.org/about'}]}],
                'steps': [{'label': 'Search for Ordinary Person', 'tool': 'web_search', 'origin': 'user',
                    'status': 'failed', 'ok': False, 'arguments': {'query': 'Ordinary Person employer'},
                    'error': 'Provider challenged this request.', 'source_id': search, 'duration_ms': 1300},
                    {'label': 'Read the public biography', 'tool': 'read_evidence', 'origin': 'ai',
                     'status': 'success', 'ok': True, 'arguments': {'id': page}, 'result_summary': 'Evidence read.'}]}
            engine.ingest(case, json.dumps(report).encode(), 'Investigation report',
                          'local-osint:investigation-report', 'json', extract=False)
            exported = engine.export(case)
            self.assertEqual(exported['investigation']['stop_reason'], report['stop_reason'])
            self.assertEqual(exported['investigation']['steps'][0]['arguments']['query'], 'Ordinary Person employer')
            markdown = engine.markdown(case)
            readable = re.sub(r'\\(.)', r'\1', markdown)
            for meaning in ['Selected sources finished', '9 of 24', 'limit, not a completion target',
                            'Public employment information', 'identity unconfirmed', search, page,
                            'Ordinary Person public biography', 'About this person', 'web_search',
                            'Ordinary Person employer', 'Provider challenged this request', 'Local AI']:
                self.assertIn(meaning, readable)
            for url in ['https://example.org/profile?id=3', 'https://example.org/bio', 'https://example.org/about']:
                self.assertIn(url, readable)
            self.assertNotIn('Unrelated private', markdown)

    def test_exported_search_and_tool_content_remains_inert_markdown(self):
        """Source and tool text must not produce executable HTML or Markdown links."""
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as folder:
            engine = Engine(Path(folder) / 'private')
            case = engine.create_case('Ordinary Person')['id']
            payload = '<script>alert(1)</script> [follow](javascript:alert(1)) ![image](https://example.org/track)'
            report = {'report_type': 'investigation', 'run_id': 'inert-run', 'case_id': case,
                'status': 'partial', 'interpretation': 'Unconfirmed.', 'profiles': [], 'articles': [],
                'stop_reason': payload,
                'web_results': [{'title': payload, 'url': 'https://example.org/', 'snippet': payload}],
                'steps': [{'label': 'Web search', 'ok': False, 'tool': 'web_search', 'origin': 'user',
                           'arguments': {'query': payload}, 'error': payload}]}
            engine.ingest(case, json.dumps(report).encode(), 'Investigation report',
                          'local-osint:investigation-report', 'json', extract=False)
            markdown = engine.markdown(case)
            self.assertNotIn('<script>', markdown)
            self.assertNotIn('[follow](javascript:', markdown)
            self.assertNotIn('![image](', markdown)
            self.assertIn('&lt;script&gt;', markdown)
