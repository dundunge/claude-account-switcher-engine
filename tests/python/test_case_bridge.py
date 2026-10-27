import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

import test_engine as base

SCRIPT = base.ENGINE.parents[1] / 'tools' / 'case_sync_bridge.py'
spec = importlib.util.spec_from_file_location('case_bridge_test', SCRIPT)
bridge = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bridge
spec.loader.exec_module(bridge)
s = base.s


class CaseBridgeTests(unittest.TestCase):
    write_config = base.EngineTests.write_config
    path = base.EngineTests.path
    put = base.EngineTests.put
    tree = base.EngineTests.tree

    def setUp(self):
        base.EngineTests.setUp(self)
        self.parent = self.root / 'profiles'; self.parent.mkdir()
        self.accounts = []
        self.accounts_file = self.root / 'accounts.json'
        for i, identity in enumerate((base.A, base.B)):
            directory = self.parent / ('Claude-Pro-' + str(i)); directory.mkdir()
            self.accounts.append({'id': identity, 'name': 'Pro ' + str(i), 'dir': str(directory)})
        self.save_accounts()
        self.hook = {'schema_version': 1, 'hook_enabled': True,
                     'engine_path': str(base.ENGINE),
                     'engine_sha256': hashlib.sha256(base.ENGINE.read_bytes()).hexdigest(),
                     'source_config_path': str(self.config),
                     'case_accounts_path': str(self.accounts_file), 'profiles_parent': str(self.parent)}
        self.target = Path(self.accounts[0]['dir'])

    def save_accounts(self):
        self.accounts_file.write_text(json.dumps({'accounts': self.accounts}))

    def init_profile(self, i, account=base.A, org=base.OA):
        p = Path(self.accounts[i]['dir']) / 'claude-code-sessions' / account / org
        p.mkdir(parents=True, exist_ok=True)
        return p

    def apply_offline(self, config=None):
        real = s.apply
        with mock.patch.object(bridge, 'graceful_close'), mock.patch.object(
                s, 'apply', side_effect=lambda c, p: real(c, p, guard=lambda: None)):
            return bridge.run(config or self.hook, self.target, s)

    def test_bootstrap_preview_is_read_only_and_does_not_close_claude(self):
        self.put(); before = self.tree()
        with mock.patch.object(bridge, 'graceful_close') as close:
            result = bridge.run(self.hook, self.target, s, dry_run=True)
        close.assert_not_called()
        self.assertTrue(result['bootstrap'])
        self.assertEqual(result['partition_count'], 2)
        self.assertEqual(before, self.tree())
        self.assertFalse(self.state.exists())

    def test_initialized_profiles_import_history_and_preserve_bodies(self):
        self.put(); dest = self.init_profile(0); self.init_profile(1, base.B, base.OB)
        bodies = self.tree(self.projects)
        result = self.apply_offline()
        self.assertFalse(result['bootstrap'])
        self.assertEqual(result['partition_count'], 4)
        self.assertTrue((dest / base.NAME).is_file())
        self.assertEqual(self.tree(self.projects), bodies)
        config = s.Config.load(Path(result['runtime_config_path']))
        self.assertEqual(s.build_plan(config).operations, [])

    def test_new_third_and_fourth_profiles_are_discovered_without_credentials(self):
        self.put(); self.init_profile(0)
        for i in (2, 3):
            identity = str(__import__('uuid').uuid4())
            directory = self.parent / ('Claude-Pro-' + str(i)); directory.mkdir()
            self.accounts.append({'id': identity, 'name': 'Pro ' + str(i), 'dir': str(directory)})
            self.init_profile(i, identity, base.OA)
        self.save_accounts()
        value, bootstrap, initialized = bridge.discover(self.hook, self.target, s)
        self.assertEqual(initialized, 3)
        self.assertEqual(len(value['partitions']), 5)
        self.assertFalse(bootstrap)

    def test_default_claude_profile_name_is_allowed(self):
        renamed = self.parent / 'Claude'
        self.target.rename(renamed)
        self.accounts[0]['dir'] = str(renamed)
        self.target = renamed
        self.save_accounts()
        value, bootstrap, initialized = bridge.discover(self.hook, self.target, s)
        self.assertTrue(bootstrap)
        self.assertEqual(initialized, 0)
        self.assertEqual(len(value['partitions']), 2)

    def test_ninth_account_is_rejected_before_sync(self):
        for i in range(2, 9):
            directory = self.parent / ('Claude-Pro-' + str(i))
            directory.mkdir()
            self.accounts.append({'id': str(__import__('uuid').uuid4()),
                                  'name': 'Pro ' + str(i), 'dir': str(directory)})
        self.save_accounts()
        with self.assertRaisesRegex(bridge.BridgeError, 'CASE_ACCOUNTS_REQUIRED'):
            bridge.discover(self.hook, self.target, s)

    def test_pinned_bridge_hash_blocks_mixed_release(self):
        with self.assertRaisesRegex(bridge.BridgeError, 'BRIDGE_HASH_MISMATCH'):
            bridge.check_bridge_hash({**self.hook, 'bridge_sha256': '0' * 64})

    def test_repair_uses_the_transaction_engine_and_preserves_permissions(self):
        self.put()
        self.init_profile(0)
        self.init_profile(1, base.B, base.OB)
        orphan = {
            'localId': 'local_' + base.SID,
            'cliSessionId': base.CLI,
            'cwd': str(self.root / 'offline-project'),
            'createdAt': 1790000000000,
            'lastActivityAt': 1790000001000,
            'title': 'Recovered',
            'model': 'claude-test',
        }
        real = s.apply
        with mock.patch.object(s, 'assert_stopped'), mock.patch.object(
                s, 'apply', side_effect=lambda c, p, **_: real(c, p, guard=lambda: None)):
            result = bridge.run_repair(self.hook, self.target, s, orphan)
        self.assertEqual(result['status'], 'completed')
        indexes = (
            self.init_profile(0),
            self.init_profile(1, base.B, base.OB),
            self.index / base.A / base.OA,
            self.index / base.B / base.OB,
        )
        for index in indexes:
            entry = index / base.NAME
            self.assertTrue(entry.is_file())
            self.assertNotIn('permissionMode', json.loads(entry.read_text()))

    def test_repair_refuses_an_explicit_deletion_marker(self):
        self.put()
        namespace = self.init_profile(0)
        self.init_profile(1, base.B, base.OB)
        (namespace / ('deleted_' + base.SID)).write_text(str(1790000002000))
        orphan = {
            'localId': 'local_' + base.SID,
            'cliSessionId': base.CLI,
            'cwd': str(self.root / 'offline-project'),
            'createdAt': 1790000000000,
            'lastActivityAt': 1790000001000,
        }
        with mock.patch.object(s, 'assert_stopped'):
            with self.assertRaisesRegex(s.SyncError, 'SESSION_MARKED_DELETED'):
                bridge.run_repair(self.hook, self.target, s, orphan)

    def test_unknown_target_and_ambiguous_organization_do_not_request_exit(self):
        self.init_profile(0); self.init_profile(0, base.A, base.OB)
        with mock.patch.object(bridge, 'graceful_close') as close:
            with self.assertRaisesRegex(bridge.BridgeError, 'MULTIPLE_PROFILE_PARTITIONS'):
                bridge.run(self.hook, self.target, s)
        close.assert_not_called()
        self.init_profile(1, base.B, base.OB)
        self.accounts[0]['dir'] = str(self.parent / 'Claude-Unknown')
        Path(self.accounts[0]['dir']).mkdir(); self.save_accounts()
        with self.assertRaisesRegex(bridge.BridgeError, 'TARGET_NOT_CONFIGURED'):
            bridge.discover(self.hook, self.target, s)

    def test_duplicate_directory_and_symlink_are_rejected(self):
        self.accounts[1]['dir'] = self.accounts[0]['dir']; self.save_accounts()
        with self.assertRaisesRegex(bridge.BridgeError, 'DUPLICATE_OR_INVALID'):
            bridge.discover(self.hook, self.target, s)
        self.accounts.pop(); self.save_accounts()
        actual = self.target.with_name('real'); self.target.rename(actual)
        self.target.symlink_to(actual, target_is_directory=True)
        with self.assertRaisesRegex(s.SyncError, 'SYMLINK_REFUSED'):
            bridge.discover(self.hook, self.target, s)

    def test_engine_hash_and_disabled_hook_are_blocked(self):
        with self.assertRaisesRegex(bridge.BridgeError, 'ENGINE_HASH_MISMATCH'):
            bridge.load_engine({**self.hook, 'engine_sha256': '0' * 64})
        with self.assertRaisesRegex(bridge.BridgeError, 'CASE_SYNC_NOT_ENABLED'):
            bridge.run({**self.hook, 'hook_enabled': False}, self.target, s)

    def test_switch_lock_prevents_overlapping_switches(self):
        with bridge.switch_lock(s, self.state):
            with self.assertRaisesRegex(bridge.BridgeError, 'SWITCH_ALREADY_RUNNING'):
                self.apply_offline()

    def test_cannot_proceed_when_claude_declines_normal_exit(self):
        with mock.patch.object(s, 'assert_stopped', side_effect=s.SyncError('CLAUDE_MUST_BE_FULLY_CLOSED')):
            with mock.patch.object(bridge.subprocess, 'run', return_value=mock.Mock(returncode=0)):
                with self.assertRaisesRegex(bridge.BridgeError, 'CLAUDE_STILL_RUNNING'):
                    bridge.graceful_close(s, timeout=0)

    def test_shutdown_is_not_requested_when_claude_is_already_closed(self):
        with mock.patch.object(s, 'assert_stopped'), mock.patch.object(bridge.subprocess, 'run') as proc:
            bridge.graceful_close(s)
        proc.assert_not_called()


if __name__ == '__main__': unittest.main()
