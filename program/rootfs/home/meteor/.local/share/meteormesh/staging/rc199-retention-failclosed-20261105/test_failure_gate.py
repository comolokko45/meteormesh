import importlib.util
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('gate', Path(__file__).with_name('post-analysis-condition-gate.py'))
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class FailureGateTests(unittest.TestCase):
    def test_failures_and_unknown_only_run_backup(self):
        for result in ('exec-condition', 'exit-code', 'timeout', 'signal', ''):
            with self.subTest(result=result), patch.dict(os.environ, {'SERVICE_RESULT': result}), \
                    patch.object(gate.subprocess, 'run', return_value=SimpleNamespace(
                        returncode=0, stdout='MM153_DB_BACKUP: PASS', stderr='')) as run:
                self.assertEqual(gate.main(), 0)
                self.assertEqual(run.call_count, 1)
                self.assertIn(str(gate.DB_BACKUP), run.call_args.args[0])
                self.assertNotIn(str(gate.NEW_HOOK), run.call_args.args[0])

    def test_success_runs_existing_hook(self):
        with patch.dict(os.environ, {'SERVICE_RESULT': 'success'}), \
                patch.object(gate.subprocess, 'run', return_value=SimpleNamespace(
                    returncode=0, stdout='', stderr='')) as run:
            self.assertEqual(gate.main(), 0)
            self.assertIn(str(gate.NEW_HOOK), run.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
