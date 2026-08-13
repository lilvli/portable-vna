from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pvna_host.protocol import PointResult


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class EvidenceSource(StrEnum):
    SIMULATED = "SIMULATED"
    HARDWARE = "HARDWARE"


class DeviceState(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    BOOT = "BOOT"
    HOLD = "HOLD"
    IDLE = "IDLE"
    BUSY = "BUSY"
    RESULT_READY = "RESULT_READY"
    FAULT = "FAULT"
    UNKNOWN = "UNKNOWN"


class RunState(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"

    @property
    def terminal(self) -> bool:
        return self in {self.CANCELLED, self.COMPLETED, self.FAILED, self.UNKNOWN}


@dataclass(frozen=True, slots=True)
class SweepPlan:
    start_hz: int
    stop_hz: int
    points: int
    spacing: str = "linear"
    stimulus_amplitude_q15: int = 8192
    settle_us: int = 1000
    integration_count: int = 65536
    point_timeout_ms: int = 2000
    measurement_role: str = "dut"
    simulation_profile: str = "dut"
    port_path: str = "PORT1_REFLECTION"
    reference_impedance_ohm: float = 50.0

    def __post_init__(self) -> None:
        if self.start_hz <= 0 or self.stop_hz <= 0 or self.stop_hz < self.start_hz:
            raise ValueError("frequency range must be positive and ordered")
        if not 1 <= self.points <= 100_001:
            raise ValueError("points must be in 1..100001")
        if self.spacing not in {"linear", "log"}:
            raise ValueError("spacing must be linear or log")
        if not 0 <= self.stimulus_amplitude_q15 <= 32767:
            raise ValueError("stimulus_amplitude_q15 must be in 0..32767")
        if self.integration_count <= 0 or self.point_timeout_ms <= 0:
            raise ValueError("integration_count and point_timeout_ms must be positive")
        if self.settle_us < 0:
            raise ValueError("settle_us cannot be negative")
        if self.measurement_role not in {"dut", "open", "short", "load"}:
            raise ValueError("measurement_role must be dut, open, short, or load")
        if self.simulation_profile not in {"dut", "open", "short", "load"}:
            raise ValueError("simulation_profile must be dut, open, short, or load")
        if self.port_path != "PORT1_REFLECTION":
            raise ValueError("phase one supports only PORT1_REFLECTION")
        if not math.isfinite(self.reference_impedance_ohm) or self.reference_impedance_ohm <= 0:
            raise ValueError("reference_impedance_ohm must be positive and finite")

    def frequency_axis(self) -> tuple[int, ...]:
        if self.points == 1:
            return (self.start_hz,)
        if self.spacing == "linear":
            step = (self.stop_hz - self.start_hz) / (self.points - 1)
            return tuple(round(self.start_hz + index * step) for index in range(self.points))
        start_log = math.log(self.start_hz)
        step_log = (math.log(self.stop_hz) - start_log) / (self.points - 1)
        return tuple(round(math.exp(start_log + index * step_log)) for index in range(self.points))

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["start_hz"] = str(self.start_hz)
        value["stop_hz"] = str(self.stop_hz)
        value["frequency_axis_hz"] = [str(item) for item in self.frequency_axis()]
        return value


@dataclass(frozen=True, slots=True)
class DeviceStatus:
    connected: bool
    source: EvidenceSource | None
    state: DeviceState
    rf_output_enabled: bool
    last_result_valid: bool
    active_measurement_id: int = 0
    active_point_index: int = 0xFFFFFFFF
    last_measurement_id: int = 0
    last_point_index: int = 0xFFFFFFFF
    last_error: str | None = None
    link_flags: dict[str, bool] = field(default_factory=dict)

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "source": self.source.value if self.source else None,
            "state": self.state.value,
            "rf_output_enabled": self.rf_output_enabled,
            "last_result_valid": self.last_result_valid,
            "active_measurement_id": self.active_measurement_id,
            "active_point_index": self.active_point_index,
            "last_measurement_id": self.last_measurement_id,
            "last_point_index": self.last_point_index,
            "last_error": self.last_error,
            "link_flags": self.link_flags,
        }


@dataclass(slots=True)
class RunRecord:
    run_id: str
    measurement_id: int
    source: EvidenceSource
    plan: SweepPlan
    device_id: str = "UNKNOWN"
    fpga_build_id: str = "UNKNOWN"
    state: RunState = RunState.CREATED
    confirmed_points: int = 0
    error: str | None = None
    created_at_utc: str = field(default_factory=utc_now)
    started_at_utc: str | None = None
    finished_at_utc: str | None = None
    data_validation: str = "PENDING"
    points_sha256: str | None = None
    safe_hold_confirmed: bool = False
    recovered_after_interruption: bool = False
    calibration_id: str | None = None
    cancel_requested: bool = False
    points_data: list[PointResult] = field(default_factory=list, repr=False)

    @property
    def expected_points(self) -> int:
        return self.plan.points

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "measurement_id": self.measurement_id,
            "source": self.source.value,
            "device_id": self.device_id,
            "fpga_build_id": self.fpga_build_id,
            "state": self.state.value,
            "confirmed_points": self.confirmed_points,
            "expected_points": self.expected_points,
            "progress": self.confirmed_points / self.expected_points,
            "error": self.error,
            "created_at_utc": self.created_at_utc,
            "started_at_utc": self.started_at_utc,
            "finished_at_utc": self.finished_at_utc,
            "data_validation": self.data_validation,
            "points_sha256": self.points_sha256,
            "safe_hold_confirmed": self.safe_hold_confirmed,
            "recovered_after_interruption": self.recovered_after_interruption,
            "calibration_id": self.calibration_id,
            "plan": self.plan.to_dict(),
        }

    @classmethod
    def from_manifest(cls, manifest: dict[str, Any]) -> RunRecord:
        plan_data = manifest["plan"]
        plan = SweepPlan(
            start_hz=int(plan_data["start_hz"]),
            stop_hz=int(plan_data["stop_hz"]),
            points=int(plan_data["points"]),
            spacing=str(plan_data["spacing"]),
            stimulus_amplitude_q15=int(plan_data["stimulus_amplitude_q15"]),
            settle_us=int(plan_data["settle_us"]),
            integration_count=int(plan_data["integration_count"]),
            point_timeout_ms=int(plan_data["point_timeout_ms"]),
            measurement_role=str(
                plan_data.get("measurement_role", plan_data.get("simulation_profile", "dut"))
            ),
            simulation_profile=str(plan_data.get("simulation_profile", "dut")),
            port_path=str(plan_data.get("port_path", "PORT1_REFLECTION")),
            reference_impedance_ohm=float(plan_data.get("reference_impedance_ohm", 50.0)),
        )
        return cls(
            run_id=str(manifest["run_id"]),
            measurement_id=int(manifest["measurement_id"]),
            source=EvidenceSource(str(manifest["source"])),
            plan=plan,
            device_id=str(manifest.get("device_id", "UNKNOWN")),
            fpga_build_id=str(manifest.get("fpga_build_id", "UNKNOWN")),
            state=RunState(str(manifest["state"])),
            confirmed_points=int(manifest.get("confirmed_points", 0)),
            error=manifest.get("error"),
            created_at_utc=str(manifest["created_at_utc"]),
            started_at_utc=manifest.get("started_at_utc"),
            finished_at_utc=manifest.get("finished_at_utc"),
            data_validation=str(manifest.get("data_validation", "PENDING")),
            points_sha256=manifest.get("points_sha256"),
            safe_hold_confirmed=bool(manifest.get("safe_hold_confirmed", False)),
            recovered_after_interruption=bool(manifest.get("recovered_after_interruption", False)),
            calibration_id=manifest.get("calibration_id"),
        )
