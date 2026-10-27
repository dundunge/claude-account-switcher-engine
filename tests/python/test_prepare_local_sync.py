import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import test_engine as base
import test_case_bridge as bridge_tests

spec = importlib.util.spec_from_file_location('case_prepare_test', base.ENGINE.parents[1] / 'tools/prepare_local_sync.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.support = self.home / ('Library/Application Support' if sys.platform == 'darwin' else 'Support')
        self.support.mkdir(parents=True)
        self.profile = self.support / 'Claude-Pro-First'
        self.profile.mkdir()
        (self.support / 'CASE').mkdir()
        (self.support / 'CASE/accounts.json').write_text(json.dumps({'accounts': [{'id': base.A, 'dir': str(self.profile), 'name': 'First'}]}))
        self.hook = self.support / 'ClaudeSessionSync/CASE/hook.json'
        self.patchers = [mock.patch.object(prepare.Path, 'home', return_value=self.home),
                         mock.patch.dict(os.environ, {'APPDATA': str(self.support), 'LOCALAPPDATA': str(self.support), 'XDG_CONFIG_HOME': str(self.support)}),
                         mock.patch.object(prepare, 'load_engine', return_value=base.s),
                         mock.patch.object(base.s, 'assert_stopped')]
        for patcher in self.patchers:
            patcher.start(); self.addCleanup(patcher.stop)

    def tree(self):
        return {str(p.relative_to(self.home)): p.read_bytes() for p in self.home.rglob('*') if p.is_file()}

    def test_preview_does_not_create_runtime_or_change_account_data(self):
        before = self.tree()
        self.assertEqual(prepare.prepare()['status'], 'preview')
        self.assertEqual(self.tree(), before)
        self.assertFalse(self.hook.exists())

    def test_initialization_pins_sources_refuses_overwrite_and_bootstraps_without_fake_indexes(self):
        before = (self.support / 'CASE/accounts.json').read_bytes()
        self.assertEqual(prepare.prepare(True)['status'], 'initialized')
        hook = json.loads(self.hook.read_text())
        for key in ['engine', 'bridge']:
            self.assertEqual(hashlib.sha256(Path(hook[key + '_path']).read_bytes()).hexdigest(), hook[key + '_sha256'])
        self.assertEqual(before, (self.support / 'CASE/accounts.json').read_bytes())
        with mock.patch.object(bridge_tests.bridge, 'graceful_close'):
            result = bridge_tests.bridge.run(hook, self.profile, base.s)
        self.assertTrue(result['bootstrap'])
        self.assertFalse((self.profile / 'claude-code-sessions').exists())
        saved = self.tree()
        with self.assertRaisesRegex(RuntimeError, 'EXISTING_HOOK_REQUIRES_REVIEW'):
            prepare.prepare(True)
        self.assertEqual(self.tree(), saved)

    def test_first_initialization_refuses_profiles_outside_the_allowed_directory(self):
        outside = self.home / 'unrelated/Claude-Pro-Other'
        outside.mkdir(parents=True)
        (self.support / 'CASE/accounts.json').write_text(json.dumps({'accounts': [{'id': base.A, 'dir': str(outside)}]}))
        before = self.tree()
        with self.assertRaisesRegex(RuntimeError, 'UNEXPECTED_PROFILE_LOCATION'):
            prepare.prepare(True)
        self.assertEqual(self.tree(), before)
