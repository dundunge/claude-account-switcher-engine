#!/usr/bin/env python3
"""CASE launch hook: discover explicit profiles, quit normally, sync indexes.

No credentials, transcript reads, network requests, or force termination.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time


class BridgeError(Exception):
    pass


MAX_ACCOUNTS = 8
DEFAULT_PROFILE_NAME = "Claude"
BRIDGE_VERSION = "0.4.0"


def progress(stage):
    # Progress is a side channel for CASE's UI.  Keep stdout as one final JSON
    # document so existing launch hooks and offline callers remain compatible.
    if os.environ.get("CASE_PROGRESS") != "1":
        return
    print(f"CASE_STAGE {stage}", file=sys.stderr, flush=True)


def valid_profile_name(name):
    return name == DEFAULT_PROFILE_NAME or (
        name.startswith("Claude-") and len(name) > len("Claude-")
    )


def checked(path):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise BridgeError("ABSOLUTE_PATH_REQUIRED")
    for p in [*reversed(path.parents), path]:
        if p.is_symlink() or p.exists() and getattr(p.lstat(), 'st_file_attributes', 0) & 0x400:
            raise BridgeError("SYMLINK_REFUSED")
    info = path.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or os.name != 'nt' and (info.st_mode & 0o022 or info.st_uid not in (0, os.getuid()))
            or info.st_size > 1024 * 1024):
        raise BridgeError("UNSAFE_HOOK_FILE")
    return path


def read_json(path):
    value = json.loads(checked(path).read_bytes())
    if not isinstance(value, dict):
        raise BridgeError("INVALID_HOOK_CONFIG")
    return value


def load_engine(config):
    path = checked(config["engine_path"])
    if hashlib.sha256(path.read_bytes()).hexdigest() != config["engine_sha256"]:
        raise BridgeError("ENGINE_HASH_MISMATCH")
    spec = importlib.util.spec_from_file_location("case_session_sync_engine", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if module.VERSION not in ("0.3.0", "0.4.0"):
        raise BridgeError("UNSUPPORTED_ENGINE_VERSION")
    return module


def check_bridge_hash(config):
    configured_version = config.get("bridge_version")
    if configured_version is not None and configured_version not in ('0.3.0', BRIDGE_VERSION):
        raise BridgeError("UNSUPPORTED_BRIDGE_VERSION")
    expected = config.get("bridge_sha256")
    if expected is None:
        return  # Existing installations can be upgraded once by the installer.
    if (not isinstance(expected, str) or
            not re.fullmatch(r"[0-9a-f]{64}", expected) or
            hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != expected):
        raise BridgeError("BRIDGE_HASH_MISMATCH")


def discover(config, target, engine):
    """Only CASE's explicit account list and the old opt-in partitions are used."""
    legacy = engine.Config.load(checked(config["source_config_path"])) if config.get('source_config_path') else None
    if legacy and legacy.locations:
        raise BridgeError("LEGACY_SCHEMA_ONE_REQUIRED")
    accounts = read_json(config["case_accounts_path"]).get("accounts")
    if not isinstance(accounts, list) or not 1 <= len(accounts) <= MAX_ACCOUNTS:
        raise BridgeError("CASE_ACCOUNTS_REQUIRED")
    parents = [engine.absolute(p) for p in config.get('profiles_parents', [config.get('profiles_parent')])]
    target = engine.absolute(str(target))
    for parent in parents: engine.safe_chain(parent)
    partitions = [{"profile": "legacy-" + str(i), "index_root": str(legacy.index_root),
                   "account": peer.split('/')[0], "organization": peer.split('/')[1]}
                  for i, peer in enumerate(legacy.peers)] if legacy else []
    seen_dirs, seen_ids, target_seen, bootstrap = set(), set(), False, False
    initialized = 0
    for row in accounts:
        directory = engine.absolute(row["dir"])
        identity = row["id"]
        if (not isinstance(identity, str) or not re.fullmatch(engine.UUID, identity)
                or directory in seen_dirs or identity in seen_ids):
            raise BridgeError("DUPLICATE_OR_INVALID_CASE_ACCOUNT")
        if directory.parent not in parents or not valid_profile_name(directory.name):
            raise BridgeError("UNEXPECTED_PROFILE_LOCATION")
        engine.safe_chain(directory)
        if not directory.is_dir():
            raise BridgeError("PROFILE_DIRECTORY_MISSING")
        seen_dirs.add(directory); seen_ids.add(identity)
        target_seen |= directory == target
        root = directory / "claude-code-sessions"
        engine.safe_chain(root)
        candidates = []
        if root.exists():
            children = list(root.iterdir())
            if len(children) > 32:
                raise BridgeError("TOO_MANY_PROFILE_PARTITIONS")
            for account in children:
                engine.safe_chain(account)
                if not account.is_dir() or not re.fullmatch(engine.UUID, account.name):
                    continue
                orgs = list(account.iterdir())
                if len(orgs) > 32:
                    raise BridgeError("TOO_MANY_PROFILE_PARTITIONS")
                for org in orgs:
                    engine.safe_chain(org)
                    if org.is_dir() and re.fullmatch(engine.UUID, org.name):
                        candidates.append((account.name, org.name))
        # Selecting an organization is an explicit later migration, never guessed.
        if len(candidates) > 1:
            raise BridgeError("MULTIPLE_PROFILE_PARTITIONS")
        if not candidates:
            bootstrap |= directory == target
            continue
        initialized += 1
        account, org = candidates[0]
        partitions.append({"profile": "case-" + identity.replace('-', ''),
                           "index_root": str(root), "account": account, "organization": org})
    if not target_seen:
        raise BridgeError("TARGET_NOT_CONFIGURED_IN_CASE")
    value = {"schema_version": 2, "projects_root": str(legacy.projects_root if legacy else engine.absolute(config['projects_root'])),
             "state_dir": str(legacy.state_dir if legacy else engine.absolute(config['state_dir'])), "partitions": partitions}
    return value, bootstrap, initialized


