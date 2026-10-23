import dataclasses
import json
import os
from pathlib import Path
import unittest
from unittest import mock

import test_engine as base
from test_engine import s, A, B, OA, OB, CLI, NAME, T0, PARENT, CHILD


class MultiProfileTests(unittest.TestCase):
    setUp = base.EngineTests.setUp
    write_config = base.EngineTests.write_config
    path = base.EngineTests.path
    put = base.EngineTests.put
    put_session = base.EngineTests.put_session
    child_link = base.EngineTests.child_link
    tree = base.EngineTests.tree
    run_sync = base.EngineTests.run_sync

    def multi(self):
        parts = [
            {"profile": "legacy-a", "index_root": str(self.index), "account": A, "organization": OA},
            {"profile": "legacy-b", "index_root": str(self.index), "account": B, "organization": OB},
        ]
        for i, (account, org) in enumerate(((A, OA), (B, OB))):
            root = self.root / ("profile-" + str(i)) / "index"
            (root / account / org).mkdir(parents=True)
            parts.append({"profile": "case-" + str(i), "index_root": str(root),
                          "account": account, "organization": org})
        self.write_config(schema_version=2, partitions=parts)
        self.c = s.Config.load(self.config)
        return parts

    def test_four_locations_merge_hierarchy_and_keep_target_permissions(self):
        self.put_session(PARENT)
        self.put_session(CHILD, spawnedFrom=self.child_link(wakeParent=True))
        self.multi()
        body_before = self.tree(self.projects)
        r = self.run_sync()
        self.assertEqual(r["sessions"], 2)
        for peer in self.c.peers:
            row = json.loads(self.c.path(peer + "/" + CHILD + ".json").read_text())
            self.assertEqual(row["spawnedFrom"]["sessionId"], PARENT)
            if peer.startswith("case-"):
                self.assertNotIn("wakeParent", row["spawnedFrom"])
        self.assertEqual(self.tree(self.projects), body_before)
        self.assertEqual(s.build_plan(self.c).operations, [])

    def test_four_locations_update_roundtrip_and_restore_original_bytes(self):
        self.put()
        self.multi()
        before = [self.tree(p) for _, p in self.c.locations]
        result = self.run_sync()
        path = self.c.path(self.c.peers[-1] + "/" + NAME)
        row = json.loads(path.read_text()); row["title"] = "from fourth profile"
        path.write_bytes(s.encoded(row)); os.utime(path, ns=(T0 + 100, T0 + 100))
        changed = self.run_sync()
        for peer in self.c.peers:
            self.assertEqual(json.loads(self.c.path(peer + "/" + NAME).read_text())["title"], row["title"])
        s.restore(self.c, changed["snapshot"], guard=lambda: None)
        # The user edit must be removed explicitly before restoring the earlier snapshot.
        with self.assertRaisesRegex(s.SyncError, "RESTORE_CONFLICT"):
            s.restore(self.c, result["snapshot"], guard=lambda: None)
        path.write_bytes(s.encoded(json.loads(self.path().read_text()))); os.utime(path, ns=(T0, T0))
        s.restore(self.c, result["snapshot"], guard=lambda: None)
        self.assertEqual([self.tree(p) for _, p in self.c.locations], before)

    def test_snapshot_scope_binds_every_profile_location(self):
        self.put(); parts = self.multi(); result = self.run_sync()
        replacement = self.root / "different" / A / OA
        replacement.mkdir(parents=True)
        parts[2]["index_root"] = str(replacement.parents[1])
        self.write_config(schema_version=2, partitions=parts)
        moved = s.Config.load(self.config)
        with self.assertRaisesRegex(s.SyncError, "SNAPSHOT_SCOPE_MISMATCH"):
            s.restore_plan(moved, result["snapshot"])

    def test_duplicate_destinations_and_overlaps_are_rejected(self):
        parts = self.multi()
        for bad in (
            parts + [{**parts[0], "profile": "duplicate"}],
            [{**parts[0], "index_root": str(self.projects)}, *parts[1:]],
            [{**parts[0], "profile": "../../unsafe"}, *parts[1:]],
        ):
            self.write_config(schema_version=2, partitions=bad)
            with self.assertRaises(s.SyncError): s.Config.load(self.config)

    def test_equal_time_conflict_in_fourth_profile_prevents_every_write(self):
        self.put(); self.multi(); self.run_sync()
        path = self.c.path(self.c.peers[-1] + "/" + NAME)
        row = json.loads(path.read_text()); row["title"] = "conflict"
        path.write_bytes(s.encoded(row)); os.utime(path, ns=(T0, T0))
        before = [self.tree(p) for _, p in self.c.locations]
        with self.assertRaisesRegex(s.SyncError, "EQUAL_TIME_CONFLICT"): self.run_sync()
        self.assertEqual([self.tree(p) for _, p in self.c.locations], before)

    def test_interruption_across_roots_is_recoverable(self):
        self.put(isArchived=True); self.multi()
        before = [self.tree(p) for _, p in self.c.locations]
        real = s.atomic_write; writes = 0
        def fail(path, f, creating=False):
            nonlocal writes
            if any(root in path.parents for _, root in self.c.locations):
                writes += 1
                if writes == 3: raise OSError("offline failure")
            return real(path, f, creating)
        with mock.patch.object(s, "atomic_write", side_effect=fail):
            with self.assertRaises(s.SyncError) as ctx: self.run_sync()
        self.assertTrue(s.unfinished(self.c))
        s.restore(self.c, ctx.exception.snapshot, guard=lambda: None)
        self.assertEqual([self.tree(p) for _, p in self.c.locations], before)

    def test_legacy_configuration_can_read_completed_multi_snapshots(self):
        self.put(); self.multi(); self.run_sync()
        self.write_config(); legacy = s.Config.load(self.config)
        self.assertEqual(s.unfinished(legacy), [])
        self.assertEqual(legacy.scope()["schema_version"], 1)

    def test_manifest_traversal_and_symlinked_profile_rejected(self):
        self.multi()
        for path in ("/absolute", "../../unsafe", self.c.peers[0] + "/../" + NAME):
            with self.assertRaisesRegex(s.SyncError, "INVALID_MANIFEST_PATH"): self.c.path(path)
        root = self.c.locations[-1][1]
        moved = root.with_name("real"); root.rename(moved); root.symlink_to(moved, target_is_directory=True)
        with self.assertRaisesRegex(s.SyncError, "SYMLINK_REFUSED"): s.Config.load(self.config)


if __name__ == "__main__": unittest.main()
