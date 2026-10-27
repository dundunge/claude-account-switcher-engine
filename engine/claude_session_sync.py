#!/usr/bin/env python3
"""Offline Claude Desktop Code index sync. No credentials, network, or daemon."""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
import uuid

VERSION = "0.4.0"
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
LOCAL = re.compile(r"local_(" + UUID + r")\.json\Z")
DELETED = re.compile(r"deleted_((?:local_)?" + UUID + r")\Z")
PORTABLE = frozenset({
    "sessionId", "cliSessionId", "title", "titleSource", "titleTurn", "cwd",
    "originCwd", "createdAt", "lastActivityAt", "indexedAt", "lastFocusedAt",
    "isArchived", "model", "effort", "effortInherited", "branch", "sourceBranch",
    "worktreeName", "worktreePath", "priorCliSessionIds", "preClearCliSessionId",
})
HIERARCHY = frozenset({
    "spawnedFrom", "forkedFromSessionId", "forkedAtMessageUuid", "lineageDetached",
})
# These describe a historical relationship, not a request to resume work or
# inherit another account's folder/checkout permissions.
SPAWN_PORTABLE = frozenset({
    "sessionId", "taskId", "title", "name", "sideSession", "userFork",
    "handOff", "btw", "deletedParent",
})
SPAWN_FLAGS = frozenset({"sideSession", "userFork", "handOff", "btw"})
MAX_BYTES = 8 * 1024 * 1024
MAX_ENTRIES = 10000

# ``link(temp, destination); unlink(temp)`` is not a transaction: a process
# exit between the two syscalls leaves a destination with two hard links.  The
# next read quite correctly rejects that inode, which used to strand the
# recovery journal.  macOS and Linux both expose an atomic no-replace rename;
# use it when available and keep a conservative create-new fallback for other
# platforms.
AT_FDCWD = -2
RENAME_EXCL_MACOS = 0x00000004
RENAME_NOREPLACE_LINUX = 1


def _rename_noreplace(source, destination):
    if os.name == 'nt':
        # Windows rename is atomic and refuses an existing destination.
        os.rename(source, destination)
        return
    source_b, destination_b = os.fsencode(source), os.fsencode(destination)
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin" and hasattr(libc, "renameatx_np"):
        fn = libc.renameatx_np
        fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                       ctypes.c_char_p, ctypes.c_uint]
        fn.restype = ctypes.c_int
        if fn(AT_FDCWD, source_b, AT_FDCWD, destination_b, RENAME_EXCL_MACOS) == 0:
            return
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), destination)
    if sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        fn = libc.renameat2
        fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                       ctypes.c_char_p, ctypes.c_uint]
        fn.restype = ctypes.c_int
        if fn(-100, source_b, -100, destination_b, RENAME_NOREPLACE_LINUX) == 0:
            return
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), destination)

    # Platforms without a no-replace rename still get overwrite protection. A
    # direct create-new is deliberately used instead of the old hard-link pair;
    # the file is fully fsynced before this function is called.  macOS, the
    # supported apply platform, always takes the atomic branch above.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(destination_b, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as out, open(source, "rb") as inp:
            fd = -1
            while True:
                chunk = inp.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
    finally:
        if fd >= 0:
            os.close(fd)
    os.unlink(source)


class SyncError(Exception):
    def __init__(self, code, snapshot=None):
        super().__init__(code)
        self.code, self.snapshot = code, snapshot


