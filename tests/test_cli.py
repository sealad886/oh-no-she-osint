from contextlib import redirect_stdout, redirect_stderr
import io
import errno
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from local_osint.__main__ import main, parser, settings


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.root = Path(self.temp.name)
        self.data = ['--data-dir', str(self.root / 'private')]

    def tearDown(self):
        self.temp.cleanup()

    def run_command(self, args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(args + self.data)
        return code, out.getvalue(), err.getvalue()

    def test_case_import_search_export_backup_workflow(self):
        code, out, _ = self.run_command(['case', 'Example Foundation', '--type', 'organization', '--alias', 'Example', '--identifier', 'example.org'])
        self.assertEqual(code, 0)
        case = json.loads(out)
        source = self.root / 'note.txt'
        source.write_text('hello@example.org', encoding='utf-8')
        self.assertEqual(self.run_command(['import', case['id'], str(source)])[0], 0)
        code, out, _ = self.run_command(['search', case['id'], 'hello'])
        self.assertEqual(json.loads(out)['entities'][0]['kind'], 'email')
        destination = self.root / 'case.json'
        self.assertEqual(self.run_command(['export', case['id'], str(destination)])[0], 0)
        self.assertEqual(json.loads(destination.read_text())['case']['focus_type'], 'organization')
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.run_command(['export', case['id'], str(destination)])[0], 1)
        self.assertEqual(self.run_command(['backup', str(self.root / 'backup.sqlite3')])[0], 0)

    def test_configuration_defaults_overrides_and_invalid_keys(self):
        config = self.root / 'config.toml'
        config.write_text('data_dir = "private"\nport = 9000\n')
        args = parser().parse_args(['serve','--config',str(config)])
        self.assertEqual(settings(args), (str(self.root / 'private'), 9000))
        args = parser().parse_args(['serve','--config',str(config),'--port','0','--data-dir',str(self.root / 'other'),'--allow-network','--offline'])
        self.assertEqual(settings(args), (str(self.root / 'other'), 0))
        self.assertFalse(args.allow_network)
        for content in ['allow_network=true', 'port=true', 'port=70000', 'data_dir=123', 'bad TOML']:
            config.write_text(content)
            self.assertEqual(self.run_command(['cases','--config',str(config)])[0], 1)

    def test_default_port_is_automatic_but_explicit_port_is_respected(self):
        args = parser().parse_args(['serve'])
        self.assertEqual(settings(args)[1], 0)
        args = parser().parse_args(['serve', '--port', '8765'])
        self.assertEqual(settings(args)[1], 8765)

    def test_explicit_busy_port_has_actionable_error(self):
        with patch('local_osint.__main__.LocalServer', side_effect=OSError(errno.EADDRINUSE, 'private diagnostic')):
            code, out, err = self.run_command(['serve', '--port', '8765'])
        self.assertEqual(code, 1)
        self.assertIn('--port 0', err)
        self.assertIn('8765', err)
        self.assertNotIn('private diagnostic', err)
        self.assertNotIn('ready', out)

    def test_output_escapes_terminal_control_characters(self):
        code, out, _ = self.run_command(['case', '\x1b[31mName'])
        self.assertEqual(code, 0)
        self.assertNotIn('\x1b', out)
        self.assertIn('\\u001b', out)


if __name__ == '__main__':
    unittest.main()
