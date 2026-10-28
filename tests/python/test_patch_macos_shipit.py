import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import test_engine as base
from test_case_bridge import bridge

spec = importlib.util.spec_from_file_location('case_shipit_patch_test', base.ENGINE.parents[1] / 'tools/patch_macos_shipit.py')
hotfix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hotfix)

GUARD_FIX = '''        # Squirrel's updater does not host a Desktop profile. A failed update
        # can keep restarting it after Claude exits, so recognize only this
        # exact helper path. Every other process in the bundle still blocks.
        if sys.platform == 'darwin' and command.strip().endswith("/Claude.app/Contents/Frameworks/Squirrel.framework/Resources/ShipIt"):
            continue
'''


def unpatched_source(version='0.4.0'):
    source = base.ENGINE.read_text()
    if source.count(GUARD_FIX) != 1:
        raise AssertionError('Update the historical engine fixture when changing its guard.')
    return source.replace(GUARD_FIX, '').replace('VERSION = "0.4.0"', f'VERSION = "{version}"').encode()


class SourcePatchTests(unittest.TestCase):
    def test_both_supported_versions_preserve_all_other_code(self):
        for version in ('0.3.0', '0.4.0'):
            with self.subTest(version=version):
                original = unpatched_source(version)
                candidate, observed = hotfix.patched_source(original)
                self.assertEqual(observed, version)
                self.assertEqual(candidate.decode().replace(hotfix.FIX, hotfix.ORIGINAL).encode(), original)
                self.assertEqual(hotfix.patched_source(candidate), (candidate, version))

    def test_the_repository_and_prior_local_hotfix_are_already_patched(self):
        self.assertEqual(hotfix.patched_source(base.ENGINE.read_bytes())[0], base.ENGINE.read_bytes())
        candidate, version = hotfix.patched_source(unpatched_source('0.3.0'))
        local = candidate.replace(b'sys.platform == "darwin" and ', b'')
        self.assertEqual(hotfix.patched_source(local), (local, version))

    def test_unknown_versions_and_customized_guards_are_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'UNSUPPORTED_ENGINE_VERSION'):
            hotfix.patched_source(unpatched_source('9.0.0'))
        customized = unpatched_source().replace(b'name = Path(command.strip()).name', b'name = command.strip()')
        with self.assertRaisesRegex(RuntimeError, 'UNRECOGNIZED_PROCESS_GUARD'):
            hotfix.patched_source(customized)