def digest(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def absolute(value):
    if not isinstance(value, str) or not value:
        raise SyncError("INVALID_PATH")
    p = Path(value).expanduser()
    if not p.is_absolute() or ".." in p.parts:
        raise SyncError("ABSOLUTE_PATH_REQUIRED")
    return p


def safe_chain(path):
    """Do not resolve away symlinks, including symlinks in ancestor directories."""
    path = absolute(str(path))
    for p in list(reversed(path.parents)) + [path]:
        try:
            s = p.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(s.st_mode) or getattr(s, 'st_file_attributes', 0) & 0x400:
            raise SyncError("SYMLINK_REFUSED")
        if p != path and not stat.S_ISDIR(s.st_mode):
            raise SyncError("INVALID_PARENT")


def mkdir_private(path):
    safe_chain(path)
    if not path.exists():
        mkdir_private(path.parent)
        path.mkdir(mode=0o700)
    if not path.is_dir():
        raise SyncError("DIRECTORY_REQUIRED")


def lock_fd(fd):
    """Nonblocking process lock; closing the descriptor releases it."""
    if os.name == 'nt':
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def safe_open(path, flags):
    """Refuse links at the final open as well as in every parent."""
    safe_chain(path)
    if os.name != 'nt':
        return os.open(str(path), flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    import msvcrt
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE]
    kernel.CreateFileW.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.GetFileInformationByHandleEx.argtypes = [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD]
    class Attributes(ctypes.Structure):
        _fields_ = [('attributes', w.DWORD), ('tag', w.DWORD)]
    handle = kernel.CreateFileW(str(path), 0xC0000000 if flags & os.O_RDWR else 0x80000000,
                               7, None, 4 if flags & os.O_CREAT else 3, 0x00200000, None)
    if handle == w.HANDLE(-1).value:
        error = ctypes.get_last_error()
        if error in (2, 3): raise FileNotFoundError(str(path))
        raise OSError(error, 'Cannot safely open file')
    transferred = False
    try:
        info = Attributes()
        if not kernel.GetFileInformationByHandleEx(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise OSError('Cannot verify file attributes')
        if info.attributes & (0x400 | 0x10):
            raise SyncError('NONREGULAR_OR_HARDLINK_REFUSED')
        fd = msvcrt.open_osfhandle(handle, (os.O_RDWR if flags & os.O_RDWR else os.O_RDONLY) | os.O_BINARY)
        transferred = True
        return fd
    finally:
        if not transferred: kernel.CloseHandle(handle)


@dataclasses.dataclass(frozen=True)
class File:
    data: bytes
    mtime: int
    mode: int


def read_file(path):
    safe_chain(path)
    try:
        fd = safe_open(path, os.O_RDONLY)
    except FileNotFoundError:
        return None
    except OSError:
        raise SyncError("UNREADABLE_FILE") from None
    with os.fdopen(fd, "rb") as f:
        before = os.fstat(f.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise SyncError("NONREGULAR_OR_HARDLINK_REFUSED")
        if before.st_size > MAX_BYTES:
            raise SyncError("FILE_TOO_LARGE")
        data = f.read(MAX_BYTES + 1)
        after = os.fstat(f.fileno())
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_ino, after.st_size, after.st_mtime_ns
        ) or len(data) > MAX_BYTES:
            raise SyncError("CONCURRENT_CHANGE")
        return File(data, before.st_mtime_ns, stat.S_IMODE(before.st_mode))


def parse(data):
    try:
        return json.loads(data)
    except (ValueError, UnicodeError):
        raise SyncError("INVALID_JSON") from None


@dataclasses.dataclass(frozen=True)
class Config:
    index_root: object
    projects_root: Path
    state_dir: Path
    peers: tuple
    locations: tuple = ()

    @classmethod
    def load(cls, path):
        f = read_file(absolute(str(path)))
        if f is None:
            raise SyncError("CONFIG_NOT_FOUND")
        d = parse(f.data)
        if not isinstance(d, dict) or d.get("schema_version") not in (1, 2):
            raise SyncError("UNSUPPORTED_CONFIG_SCHEMA")
        if d["schema_version"] == 2:
            return cls.load_multi(d)
        try:
            peers = tuple(p["account"] + "/" + p["organization"] for p in d["partitions"])
            roots = [absolute(d[k]) for k in ("index_root", "projects_root", "state_dir")]
        except (KeyError, TypeError):
            raise SyncError("INVALID_CONFIG") from None
        if len(peers) != 2 or len(set(peers)) != 2 or len({p.split('/')[0] for p in peers}) != 2:
            raise SyncError("EXACTLY_TWO_ACCOUNTS_REQUIRED")
        if any(not re.fullmatch(UUID + "/" + UUID, p) for p in peers):
            raise SyncError("INVALID_PARTITION")
        for p in roots:
            safe_chain(p)
        for i, a in enumerate(roots):
            for b in roots[i + 1:]:
                if a == b or a in b.parents or b in a.parents:
                    raise SyncError("OVERLAPPING_ROOTS")
        c = cls(*roots, peers)
        for p in [c.index_root, c.projects_root] + [c.index_root / p for p in peers]:
            safe_chain(p)
            if not p.is_dir():
                raise SyncError("CONFIGURED_DIRECTORY_MISSING")
        return c

    @classmethod
    def load_multi(cls, d):
        try:
            projects, state = (absolute(d[k]) for k in ("projects_root", "state_dir"))
            partitions = d["partitions"]
            if not isinstance(partitions, list) or not 2 <= len(partitions) <= 16:
                raise SyncError("PARTITION_COUNT_REQUIRED")
            locations, roots = [], [projects, state]
            for item in partitions:
                key, account, org = (item[k] for k in ("profile", "account", "organization"))
                if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", key):
                    raise SyncError("INVALID_PROFILE_KEY")
                if any(not isinstance(v, str) or not re.fullmatch(UUID, v) for v in (account, org)):
                    raise SyncError("INVALID_PARTITION")
                root = absolute(item["index_root"])
                if root in (projects, state):
                    raise SyncError("OVERLAPPING_ROOTS")
                locations.append((key + "/" + account + "/" + org, root / account / org))
                if root not in roots:
                    roots.append(root)
        except (KeyError, TypeError, ValueError):
            raise SyncError("INVALID_CONFIG") from None
        if len({p for p, _ in locations}) != len(locations) or len({p for _, p in locations}) != len(locations):
            raise SyncError("DUPLICATE_PARTITION")
        for i, a in enumerate(roots):
            safe_chain(a)
            for b in roots[i + 1:]:
                if a == b or a in b.parents or b in a.parents:
                    raise SyncError("OVERLAPPING_ROOTS")
        for p in [projects] + [p for _, p in locations]:
            safe_chain(p)
            if not p.is_dir():
                raise SyncError("CONFIGURED_DIRECTORY_MISSING")
        return cls(None, projects, state, tuple(p for p, _ in locations), tuple(locations))

    def peer_path(self, peer):
        if peer not in self.peers:
            raise SyncError("INVALID_PARTITION")
        return dict(self.locations)[peer] if self.locations else self.index_root / peer

    def scope(self):
        if self.locations:
            return {"schema_version": 2, "partitions": list(self.peers),
                    "partition_paths": {peer: str(path) for peer, path in self.locations}}
        return {"schema_version": 1, "index_root": str(self.index_root), "partitions": list(self.peers)}

    def path(self, relative):
        if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise SyncError("INVALID_MANIFEST_PATH")
        parts = Path(relative).parts
        peer = "/".join(parts[:-1])
        if not parts or peer not in self.peers or not managed(parts[-1]):
            raise SyncError("INVALID_MANIFEST_PATH")
        p = self.peer_path(peer) / parts[-1]
        safe_chain(p)
        return p


def managed(name):
    return bool(LOCAL.fullmatch(name) or DELETED.fullmatch(name) or name == "archived-sessions.idx")


def scan(c):
    result = {}
    for peer in c.peers:
        directory = c.peer_path(peer)
        safe_chain(directory)
        names = sorted(directory.iterdir())
        if len(names) > MAX_ENTRIES:
            raise SyncError("TOO_MANY_ENTRIES")
        for p in names:
            if p.name.startswith("deleted_") and not DELETED.fullmatch(p.name):
                raise SyncError("UNSUPPORTED_DELETION_FILENAME")
            if p.name.startswith(("local_", "deleted_")) and p.suffix == ".json" and not managed(p.name):
                raise SyncError("UNSUPPORTED_INDEX_FILENAME")
            if not managed(p.name):
                continue  # No tasks, permissions, payload directories or credentials.
            f = read_file(p)
            if f is None:
                raise SyncError("CONCURRENT_CHANGE")
            result[peer + "/" + p.name] = f
    return result


def same(a, b):
    return a == b


def check_inputs(c, expected):
    if scan(c) != expected:
        raise SyncError("CONCURRENT_CHANGE")


def transcript_index(c):
    """Index transcript file metadata once for the whole reconciliation.

    The old per-record lookup walked every project directory for every session.
    A profile with a few thousand sessions therefore repeated the same directory
    scans thousands of times.  Keep only the filename identity and the safety
    result; transcript bytes remain outside the sync engine.
    """
    safe_chain(c.projects_root)
    result = {}
    for directory in c.projects_root.iterdir():
        if directory.is_symlink():
            raise SyncError("SYMLINK_REFUSED")
        if not directory.is_dir():
            continue
        for p in directory.iterdir():
            if p.suffix != ".jsonl" or not re.fullmatch(UUID, p.stem):
                continue
            safe_chain(p)
            try:
                s = p.lstat()
            except FileNotFoundError:
                continue
            safe = stat.S_ISREG(s.st_mode) and s.st_nlink == 1
            result[p.stem] = result.get(p.stem, False) or safe
    return result


def transcript_available(c, sid):
    """Compatibility helper for callers that inspect one transcript."""
    return transcript_index(c).get(sid, False)


def record(c, name, f):
    d = parse(f.data)
    if not isinstance(d, dict) or d.get("sessionId") != name[:-5]:
        raise SyncError("SESSION_ID_MISMATCH")
    sid = d.get("cliSessionId")
    if not isinstance(sid, str) or not re.fullmatch(UUID, sid):
        raise SyncError("TRANSCRIPT_LINK_REQUIRED")
    if not isinstance(d.get("cwd"), str) or not Path(d["cwd"]).is_absolute():
        raise SyncError("INVALID_SESSION_FOLDER")
    if "isArchived" in d and not isinstance(d["isArchived"], bool):
        raise SyncError("INVALID_ARCHIVE_STATE")
    return d


def hierarchy(d):
    """Return validated relationship evidence; absence is not a detach action."""
    result = {}

    def local_id(value):
        if not isinstance(value, str) or not re.fullmatch("local_" + UUID, value):
            raise SyncError("INVALID_HIERARCHY")
        return value

    def text(value, limit=8192):
        if not isinstance(value, str) or len(value) > limit:
            raise SyncError("INVALID_HIERARCHY")
        return value

    spawned = d.get("spawnedFrom")
    if spawned is not None:
        if not isinstance(spawned, dict):
            raise SyncError("INVALID_HIERARCHY")
        task = text(spawned.get("taskId"), 256)
        if not task:
            raise SyncError("INVALID_HIERARCHY")
        link = {"sessionId": local_id(spawned.get("sessionId")), "taskId": task}
        for key in ("title", "name"):
            if key in spawned:
                link[key] = text(spawned[key])
        for key in SPAWN_FLAGS:
            if key in spawned and type(spawned[key]) is not bool:
                raise SyncError("INVALID_HIERARCHY")
            if spawned.get(key) is True:
                link[key] = True
        if "deletedParent" in spawned:
            deleted = spawned["deletedParent"]
            if not isinstance(deleted, dict):
                raise SyncError("INVALID_HIERARCHY")
            link["deletedParent"] = {"sessionId": local_id(deleted.get("sessionId"))}
            if "title" in deleted:
                link["deletedParent"]["title"] = text(deleted["title"])
        result["spawnedFrom"] = link
    if d.get("forkedFromSessionId") is not None:
        result["forkedFromSessionId"] = local_id(d["forkedFromSessionId"])
    if d.get("forkedAtMessageUuid") is not None:
        value = d["forkedAtMessageUuid"]
        if not isinstance(value, str) or not re.fullmatch(UUID, value):
            raise SyncError("INVALID_HIERARCHY")
        result["forkedAtMessageUuid"] = value
    if "lineageDetached" in d and type(d["lineageDetached"]) is not bool:
        raise SyncError("INVALID_HIERARCHY")
    if d.get("lineageDetached") is True:
        result["lineageDetached"] = True
    if not ("spawnedFrom" in result or "forkedFromSessionId" in result
            or result.get("lineageDetached")):
        return None
    return result


def choose_hierarchy(versions):
    # Old sync versions omitted these fields. A newer flat copy is therefore
    # not evidence that the user moved a child to the top level. Claude uses
    # lineageDetached=true for that explicit action.
    evidence = [(f.mtime, h) for _, f, d in versions if (h := hierarchy(d)) is not None]
    if not evidence:
        return None
    newest = max(stamp for stamp, _ in evidence)
    candidates = [h for stamp, h in evidence if stamp == newest]
    if len({encoded(h) for h in candidates}) != 1:
        raise SyncError("HIERARCHY_EQUAL_TIME_CONFLICT")
    return candidates[0]


def with_hierarchy(target, chosen):
    if hierarchy(target) == chosen:
        return dict(target)
    result = {key: value for key, value in target.items() if key not in HIERARCHY}
    if chosen is None:
        return result
    result.update(chosen)
    if "spawnedFrom" in chosen:
        old, new = target.get("spawnedFrom"), chosen["spawnedFrom"]
        # Keep target-local runtime settings only for the very same relation.
        # On reparenting, do not retain permissions tied to the previous parent.
        local = {}
        if isinstance(old, dict) and all(old.get(k) == new[k] for k in ("sessionId", "taskId")):
            local = {k: v for k, v in old.items() if k not in SPAWN_PORTABLE}
        result["spawnedFrom"] = {**local, **new}
    return result


def hierarchy_graph(c, output):
    """Validate each complete output forest without inventing missing parents."""
    links, orphans = set(), set()
    for peer in c.peers:
        rows = {parse(f.data)["sessionId"]: parse(f.data)
                for rel, f in output.items()
                if str(Path(rel).parent) == peer and LOCAL.fullmatch(Path(rel).name)}
        parents, children = {}, {}
        for sid, row in rows.items():
            h = hierarchy(row) or {}
            refs = set()
            if not h.get("lineageDetached"):
                if "spawnedFrom" in h:
                    refs.add(h["spawnedFrom"]["sessionId"])
                if "forkedFromSessionId" in h:
                    refs.add(h["forkedFromSessionId"])
            if sid in refs:
                raise SyncError("HIERARCHY_SELF_REFERENCE")
            for parent in refs:
                links.add((sid, parent))
                if parent not in rows:
                    orphans.add((sid, parent))
                else:
                    children.setdefault(parent, []).append(sid)
            parents[sid] = len(refs.intersection(rows))
        ready = [sid for sid, count in parents.items() if count == 0]
        visited = 0
        while ready:
            sid = ready.pop()
            visited += 1
            for child in children.get(sid, []):
                parents[child] -= 1
                if parents[child] == 0:
                    ready.append(child)
        if visited != len(rows):
            raise SyncError("HIERARCHY_CYCLE")
    return len(links), len(orphans)


@dataclasses.dataclass
class Op:
    relative: str
    before: object
    after: object
    kind: str


@dataclasses.dataclass
class Plan:
    inputs: dict
    operations: list
    sessions: int
    hierarchy_links: int = 0
    hierarchy_repairs: int = 0
    hierarchy_orphans: int = 0

    def summary(self):
        counts = {"add": 0, "update": 0, "remove": 0, "tombstone": 0, "archive": 0}
        for op in self.operations:
            counts[op.kind] += 1
        return {"sessions": self.sessions, "operations": len(self.operations), **counts,
                "hierarchy_links": self.hierarchy_links,
                "hierarchy_repairs": self.hierarchy_repairs,
                "hierarchy_orphans": self.hierarchy_orphans}


def build_plan(c, seed=None):
    actual = scan(c)
    inputs = dict(actual)
    if seed is not None:
        relative, seeded = seed
        if relative in inputs:
            raise SyncError("SESSION_INDEX_ALREADY_EXISTS")
        c.path(relative)
        inputs[relative] = seeded
    transcripts = transcript_index(c)
    records, tombstones = {}, {}
    for relative, f in inputs.items():
        name = Path(relative).name
        if LOCAL.fullmatch(name):
            d = record(c, name, f)
            records.setdefault(name, []).append((relative, f, d))
        elif DELETED.fullmatch(name):
            value = parse(f.data)
            if type(value) is not int or not 0 < value < 10**15:
                raise SyncError("UNSUPPORTED_DELETION_MARKER")
            old = tombstones.get(name)
            if old is None or value > old[0]:
                tombstones[name] = (value, f)
        else:
            d = parse(f.data)
            if not isinstance(d, dict) or d.get("v") != 1 or not isinstance(d.get("archived"), list):
                raise SyncError("UNSUPPORTED_ARCHIVE_INDEX")

    output = dict(inputs)
    stale_markers = set()
    hierarchy_repairs = 0
    for name, versions in records.items():
        # mtime is preserved on copy. Equal clocks with different portable values are a conflict.
        newest = max(v[1].mtime for v in versions)
        candidates = [v for v in versions if v[1].mtime == newest]
        projections = [encoded({k: v for k, v in d.items() if k in PORTABLE}) for _, _, d in candidates]
        if len(set(projections)) != 1:
            raise SyncError("EQUAL_TIME_CONFLICT")
        _, winner, d = candidates[0]
        identities = {d["sessionId"], d["sessionId"][6:], d["cliSessionId"]}
        markers = [(n, value) for n, value in tombstones.items() if DELETED.fullmatch(n).group(1) in identities]
        deleted = any(value[0] * 1_000_000 >= winner.mtime for _, value in markers)
        if deleted:
            for peer in c.peers:
                output.pop(peer + "/" + name, None)
            continue
        if not transcripts.get(d["cliSessionId"], False):
            raise SyncError("TRANSCRIPT_MISSING")
        stale_markers.update(n for n, _ in markers)
        portable = {k: v for k, v in d.items() if k in PORTABLE}
        chosen_hierarchy = choose_hierarchy(versions)
        for peer in c.peers:
            relative = peer + "/" + name
            existing = inputs.get(relative)
            target = parse(existing.data) if existing else {}
            previous = {k: v for k, v in target.items() if k in PORTABLE}
            merged = with_hierarchy(target, chosen_hierarchy)
            if hierarchy(target) != chosen_hierarchy:
                hierarchy_repairs += 1
            # Preserve every destination-only/account/security/runtime field. New records use defaults.
            if existing and previous == portable and merged == target:
                continue
            target = {k: v for k, v in merged.items() if k not in PORTABLE}
            target.update(portable)
            output[relative] = File(encoded(target), winner.mtime, existing.mode if existing else 0o600)

    for name, (_, f) in tombstones.items():
        for peer in c.peers:
            relative = peer + "/" + name
            if name in stale_markers:
                output.pop(relative, None)
            elif relative not in inputs or parse(inputs[relative].data) != parse(f.data):
                output[relative] = File(f.data, f.mtime, 0o600)

    # Update the archive hint together with record changes; the same manifest covers both.
    for peer in c.peers:
        archived = []
        for relative, f in output.items():
            if str(Path(relative).parent) == peer and LOCAL.fullmatch(Path(relative).name):
                d = parse(f.data)
                if d.get("isArchived", False):
                    archived.append(d["sessionId"])
        rel = peer + "/archived-sessions.idx"
        if archived or rel in inputs:
            data = encoded({"v": 1, "archived": sorted(archived)})
            if rel not in inputs or parse(inputs[rel].data) != parse(data):
                output[rel] = File(data, max((f.mtime for f in output.values()), default=time.time_ns()), 0o600)

    hierarchy_links, hierarchy_orphans = hierarchy_graph(c, output)
    ops = []
    for rel in sorted(set(actual) | set(output)):
        # A repair seed participates in reconciliation as virtual input, but it
        # is absent on disk and therefore must still produce an add operation.
        before, after = actual.get(rel), output.get(rel)
        if before == after:
            continue
        name = Path(rel).name
        kind = "remove" if after is None else "tombstone" if DELETED.fullmatch(name) else "archive" if name == "archived-sessions.idx" else "add" if before is None else "update"
        ops.append(Op(rel, before, after, kind))
    check_inputs(c, actual)
    return Plan(actual, ops, len(records), hierarchy_links, hierarchy_repairs, hierarchy_orphans)


def assert_stopped():
    try:
        if os.name == 'nt':
            # Names only. Never collect command lines or credentials.
            p = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                'Get-Process -ErrorAction Stop | ForEach-Object { $_.ProcessName }'],
                capture_output=True, check=True, timeout=8, creationflags=0x08000000)
        elif sys.platform in ('darwin', 'linux'):
            p = subprocess.run(["/bin/ps", "-axo", "comm="], capture_output=True, check=True, timeout=5)
        else:
            raise SyncError('PLATFORM_UNSUPPORTED')
    except (OSError, subprocess.SubprocessError):
        raise SyncError("PROCESS_STATE_UNKNOWN") from None
    for command in p.stdout.decode(errors="replace").splitlines():
        name = Path(command.strip()).name
        desktop = ("claude.app/contents/" in command.lower() or re.fullmatch(r"Claude(?: Helper(?: \([^)]*\))?)?", name)) if sys.platform == 'darwin' else re.fullmatch(r"claude(?:-desktop|[- ]helper(?: \([^)]*\))?)?(?:\.exe)?", name, re.I)
        if desktop:
            raise SyncError("CLAUDE_MUST_BE_FULLY_CLOSED")


