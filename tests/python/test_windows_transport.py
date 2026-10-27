"""Windows runner integration uses only synthetic output, no real Claude login."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('case_win_probe', Path(__file__).resolve().parents[2] / 'tools/claude_usage_probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


@unittest.skipUnless(os.name == 'nt', 'Windows ConPTY and named-pipe integration')
class WindowsTransportTests(unittest.TestCase):
    def test_conpty_and_private_diagnostic_pipe_require_fresh_success(self):
        code = """import os,sys,time
path=sys.argv[sys.argv.index('--debug-file')+1]
with open(path, 'w') as log:
 log.write('2026-10-26T03:00:00.000Z [DEBUG] fetchUtilization: GET /api/oauth/usage (attempt 1)\\n')
 log.write('2026-10-26T03:00:00.100Z [DEBUG] fetchUtilization: 200 after 1 attempt(s)\\n')
print('\\x1b[2J\\x1b[HCurrent session\\n21% used\\nResets in 2 hr\\nCurrent week (all models)\\n45% used', flush=True)
time.sleep(20)
"""
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            with mock.patch.object(probe, 'cli_arguments', return_value=[sys.executable, '-c', code]):
                value = probe.capture_windows('unused', directory, directory, time.monotonic() + 10)
            self.assertEqual((value['fh'], value['sd']), (21, 45))
            self.assertEqual(value['source'], 'claude-cli')
            self.assertEqual(list(directory.iterdir()), [])

    def test_a_background_probe_without_fresh_request_evidence_is_rejected(self):
        code = "import time; print('Current session\\n88% used', flush=True); time.sleep(20)"
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(probe, 'cli_arguments', return_value=[sys.executable, '-c', code]):
                with self.assertRaisesRegex(probe.ProbeError, 'CLI_USAGE_UNAVAILABLE'):
                    probe.capture_windows('unused', Path(root), Path(root), time.monotonic() + 10)

    def test_job_object_cleanup_cannot_end_a_preexisting_process(self):
        survivor = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        try:
            code = 'import runpy,time; m=runpy.run_path(' + repr(str(Path(probe.__file__))) + '); m["windows_job"](); print("READY",flush=True); time.sleep(30)'
            child = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE)
            try:
                self.assertEqual(child.stdout.readline().strip(), b'READY')
                child.terminate()
                child.wait(timeout=5)
                self.assertIsNone(survivor.poll())
            finally:
                if child.poll() is None: child.kill(); child.wait()
                child.stdout.close()
        finally:
            survivor.terminate(); survivor.wait(timeout=5)

    def test_authorization_ipc_keeps_account_profiles_separate_and_rejects_wrong_key(self):
        url = 'https://claude.com/cai/oauth/authorize?redirect_uri=http%3A%2F%2Flocalhost%3A12345%2Fcallback&state=synthetic'
        with tempfile.TemporaryDirectory() as root:
            for name in ['first', 'second', 'third']:
                base = Path(root).resolve() / name
                (base / 'cli').mkdir(parents=True)
                (base / 'workspace').mkdir()
                script = base / 'auth-browser.cjs'
                script.write_text("import json,sys,time\nd=json.load(sys.stdin)\nassert d['userData'].endswith('browser')\nprint('CASE_AUTH_READY',flush=True)\ntime.sleep(20)\n")
                opener = base / 'browser.cmd'
                opener.write_text('synthetic only')
                payload = {'config': str(base / 'cli'), 'workspace': str(base / 'workspace'),
                           'browser': {'binary': str(Path(sys.executable).resolve()), 'script': str(script),
                                       'opener': str(opener), 'userData': str(base / 'browser'), 'label': name}}
                with probe.AuthBrowser(payload) as browser:
                    with mock.patch.dict(os.environ, {'CASE_AUTH_SOCKET': browser.address, 'CASE_AUTH_IPC_KEY': '0' * 64}):
                        self.assertEqual(probe.browser_open(url), 1)
                    self.assertIsNone(browser.error)
                    self.assertIsNone(browser.child)
                    with mock.patch.dict(os.environ, {'CASE_AUTH_SOCKET': browser.address, 'CASE_AUTH_IPC_KEY': browser.key}):
                        self.assertEqual(probe.browser_open(url), 0)
                    self.assertIsNone(browser.error)
