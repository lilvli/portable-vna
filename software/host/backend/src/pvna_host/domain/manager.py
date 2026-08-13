from __future__ import annotations

import asyncio
import contextlib
import secrets
import uuid
from collections.abc import Callable

from pvna_host.protocol import StartPoint
from pvna_host.protocol.session import validate_point_result

from .device import (
    DeviceAdapter,
    DeviceError,
    MeasurementCancelled,
    MeasurementResultUnknown,
)
from .events import EventBroker
from .models import (
    DeviceState,
    DeviceStatus,
    RunRecord,
    RunState,
    SweepPlan,
    utc_now,
)
from .simulated import SimulatedDevice
from .store import RunStore

SerialDeviceFactory = Callable[[str, int], DeviceAdapter]


class RunNotFound(KeyError):
    pass


class RunConflict(RuntimeError):
    pass


class _RunCancelled(RuntimeError):
    pass


class RunManager:
    """The sole owner of device state, frozen sweep plans, and run progress."""

    def __init__(
        self,
        store: RunStore,
        *,
        simulated_factory: Callable[[], DeviceAdapter] = SimulatedDevice,
        serial_factory: SerialDeviceFactory | None = None,
    ) -> None:
        self.store = store
        self.events = EventBroker()
        self._simulated_factory = simulated_factory
        self._serial_factory = serial_factory
        self._device: DeviceAdapter | None = None
        self._runs: dict[str, RunRecord] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._active_handle: dict[str, tuple[int, int]] = {}
        self._lock = asyncio.Lock()
        self._archive_errors: list[dict[str, object]] = []
        self._load_archive()

    async def connect(
        self, source: str, *, port: str | None = None, baud_rate: int = 115_200
    ) -> DeviceStatus:
        async with self._lock:
            if self._device is not None:
                raise RunConflict("a device is already connected")
            if source.lower() == "simulated":
                device = self._simulated_factory()
            elif source.lower() == "serial":
                if not port:
                    raise ValueError("serial source requires an explicit port")
                if self._serial_factory is not None:
                    device = self._serial_factory(port, baud_rate)
                else:
                    from pvna_host.protocol.session import ProtocolSession
                    from pvna_host.transport import SerialTransport

                    from .serial_device import SerialDeviceAdapter

                    device = SerialDeviceAdapter(
                        ProtocolSession(SerialTransport(port, baudrate=baud_rate))
                    )
            else:
                raise ValueError("source must be simulated or serial")
            status = await device.connect()
            self._device = device
        await self.events.publish("device.status_changed", status.to_api_dict())
        return status

    async def disconnect(self) -> DeviceStatus:
        async with self._lock:
            if any(not run.state.terminal for run in self._runs.values()):
                raise RunConflict("cannot disconnect while a run is active")
            if self._device is None:
                return self._disconnected_status()
            device = self._device
        try:
            safe_status = await device.enter_hold()
            if safe_status.state is not DeviceState.HOLD or safe_status.rf_output_enabled:
                raise DeviceError("safe HOLD could not be confirmed before disconnect")
            status = await device.disconnect()
        except Exception as exc:
            raise RunConflict(f"disconnect safety could not be confirmed: {exc}") from exc
        async with self._lock:
            if self._device is device:
                self._device = None
        await self.events.publish("device.status_changed", status.to_api_dict())
        return status

    async def get_device_status(self) -> DeviceStatus:
        device = self._device
        if device is None:
            return self._disconnected_status()
        return await device.get_status()

    async def hold(self) -> DeviceStatus:
        device = self._require_device()
        status = await device.enter_hold()
        await self.events.publish("device.status_changed", status.to_api_dict())
        return status

    async def start_sweep(self, plan: SweepPlan) -> RunRecord:
        async with self._lock:
            device = self._require_device()
            status = await device.get_status()
            if status.state not in {
                DeviceState.HOLD,
                DeviceState.IDLE,
                DeviceState.RESULT_READY,
            }:
                raise RunConflict(f"device cannot start a sweep from {status.state.value}")
            if any(not run.state.terminal for run in self._runs.values()):
                raise RunConflict("only one run may be active")
            run_id = f"run_{uuid.uuid4().hex}"
            measurement_id = secrets.randbelow(0xFFFFFFFF) + 1
            record = RunRecord(
                run_id=run_id,
                measurement_id=measurement_id,
                source=device.source,
                plan=plan,
                device_id=device.device_id,
                fpga_build_id=device.fpga_build_id,
            )
            self.store.create(record)
            self._runs[run_id] = record
            self._tasks[run_id] = asyncio.create_task(self._execute(record, device))
            return record

    async def cancel_run(self, run_id: str) -> RunRecord:
        record = self.get_run(run_id)
        if record.state.terminal:
            return record
        record.cancel_requested = True
        record.state = RunState.CANCELLING
        self.store.update_manifest(record)
        active = self._active_handle.get(run_id)
        if active is not None and self._device is not None:
            with contextlib.suppress(DeviceError):
                await self._device.cancel(*active)
        return record

    def get_run(self, run_id: str) -> RunRecord:
        try:
            return self._runs[run_id]
        except KeyError as exc:
            raise RunNotFound(run_id) from exc

    def get_points(self, run_id: str) -> list[dict[str, object]]:
        self.get_run(run_id)
        return self.store.read_points(run_id)

    def list_runs(self) -> list[dict[str, object]]:
        records = sorted(self._runs.values(), key=lambda item: item.created_at_utc, reverse=True)
        return [record.to_api_dict() for record in records] + list(self._archive_errors)

    def get_summary(self, run_id: str) -> dict[str, object]:
        self.get_run(run_id)
        return self.store.read_summary(run_id)

    async def wait(self, run_id: str) -> RunRecord:
        self.get_run(run_id)
        task = self._tasks.get(run_id)
        if task is not None:
            await task
        return self.get_run(run_id)

    async def safe_shutdown(self) -> None:
        for record in tuple(self._runs.values()):
            if not record.state.terminal:
                await self.cancel_run(record.run_id)
        pending = tuple(task for task in self._tasks.values() if not task.done())
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if self._device is not None:
            with contextlib.suppress(Exception):
                await self._device.enter_hold()
            with contextlib.suppress(Exception):
                await self._device.disconnect()
            self._device = None

    async def _execute(self, record: RunRecord, device: DeviceAdapter) -> None:
        record.state = RunState.RUNNING
        record.started_at_utc = utc_now()
        self.store.update_manifest(record)
        await self.events.publish("run.started", record.to_api_dict(), run_id=record.run_id)
        try:
            await device.prepare_plan(record.plan)
            status = await device.get_status()
            if status.state is DeviceState.HOLD:
                status = await device.exit_hold()
                await self.events.publish("device.status_changed", status.to_api_dict())
            for point_index, frequency_hz in enumerate(record.plan.frequency_axis()):
                if record.cancel_requested:
                    raise _RunCancelled
                request = StartPoint(
                    measurement_id=record.measurement_id,
                    point_index=point_index,
                    frequency_hz=frequency_hz,
                    stimulus_amplitude_q15=record.plan.stimulus_amplitude_q15,
                    measure_flags=0x0001,
                    settle_us=record.plan.settle_us,
                    integration_count=record.plan.integration_count,
                    max_duration_ms=record.plan.point_timeout_ms,
                )
                accepted = await device.start_point(request)
                self._active_handle[record.run_id] = (record.measurement_id, point_index)
                await self.events.publish(
                    "point.accepted",
                    {"measurement_id": record.measurement_id, "point_index": point_index},
                    run_id=record.run_id,
                )
                completed, _ = await asyncio.wait(
                    {accepted.result}, timeout=record.plan.point_timeout_ms / 1000
                )
                if not completed:
                    with contextlib.suppress(DeviceError):
                        await device.cancel(record.measurement_id, point_index)
                    try:
                        await asyncio.wait_for(accepted.result, timeout=0.1)
                    except MeasurementCancelled:
                        pass
                    except TimeoutError:
                        accepted.result.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await accepted.result
                    raise TimeoutError
                try:
                    result = await accepted.result
                except MeasurementCancelled as exc:
                    raise _RunCancelled from exc
                self._active_handle.pop(record.run_id, None)
                if (
                    result.point_index != point_index
                    or result.measurement_id != record.measurement_id
                ):
                    raise RuntimeError("result correlation mismatch")
                if result.requested_frequency_hz != frequency_hz:
                    raise RuntimeError("result frequency mismatch")
                validate_point_result(result, request)
                self.store.append_point(record.run_id, result, record.source.value)
                record.points_data.append(result)
                record.confirmed_points += 1
                self.store.update_manifest(record)
                await self.events.publish(
                    "point.confirmed", result.to_api_dict(), run_id=record.run_id
                )
                await self.events.publish(
                    "run.progress",
                    {
                        "confirmed_points": record.confirmed_points,
                        "expected_points": record.expected_points,
                        "progress": record.confirmed_points / record.expected_points,
                    },
                    run_id=record.run_id,
                )
            await self._finish_in_hold(record, device, RunState.COMPLETED, "run.completed")
        except _RunCancelled:
            await self._finish_in_hold(record, device, RunState.CANCELLED, "run.cancelled")
        except TimeoutError:
            record.error = "point result timeout"
            await self._finish_in_hold(record, device, RunState.FAILED, "run.failed")
        except MeasurementResultUnknown as exc:
            record.error = str(exc)
            await self._finish_in_hold(record, device, RunState.UNKNOWN, "run.unknown")
        except Exception as exc:
            record.error = str(exc)
            await self._finish_in_hold(record, device, RunState.FAILED, "run.failed")
        finally:
            self._active_handle.pop(record.run_id, None)

    async def _finish_in_hold(
        self,
        record: RunRecord,
        device: DeviceAdapter,
        target_state: RunState,
        event: str,
    ) -> None:
        try:
            status = await device.enter_hold()
            if status.state is not DeviceState.HOLD or status.rf_output_enabled:
                raise DeviceError("safe HOLD could not be confirmed")
            record.safe_hold_confirmed = True
            if target_state is RunState.COMPLETED:
                record.points_sha256 = self.store.validate_complete_run(record)
                record.data_validation = "VALID"
            else:
                record.data_validation = "PARTIAL"
            record.state = target_state
        except Exception as exc:
            if record.safe_hold_confirmed:
                record.state = RunState.FAILED
                record.data_validation = "INVALID"
                reason = f"final data validation failed: {exc}"
            else:
                record.state = RunState.UNKNOWN
                reason = f"safe state unknown: {exc}"
            record.error = f"{record.error + '; ' if record.error else ''}{reason}"
            event = "run.failed"
        record.finished_at_utc = utc_now()
        # Publish terminal evidence first; the terminal manifest is the final commit marker.
        self.store.write_summary(record)
        self.store.update_manifest(record)
        await self.events.publish(event, record.to_api_dict(), run_id=record.run_id)

    def _load_archive(self) -> None:
        records, self._archive_errors = self.store.load_records()
        for record in records:
            changed = False
            if not record.state.terminal:
                record.state = RunState.UNKNOWN
                record.recovered_after_interruption = True
                record.safe_hold_confirmed = False
                record.data_validation = "PARTIAL"
                record.error = (
                    f"{record.error + '; ' if record.error else ''}"
                    "service restarted before terminal state; device state was not provable"
                )
                record.finished_at_utc = record.finished_at_utc or utc_now()
                changed = True
            elif record.state is RunState.COMPLETED:
                try:
                    digest = self.store.validate_complete_run(record)
                    if (
                        not record.safe_hold_confirmed
                        or record.data_validation != "VALID"
                        or record.points_sha256 != digest
                    ):
                        raise ValueError("completed manifest integrity evidence is inconsistent")
                    self.store.validate_terminal_publication(record)
                except Exception as exc:
                    record.state = RunState.UNKNOWN
                    record.recovered_after_interruption = True
                    record.data_validation = "INVALID"
                    record.error = f"completed archive validation failed: {exc}"
                    changed = True
            self._runs[record.run_id] = record
            if changed:
                self.store.write_summary(record)
                self.store.update_manifest(record)

    def _require_device(self) -> DeviceAdapter:
        if self._device is None:
            raise RunConflict("no device is connected")
        return self._device

    @staticmethod
    def _disconnected_status() -> DeviceStatus:
        return DeviceStatus(
            connected=False,
            source=None,
            state=DeviceState.DISCONNECTED,
            rf_output_enabled=False,
            last_result_valid=False,
        )