def fsync_dir(path):
    safe_chain(path)
    if os.name == 'nt':
        # Windows has no portable directory fsync. Each file is flushed before
        # replace; snapshots remain available after an interrupted transaction.
        return
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path, f, creating=False):
    safe_chain(path)
    safe_chain(path.parent)
    fd, temp = tempfile.mkstemp(prefix=".session-sync-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as out:
            if os.name != 'nt':
                os.fchmod(out.fileno(), f.mode & 0o777)
            out.write(f.data)
            out.flush()
            os.fsync(out.fileno())
            os.utime(temp if os.name == 'nt' else out.fileno(), ns=(f.mtime, f.mtime))
            os.fsync(out.fileno())
        if creating:
            # Atomic create without replacing a file created by somebody else.
            # In particular, do not use link+unlink here: a crash in that
            # two-step sequence creates a hard-linked destination that the
            # reader must reject and the recovery journal can no longer scan.
            _rename_noreplace(temp, str(path))
        else:
            if os.name == 'nt' and path.exists():
                # Preserve the destination's DACL, never copy another account's
                # permissions from a source index or reset a custom target ACL.
                from ctypes import wintypes as w
                api = ctypes.WinDLL('advapi32', use_last_error=True)
                api.GetFileSecurityW.argtypes = [w.LPCWSTR, w.DWORD, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD)]
                api.SetFileSecurityW.argtypes = [w.LPCWSTR, w.DWORD, w.LPVOID]
                api.GetSecurityDescriptorControl.argtypes = [w.LPVOID, ctypes.POINTER(w.WORD), ctypes.POINTER(w.DWORD)]
                needed = w.DWORD()
                api.GetFileSecurityW(str(path), 4, None, 0, ctypes.byref(needed))
                if not needed.value: raise SyncError('FILE_PERMISSIONS_UNAVAILABLE')
                descriptor = ctypes.create_string_buffer(needed.value)
                control, revision = w.WORD(), w.DWORD()
                if not api.GetFileSecurityW(str(path), 4, descriptor, len(descriptor), ctypes.byref(needed)) or not api.GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision)):
                    raise SyncError('FILE_PERMISSIONS_UNAVAILABLE')
                flags = 4 | (0x80000000 if control.value & 0x1000 else 0x20000000)
                if not api.SetFileSecurityW(temp, flags, descriptor):
                    raise SyncError('FILE_PERMISSIONS_UNAVAILABLE')
            os.replace(temp, str(path))
        fsync_dir(path.parent)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def state_write(path, value):
    atomic_write(path, File(encoded(value), time.time_ns(), 0o600))