QUIT_SCRIPT = '''ObjC.import('AppKit');
function run() {
  var apps = $.NSRunningApplication.runningApplicationsWithBundleIdentifier('com.anthropic.claudefordesktop');
  for (var i = 0; i < Number(apps.count); i++) {
    apps.objectAtIndex(i).terminate;
  }
  return '';
}'''


def graceful_close(engine, timeout=15):
    try:
        engine.assert_stopped()
        return
    except engine.SyncError as error:
        if error.code != "CLAUDE_MUST_BE_FULLY_CLOSED":
            raise
    if sys.platform != 'darwin':
        # A window-close request is the only permitted automation. Wayland and
        # windowless/background instances must be quit by the user instead.
        if os.name == 'nt':
            command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                "Get-Process -Name claude,claude-desktop -ErrorAction SilentlyContinue | ForEach-Object { if ($_.MainWindowHandle -ne 0) { [void]$_.CloseMainWindow() } }"]
        else:
            result = subprocess.run(['wmctrl', '-lp'], capture_output=True, timeout=5)
            if result.returncode: raise BridgeError('CLAUDE_QUIT_REQUEST_FAILED')
            # Match PIDs by their executable names; never infer from window titles.
            windows = []
            for line in result.stdout.decode(errors='replace').splitlines():
                cols = line.split(None, 4)
                if len(cols) < 3 or not cols[2].isdigit(): continue
                try: name = Path(os.readlink('/proc/' + cols[2] + '/exe')).name.lower()
                except OSError: continue
                if name in ('claude', 'claude-desktop'): windows.append(cols[0])
            if not windows: raise BridgeError('CLAUDE_STILL_RUNNING')
            command = None
            for window in windows:
                subprocess.run(['wmctrl', '-ic', window], check=True, timeout=5)
        if command:
            subprocess.run(command, check=True, timeout=8, **({'creationflags': 0x08000000} if os.name == 'nt' else {}))
        deadline = time.monotonic() + timeout
        while True:
            try: engine.assert_stopped(); return
            except engine.SyncError as error:
                if error.code != 'CLAUDE_MUST_BE_FULLY_CLOSED': raise
            if time.monotonic() >= deadline: raise BridgeError('CLAUDE_STILL_RUNNING')
            time.sleep(.2)
    # NSRunningApplication.terminate requests normal application termination.
    # It does not send SIGTERM/SIGKILL and does not bypass a task/quit dialog.
    try:
        result = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', '-e', QUIT_SCRIPT],
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
        if result.returncode:
            raise BridgeError("CLAUDE_QUIT_REQUEST_FAILED")
    except (OSError, subprocess.SubprocessError):
        raise BridgeError("CLAUDE_QUIT_REQUEST_FAILED") from None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            engine.assert_stopped()
            return
        except engine.SyncError as error:
            if error.code != "CLAUDE_MUST_BE_FULLY_CLOSED":
                raise
        time.sleep(0.2)
    raise BridgeError("CLAUDE_STILL_RUNNING")