@unittest.skipUnless(sys.platform == 'darwin', 'The installer only supports macOS')
class InstalledPatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.runtime = self.root / 'ClaudeSessionSync'
        self.state = self.runtime / 'state'
        self.state.mkdir(parents=True)
        self.engine_path = self.runtime / 'engine/claude_session_sync.py'
        self.hook_path = self.runtime / 'CASE/hook.json'
        self.source_config = self.runtime / 'config.json'
        self.original_engine = unpatched_source('0.3.0')
        self.write(self.engine_path, self.original_engine)
        self.write(self.source_config, base.s.encoded({'state_dir': str(self.state)}))
        self.hook = {'schema_version': 1, 'hook_enabled': True, 'engine_path': str(self.engine_path),
                     'engine_sha256': base.s.digest(self.original_engine),
                     'source_config_path': str(self.source_config), 'custom_setting': 'preserve'}
        self.write(self.hook_path, base.s.encoded(self.hook))
        self.sentinel = self.root / 'profile/claude-code-sessions/untouched.json'
        self.write(self.sentinel, b'ACCOUNT INDEX MUST STAY UNCHANGED')
        self.body = self.root / 'projects/session.jsonl'
        self.write(self.body, b'TRANSCRIPT BODY MUST NOT BE READ OR CHANGED')
        patcher = mock.patch.object(hotfix, 'load_reviewed', side_effect=lambda name, path: base.s if name.endswith('_engine') else bridge)
        patcher.start(); self.addCleanup(patcher.stop)
        patcher = mock.patch.object(base.s, 'assert_stopped')
        self.guard = patcher.start(); self.addCleanup(patcher.stop)

    def write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o600)

    def tree(self):
        return {str(p.relative_to(self.root)): (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_mode)
                for p in self.root.rglob('*') if p.is_file()}

    def test_preview_is_read_only(self):
        before = self.tree()
        self.assertEqual(hotfix.patch(self.hook_path)['status'], 'preview')
        self.assertEqual(self.tree(), before)
        self.guard.assert_not_called()

    def test_apply_backs_up_and_pins_only_the_minimal_patch(self):
        self.write(self.state / 'snapshots/pending/manifest.json', base.s.encoded({'schema_version': 2, 'state': 'interrupted'}))
        protected = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (self.body, self.sentinel, self.source_config, self.state / 'snapshots/pending/manifest.json')}
        result = hotfix.patch(self.hook_path, True)
        self.assertEqual(result['status'], 'patched')
        self.assertEqual(result['engine_version'], '0.3.0')
        self.assertEqual(result['unfinished_transactions'], 1)
        saved = Path(result['backup_directory'])
        self.assertEqual((saved / 'engine-before.py').read_bytes(), self.original_engine)
        self.assertEqual(json.loads((saved / 'hook-before.json').read_bytes()), self.hook)
        updated = json.loads(self.hook_path.read_bytes())
        self.assertEqual(updated, {**self.hook, 'engine_sha256': base.s.digest(self.engine_path.read_bytes())})
        self.assertEqual(protected, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in protected})
        before = self.tree()
        self.guard.side_effect = AssertionError('An idempotent check must not request process exit')
        self.assertEqual(hotfix.patch(self.hook_path, True)['status'], 'unchanged')
        self.assertEqual(self.tree(), before)

    def test_hash_mismatch_blocks_all_writes(self):
        self.write(self.engine_path, self.original_engine + b'\n# unexpected edit\n')
        before = self.tree()
        with self.assertRaisesRegex(RuntimeError, 'ENGINE_HASH_MISMATCH'):
            hotfix.patch(self.hook_path, True)
        self.assertEqual(self.tree(), before)

    def test_running_desktop_blocks_before_any_write(self):
        before = self.tree()
        self.guard.side_effect = base.s.SyncError('CLAUDE_MUST_BE_FULLY_CLOSED')
        with self.assertRaisesRegex(base.s.SyncError, 'CLAUDE_MUST_BE_FULLY_CLOSED'):
            hotfix.patch(self.hook_path, True)
        self.assertEqual(self.tree(), before)

    def test_concurrent_engine_operation_blocks_installation(self):
        config = base.s.Config(None, None, self.state, ())
        with base.s.lock(config):
            with self.assertRaisesRegex(base.s.SyncError, 'SYNC_ALREADY_RUNNING'):
                hotfix.patch(self.hook_path, True)
        self.assertEqual(self.engine_path.read_bytes(), self.original_engine)
        self.assertEqual(json.loads(self.hook_path.read_bytes()), self.hook)

    def test_hook_write_failure_rolls_the_engine_back(self):
        saved_engine = base.s.read_file(self.engine_path)
        saved_hook = base.s.read_file(self.hook_path)
        original_write = base.s.atomic_write
        failed = False

        def fail_once(path, value, creating=False):
            nonlocal failed
            if path == self.hook_path and not failed:
                failed = True
                raise OSError('simulated hook write failure')
            return original_write(path, value, creating)

        with mock.patch.object(base.s, 'atomic_write', side_effect=fail_once):
            with self.assertRaises(OSError):
                hotfix.patch(self.hook_path, True)
        self.assertEqual(base.s.read_file(self.engine_path), saved_engine)
        self.assertEqual(base.s.read_file(self.hook_path), saved_hook)