@contextlib.contextmanager
def lock(c):
    mkdir_private(c.state_dir)
    path = c.state_dir / "lock"
    safe_chain(path)
    fd = safe_open(path, os.O_CREAT | os.O_RDWR)
    try:
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1:
            raise SyncError("INVALID_LOCK_FILE")
        try:
            lock_fd(fd)
        except (BlockingIOError, PermissionError):
            raise SyncError("SYNC_ALREADY_RUNNING") from None
        yield
    finally:
        os.close(fd)


def unfinished(c):
    root = c.state_dir / "snapshots"
    safe_chain(root)
    if not root.exists():
        return []
    ids = []
    for p in sorted(root.iterdir()):
        safe_chain(p)
        f = read_file(p / "manifest.json")
        # No manifest means interruption while backing up, before any index write.
        if f is not None:
            manifest = parse(f.data)
            if not isinstance(manifest, dict) or manifest.get("schema_version") not in (1, 2):
                raise SyncError("INVALID_MANIFEST")
            if manifest.get("state") not in ("completed", "rolled_back"):
                ids.append(p.name)
    return ids


def apply(c, plan, guard=assert_stopped):
    with lock(c):
        if unfinished(c):
            raise SyncError("RECOVERY_REQUIRED")
        guard()
        check_inputs(c, plan.inputs)
        if not plan.operations:
            return {"status": "unchanged", **plan.summary()}
        sid = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:12]
        directory = c.state_dir / "snapshots" / sid
        mkdir_private(directory)
        manifest = {**c.scope(), "state": "prepared", "operations": []}
        for i, op in enumerate(plan.operations):
            entry = {"path": op.relative, "before": digest(op.before.data) if op.before else None, "after": digest(op.after.data) if op.after else None}
            if op.before:
                blob = "before-" + str(i) + ".bin"
                atomic_write(directory / blob, File(op.before.data, op.before.mtime, 0o600), creating=True)
                entry.update(blob=blob, mtime=op.before.mtime, mode=op.before.mode)
            manifest["operations"].append(entry)
        manifest_path = directory / "manifest.json"
        state_write(manifest_path, manifest)
        current = dict(plan.inputs)
        try:
            guard()
            check_inputs(c, current)
            manifest["state"] = "applying"
            state_write(manifest_path, manifest)
            for op in plan.operations:
                guard()
                path = c.path(op.relative)
                if not same(read_file(path), op.before):
                    raise SyncError("CONCURRENT_CHANGE")
                if op.after is None:
                    path.unlink()
                    fsync_dir(path.parent)
                    current.pop(op.relative, None)
                else:
                    atomic_write(path, op.after, creating=op.before is None)
                    current[op.relative] = op.after
            guard()
            check_inputs(c, current)
            manifest["state"] = "completed"
            state_write(manifest_path, manifest)
        except Exception as error:
            manifest["state"] = "interrupted"
            try:
                state_write(manifest_path, manifest)
            except OSError:
                pass
            raise SyncError(error.code if isinstance(error, SyncError) else "WRITE_INTERRUPTED", sid) from None
        return {"status": "completed", "snapshot": sid, **plan.summary()}


