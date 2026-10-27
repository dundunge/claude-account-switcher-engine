#!/usr/bin/env python3
"""Preview, or explicitly initialize, local sync for an existing CASE account list.

No Claude authentication files, transcripts or network are read. Existing hooks
are never overwritten. Closing Claude is left to the user during installation.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def prepare(apply=False):
    spec = importlib.util.spec_from_file_location('case_prepare_engine', ROOT / 'engine/claude_session_sync.py')
    engine = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = engine
    spec.loader.exec_module(engine)
    if sys.platform == 'darwin': support = Path.home() / 'Library/Application Support'
    elif os.name == 'nt': support = Path(os.environ['APPDATA'])
    elif sys.platform == 'linux': support = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
    else: raise RuntimeError('PLATFORM_UNSUPPORTED')
    runtime = support / 'ClaudeSessionSync'
    hook_path = runtime / 'CASE/hook.json'
    accounts = support / 'CASE/accounts.json'
    for path in (runtime, hook_path, accounts): engine.safe_chain(path)
    if hook_path.exists(): raise RuntimeError('EXISTING_HOOK_REQUIRES_REVIEW')
    listing = json.loads(engine.read_file(accounts).data)
    rows = listing.get('accounts', [])
    if not isinstance(rows, list) or not 1 <= len(rows) <= 8: raise RuntimeError('ADD_CASE_ACCOUNTS_FIRST')
    parents = [support]
    if os.name == 'nt': parents.append(Path(os.environ['LOCALAPPDATA']))
    for row in rows:
        path = engine.absolute(row['dir'])
        engine.safe_chain(path)
        if path.parent not in parents or not (path.name == 'Claude' or path.name.startswith('Claude-')):
            raise RuntimeError('UNEXPECTED_PROFILE_LOCATION')
    projects = Path.home() / '.claude/projects'
    state = runtime / 'state'
    sources = {'engine/claude_session_sync.py': ROOT / 'engine/claude_session_sync.py',
               'CASE/case_sync_bridge.py': ROOT / 'tools/case_sync_bridge.py'}
    for name in sources:
        engine.safe_chain(runtime / name)
        if (runtime / name).exists(): raise RuntimeError('EXISTING_RUNTIME_REQUIRES_REVIEW')
    hook = {'schema_version': 1, 'hook_enabled': True, 'python_path': str(Path(sys.executable).resolve()),
            'engine_path': str(runtime / 'engine/claude_session_sync.py'),
            'engine_sha256': hashlib.sha256(sources['engine/claude_session_sync.py'].read_bytes()).hexdigest(),
            'bridge_path': str(runtime / 'CASE/case_sync_bridge.py'), 'bridge_version': '0.4.0',
            'bridge_sha256': hashlib.sha256(sources['CASE/case_sync_bridge.py'].read_bytes()).hexdigest(),
            'case_accounts_path': str(accounts), 'profiles_parents': [str(p) for p in parents],
            'projects_root': str(projects), 'state_dir': str(state)}
    if apply:
        engine.assert_stopped()
        engine.mkdir_private(projects)
        for name, source in sources.items():
            path = runtime / name
            engine.mkdir_private(path.parent)
            engine.atomic_write(path, engine.File(source.read_bytes(), time.time_ns(), 0o600), creating=True)
        engine.mkdir_private(hook_path.parent)
        engine.atomic_write(hook_path, engine.File(engine.encoded(hook), time.time_ns(), 0o600), creating=True)
    return {'status': 'initialized' if apply else 'preview', 'account_count': len(rows),
            'engine_version': engine.VERSION, 'hook_path': str(hook_path)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.apply)))
    except Exception as error:
        print(json.dumps({'status': 'blocked', 'code': getattr(error, 'code', None) or str(error)}))
        sys.exit(2)
