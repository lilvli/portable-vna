from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pvna_host.domain import RunManager, RunState, RunStore, SweepPlan  # noqa: E402
from pvna_host.domain.models import EvidenceSource, RunRecord  # noqa: E402


class PersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_run_reopens_read_only_with_integrity_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = RunStore(Path(temporary))
            manager = RunManager(store)
            await manager.connect("simulated")
            record = await manager.start_sweep(SweepPlan(5_000_000, 7_000_000, 3))
            completed = await manager.wait(record.run_id)
            self.assertEqual(completed.state, RunState.COMPLETED)
            self.assertEqual(completed.data_validation, "VALID")
            self.assertTrue(completed.safe_hold_confirmed)
            self.assertEqual(len(completed.points_sha256 or ""), 64)

            reopened = RunManager(store).get_run(record.run_id)
            self.assertEqual(reopened.state, RunState.COMPLETED)
            self.assertEqual(reopened.points_sha256, completed.points_sha256)
            summary = store.read_summary(record.run_id)
            self.assertEqual(summary["state"], "COMPLETED")
            self.assertIn("not serial, FPGA, JESD, or RF", summary["evidence_boundary"])

    async def test_interrupted_run_is_recovered_as_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = RunStore(Path(temporary))
            record = RunRecord(
                run_id="run_interrupted",
                measurement_id=9,
                source=EvidenceSource.SIMULATED,
                plan=SweepPlan(5_000_000, 6_000_000, 2),
                state=RunState.RUNNING,
            )
            store.create(record)
            reopened = RunManager(store).get_run(record.run_id)
            self.assertEqual(reopened.state, RunState.UNKNOWN)
            self.assertTrue(reopened.recovered_after_interruption)
            self.assertFalse(reopened.safe_hold_confirmed)
            self.assertIn("service restarted", reopened.error or "")

    async def test_tampered_completed_data_is_not_replayed_as_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = RunStore(root)
            manager = RunManager(store)
            await manager.connect("simulated")
            record = await manager.start_sweep(SweepPlan(5_000_000, 5_000_000, 1))
            await manager.wait(record.run_id)
            points_path = root / record.run_id / "points.jsonl"
            point = json.loads(points_path.read_text(encoding="utf-8"))
            point["r_i_acc"] = "999999999999999999"
            points_path.write_text(json.dumps(point) + "\n", encoding="utf-8")

            reopened = RunManager(store).get_run(record.run_id)
            self.assertEqual(reopened.state, RunState.UNKNOWN)
            self.assertEqual(reopened.data_validation, "INVALID")
            self.assertIn("integrity evidence", reopened.error or "")

    async def test_terminal_manifest_without_matching_summary_recovers_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = RunStore(root)
            manager = RunManager(store)
            await manager.connect("simulated")
            record = await manager.start_sweep(SweepPlan(5_000_000, 5_000_000, 1))
            completed = await manager.wait(record.run_id)
            self.assertEqual(completed.state, RunState.COMPLETED)

            (root / record.run_id / "summary.json").unlink()
            reopened = RunManager(store).get_run(record.run_id)
            self.assertEqual(reopened.state, RunState.UNKNOWN)
            self.assertEqual(reopened.data_validation, "INVALID")
            self.assertIn("summary is not available", reopened.error or "")

    async def test_confirmed_point_count_tamper_recovers_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = RunStore(root)
            manager = RunManager(store)
            await manager.connect("simulated")
            record = await manager.start_sweep(SweepPlan(5_000_000, 6_000_000, 2))
            await manager.wait(record.run_id)

            manifest_path = root / record.run_id / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["confirmed_points"] = 1
            manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")

            reopened = RunManager(store).get_run(record.run_id)
            self.assertEqual(reopened.state, RunState.UNKNOWN)
            self.assertIn("confirmed point count", reopened.error or "")


if __name__ == "__main__":
    unittest.main()