def rebuild(c, relative, entry, guard=assert_stopped):
    """Seed one missing record and let the normal transaction propagate it."""
    if not isinstance(entry, dict):
        raise SyncError("INVALID_REBUILD_ENTRY")
    name = Path(relative).name
    if not LOCAL.fullmatch(name):
        raise SyncError("INVALID_REBUILD_PATH")
    session_id = entry.get("sessionId")
    cli_session_id = entry.get("cliSessionId")
    identities = {
        session_id,
        session_id[6:] if isinstance(session_id, str) and session_id.startswith("local_") else None,
        cli_session_id,
    }
    for peer in c.peers:
        directory = c.peer_path(peer)
        for marker in directory.iterdir():
            match = DELETED.fullmatch(marker.name)
            if not match or match.group(1) not in identities:
                continue
            value = read_file(marker)
            if value is not None and type(parse(value.data)) is int and parse(value.data) > 0:
                raise SyncError("SESSION_MARKED_DELETED")
    data = encoded(entry)
    mtime = entry.get("lastActivityAt")
    if type(mtime) is not int or mtime <= 0:
        mtime = time.time_ns()
    # Health timestamps are milliseconds; regular index mtimes are nanoseconds.
    stamp = mtime * 1_000_000 if mtime < 10**15 else mtime
    plan = build_plan(c, (relative, File(data, stamp, 0o600)))
    if not plan.operations:
        raise SyncError("SESSION_MARKED_DELETED")
    return apply(c, plan, guard=guard)


