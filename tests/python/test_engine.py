import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest import mock

ENGINE = Path(__file__).resolve().parents[2] / "engine" / "claude_session_sync.py"
spec = importlib.util.spec_from_file_location("session_sync", ENGINE)
s = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = s
spec.loader.exec_module(s)

A = "11111111-1111-4111-8111-111111111111"
B = "22222222-2222-4222-8222-222222222222"
OA = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OB = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
SID = "33333333-3333-4333-8333-333333333333"
CLI = "44444444-4444-4444-8444-444444444444"
PEERS = (A + "/" + OA, B + "/" + OB)
NAME = "local_" + SID + ".json"
T0 = 1_790_000_000_000_000_000
PARENT = "local_" + SID
CHILD = "local_" + A
SIBLING = "local_" + B
GRANDCHILD = "local_" + OA


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.index, self.projects, self.state = [self.root / p for p in ("index", "projects", "state")]
        self.projects.mkdir()
        (self.projects / "project").mkdir()
        (self.projects / "project" / (CLI + ".jsonl")).write_bytes(b"DO NOT READ OR MODIFY TRANSCRIPT\n")
        for peer in PEERS:
            (self.index / peer).mkdir(parents=True)
        self.config = self.root / "config.json"
        self.write_config()
        self.c = s.Config.load(self.config)

    def write_config(self, **changes):
        d = {"schema_version": 1, "index_root": str(self.index), "projects_root": str(self.projects), "state_dir": str(self.state), "partitions": [{"account": A, "organization": OA}, {"account": B, "organization": OB}], "hook_enabled": False}
        d.update(changes)
        self.config.write_text(json.dumps(d))

    def path(self, peer=0, name=NAME):
        return self.index / PEERS[peer] / name

    def put(self, peer=0, stamp=T0, name=NAME, **fields):
        d = {"sessionId": name[:-5], "cliSessionId": CLI, "cwd": "/offline/project", "title": "source", "isArchived": False}
        d.update(fields)
        self.path(peer, name).write_text(json.dumps(d))
        os.utime(self.path(peer, name), ns=(stamp, stamp))
        return d

    def marker(self, peer=0, milliseconds=None, identity=SID):
        p = self.path(peer, "deleted_" + identity)
        p.write_text(str(milliseconds if milliseconds is not None else T0 // 1_000_000 + 1000))
        return p

    def tree(self, root=None):
        root = root or self.index
        return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_mode) for p in root.rglob("*") if p.is_file()}

    def run_sync(self):
        return s.apply(self.c, s.build_plan(self.c), guard=lambda: None)

    def put_session(self, sid, peer=0, stamp=T0, **fields):
        cli = str(uuid.uuid5(uuid.NAMESPACE_URL, sid))
        (self.projects / "project" / (cli + ".jsonl")).write_bytes(b"UNCHANGED BODY\n")
        return self.put(peer, stamp, name=sid + ".json", cliSessionId=cli, **fields)

    def child_link(self, parent=PARENT, **fields):
        return {"sessionId": parent, "taskId": "offline-task-1", "sideSession": True, **fields}

    def session(self, peer, sid):
        return json.loads(self.path(peer, sid + ".json").read_text())

    def test_hierarchy_siblings_and_nested_children_are_preserved_and_idempotent(self):
        self.put_session(PARENT)
        for sid, parent in [(CHILD, PARENT), (SIBLING, PARENT), (GRANDCHILD, CHILD)]:
            self.put_session(sid, spawnedFrom=self.child_link(parent))
        bodies = self.tree(self.projects)
        result = self.run_sync()
        self.assertEqual(result["hierarchy_links"], 3)
        self.assertEqual(result["hierarchy_repairs"], 3)
        self.assertEqual(result["hierarchy_orphans"], 0)
        for sid in (PARENT, CHILD, SIBLING, GRANDCHILD):
            self.assertEqual(s.hierarchy(self.session(0, sid)), s.hierarchy(self.session(1, sid)))
        self.assertEqual(self.session(1, GRANDCHILD)["spawnedFrom"]["sessionId"], CHILD)
        self.assertEqual(self.tree(self.projects), bodies)
        self.assertEqual(s.build_plan(self.c).operations, [])

    def test_hierarchy_old_evidence_repairs_newer_flat_copy_without_losing_new_title(self):
        self.put_session(PARENT)
        self.put_session(CHILD, spawnedFrom=self.child_link())
        self.put_session(CHILD, peer=1, stamp=T0 + 100, title="new title", permissionMode="plan")
        self.run_sync()
        for peer in range(2):
            row = self.session(peer, CHILD)
            self.assertEqual(row["title"], "new title")
            self.assertEqual(row["spawnedFrom"], self.child_link())
            self.assertEqual(self.path(peer, CHILD + ".json").stat().st_mtime_ns, T0 + 100)
        self.assertEqual(self.session(1, CHILD)["permissionMode"], "plan")

    def test_hierarchy_equal_time_flat_copy_is_repaired(self):
        self.put_session(PARENT)
        self.put_session(CHILD, spawnedFrom=self.child_link())
        self.put_session(CHILD, peer=1)
        result = self.run_sync()
        self.assertEqual(result["hierarchy_repairs"], 1)
        self.assertEqual(self.session(1, CHILD)["spawnedFrom"], self.child_link())

    def test_hierarchy_new_peer_does_not_inherit_runtime_or_permissions(self):
        self.put_session(PARENT)
        link = self.child_link(title="historical title", name="child", userFork=False,
                               wakeParent=True, sharedCheckout=True, sharedFolder=True,
                               pendingPop=True, proposalPicks=["private"], futureRuntime="no")
        self.put_session(CHILD, spawnedFrom=link, permissionMode="bypassPermissions",
                         lineage={"kind": "guessed"}, futureAccountField="no")
        self.run_sync()
        self.assertEqual(self.session(0, CHILD)["spawnedFrom"], link)
        row = self.session(1, CHILD)
        self.assertEqual(row["spawnedFrom"], self.child_link(title="historical title", name="child"))
        for key in ("permissionMode", "lineage", "futureAccountField"):
            self.assertNotIn(key, row)

    def test_hierarchy_same_relation_keeps_target_runtime_when_metadata_changes(self):
        self.put_session(PARENT)
        self.put_session(CHILD, stamp=T0 + 100,
                         spawnedFrom=self.child_link(title="latest", wakeParent=True))
        self.put_session(CHILD, peer=1,
                         spawnedFrom=self.child_link(title="old", wakeParent=False, sharedFolder=True),
                         permissionMode="plan")
        self.run_sync()
        self.assertEqual(self.session(1, CHILD)["spawnedFrom"],
                         self.child_link(title="latest", wakeParent=False, sharedFolder=True))
        self.assertEqual(self.session(1, CHILD)["permissionMode"], "plan")

    def test_hierarchy_newer_explicit_detach_is_propagated(self):
        self.put_session(PARENT)
        self.put_session(CHILD, spawnedFrom=self.child_link())
        self.put_session(CHILD, peer=1, stamp=T0 + 100,
                         spawnedFrom=self.child_link(), lineageDetached=True)
        result = self.run_sync()
        self.assertEqual(result["hierarchy_links"], 0)
        for peer in range(2):
            self.assertIs(self.session(peer, CHILD)["lineageDetached"], True)
        self.assertEqual(s.build_plan(self.c).operations, [])

    def test_hierarchy_newer_flat_copy_cannot_erase_explicit_detach(self):
        self.put_session(PARENT)
        self.put_session(CHILD, spawnedFrom=self.child_link(), lineageDetached=True)
        self.put_session(CHILD, peer=1, stamp=T0 + 100, title="flat copy")
        self.run_sync()
        for peer in range(2):
            row = self.session(peer, CHILD)
            self.assertIs(row["lineageDetached"], True)
            self.assertEqual(row["title"], "flat copy")

    def test_hierarchy_reparenting_clears_old_detach_and_old_parent_runtime(self):
        self.put_session(PARENT)
        self.put_session(SIBLING)
        self.put_session(CHILD, spawnedFrom=self.child_link(sharedCheckout=True,
                         sharedFolder=True, wakeParent=True), lineageDetached=True)
        new = self.child_link(SIBLING, taskId="new-parent-task")
        self.put_session(CHILD, peer=1, stamp=T0 + 100, spawnedFrom=new)
        self.run_sync()
        for peer in range(2):
            row = self.session(peer, CHILD)
            self.assertNotIn("lineageDetached", row)
            self.assertEqual(row["spawnedFrom"], new)

    def test_hierarchy_equal_time_different_parents_block_all_writes(self):
        self.put_session(PARENT)
        self.put_session(SIBLING)
        self.put_session(CHILD, spawnedFrom=self.child_link())
        self.put_session(CHILD, peer=1, spawnedFrom=self.child_link(SIBLING))
        before = self.tree()
        with self.assertRaisesRegex(s.SyncError, "HIERARCHY_EQUAL_TIME_CONFLICT"):
            self.run_sync()
        self.assertEqual(self.tree(), before)
        self.assertFalse(self.state.exists())

    def test_hierarchy_equal_time_explicit_detach_conflicts_with_attached(self):
        self.put_session(PARENT)
        self.put_session(CHILD, spawnedFrom=self.child_link())
        self.put_session(CHILD, peer=1, spawnedFrom=self.child_link(), lineageDetached=True)
        before = self.tree()
        with self.assertRaisesRegex(s.SyncError, "HIERARCHY_EQUAL_TIME_CONFLICT"):
            s.build_plan(self.c)
        self.assertEqual(self.tree(), before)

    def test_hierarchy_self_reference_and_cycle_block_before_writing(self):
        self.put_session(PARENT)
        self.put_session(CHILD, spawnedFrom=self.child_link(CHILD))
        before = self.tree()
        with self.assertRaisesRegex(s.SyncError, "HIERARCHY_SELF_REFERENCE"):
            self.run_sync()
        self.assertEqual(self.tree(), before)
        self.put_session(PARENT, spawnedFrom=self.child_link(CHILD))
        self.put_session(CHILD, spawnedFrom=self.child_link())
        before = self.tree()
        with self.assertRaisesRegex(s.SyncError, "HIERARCHY_CYCLE"):
            self.run_sync()
        self.assertEqual(self.tree(), before)
        self.assertFalse(self.state.exists())

    def test_hierarchy_deleted_parent_is_not_resurrected_and_orphan_is_reported(self):
        self.put_session(PARENT)
        self.put_session(CHILD, spawnedFrom=self.child_link())
        self.marker(identity=SID)
        result = self.run_sync()
        self.assertEqual(result["hierarchy_orphans"], 1)
        self.assertEqual(result["hierarchy_links"], 1)
        for peer in range(2):
            self.assertFalse(self.path(peer, PARENT + ".json").exists())
            self.assertEqual(self.session(peer, CHILD)["spawnedFrom"], self.child_link())
        self.assertEqual(s.build_plan(self.c).operations, [])

    def test_hierarchy_invalid_evidence_blocks_all_writes(self):
        invalid = [
            {"spawnedFrom": []},
            {"spawnedFrom": {"sessionId": PARENT}},
            {"spawnedFrom": self.child_link(taskId="")},
            {"spawnedFrom": self.child_link(sessionId="../outside")},
            {"spawnedFrom": self.child_link(sideSession=1)},
            {"spawnedFrom": self.child_link(title=[])},
            {"spawnedFrom": self.child_link(deletedParent={"sessionId": "invalid"})},
            {"forkedFromSessionId": "invalid"},
            {"forkedAtMessageUuid": "invalid"},
            {"lineageDetached": "true"},
        ]
        self.put_session(PARENT)
        for fields in invalid:
            with self.subTest(fields=fields):
                self.put_session(CHILD, **fields)
                before = self.tree()
                with self.assertRaisesRegex(s.SyncError, "INVALID_HIERARCHY"):
                    self.run_sync()
                self.assertEqual(self.tree(), before)
                self.assertFalse(self.state.exists())

    def test_hierarchy_repair_can_restore_original_bytes_mtime_and_mode(self):
        self.put_session(PARENT)
        self.put_session(CHILD, spawnedFrom=self.child_link(wakeParent=True))
        self.put_session(CHILD, peer=1, stamp=T0 + 100, title="new title")
        self.path(1, CHILD + ".json").chmod(0o640)
        before = self.tree()
        result = self.run_sync()
        s.restore(self.c, result["snapshot"], guard=lambda: None)
        self.assertEqual(self.tree(), before)

    def test_hierarchy_fork_fields_are_preserved(self):
        self.put_session(PARENT)
        self.put_session(CHILD, forkedFromSessionId=PARENT, forkedAtMessageUuid=CLI)
        result = self.run_sync()
        self.assertEqual(result["hierarchy_links"], 1)
        row = self.session(1, CHILD)
        self.assertEqual(row["forkedFromSessionId"], PARENT)
        self.assertEqual(row["forkedAtMessageUuid"], CLI)
        self.assertEqual(s.build_plan(self.c).operations, [])

    def test_hierarchy_false_flags_do_not_create_equal_time_conflicts(self):
        self.put_session(PARENT)
        self.put_session(CHILD, spawnedFrom=self.child_link(userFork=False), lineageDetached=False)
        self.put_session(CHILD, peer=1, spawnedFrom=self.child_link())
        before = self.tree()
        plan = s.build_plan(self.c)
        self.assertEqual(len(plan.operations), 1)
        self.assertEqual(Path(plan.operations[0].relative).name, PARENT + ".json")
        self.assertEqual(plan.hierarchy_repairs, 0)
        self.run_sync()
        for peer in range(2):
            rel = PEERS[peer] + "/" + CHILD + ".json"
            self.assertEqual(self.tree()[rel], before[rel])

    def test_hierarchy_deleted_parent_metadata_is_filtered(self):
        self.put_session(PARENT)
        historical = {"sessionId": PARENT, "title": "deleted title", "permissionMode": "no"}
        self.put_session(CHILD, spawnedFrom=self.child_link(deletedParent=historical))
        self.run_sync()
        self.assertEqual(self.session(1, CHILD)["spawnedFrom"]["deletedParent"],
                         {"sessionId": PARENT, "title": "deleted title"})

    def test_hierarchy_explicit_detach_without_link_is_preserved(self):
        self.put_session(CHILD, lineageDetached=True)
        self.put_session(CHILD, peer=1, stamp=T0 + 100)
        result = self.run_sync()
        self.assertEqual(result["hierarchy_links"], 0)
        for peer in range(2):
            self.assertIs(self.session(peer, CHILD)["lineageDetached"], True)

    def test_preview_and_cli_default_are_read_only(self):
        self.put()
        before = self.tree()
        p = subprocess.run([sys.executable, str(ENGINE), "sync", "--config", str(self.config)], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["status"], "preview")
        self.assertEqual(before, self.tree())
        self.assertFalse(self.state.exists())
        self.assertNotIn("source", p.stdout)
        self.assertNotIn(A, p.stdout)

    def test_creates_peer_and_keeps_transcript_unchanged(self):
        self.put(remoteMcpServersConfig={"secret": "must-not-copy"}, permissionMode="bypassPermissions", scheduledTaskId="task", isStarred=True, futureAccountField="no")
        body_before = self.tree(self.projects)
        result = self.run_sync()
        d = json.loads(self.path(1).read_text())
        self.assertEqual(d["cliSessionId"], CLI)
        for k in ("permissionMode", "remoteMcpServersConfig", "scheduledTaskId", "isStarred", "futureAccountField"):
            self.assertNotIn(k, d)
        self.assertEqual(body_before, self.tree(self.projects))
        self.assertEqual(self.path(1).stat().st_mtime_ns, T0)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(s.build_plan(self.c).operations, [])

    def test_latest_title_preserves_target_security_and_cloud_fields(self):
        self.put(stamp=T0 + 100, title="new", permissionMode="bypassPermissions", remoteMcpServersConfig={"from": "A"})
        self.put(1, title="old", permissionMode="plan", remoteMcpServersConfig={"from": "B"}, futureField="keep")
        self.run_sync()
        d = json.loads(self.path(1).read_text())
        self.assertEqual(d["title"], "new")
        self.assertEqual(d["permissionMode"], "plan")
        self.assertEqual(d["remoteMcpServersConfig"], {"from": "B"})
        self.assertEqual(d["futureField"], "keep")

    def test_b_to_a_to_b_keeps_same_transcript_and_latest_metadata(self):
        self.put()
        self.run_sync()
        self.put(1, stamp=T0 + 1000, title="B continued", lastActivityAt=1790000001)
        self.run_sync()
        self.assertEqual(json.loads(self.path().read_text())["title"], "B continued")
        self.put(stamp=T0 + 2000, title="A continued", lastActivityAt=1790000002)
        self.run_sync()
        self.assertEqual(json.loads(self.path(1).read_text())["title"], "A continued")
        self.assertEqual(json.loads(self.path(1).read_text())["cliSessionId"], CLI)

    def test_equal_time_different_values_block_every_write(self):
        self.put(title="A")
        self.put(1, title="B")
        before = self.tree()
        with self.assertRaisesRegex(s.SyncError, "EQUAL_TIME_CONFLICT"):
            s.build_plan(self.c)
        self.assertEqual(before, self.tree())

    def test_newer_delete_removes_both_indexes_and_propagates_marker(self):
        self.put()
        self.put(1)
        p = self.marker()
        result = self.run_sync()
        self.assertFalse(self.path().exists())
        self.assertFalse(self.path(1).exists())
        self.assertEqual(self.path(1, p.name).read_bytes(), p.read_bytes())
        self.assertTrue((self.state / "snapshots" / result["snapshot"] / "before-0.bin").exists())
        self.assertEqual(s.build_plan(self.c).operations, [])

    def test_transcript_id_delete_is_honored(self):
        self.put()
        self.marker(identity=CLI)
        self.run_sync()
        self.assertFalse(self.path().exists())
        self.assertFalse(self.path(1).exists())

    def test_local_prefixed_delete_is_honored(self):
        self.put()
        self.marker(identity="local_" + SID)
        self.run_sync()
        self.assertFalse(self.path().exists())

    def test_newer_record_removes_stale_delete_marker(self):
        self.put(stamp=T0 + 2_000_000_000)
        self.marker()
        self.run_sync()
        self.assertTrue(self.path(1).exists())
        self.assertFalse(self.path(0, "deleted_" + SID).exists())
        self.assertFalse(self.path(1, "deleted_" + SID).exists())

    def test_archive_hint_is_consistent_and_reversible(self):
        self.put(isArchived=True)
        before = self.tree()
        result = self.run_sync()
        for i in range(2):
            d = json.loads(self.path(i, "archived-sessions.idx").read_text())
            self.assertEqual(d, {"v": 1, "archived": ["local_" + SID]})
        s.restore(self.c, result["snapshot"], guard=lambda: None)
        self.assertEqual(self.tree(), before)

    def test_restore_recovers_original_bytes_mtime_and_mode(self):
        self.put(title="new", stamp=T0 + 500)
        self.put(1, title="old", permissionMode="plan")
        self.path(1).chmod(0o640)
        before = self.tree()
        result = self.run_sync()
        s.restore(self.c, result["snapshot"], guard=lambda: None)
        self.assertEqual(before, self.tree())

    def test_restore_does_not_overwrite_new_user_edits(self):
        self.put()
        result = self.run_sync()
        self.put(1, stamp=T0 + 2000, title="real new edit")
        before = self.tree()
        with self.assertRaisesRegex(s.SyncError, "RESTORE_CONFLICT"):
            s.restore(self.c, result["snapshot"], guard=lambda: None)
        self.assertEqual(before, self.tree())

    def test_new_index_creation_leaves_single_link_inodes(self):
        self.put()
        self.run_sync()
        for peer in range(2):
            self.assertEqual(self.path(peer).stat().st_nlink, 1)

    def test_transcript_metadata_is_indexed_once_per_plan(self):
        self.put()
        with mock.patch.object(s, 'transcript_index', wraps=s.transcript_index) as index:
            s.build_plan(self.c)
        self.assertEqual(index.call_count, 1)

    def test_running_process_blocks_apply_without_index_change(self):
        self.put()
        before = self.tree()
        def guard():
            raise s.SyncError("CLAUDE_MUST_BE_FULLY_CLOSED")
        with self.assertRaisesRegex(s.SyncError, "CLAUDE_MUST_BE_FULLY_CLOSED"):
            s.apply(self.c, s.build_plan(self.c), guard=guard)
        self.assertEqual(before, self.tree())

    def test_detects_edit_between_plan_and_apply(self):
        self.put()
        plan = s.build_plan(self.c)
        self.put(title="edited", stamp=T0 + 1)
        before = self.tree()
        with self.assertRaisesRegex(s.SyncError, "CONCURRENT_CHANGE"):
            s.apply(self.c, plan, guard=lambda: None)
        self.assertEqual(before, self.tree())

    def test_crash_mid_apply_requires_recovery_and_can_restore(self):
        self.put(isArchived=True)
        before = self.tree()
        real_write = s.atomic_write
        writes = 0
        def fail_on_second_index(path, f, creating=False):
            nonlocal writes
            if self.index in path.parents:
                writes += 1
                if writes == 2:
                    raise OSError("simulated disk failure")
            return real_write(path, f, creating)
        with mock.patch.object(s, "atomic_write", side_effect=fail_on_second_index):
            with self.assertRaises(s.SyncError) as ctx:
                self.run_sync()
        sid = ctx.exception.snapshot
        self.assertTrue(sid)
        with self.assertRaisesRegex(s.SyncError, "RECOVERY_REQUIRED"):
            self.run_sync()
        s.restore(self.c, sid, guard=lambda: None)
        self.assertEqual(before, self.tree())
        self.assertEqual(s.unfinished(self.c), [])

    def test_restore_interruption_is_journaled_and_can_resume(self):
        self.put(isArchived=True)
        result = self.run_sync()
        calls = 0

        def guard():
            nonlocal calls
            calls += 1
            # Initial guard and first operation guard pass; stop before the
            # second operation so the restore has both applied and pending work.
            if calls == 3:
                raise s.SyncError("CLAUDE_MUST_BE_FULLY_CLOSED")

        with self.assertRaises(s.SyncError) as ctx:
            s.restore(self.c, result["snapshot"], guard=guard)
        self.assertEqual(ctx.exception.code, "CLAUDE_MUST_BE_FULLY_CLOSED")
        self.assertEqual(ctx.exception.snapshot, result["snapshot"])
        manifest = self.state / "snapshots" / result["snapshot"] / "manifest.json"
        self.assertEqual(json.loads(manifest.read_text())["state"], "restore_interrupted")
        s.restore(self.c, result["snapshot"], guard=lambda: None)
        self.assertEqual(s.unfinished(self.c), [])

    def test_process_start_mid_apply_stops_and_leaves_recovery_journal(self):
        self.put(isArchived=True)
        calls = 0
        def guard():
            nonlocal calls
            calls += 1
            if calls == 4:
                raise s.SyncError("CLAUDE_MUST_BE_FULLY_CLOSED")
        with self.assertRaises(s.SyncError) as ctx:
            s.apply(self.c, s.build_plan(self.c), guard=guard)
        self.assertEqual(ctx.exception.code, "CLAUDE_MUST_BE_FULLY_CLOSED")
        self.assertTrue(ctx.exception.snapshot)
        s.restore(self.c, ctx.exception.snapshot, guard=lambda: None)

    def test_symlinked_record_and_parent_refused(self):
        other = self.root / "other.json"
        other.write_text("{}")
        self.path().symlink_to(other)
        with self.assertRaisesRegex(s.SyncError, "SYMLINK_REFUSED"):
            s.build_plan(self.c)
        self.path().unlink()
        link = self.root / "linked-index"
        link.symlink_to(self.index, target_is_directory=True)
        self.write_config(index_root=str(link))
        with self.assertRaisesRegex(s.SyncError, "SYMLINK_REFUSED"):
            s.Config.load(self.config)

    def test_hardlinked_record_refused(self):
        self.put()
        os.link(self.path(), self.root / "hardlink")
        with self.assertRaisesRegex(s.SyncError, "NONREGULAR_OR_HARDLINK_REFUSED"):
            s.build_plan(self.c)

    def test_missing_transcript_blocks_sync(self):
        self.put()
        (self.projects / "project" / (CLI + ".jsonl")).unlink()
        with self.assertRaisesRegex(s.SyncError, "TRANSCRIPT_MISSING"):
            s.build_plan(self.c)

    def test_malformed_record_and_id_mismatch_block(self):
        self.put(sessionId="local_" + CLI)
        with self.assertRaisesRegex(s.SyncError, "SESSION_ID_MISMATCH"):
            s.build_plan(self.c)
        self.path().write_bytes(b"{")
        with self.assertRaisesRegex(s.SyncError, "INVALID_JSON"):
            s.build_plan(self.c)

    def test_unexpected_deletion_format_blocks(self):
        self.put()
        self.marker().write_text('{"deletedAt": 123}')
        with self.assertRaisesRegex(s.SyncError, "UNSUPPORTED_DELETION_MARKER"):
            s.build_plan(self.c)

    def test_unrecognized_marker_filename_blocks_instead_of_resurrecting(self):
        self.put()
        self.path(0, "deleted_changed-format").write_text("123")
        with self.assertRaisesRegex(s.SyncError, "UNSUPPORTED_DELETION_FILENAME"):
            s.build_plan(self.c)

    def test_snapshot_cannot_write_outside_configured_partitions(self):
        self.put()
        result = self.run_sync()
        path = self.state / "snapshots" / result["snapshot"] / "manifest.json"
        d = json.loads(path.read_text())
        d["operations"][0]["path"] = "../../outside"
        path.write_text(json.dumps(d))
        before = self.tree()
        with self.assertRaisesRegex(s.SyncError, "INVALID_MANIFEST_PATH"):
            s.restore(self.c, result["snapshot"], guard=lambda: None)
        self.assertEqual(before, self.tree())

    def test_corrupt_journal_blocks_with_a_structured_error(self):
        self.put()
        result = self.run_sync()
        path = self.state / "snapshots" / result["snapshot"] / "manifest.json"
        path.write_text("[]")
        p = subprocess.run([sys.executable, str(ENGINE), "doctor", "--config", str(self.config)], capture_output=True, text=True)
        self.assertEqual(p.returncode, 2)
        self.assertEqual(json.loads(p.stderr)["code"], "INVALID_MANIFEST")
        self.assertNotIn("Traceback", p.stderr)

    def test_tasks_and_third_account_untouched(self):
        self.put()
        tasks = self.path(0, "scheduled-tasks.json")
        tasks.write_bytes(b"PRIVATE TASK MUST NOT PROPAGATE")
        third = self.index / "third-account"
        third.mkdir()
        (third / "record.json").write_bytes(b"IGNORE")
        before = self.tree(third)
        self.run_sync()
        self.assertFalse(self.path(1, "scheduled-tasks.json").exists())
        self.assertEqual(self.tree(third), before)
        self.assertEqual(tasks.read_bytes(), b"PRIVATE TASK MUST NOT PROPAGATE")

    def test_invalid_config_and_snapshot_traversal_refused(self):
        with self.assertRaisesRegex(s.SyncError, "INVALID_SNAPSHOT_ID"):
            s.restore_plan(self.c, "../../outside")
        self.write_config(state_dir=str(self.index / "state"))
        with self.assertRaisesRegex(s.SyncError, "OVERLAPPING_ROOTS"):
            s.Config.load(self.config)
        self.write_config(partitions=[{"account": A, "organization": OA}])
        with self.assertRaisesRegex(s.SyncError, "EXACTLY_TWO_ACCOUNTS_REQUIRED"):
            s.Config.load(self.config)

    def test_corrupt_backup_refused(self):
        self.put(title="new", stamp=T0 + 100)
        self.put(1, title="old")
        result = self.run_sync()
        directory = self.state / "snapshots" / result["snapshot"]
        next(directory.glob("before-*.bin")).write_bytes(b"tampered")
        before = self.tree()
        with self.assertRaisesRegex(s.SyncError, "CORRUPT_BACKUP"):
            s.restore(self.c, result["snapshot"], guard=lambda: None)
        self.assertEqual(before, self.tree())

    def test_lock_prevents_concurrent_apply(self):
        self.put()
        with s.lock(self.c):
            with self.assertRaisesRegex(s.SyncError, "SYNC_ALREADY_RUNNING"):
                self.run_sync()

    def test_process_guard_matches_desktop_helpers_but_not_code_cli(self):
        with mock.patch.object(s.sys, "platform", "darwin"):
            for command in [b"/Applications/Claude.app/Contents/MacOS/Claude\n", b"Claude Helper (Renderer)\n"]:
                with mock.patch.object(s.subprocess, "run", return_value=mock.Mock(stdout=command)):
                    with self.assertRaisesRegex(s.SyncError, "CLAUDE_MUST_BE_FULLY_CLOSED"):
                        s.assert_stopped()
            with mock.patch.object(s.subprocess, "run", return_value=mock.Mock(stdout=b"/usr/local/bin/claude\n/Applications/Cockpit Tools.app/Contents/MacOS/app\n")):
                s.assert_stopped()

    def test_snapshot_files_are_private(self):
        self.put()
        result = self.run_sync()
        directory = self.state / "snapshots" / result["snapshot"]
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        for p in directory.iterdir():
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
