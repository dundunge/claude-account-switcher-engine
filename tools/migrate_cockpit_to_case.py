#!/usr/bin/env python3
"""Install the reviewed CASE local-sync build for the current macOS user.

First installation only. No credentials are read or copied. Run without --apply
for a read-only preview. Existing CASE installations/configurations are refused.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_engine(path):
    spec = importlib.util.spec_from_file_location("case_install_engine", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def inventory(root):
    """Stat only: no transcript contents are opened."""
    result = {}
    for current, directories, names in os.walk(root, followlinks=False):
        for name in directories + names:
            path = Path(current) / name
            info = path.lstat()
            result[str(path.relative_to(root))] = [info.st_size, info.st_mtime_ns,
                                                  info.st_ino, info.st_mode]
    return result


def install(apply=False):
    if sys.platform != "darwin":
        raise RuntimeError("MACOS_REQUIRED")
    home = Path.home()
    support = home / "Library/Application Support"
    runtime = support / "ClaudeSessionSync"
    old_config = runtime / "config.json"
    engine_path = runtime / "engine/claude_session_sync.py"
    bridge_path = runtime / "CASE/case_sync_bridge.py"
    hook_path = runtime / "CASE/hook.json"
    accounts_path = support / "CASE/accounts.json"
    app_source = ROOT / "target/release/bundle/macos/CASE.app"
    app_target = home / "Applications/CASE.app"
    binary_source = app_source / "Contents/MacOS/CASE"
    engine_source = ROOT / "engine/claude_session_sync.py"
    bridge_source = ROOT / "tools/case_sync_bridge.py"
    engine = load_engine(engine_source)
    if engine.VERSION != "0.4.0":
        raise RuntimeError("UNSUPPORTED_ENGINE")
    config = engine.Config.load(old_config)
    profiles = [support / "Claude-Pro-A", support / "Claude-Pro-B"]
    for path in [old_config, engine_path, bridge_path, hook_path, accounts_path,
                 app_source, app_target, support / "CASE", *profiles]:
        engine.safe_chain(path)
    for path in [bridge_path, hook_path, accounts_path, app_target,
                 Path('/Applications/CASE.app'), *profiles]:
        if path.exists():
            raise RuntimeError("EXISTING_CASE_OR_PROFILE_REQUIRES_REVIEW")
    if engine.unfinished(config):
        raise RuntimeError("RECOVERY_REQUIRED")
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app_source)],
                   check=True, capture_output=True)
    if b"CASE_LOCAL_SYNC_EDITION_V1" not in binary_source.read_bytes():
        raise RuntimeError("CUSTOM_BUILD_MARKER_MISSING")
    preview = {"status": "preview", "engine_version": engine.VERSION,
               "profiles_to_create": 2, "plan": engine.build_plan(config).summary(),
               "binary_sha256": sha(binary_source), "engine_sha256": sha(engine_source),
               "bridge_sha256": sha(bridge_source)}
    if not apply:
        return preview

    engine.assert_stopped()
    sid = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + uuid.uuid4().hex[:8]
    backup = ROOT / 'artifacts' / ('case-deployment-' + sid)
    engine.mkdir_private(backup)
    journal = {**preview, "status": "installing", "backup_directory": str(backup),
               "app_path": str(app_target), "source_config_sha256": sha(old_config),
               "previous_engine_sha256": sha(engine_path), "steps": []}
    journal_path = backup / 'deployment.json'

    def record(step):
        journal['steps'].append(step)
        engine.state_write(journal_path, journal)

    with engine.lock(config):
        engine.assert_stopped()
        shutil.copy2(engine_path, backup / 'previous-engine.py')
        shutil.copy2(old_config, backup / 'previous-config.json')
        engine.state_write(backup / 'transcript-stat-before.json', inventory(config.projects_root))
        record('backed_up_engine_config_and_transcript_stats')
        try:
            # Stage the complete signed app before replacing any runtime code.
            stage = app_target.with_name('CASE-install-' + sid + '.app')
            shutil.copytree(app_source, stage, symlinks=True)
            subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(stage)],
                           check=True, capture_output=True)
            record('staged_verified_app')
            for directory in profiles:
                directory.mkdir(mode=0o700)
            engine.mkdir_private(accounts_path.parent)
            accounts = {'version': 2, 'accounts': [
                {'id': str(uuid.uuid4()), 'name': name, 'dir': str(directory),
                 'icon': 'briefcase', 'hue': hue}
                for name, directory, hue in zip(('Pro A', 'Pro B'), profiles, (35, 205))],
                'settings': {'autoUpdateCheck': False}, 'archived': []}
            engine.atomic_write(accounts_path, engine.File(engine.encoded(accounts), time.time_ns(), 0o600), creating=True)
            record('created_two_blank_profiles_and_case_account_list')
            engine.mkdir_private(bridge_path.parent)
            engine.atomic_write(engine_path, engine.File(engine_source.read_bytes(), time.time_ns(), 0o700))
            engine.atomic_write(bridge_path, engine.File(bridge_source.read_bytes(), time.time_ns(), 0o700), creating=True)
            hook = {'schema_version': 1, 'hook_enabled': True,
                    'python_path': '/usr/bin/python3', 'bridge_path': str(bridge_path),
                    'bridge_version': '0.4.0', 'bridge_sha256': sha(bridge_source),
                    'engine_path': str(engine_path), 'engine_sha256': sha(engine_path),
                    'source_config_path': str(old_config),
                    'case_accounts_path': str(accounts_path), 'profiles_parent': str(support)}
            engine.atomic_write(hook_path, engine.File(engine.encoded(hook), time.time_ns(), 0o600), creating=True)
            record('installed_engine_bridge_and_hook')
            engine.assert_stopped()
            stage.rename(app_target)
            record('installed_signed_case_app')
            if sha(old_config) != journal['source_config_sha256']:
                raise RuntimeError('SOURCE_CONFIG_CHANGED')
            if inventory(config.projects_root) != json.loads((backup / 'transcript-stat-before.json').read_bytes()):
                raise RuntimeError('TRANSCRIPT_METADATA_CHANGED')
            journal.update(status='installed', bridge_sha256=sha(bridge_path),
                           hook_sha256=sha(hook_path), binary_sha256=sha(app_target/'Contents/MacOS/CASE'),
                           transcript_metadata_unchanged=True)
            record('verified_source_config_and_transcript_metadata_unchanged')
        except Exception:
            journal['status'] = 'incomplete_review_journal_before_retry'
            engine.state_write(journal_path, journal)
            raise
    return {'status': journal['status'], 'deployment_record': str(journal_path),
            'engine_version': engine.VERSION, 'profiles_created': 2,
            'transcript_metadata_unchanged': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        print(json.dumps(install(args.apply), sort_keys=True))
        return 0
    except Exception as error:
        code = getattr(error, 'code', None) or (str(error) if isinstance(error, RuntimeError) else type(error).__name__)
        print(json.dumps({'status': 'blocked', 'code': code}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