def restore_plan(c, sid):
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}", sid):
        raise SyncError("INVALID_SNAPSHOT_ID")
    directory = c.state_dir / "snapshots" / sid
    f = read_file(directory / "manifest.json")
    if f is None:
        raise SyncError("SNAPSHOT_NOT_FOUND")
    m = parse(f.data)
    if not isinstance(m, dict) or not isinstance(m.get("operations"), list):
        raise SyncError("INVALID_MANIFEST")
    if m.get("state") not in (
        "prepared",
        "applying",
        "interrupted",
        "restoring",
        "restore_interrupted",
        "completed",
    ):
        raise SyncError("INVALID_MANIFEST")
    if any(m.get(key) != value for key, value in c.scope().items()):
        raise SyncError("SNAPSHOT_SCOPE_MISMATCH")
    if m.get("state") == "rolled_back":
        raise SyncError("ALREADY_ROLLED_BACK")
    inputs, ops = scan(c), []
    seen = set()
    for entry in m["operations"]:
        if not isinstance(entry, dict) or not {"path", "before", "after"}.issubset(entry):
            raise SyncError("INVALID_MANIFEST")
        if not isinstance(entry["path"], str):
            raise SyncError("INVALID_MANIFEST")
        for key in ("before", "after"):
            if entry[key] is not None and (not isinstance(entry[key], str) or not re.fullmatch(r"[0-9a-f]{64}", entry[key])):
                raise SyncError("INVALID_MANIFEST")
        rel = entry["path"]
        c.path(rel)
        if rel in seen:
            raise SyncError("INVALID_MANIFEST")
        seen.add(rel)
        now = inputs.get(rel)
        current_hash = digest(now.data) if now else None
        if current_hash not in (entry["before"], entry["after"]):
            raise SyncError("RESTORE_CONFLICT")
        before = None
        if entry["before"] is not None:
            if type(entry.get("mtime")) is not int or type(entry.get("mode")) is not int or not 0 <= entry["mode"] <= 0o777:
                raise SyncError("INVALID_MANIFEST")
            blob = entry.get("blob", "")
            if not re.fullmatch(r"before-[0-9]+\.bin", blob):
                raise SyncError("INVALID_MANIFEST")
            b = read_file(directory / blob)
            if b is None or digest(b.data) != entry["before"]:
                raise SyncError("CORRUPT_BACKUP")
            before = File(b.data, entry["mtime"], entry["mode"])
        if current_hash != entry["before"]:
            ops.append(Op(rel, now, before, "remove" if before is None else "update"))
    check_inputs(c, inputs)
    return Plan(inputs, ops, 0), m, directory / "manifest.json"


