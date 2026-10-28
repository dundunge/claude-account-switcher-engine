#!/usr/bin/env python3
"""Preview or apply the ShipIt guard fix to an existing macOS sync engine.

Preserves the installed engine version, bridge, profiles and recovery journals.
Only the engine and its hook hash are changed, with backups and rollback.
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import re
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
UPDATER = '/Claude.app/Contents/Frameworks/Squirrel.framework/Resources/ShipIt'
LOOP = '    for command in p.stdout.decode(errors="replace").splitlines():\n'
ORIGINAL = LOOP + '        name = Path(command.strip()).name\n'
FIX = (
    LOOP
    + '        # Squirrel updates the app bundle, not a Desktop session. Exclude only\n'
    + '        # this exact updater; every other process in the bundle still blocks.\n'
    + f'        if sys.platform == "darwin" and command.strip().endswith("{UPDATER}"):\n'
    + '            continue\n'
    + '        name = Path(command.strip()).name\n'
)


def load_reviewed(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def patched_source(data):
    source = data.decode('utf-8')
    tree = ast.parse(source)
    version = next((n.value.value for n in tree.body
                    if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
                    and any(isinstance(t, ast.Name) and t.id == 'VERSION' for t in n.targets)), None)
    if version not in ('0.3.0', '0.4.0'):
        raise RuntimeError('UNSUPPORTED_ENGINE_VERSION')
    guards = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'assert_stopped']
    if len(guards) != 1:
        raise RuntimeError('UNRECOGNIZED_PROCESS_GUARD')
    guard = guards[0]
    for loop in guard.body:
        if not isinstance(loop, ast.For) or not isinstance(loop.target, ast.Name) or loop.target.id != 'command':
            continue
        for node in loop.body:
            if (isinstance(node, ast.If) and len(node.body) == 1
                    and isinstance(node.body[0], ast.Continue)
                    and any(isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                            and call.func.attr == 'endswith' and len(call.args) == 1
                            and isinstance(call.args[0], ast.Constant) and call.args[0].value == UPDATER
                            for call in ast.walk(node.test))):
                return data, version
    segment = ast.get_source_segment(source, guard)
    if ORIGINAL not in segment or source.count(ORIGINAL) != 1:
        raise RuntimeError('UNRECOGNIZED_PROCESS_GUARD')
    candidate = source.replace(ORIGINAL, FIX, 1).encode('utf-8')
    compile(candidate, '<patched-session-sync>', 'exec')
    return candidate, version


def patch(hook_path=None, apply=False):
    if sys.platform != 'darwin':
        raise RuntimeError('MACOS_REQUIRED')
    engine = load_reviewed('case_shipit_patch_engine', ROOT / 'engine/claude_session_sync.py')
    bridge = load_reviewed('case_shipit_patch_bridge', ROOT / 'tools/case_sync_bridge.py')
    hook_path = engine.absolute(str(hook_path or Path.home() / 'Library/Application Support/ClaudeSessionSync/CASE/hook.json'))
    bridge.checked(hook_path)
    before_hook = engine.read_file(hook_path)
    hook = engine.parse(before_hook.data)
    if not isinstance(hook, dict) or hook.get('schema_version') != 1 or hook.get('hook_enabled') is not True:
        raise RuntimeError('CASE_SYNC_NOT_ENABLED')
    runtime = hook_path.parent.parent
    engine_path = engine.absolute(hook['engine_path'])
    if engine_path != runtime / 'engine/claude_session_sync.py':
        raise RuntimeError('UNEXPECTED_ENGINE_LOCATION')
    bridge.checked(engine_path)
    before_engine = engine.read_file(engine_path)
    if engine.digest(before_engine.data) != hook.get('engine_sha256'):
        raise RuntimeError('ENGINE_HASH_MISMATCH')
    candidate, version = patched_source(before_engine.data)
    state = hook.get('state_dir')
    if hook.get('source_config_path'):
        state = bridge.read_json(hook['source_config_path'])['state_dir']
    state = engine.absolute(state)
    if state != runtime / 'state':
        raise RuntimeError('UNEXPECTED_STATE_LOCATION')
    config = engine.Config(None, None, state, ())
    result = {'status': 'unchanged' if candidate == before_engine.data else 'preview',
              'engine_version': version, 'engine_sha256': engine.digest(candidate),
              'unfinished_transactions': len(engine.unfinished(config))}
    if not apply or result['status'] == 'unchanged':
        return result
    engine.assert_stopped()
    with bridge.switch_lock(engine, state), engine.lock(config):
        engine.assert_stopped()
        if engine.read_file(engine_path) != before_engine or engine.read_file(hook_path) != before_hook:
            raise RuntimeError('INSTALLATION_CHANGED_RETRY')
        backup = runtime / 'backups' / ('shipit-fix-' + uuid.uuid4().hex)
        engine.mkdir_private(backup)
        for name, saved in [('engine-before.py', before_engine), ('hook-before.json', before_hook)]:
            engine.atomic_write(backup / name, engine.File(saved.data, saved.mtime, 0o600), creating=True)
        new_hook = engine.File(engine.encoded({**hook, 'engine_sha256': engine.digest(candidate)}), time.time_ns(), before_hook.mode)
        new_engine = engine.File(candidate, time.time_ns(), before_engine.mode)
        try:
            engine.assert_stopped()
            if engine.read_file(engine_path) != before_engine or engine.read_file(hook_path) != before_hook:
                raise RuntimeError('INSTALLATION_CHANGED_RETRY')
            engine.atomic_write(engine_path, new_engine)
            if engine.read_file(hook_path) != before_hook:
                raise RuntimeError('INSTALLATION_CHANGED_RETRY')
            engine.atomic_write(hook_path, new_hook)
            if engine.read_file(engine_path) != new_engine or engine.read_file(hook_path) != new_hook:
                raise RuntimeError('INSTALLATION_CHANGED_RETRY')
            result.update(status='patched', backup_directory=str(backup))
            engine.state_write(backup / 'result.json', result)
        except Exception:
            # Never replace a third party's edit while rolling our own writes back.
            for path, new, old in [(hook_path, new_hook, before_hook), (engine_path, new_engine, before_engine)]:
                current = engine.read_file(path)
                if current == new:
                    engine.atomic_write(path, old)
            raise
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hook', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(patch(args.hook, args.apply), sort_keys=True))
        return 0
    except Exception as error:
        code = getattr(error, 'code', None) or str(error)
        if not re.fullmatch(r'[A-Z0-9_]{1,80}', code):
            code = 'INVALID_INPUT_OR_IO_FAILURE'
        print(json.dumps({'status': 'blocked', 'code': code}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
