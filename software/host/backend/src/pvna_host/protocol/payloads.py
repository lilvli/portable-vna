from __future__ import annotations

import struct
from dataclasses import dataclass

from .frame import ProtocolError

_START_POINT = struct.Struct("<IIQHHIII")
_POINT_RESULT = struct.Struct("<IIQQqqqqIHHQII")


@dataclass(frozen=True, slots=True)
class StartPoint:
    measurement_id: int
    point_index: int
    frequency_hz: int
    stimulus_amplitude_q15: int
    measure_flags: int
    settle_us: int
    integration_count: int
    max_duration_ms: int

    def encode(self) -> bytes:
        if self.measurement_id == 0:
            raise ProtocolError("measurement_id must be non-zero")
        if not 0 <= self.stimulus_amplitude_q15 <= 32767:
            raise ProtocolError("stimulus amplitude is outside Q15 range")
        if self.measure_flags & ~0x0001:
            raise ProtocolError("reserved measure flag is set")
        if self.integration_count == 0 or self.max_duration_ms == 0:
            raise ProtocolError("integration_count and max_duration_ms must be non-zero")
        try:
            return _START_POINT.pack(
                self.measurement_id,
                self.point_index,
                self.frequency_hz,
                self.stimulus_amplitude_q15,
                self.measure_flags,
                self.settle_us,
                self.integration_count,
                self.max_duration_ms,
            )
        except struct.error as exc:
            raise ProtocolError(str(exc)) from exc

    @classmethod
    def decode(cls, payload: bytes) -> StartPoint:
        if len(payload) != _START_POINT.size:
            raise ProtocolError("START_POINT payload must be 32 bytes")
        return cls(*_START_POINT.unpack(payload))


@dataclass(frozen=True, slots=True)
class PointResult:
    measurement_id: int
    point_index: int
    requested_frequency_hz: int
    actual_frequency_hz: int
    r_i_acc: int
    r_q_acc: int
    a_i_acc: int
    a_q_acc: int
    integration_count: int
    accumulator_right_shift: int
    result_flags: int
    fpga_timestamp_ticks: int
    duration_us: int
    reserved: int = 0

    def encode(self) -> bytes:
        if self.reserved != 0:
            raise ProtocolError("POINT_RESULT reserved field must be zero")
        try:
            return _POINT_RESULT.pack(
                self.measurement_id,
                self.point_index,
                self.requested_frequency_hz,
                self.actual_frequency_hz,
                self.r_i_acc,
                self.r_q_acc,
                self.a_i_acc,
                self.a_q_acc,
                self.integration_count,
                self.accumulator_right_shift,
                self.result_flags,
                self.fpga_timestamp_ticks,
                self.duration_us,
                self.reserved,
            )
        except struct.error as exc:
            raise ProtocolError(str(exc)) from exc

    @classmethod
    def decode(cls, payload: bytes) -> PointResult:
        if len(payload) != _POINT_RESULT.size:
            raise ProtocolError("POINT_RESULT payload must be 80 bytes")
        result = cls(*_POINT_RESULT.unpack(payload))
        if result.reserved:
            raise ProtocolError("POINT_RESULT reserved field must be zero")
        return result

    def ratio(self) -> complex | None:
        reference = complex(self.r_i_acc, self.r_q_acc)
        if reference == 0:
            return None
        return complex(self.a_i_acc, self.a_q_acc) / reference

    def to_api_dict(self) -> dict[str, object]:
        ratio = self.ratio()
        return {
            "measurement_id": self.measurement_id,
            "point_index": self.point_index,
            "requested_frequency_hz": str(self.requested_frequency_hz),
            "actual_frequency_hz": str(self.actual_frequency_hz),
            "r_i_acc": str(self.r_i_acc),
            "r_q_acc": str(self.r_q_acc),
            "a_i_acc": str(self.a_i_acc),
            "a_q_acc": str(self.a_q_acc),
            "integration_count": self.integration_count,
            "accumulator_right_shift": self.accumulator_right_shift,
            "result_flags": self.result_flags,
            "fpga_timestamp_ticks": str(self.fpga_timestamp_ticks),
            "duration_us": self.duration_us,
            "ratio_real": ratio.real if ratio is not None else None,
            "ratio_imag": ratio.imag if ratio is not None else None,
            "a_over_r_real": ratio.real if ratio is not None else None,
            "a_over_r_imag": ratio.imag if ratio is not None else None,
        }