def restore(c, sid, guard=assert_stopped):
    with lock(c):
        guard()
        plan, manifest, path = restore_plan(c, sid)
        check_inputs(c, plan.inputs)
        current = dict(plan.inputs)
        manifest["state"] = "restoring"
        state_write(path, manifest)
        try:
            for op in plan.operations:
                guard()
                target = c.path(op.relative)
                if read_file(target) != op.before:
                    raise SyncError("CONCURRENT_CHANGE", sid)
                if op.after is None:
                    target.unlink()
                    fsync_dir(target.parent)
                    current.pop(op.relative, None)
                else:
                    atomic_write(target, op.after, creating=op.before is None)
                    current[op.relative] = op.after
            guard()
            check_inputs(c, current)
            manifest["state"] = "rolled_back"
            state_write(path, manifest)
            return {"status": "rolled_back", "snapshot": sid, **plan.summary()}
        except Exception as error:
            manifest["state"] = "restore_interrupted"
            try:
                state_write(path, manifest)
            except OSError:
                pass
            raise SyncError(
                error.code if isinstance(error, SyncError) else "RESTORE_INTERRUPTED",
                sid,
            ) from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=VERSION)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("doctor", "plan", "sync", "restore"):
        p = sub.add_parser(name)
        p.add_argument("--config", required=True)
        if name in ("sync", "restore"):
            p.add_argument("--apply", action="store_true")
        if name == "restore":
            p.add_argument("--snapshot", required=True)
    args = parser.parse_args(argv)
    try:
        c = Config.load(absolute(args.config))
        if args.command == "restore":
            if args.apply:
                result = restore(c, args.snapshot)
            else:
                plan, _, _ = restore_plan(c, args.snapshot)
                result = {"status": "restore_preview", **plan.summary()}
        else:
            if unfinished(c):
                raise SyncError("RECOVERY_REQUIRED")
            plan = build_plan(c)
            if args.command == "sync" and args.apply:
                result = apply(c, plan)
            else:
                result = {"status": "preview", **plan.summary()}
        print(json.dumps(result, sort_keys=True))
        return 0
    except SyncError as error:
        print(json.dumps({"status": "blocked", "code": error.code, "snapshot": error.snapshot}), file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError, TypeError):
        print(json.dumps({"status": "blocked", "code": "INVALID_INPUT_OR_IO_FAILURE"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
