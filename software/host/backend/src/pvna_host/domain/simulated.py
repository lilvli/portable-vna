from __future__ import annotations

import asyncio
import math
import time

from pvna_host.protocol import PointResult, StartPoint

from .device import AcceptedPoint, DeviceError, MeasurementCancelled
from .models import DeviceState, DeviceStatus, EvidenceSource, SweepPlan


class SimulatedDevice:
    """Deterministic fake device that preserves the real state and safety semantics."""

    source = EvidenceSource.SIMULATED
    device_id = "SIMULATED-PVNA"
    fpga_build_id = "SIMULATED-V0.1"

    def __init__(self, *, point_latency_s: float = 0.002) -> None:
        self._point_latency_s = point_latency_s
        self._connected = False
        self._state = DeviceState.DISCONNECTED
        self._rf_enabled = False
        self._active: StartPoint | None = None
        self._active_cancel: asyncio.Event | None = None
        self._last_result: PointResult | None = None
        self._last_error: str | None = None
        self._lock = asyncio.Lock()
        self._simulation_profile = "dut"

    async def connect(self) -> DeviceStatus:
        async with self._lock:
            if self._connected:
                raise DeviceError("simulated device is already connected")
            self._connected = True
            self._state = DeviceState.HOLD
            self._rf_enabled = False
            self._last_error = None
            return self._status_unlocked()

    async def disconnect(self) -> DeviceStatus:
        if self._active_cancel:
            self._active_cancel.set()
        async with self._lock:
            self._rf_enabled = False
            self._active = None
            self._active_cancel = None
            self._connected = False
            self._state = DeviceState.DISCONNECTED
            return self._status_unlocked()

    async def get_status(self) -> DeviceStatus:
        async with self._lock:
            return self._status_unlocked()

    async def exit_hold(self) -> DeviceStatus:
        async with self._lock:
            self._require_connected()
            if self._state is not DeviceState.HOLD:
                raise DeviceError(f"cannot exit HOLD from {self._state.value}")
            self._state = DeviceState.IDLE
            return self._status_unlocked()

    async def enter_hold(self) -> DeviceStatus:
        if self._active_cancel:
            self._active_cancel.set()
        async with self._lock:
            self._require_connected()
            self._rf_enabled = False
            self._active = None
            self._active_cancel = None
            self._state = DeviceState.HOLD
            return self._status_unlocked()

    async def prepare_plan(self, plan: SweepPlan) -> None:
        async with self._lock:
            self._require_connected()
            if self._state not in {DeviceState.HOLD, DeviceState.IDLE, DeviceState.RESULT_READY}:
                raise DeviceError(f"cannot prepare a run from {self._state.value}")
            self._simulation_profile = plan.simulation_profile

    async def start_point(self, request: StartPoint) -> AcceptedPoint:
        async with self._lock:
            self._require_connected()
            if self._state not in {DeviceState.IDLE, DeviceState.RESULT_READY}:
                raise DeviceError(f"START_POINT rejected in {self._state.value}")
            cancel_event = asyncio.Event()
            self._active = request
            self._active_cancel = cancel_event
            self._state = DeviceState.BUSY
            self._rf_enabled = True
            task = asyncio.create_task(self._complete_point(request, cancel_event))
            return AcceptedPoint(request=request, result=task)

    async def cancel(self, measurement_id: int, point_index: int) -> DeviceStatus:
        async with self._lock:
            self._require_connected()
            if (
                self._active is None
                or self._active.measurement_id != measurement_id
                or self._active.point_index != point_index
            ):
                raise DeviceError("no matching active measurement")
            assert self._active_cancel is not None
            self._active_cancel.set()
        await asyncio.sleep(0)
        async with self._lock:
            return self._status_unlocked()

    async def _complete_point(
        self, request: StartPoint, cancel_event: asyncio.Event
    ) -> PointResult:
        try:
            try:
                await asyncio.wait_for(cancel_event.wait(), timeout=self._point_latency_s)
                raise MeasurementCancelled("simulated point cancelled")
            except TimeoutError:
                pass

            phase = -2.0 * math.pi * ((request.frequency_hz % 100_000_000) / 100_000_000)
            if self._simulation_profile == "open":
                true_gamma = complex(1.0, 0.0)
            elif self._simulation_profile == "short":
                true_gamma = complex(-1.0, 0.0)
            elif self._simulation_profile == "load":
                true_gamma = complex(0.0, 0.0)
            else:
                magnitude = 0.18 + 0.12 * math.sin(request.frequency_hz / 17_000_000)
                true_gamma = magnitude * complex(math.cos(phase), math.sin(phase))
            directivity = complex(0.02, 0.01)
            reflection_tracking = complex(0.90, -0.04)
            source_match = complex(0.08, 0.03)
            measured_gamma = directivity + (
                reflection_tracking * true_gamma / (1.0 - source_match * true_gamma)
            )
            reference = complex(request.integration_count * 128, request.integration_count * 3)
            reflected = reference * measured_gamma
            result = PointResult(
                measurement_id=request.measurement_id,
                point_index=request.point_index,
                requested_frequency_hz=request.frequency_hz,
                actual_frequency_hz=request.frequency_hz,
                r_i_acc=round(reference.real),
                r_q_acc=round(reference.imag),
                a_i_acc=round(reflected.real),
                a_q_acc=round(reflected.imag),
                integration_count=request.integration_count,
                accumulator_right_shift=0,
                result_flags=0,
                fpga_timestamp_ticks=time.monotonic_ns() // 10,
                duration_us=max(1, round(self._point_latency_s * 1_000_000)),
            )
            async with self._lock:
                if cancel_event.is_set():
                    raise MeasurementCancelled("simulated point cancelled")
                self._last_result = result
                self._active = None
                self._active_cancel = None
                self._rf_enabled = False
                self._state = DeviceState.RESULT_READY
            return result
        except MeasurementCancelled:
            async with self._lock:
                self._active = None
                self._active_cancel = None
                self._rf_enabled = False
                if not self._connected:
                    self._state = DeviceState.DISCONNECTED
                elif self._state is not DeviceState.HOLD:
                    self._state = DeviceState.IDLE
                self._last_error = "CANCELLED"
            raise
        except Exception as exc:
            async with self._lock:
                self._active = None
                self._active_cancel = None
                self._rf_enabled = False
                self._state = DeviceState.FAULT
                self._last_error = str(exc)
            raise

    def _require_connected(self) -> None:
        if not self._connected:
            raise DeviceError("device is disconnected")

    def _status_unlocked(self) -> DeviceStatus:
        active = self._active
        last = self._last_result
        return DeviceStatus(
            connected=self._connected,
            source=self.source if self._connected else None,
            state=self._state,
            rf_output_enabled=self._rf_enabled,
            last_result_valid=last is not None,
            active_measurement_id=active.measurement_id if active else 0,
            active_point_index=active.point_index if active else 0xFFFFFFFF,
            last_measurement_id=last.measurement_id if last else 0,
            last_point_index=last.point_index if last else 0xFFFFFFFF,
            last_error=self._last_error,
            link_flags={
                "clock_locked": self._connected,
                "sysref_seen": self._connected,
                "adc_jesd_ready": self._connected,
                "dac_jesd_ready": self._connected,
                "data_path_ready": self._connected,
            },
        )
