#!/usr/bin/env python3
"""CASE launch hook: discover explicit profiles, quit normally, sync indexes.

No credentials, transcript reads, network requests, or force termination.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time


class BridgeError(Exception):
    pass


def checked(path):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise BridgeError("ABSOLUTE_PATH_REQUIRED")
    for p in [*reversed(path.parents), path]:
        if p.is_symlink():
            raise BridgeError("SYMLINK_REFUSED")
    info = path.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_mode & 0o022 or info.st_uid not in (0, os.getuid())
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
    if module.VERSION != "0.3.0":
        raise BridgeError("UNSUPPORTED_ENGINE_VERSION")
    return module


def discover(config, target, engine):
    """Only CASE's explicit account list and the old opt-in partitions are used."""
    legacy = engine.Config.load(checked(config["source_config_path"]))
    if legacy.locations:
        raise BridgeError("LEGACY_SCHEMA_ONE_REQUIRED")
    accounts = read_json(config["case_accounts_path"]).get("accounts")
    if not isinstance(accounts, list) or not 1 <= len(accounts) <= 8:
        raise BridgeError("CASE_ACCOUNTS_REQUIRED")
    parent = engine.absolute(config["profiles_parent"])
    target = engine.absolute(str(target))
    engine.safe_chain(parent)
    partitions = [{"profile": "legacy-" + str(i), "index_root": str(legacy.index_root),
                   "account": peer.split('/')[0], "organization": peer.split('/')[1]}
                  for i, peer in enumerate(legacy.peers)]
    seen_dirs, seen_ids, target_seen, bootstrap = set(), set(), False, False
    initialized = 0
    for row in accounts:
        directory = engine.absolute(row["dir"])
        identity = row["id"]
        if (not isinstance(identity, str) or not re.fullmatch(engine.UUID, identity)
                or directory in seen_dirs or identity in seen_ids):
            raise BridgeError("DUPLICATE_OR_INVALID_CASE_ACCOUNT")
        if directory.parent != parent or not directory.name.startswith("Claude-"):
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
    value = {"schema_version": 2, "projects_root": str(legacy.projects_root),
             "state_dir": str(legacy.state_dir), "partitions": partitions}
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
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise BridgeError("INVALID_SWITCH_LOCK")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BridgeError("SWITCH_ALREADY_RUNNING") from None
        yield
    finally:
        os.close(fd)


def run(config, target, engine, dry_run=False):
    if config.get("schema_version") != 1 or config.get("hook_enabled") is not True:
        raise BridgeError("CASE_SYNC_NOT_ENABLED")
    value, bootstrap, initialized = discover(config, target, engine)
    c = engine.Config.load_multi(value)
    if engine.unfinished(c):
        raise BridgeError("RECOVERY_REQUIRED")
    details = {"bootstrap": bootstrap, "initialized_profiles": initialized,
               "partition_count": len(c.peers)}
    if dry_run:
        return {"status": "preview", **details, **engine.build_plan(c).summary()}
    with switch_lock(engine, c.state_dir):
        # Check the configured paths before requesting any application to close.
        graceful_close(engine)
        # Re-discover after exit so the last completed save and newly created
        # account/organization directory are included, but never silently swap roots.
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
        result = engine.apply(c, engine.build_plan(c))
        output = {**result, **details, "runtime_config_path": str(runtime)}
        engine.state_write(c.state_dir / 'case-last-result.json', output)
        return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    try:
        config = read_json(args.config)
        engine = load_engine(config)
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
