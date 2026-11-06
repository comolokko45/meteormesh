import io
import json
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch
from meteor_agent_cli import main, parse_input, render_text
from test_agent_engine import good, NOW
from agent_engine import evaluate

class CLITests(unittest.TestCase):
    def test_stdout_only_json(self):
        x=good();x['checked_tr']='2099-01-01T00:00:00+00:00'
        with patch('sys.stdin', io.StringIO(json.dumps(x))), redirect_stdout(io.StringIO()) as out:
            rc=main(['--input','-','--format','json'])
        self.assertEqual(rc,1)
        y=json.loads(out.getvalue())
        self.assertFalse(y['mutation_enabled'])

    def test_text_has_no_shell_command(self):
        report=evaluate(good(),now=NOW)
        t=render_text(report)
        self.assertIn('OBSERVE',t)
        self.assertNotIn('sudo',t)

    def test_bad_json_denied(self):
        with patch('sys.stdin',io.StringIO('{bad')),redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main(['--input','-']),2)
        self.assertIn('no action',err.getvalue())

    def test_oversized_json_denied(self):
        with self.assertRaises(ValueError):parse_input(io.StringIO('x'*(1024*1024+1)))

if __name__=='__main__':unittest.main()
