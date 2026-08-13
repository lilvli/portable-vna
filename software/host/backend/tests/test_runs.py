from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pvna_host.domain import (  # noqa: E402
    RunManager,
    RunState,
    RunStore,
    SimulatedDevice,
    SweepPlan,
)
from pvna_host.domain.device import DeviceError  # noqa: E402
from pvna_host.domain.manager import RunConflict  # noqa: E402
from pvna_host.domain.models import DeviceState  # noqa: E402
from pvna_host.protocol import StartPoint  # noqa: E402


class FailingPointStore(RunStore):
    def append_point(self, run_id, result, source):  # type: ignore[no-untyped-def]
        raise OSError("injected save failure")


class HoldFailureDevice(SimulatedDevice):
    async def enter_hold(self):  # type: ignore[no-untyped-def]
        raise DeviceError("injected HOLD failure")


class RunManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_does_not_forget_device_when_hold_is_unconfirmed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = RunManager(RunStore(Path(temporary)), simulated_factory=HoldFailureDevice)
            await manager.connect("simulated")
            with self.assertRaises(RunConflict):
                await manager.disconnect()
            self.assertTrue((await manager.get_device_status()).connected)

    async def test_acceptance_is_separate_from_final_point(self) -> None:
        device = SimulatedDevice(point_latency_s=0.02)
        await device.connect()
        await device.exit_hold()
        accepted = await device.start_point(StartPoint(1, 0, 5_000_000, 8192, 1, 1000, 65536, 2000))
        self.assertFalse(accepted.result.done())
        self.assertEqual((await device.get_status()).state, DeviceState.BUSY)
        result = await accepted.result
        self.assertEqual(result.measurement_id, 1)
        self.assertEqual((await device.get_status()).state, DeviceState.RESULT_READY)

    async def test_simulated_sweep_saves_before_advancing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = RunStore(Path(temporary))
            manager = RunManager(store)
            status = await manager.connect("simulated")
            self.assertEqual(status.state, DeviceState.HOLD)

            record = await manager.start_sweep(SweepPlan(5_000_000, 7_000_000, 3))
            completed = await manager.wait(record.run_id)
            self.assertEqual(completed.state, RunState.COMPLETED)
            self.assertEqual(completed.confirmed_points, 3)

            points = store.read_points(record.run_id)
            self.assertEqual(len(points), 3)
            self.assertEqual(points[0]["source"], "SIMULATED")
            self.assertIsInstance(points[0]["r_i_acc"], str)
            manifest = json.loads(
                (Path(temporary) / record.run_id / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["state"], "COMPLETED")
            self.assertEqual(manifest["confirmed_points"], 3)
            self.assertEqual((await manager.get_device_status()).state, DeviceState.HOLD)
            await manager.safe_shutdown()

    async def test_save_failure_does_not_advance_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = RunManager(FailingPointStore(Path(temporary)))
            await manager.connect("simulated")
            record = await manager.start_sweep(SweepPlan(5_000_000, 5_000_000, 1))
            failed = await manager.wait(record.run_id)
            self.assertEqual(failed.state, RunState.FAILED)
            self.assertEqual(failed.confirmed_points, 0)
            self.assertIn("injected save failure", failed.error or "")
            self.assertEqual((await manager.get_device_status()).state, DeviceState.HOLD)

    async def test_cancelled_run_finishes_in_hold(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = RunManager(RunStore(Path(temporary)))
            await manager.connect("simulated")
            record = await manager.start_sweep(SweepPlan(5_000_000, 100_000_000, 1000))
            await manager.cancel_run(record.run_id)
            cancelled = await manager.wait(record.run_id)
            self.assertEqual(cancelled.state, RunState.CANCELLED)
            status = await manager.get_device_status()
            self.assertEqual(status.state, DeviceState.HOLD)
            self.assertFalse(status.rf_output_enabled)

    async def test_point_timeout_is_failed_and_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = RunManager(
                RunStore(Path(temporary)),
                simulated_factory=lambda: SimulatedDevice(point_latency_s=0.05),
            )
            await manager.connect("simulated")
            record = await manager.start_sweep(
                SweepPlan(5_000_000, 5_000_000, 1, point_timeout_ms=1)
            )
            failed = await manager.wait(record.run_id)
            self.assertEqual(failed.state, RunState.FAILED)
            self.assertEqual(failed.confirmed_points, 0)
            self.assertIn("timeout", failed.error or "")
            status = await manager.get_device_status()
            self.assertEqual(status.state, DeviceState.HOLD)
            self.assertFalse(status.rf_output_enabled)

    async def test_shutdown_cancels_active_run_and_disconnects(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = RunManager(
                RunStore(Path(temporary)),
                simulated_factory=lambda: SimulatedDevice(point_latency_s=0.05),
            )
            await manager.connect("simulated")
            record = await manager.start_sweep(SweepPlan(5_000_000, 100_000_000, 1000))
            await manager.safe_shutdown()
            self.assertIn(
                manager.get_run(record.run_id).state, {RunState.CANCELLED, RunState.FAILED}
            )
            status = await manager.get_device_status()
            self.assertEqual(status.state, DeviceState.DISCONNECTED)
            self.assertFalse(status.rf_output_enabled)


if __name__ == "__main__":
    unittest.main()