@contextlib.contextmanager
def switch_lock(engine, state):
    engine.mkdir_private(state)
    path = state / 'case-switch.lock'
    engine.safe_chain(path)
    # Existing macOS 0.3.0 engines do not expose the new portable helpers.
    fd = engine.safe_open(path, os.O_CREAT | os.O_RDWR) if hasattr(engine, 'safe_open') else os.open(str(path), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise BridgeError("INVALID_SWITCH_LOCK")
        try:
            if hasattr(engine, 'lock_fd'):
                engine.lock_fd(fd)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, PermissionError):
            raise BridgeError("SWITCH_ALREADY_RUNNING") from None
        yield
    finally:
        os.close(fd)


def run(config, target, engine, dry_run=False):
    if config.get("schema_version") != 1 or config.get("hook_enabled") is not True:
        raise BridgeError("CASE_SYNC_NOT_ENABLED")
    value, bootstrap, initialized = discover(config, target, engine)
    if len(value['partitions']) < 2:
        # First sign-in needs no index writes. Keep normal-exit and scope
        # checks, then let the user initialize the first/second official profile.
        pending = engine.Config(None, engine.absolute(value['projects_root']), engine.absolute(value['state_dir']), ())
        if engine.unfinished(pending): raise BridgeError('RECOVERY_REQUIRED')
        if not dry_run:
            graceful_close(engine)
            if discover(config, target, engine)[0] != value:
                raise BridgeError('PROFILE_SCOPE_CHANGED_RETRY')
        return {'status': 'preview' if dry_run else 'unchanged', 'bootstrap': bootstrap,
                'initialized_profiles': initialized, 'partition_count': len(value['partitions'])}
    c = engine.Config.load_multi(value)
    if engine.unfinished(c):
        raise BridgeError("RECOVERY_REQUIRED")
    details = {"bootstrap": bootstrap, "initialized_profiles": initialized,
               "partition_count": len(c.peers)}
    if dry_run:
        return {"status": "preview", **details, **engine.build_plan(c).summary()}
    with switch_lock(engine, c.state_dir):
        # Check the configured paths before requesting any application to close.
        progress("waiting_exit")
        graceful_close(engine)
        # Re-discover after exit so the last completed save and newly created
        # account/organization directory are included, but never silently swap roots.
        progress("validate")
        fresh, bootstrap, initialized = discover(config, target, engine)
        if fresh != value:
            raise BridgeError("PROFILE_SCOPE_CHANGED_RETRY")
        config_dir = c.state_dir / "case-runtime-configs"
        engine.mkdir_private(config_dir)
        data = engine.encoded(value)
        runtime = config_dir / (hashlib.sha256(data).hexdigest() + '.json')
        old = engine.read_file(runtime)
        if old is None:
            engine.atomic_write(runtime, engine.File(data, time.time_ns(), 0o600), creating=True)
        elif old.data != data:
            raise BridgeError("RUNTIME_CONFIG_CHANGED")
        progress("sync")
        result = engine.apply(c, engine.build_plan(c))
        output = {**result, **details, "runtime_config_path": str(runtime)}
        engine.state_write(c.state_dir / 'case-last-result.json', output)
        progress("completed")
        return output


