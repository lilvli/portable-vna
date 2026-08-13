from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from pvna_host.protocol import PointResult, StartPoint
from pvna_host.protocol.session import (
    DeviceState as WireDeviceState,
)
from pvna_host.protocol.session import (
    MeasurementFailed,
    MeasurementUnknown,
    PointTransaction,
    ProtocolSession,
    SafetyStateUnknown,
    SessionError,
)

from .device import AcceptedPoint, DeviceError, MeasurementCancelled, MeasurementResultUnknown
from .models import DeviceState, DeviceStatus, EvidenceSource, SweepPlan

_REQUIRED_LINK_FLAGS = 0x1F
_LINK_NAMES = (
    "clock_locked",
    "sysref_seen",
    "adc_jesd_ready",
    "dac_jesd_ready",
    "data_path_ready",
)


class SerialDeviceAdapter:
    """Domain adapter for an explicitly opened PVNA-Link V0.1 serial session."""

    source = EvidenceSource.HARDWARE

    def __init__(self, session: ProtocolSession) -> None:
        self.session = session
        self.device_id = "UNKNOWN"
        self.fpga_build_id = "UNKNOWN"
        self._connected = False
        self._last_error: str | None = None
        self._active_tasks: dict[tuple[int, int], asyncio.Task[PointResult]] = {}

    async def connect(self) -> DeviceStatus:
        try:
            preflight = await self.session.connect(required_link_flags=_REQUIRED_LINK_FLAGS)
            self.device_id = str(preflight.info.device_id)
            self.fpga_build_id = str(preflight.info.fpga_build_id)
            self._connected = True
            return self._map_status(preflight.status)
        except Exception as exc:
            self._last_error = str(exc)
            raise DeviceError(f"serial preflight failed: {exc}") from exc

    async def disconnect(self) -> DeviceStatus:
        try:
            confirmed = await self.session.disconnect()
            if confirmed is None:
                raise SafetyStateUnknown("disconnect did not return a confirmed HOLD status")
            status = self._map_status(confirmed, connected=False)
            self._connected = False
            return status
        except Exception as exc:
            self._last_error = str(exc)
            raise DeviceError(str(exc)) from exc

    async def get_status(self) -> DeviceStatus:
        try:
            return self._map_status(await self.session.get_status())
        except Exception as exc:
            self._last_error = str(exc)
            if isinstance(exc, MeasurementUnknown | SafetyStateUnknown):
                return self._unknown_status()
            raise DeviceError(str(exc)) from exc

    async def exit_hold(self) -> DeviceStatus:
        return await self._status_command(self.session.exit_hold)

    async def enter_hold(self) -> DeviceStatus:
        return await self._status_command(self.session.enter_hold)

    async def prepare_plan(self, plan: SweepPlan) -> None:
        # measurement_role is host-side provenance. simulation_profile only
        # selects a SimulatedDevice model and never changes serial wire bytes.
        del plan

    async def start_point(self, request: StartPoint) -> AcceptedPoint:
        try:
            transaction = await self.session.start_point(request)
        except MeasurementUnknown as exc:
            self._last_error = str(exc)
            unknown = MeasurementResultUnknown(f"UNKNOWN_RESULT: {exc}")
            unknown.sequence = exc.sequence
            unknown.measurement_id = exc.measurement_id
            unknown.point_index = exc.point_index
            raise unknown from exc
        except Exception as exc:
            self._last_error = str(exc)
            raise DeviceError(str(exc)) from exc
        task = asyncio.create_task(self._wait_point(transaction))
        identity = (request.measurement_id, request.point_index)
        self._active_tasks[identity] = task
        task.add_done_callback(lambda _task: self._active_tasks.pop(identity, None))
        return AcceptedPoint(request=request, result=task)

    async def cancel(self, measurement_id: int, point_index: int) -> DeviceStatus:
        try:
            status = self._map_status(await self.session.cancel(measurement_id, point_index))
            task = self._active_tasks.get((measurement_id, point_index))
            if task is not None:
                task.cancel()
            return status
        except Exception as exc:
            self._last_error = str(exc)
            raise DeviceError(str(exc)) from exc

    async def _wait_point(self, transaction: PointTransaction) -> PointResult:
        try:
            return await self.session.wait_point_result(transaction)
        except asyncio.CancelledError as exc:
            raise MeasurementCancelled("serial point cancelled") from exc
        except MeasurementFailed as exc:
            self._last_error = str(exc)
            raise DeviceError(str(exc)) from exc
        except MeasurementUnknown as exc:
            self._last_error = str(exc)
            raise MeasurementResultUnknown(f"UNKNOWN_RESULT: {exc}") from exc
        except SessionError as exc:
            self._last_error = str(exc)
            raise DeviceError(str(exc)) from exc

    async def _status_command(self, command: Callable[[], Awaitable[object]]) -> DeviceStatus:
        try:
            status = await command()
            return self._map_status(status)
        except Exception as exc:
            self._last_error = str(exc)
            raise DeviceError(str(exc)) from exc

    def _map_status(self, status: object, *, connected: bool | None = None) -> DeviceStatus:
        raw_state = status.device_state  # type: ignore[attr-defined]
        state = {
            WireDeviceState.BOOT: DeviceState.BOOT,
            WireDeviceState.HOLD: DeviceState.HOLD,
            WireDeviceState.IDLE: DeviceState.IDLE,
            WireDeviceState.BUSY: DeviceState.BUSY,
            WireDeviceState.RESULT_READY: DeviceState.RESULT_READY,
            WireDeviceState.FAULT: DeviceState.FAULT,
        }[raw_state]
        flags = int(status.link_flags)  # type: ignore[attr-defined]
        is_connected = self._connected if connected is None else connected
        return DeviceStatus(
            connected=is_connected,
            source=self.source if is_connected else None,
            state=state if is_connected else DeviceState.DISCONNECTED,
            rf_output_enabled=bool(status.rf_output_enabled),  # type: ignore[attr-defined]
            last_result_valid=bool(status.last_result_valid),  # type: ignore[attr-defined]
            active_measurement_id=int(status.active_measurement_id),  # type: ignore[attr-defined]
            active_point_index=int(status.active_point_index),  # type: ignore[attr-defined]
            last_measurement_id=int(status.last_measurement_id),  # type: ignore[attr-defined]
            last_point_index=int(status.last_point_index),  # type: ignore[attr-defined]
            last_error=self._last_error or (str(status.last_error) if status.last_error else None),  # type: ignore[attr-defined]
            link_flags={name: bool(flags & (1 << bit)) for bit, name in enumerate(_LINK_NAMES)},
        )

    def _unknown_status(self) -> DeviceStatus:
        return DeviceStatus(
            connected=self._connected,
            source=self.source if self._connected else None,
            state=DeviceState.UNKNOWN,
            rf_output_enabled=False,
            last_result_valid=False,
            last_error=self._last_error or "serial device state is unknown",
        )