def _repair_partition(value, target):
    root = Path(target) / 'claude-code-sessions'
    matches = [p for p in value['partitions']
               if Path(p['index_root']) == root]
    if len(matches) != 1:
        raise BridgeError('REPAIR_PARTITION_UNRESOLVED')
    p = matches[0]
    return f"{p['profile']}/{p['account']}/{p['organization']}"


def _repair_entry(orphan, engine):
    if not isinstance(orphan, dict):
        raise BridgeError('INVALID_REPAIR_ENTRY')
    local_id = orphan.get('localId')
    cli_id = orphan.get('cliSessionId')
    if (not isinstance(local_id, str) or not re.fullmatch('local_' + engine.UUID, local_id)
            or not isinstance(cli_id, str) or not re.fullmatch(engine.UUID, cli_id)):
        raise BridgeError('INVALID_REPAIR_ENTRY')
    cwd = orphan.get('cwd')
    if not isinstance(cwd, str) or not Path(cwd).is_absolute() or '..' in Path(cwd).parts:
        raise BridgeError('INVALID_REPAIR_ENTRY')
    def timestamp(key):
        value = orphan.get(key)
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise BridgeError('INVALID_REPAIR_ENTRY')
        return int(round(value))
    entry = {
        'sessionId': local_id, 'cliSessionId': cli_id, 'cwd': cwd,
        'originCwd': cwd, 'createdAt': timestamp('createdAt'),
        'lastFocusedAt': timestamp('lastActivityAt'),
        'lastActivityAt': timestamp('lastActivityAt'),
        'title': orphan.get('title') if isinstance(orphan.get('title'), str) else 'Untitled session',
        'titleSource': 'user', 'isArchived': False,
    }
    if isinstance(orphan.get('model'), str) and orphan['model']:
        entry['model'] = orphan['model']
    return local_id, entry


def run_repair(config, target, engine, orphan, dry_run=False):
    if config.get('schema_version') != 1 or config.get('hook_enabled') is not True:
        raise BridgeError('CASE_SYNC_NOT_ENABLED')
    progress("validate")
    value, bootstrap, initialized = discover(config, target, engine)
    if bootstrap:
        raise BridgeError('REPAIR_REQUIRES_INITIALIZED_PROFILE')
    c = engine.Config.load_multi(value)
    if engine.unfinished(c):
        raise BridgeError('RECOVERY_REQUIRED')
    local_id, entry = _repair_entry(orphan, engine)
    peer = _repair_partition(value, target)
    relative = peer + '/' + local_id + '.json'
    if dry_run:
        return {'status': 'repair_preview', 'partition': peer, 'sessionId': local_id}
    with switch_lock(engine, c.state_dir):
        engine.assert_stopped()
        fresh, fresh_bootstrap, fresh_initialized = discover(config, target, engine)
        if fresh != value or fresh_bootstrap:
            raise BridgeError('PROFILE_SCOPE_CHANGED_RETRY')
        progress("sync")
        result = engine.rebuild(c, relative, entry)
        output = {**result, 'partition': peer, 'sessionId': local_id}
        engine.state_write(c.state_dir / 'case-last-result.json', output)
        progress("completed")
        return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--repair', action='store_true')
    args = parser.parse_args(argv)
    try:
        config = read_json(args.config)
        check_bridge_hash(config)
        engine = load_engine(config)
        if args.repair:
            orphan = json.load(sys.stdin)
            result = run_repair(config, Path(args.target), engine, orphan, args.dry_run)
        else:
            result = run(config, Path(args.target), engine, args.dry_run)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as error:
        code = getattr(error, 'code', None)
        if code is None and isinstance(error, BridgeError):
            code = str(error)
        if not isinstance(code, str) or not re.fullmatch(r'[A-Z0-9_]{1,80}', code):
            code = 'INVALID_INPUT_OR_IO_FAILURE'
        print(json.dumps({'status': 'blocked', 'code': code,
                          'snapshot': getattr(error, 'snapshot', None)}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
