"""One-port SOL calibration using the three-term error model.

The measured complex ratio is ``m = A / R`` and the model is::

    m = E_d + (E_r * gamma) / (1 - E_s * gamma)

``E_d`` is directivity, ``E_r`` is reflection tracking, and ``E_s`` is
source match.  For each frequency, three standards provide the linear system::

    [1, gamma_i, m_i * gamma_i] [E_d, B, E_s]^T = m_i

where ``B = E_r - E_d * E_s``.  After solving the linear system,
``E_r = B + E_d * E_s``.

The inverse applied to a DUT measurement is::

    gamma = (m - E_d) / (E_r + E_s * (m - E_d))

Raw R/A samples and calibrated S11 are represented by separate immutable
objects, so changing a calibration never overwrites acquisition evidence.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pvna_host.protocol import PointResult


class CalibrationError(ValueError):
    """Base class for deterministic calibration failures."""


class CalibrationValidationError(CalibrationError):
    """Input evidence is invalid or does not match the calibration binding."""


class CalibrationSingularityError(CalibrationError):
    """The SOL equations or their inverse are singular or near-singular."""


class Standard(StrEnum):
    OPEN = "OPEN"
    SHORT = "SHORT"
    LOAD = "LOAD"

    @property
    def ideal_reflection(self) -> complex:
        return {
            Standard.OPEN: 1.0 + 0.0j,
            Standard.SHORT: -1.0 + 0.0j,
            Standard.LOAD: 0.0 + 0.0j,
        }[self]


@dataclass(frozen=True, slots=True)
class AcquisitionBinding:
    """Configuration that must remain fixed for calibration and DUT sweeps."""

    source: str
    port: int
    path: str
    device_id: str
    fpga_build_id: str
    stimulus_amplitude_q15: int
    settle_us: int
    integration_count: int

    def __post_init__(self) -> None:
        if self.source not in {"SIMULATED", "HARDWARE"}:
            raise CalibrationValidationError("source must be SIMULATED or HARDWARE")
        if self.port <= 0:
            raise CalibrationValidationError("port must be positive")
        for name, value in (
            ("path", self.path),
            ("device_id", self.device_id),
            ("fpga_build_id", self.fpga_build_id),
        ):
            if not value or any(character in value for character in "\r\n"):
                raise CalibrationValidationError(f"{name} must be a non-empty single line")
        if not 0 <= self.stimulus_amplitude_q15 <= 32767:
            raise CalibrationValidationError("stimulus_amplitude_q15 must be in 0..32767")
        if self.settle_us < 0:
            raise CalibrationValidationError("settle_us cannot be negative")
        if self.integration_count <= 0:
            raise CalibrationValidationError("integration_count must be positive")


@dataclass(frozen=True, slots=True)
class RawComplexSample:
    """One raw frequency point, retaining R and A separately."""

    frequency_hz: int
    reference: complex
    reflection: complex

    @classmethod
    def from_point_result(
        cls, result: PointResult, *, use_actual_frequency: bool = True
    ) -> RawComplexSample:
        """Adapt a protocol point without changing the authoritative PointResult."""
        frequency = (
            result.actual_frequency_hz if use_actual_frequency else result.requested_frequency_hz
        )
        return cls(
            frequency_hz=frequency,
            reference=complex(result.r_i_acc, result.r_q_acc),
            reflection=complex(result.a_i_acc, result.a_q_acc),
        )

    def ratio(self, *, reference_tolerance: float) -> complex:
        """Return ``m=A/R``, rejecting non-finite or near-zero reference data.

        Near zero is relative to ``max(1, abs(A))``.  This handles normalized
        floating point samples while still accepting ordinary integer
        accumulators with magnitude one or greater.
        """
        _require_finite_complex(self.reference, "reference R")
        _require_finite_complex(self.reflection, "reflection A")
        scale = max(1.0, abs(self.reflection))
        if abs(self.reference) <= reference_tolerance * scale:
            raise CalibrationValidationError(
                f"reference R is zero or near zero at {self.frequency_hz} Hz"
            )
        value = self.reflection / self.reference
        _require_finite_complex(value, "measured ratio m")
        return value


@dataclass(frozen=True, slots=True)
class RawSweep:
    binding: AcquisitionBinding
    captured_at_utc: datetime
    samples: tuple[RawComplexSample, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "samples", tuple(self.samples))
        _validate_timestamp(self.captured_at_utc, "captured_at_utc")
        _validate_samples(self.samples)

    @property
    def frequency_axis_hz(self) -> tuple[int, ...]:
        return tuple(sample.frequency_hz for sample in self.samples)


@dataclass(frozen=True, slots=True)
class StandardSweep:
    standard: Standard
    binding: AcquisitionBinding
    captured_at_utc: datetime
    samples: tuple[RawComplexSample, ...]
    expected_reflections: tuple[complex, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "samples", tuple(self.samples))
        object.__setattr__(self, "expected_reflections", tuple(self.expected_reflections))
        _validate_timestamp(self.captured_at_utc, "captured_at_utc")
        _validate_samples(self.samples)
        if len(self.expected_reflections) != len(self.samples):
            raise CalibrationValidationError(
                "expected_reflections must contain one value per raw sample"
            )
        for value in self.expected_reflections:
            _require_finite_complex(value, "expected standard reflection")

    @classmethod
    def ideal(
        cls,
        standard: Standard,
        binding: AcquisitionBinding,
        captured_at_utc: datetime,
        samples: Iterable[RawComplexSample],
    ) -> StandardSweep:
        raw_samples = tuple(samples)
        return cls(
            standard=standard,
            binding=binding,
            captured_at_utc=captured_at_utc,
            samples=raw_samples,
            expected_reflections=(standard.ideal_reflection,) * len(raw_samples),
        )

    @property
    def frequency_axis_hz(self) -> tuple[int, ...]:
        return tuple(sample.frequency_hz for sample in self.samples)


@dataclass(frozen=True, slots=True)
class SolErrorTerms:
    directivity: complex
    reflection_tracking: complex
    source_match: complex


@dataclass(frozen=True, slots=True)
class CalibratedPoint:
    frequency_hz: int
    raw_ratio: complex
    s11: complex


@dataclass(frozen=True, slots=True)
class CalibratedSweep:
    calibration_id: str
    binding: AcquisitionBinding
    calibration_valid_from_utc: datetime
    calibration_valid_until_utc: datetime | None
    captured_at_utc: datetime
    points: tuple[CalibratedPoint, ...]


@dataclass(frozen=True, slots=True)
class OnePortCalibration:
    calibration_id: str
    binding: AcquisitionBinding
    frequency_axis_hz: tuple[int, ...]
    terms: tuple[SolErrorTerms, ...]
    standard_capture_times_utc: tuple[tuple[Standard, datetime], ...]
    valid_from_utc: datetime
    valid_until_utc: datetime | None
    reference_tolerance: float
    singularity_tolerance: float

    def apply(self, sweep: RawSweep) -> CalibratedSweep:
        """Apply this immutable solution without modifying the raw sweep."""
        if sweep.binding != self.binding:
            raise CalibrationValidationError(_binding_mismatch_message(self.binding, sweep.binding))
        if sweep.frequency_axis_hz != self.frequency_axis_hz:
            raise CalibrationValidationError("DUT frequency axis does not match calibration")
        if sweep.captured_at_utc < self.valid_from_utc:
            raise CalibrationValidationError("DUT capture predates completed SOL acquisition")
        if self.valid_until_utc is not None and sweep.captured_at_utc > self.valid_until_utc:
            raise CalibrationValidationError("calibration is expired for this DUT capture")

        calibrated: list[CalibratedPoint] = []
        for sample, terms in zip(sweep.samples, self.terms, strict=True):
            measured = sample.ratio(reference_tolerance=self.reference_tolerance)
            delta = measured - terms.directivity
            denominator = terms.reflection_tracking + terms.source_match * delta
            denominator_scale = max(
                1.0,
                abs(terms.reflection_tracking),
                abs(terms.source_match * delta),
            )
            if abs(denominator) <= self.singularity_tolerance * denominator_scale:
                raise CalibrationSingularityError(
                    f"calibration inverse is singular or near-singular at {sample.frequency_hz} Hz"
                )
            s11 = delta / denominator
            _require_finite_complex(s11, "calibrated S11")
            calibrated.append(CalibratedPoint(sample.frequency_hz, measured, s11))

        return CalibratedSweep(
            calibration_id=self.calibration_id,
            binding=self.binding,
            calibration_valid_from_utc=self.valid_from_utc,
            calibration_valid_until_utc=self.valid_until_utc,
            captured_at_utc=sweep.captured_at_utc,
            points=tuple(calibrated),
        )


def solve_one_port_sol(
    calibration_id: str,
    standards: Iterable[StandardSweep],
    *,
    valid_until_utc: datetime | None = None,
    reference_tolerance: float = 1e-12,
    singularity_tolerance: float = 1e-12,
) -> OnePortCalibration:
    """Solve frequency-by-frequency SOL terms from exactly one O/S/L sweep."""
    if not calibration_id or any(character in calibration_id for character in "\r\n"):
        raise CalibrationValidationError("calibration_id must be a non-empty single line")
    _validate_tolerance(reference_tolerance, "reference_tolerance")
    _validate_tolerance(singularity_tolerance, "singularity_tolerance")

    by_standard: dict[Standard, StandardSweep] = {}
    for sweep in standards:
        if sweep.standard in by_standard:
            raise CalibrationValidationError(f"duplicate {sweep.standard.value} standard")
        by_standard[sweep.standard] = sweep
    missing = set(Standard) - by_standard.keys()
    if missing or len(by_standard) != len(Standard):
        names = ", ".join(sorted(standard.value for standard in missing))
        raise CalibrationValidationError(
            f"exactly OPEN, SHORT, and LOAD are required; missing {names}"
        )

    ordered = tuple(by_standard[standard] for standard in Standard)
    baseline = ordered[0]
    for sweep in ordered[1:]:
        if sweep.binding != baseline.binding:
            raise CalibrationValidationError(
                _binding_mismatch_message(baseline.binding, sweep.binding)
            )
        if sweep.frequency_axis_hz != baseline.frequency_axis_hz:
            raise CalibrationValidationError(
                "standard frequency axes must match exactly and in order"
            )

    valid_from = max(sweep.captured_at_utc for sweep in ordered)
    if valid_until_utc is not None:
        _validate_timestamp(valid_until_utc, "valid_until_utc")
        if valid_until_utc <= valid_from:
            raise CalibrationValidationError("valid_until_utc must be later than all standards")

    ratios = {
        sweep.standard: tuple(
            sample.ratio(reference_tolerance=reference_tolerance) for sample in sweep.samples
        )
        for sweep in ordered
    }
    terms: list[SolErrorTerms] = []
    for index, frequency_hz in enumerate(baseline.frequency_axis_hz):
        matrix: list[list[complex]] = []
        measured_values: list[complex] = []
        for sweep in ordered:
            measured = ratios[sweep.standard][index]
            expected = sweep.expected_reflections[index]
            matrix.append([1.0 + 0.0j, expected, measured * expected])
            measured_values.append(measured)
        directivity, numerator_tracking, source_match = _solve_three_complex(
            matrix,
            measured_values,
            tolerance=singularity_tolerance,
            frequency_hz=frequency_hz,
        )
        tracking = numerator_tracking + directivity * source_match
        for name, value in (
            ("directivity", directivity),
            ("reflection tracking", tracking),
            ("source match", source_match),
        ):
            _require_finite_complex(value, name)
        terms.append(SolErrorTerms(directivity, tracking, source_match))

    return OnePortCalibration(
        calibration_id=calibration_id,
        binding=baseline.binding,
        frequency_axis_hz=baseline.frequency_axis_hz,
        terms=tuple(terms),
        standard_capture_times_utc=tuple(
            (sweep.standard, sweep.captured_at_utc) for sweep in ordered
        ),
        valid_from_utc=valid_from,
        valid_until_utc=valid_until_utc,
        reference_tolerance=reference_tolerance,
        singularity_tolerance=singularity_tolerance,
    )


def _solve_three_complex(
    matrix: list[list[complex]],
    right_hand_side: list[complex],
    *,
    tolerance: float,
    frequency_hz: int,
) -> tuple[complex, complex, complex]:
    """Scaled partial-pivot Gaussian elimination for a complex 3x3 system."""
    column_scales = [max(abs(matrix[row][column]) for row in range(3)) for column in range(3)]
    if any(scale == 0.0 for scale in column_scales):
        raise CalibrationSingularityError(
            f"SOL system is singular or near-singular at {frequency_hz} Hz"
        )
    work = [
        [matrix[row][column] / column_scales[column] for column in range(3)]
        + [right_hand_side[row]]
        for row in range(3)
    ]
    matrix_scale = max(sum(abs(value) for value in row[:3]) for row in work)

    for column in range(3):
        pivot_row = max(range(column, 3), key=lambda row: abs(work[row][column]))
        pivot_size = abs(work[pivot_row][column])
        if pivot_size <= tolerance * matrix_scale:
            raise CalibrationSingularityError(
                f"SOL system is singular or near-singular at {frequency_hz} Hz"
            )
        if pivot_row != column:
            work[column], work[pivot_row] = work[pivot_row], work[column]
        pivot = work[column][column]
        for row in range(column + 1, 3):
            factor = work[row][column] / pivot
            work[row][column] = 0.0 + 0.0j
            for item in range(column + 1, 4):
                work[row][item] -= factor * work[column][item]

    scaled_solution = [0.0 + 0.0j] * 3
    for row in range(2, -1, -1):
        pivot = work[row][row]
        if abs(pivot) <= tolerance * matrix_scale:
            raise CalibrationSingularityError(
                f"SOL system is singular or near-singular at {frequency_hz} Hz"
            )
        remainder = sum(work[row][column] * scaled_solution[column] for column in range(row + 1, 3))
        scaled_solution[row] = (work[row][3] - remainder) / pivot
    solution = tuple(scaled_solution[column] / column_scales[column] for column in range(3))
    return solution  # type: ignore[return-value]


def _validate_samples(samples: tuple[RawComplexSample, ...]) -> None:
    if not samples:
        raise CalibrationValidationError("a sweep must contain at least one sample")
    previous = 0
    for sample in samples:
        if sample.frequency_hz <= previous:
            raise CalibrationValidationError(
                "frequency axis must be positive, unique, and strictly increasing"
            )
        previous = sample.frequency_hz


def _validate_timestamp(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CalibrationValidationError(f"{name} must be timezone-aware")


def _validate_tolerance(value: float, name: str) -> None:
    if not math.isfinite(value) or not 0.0 < value < 1.0:
        raise CalibrationValidationError(f"{name} must be finite and in (0, 1)")


def _require_finite_complex(value: complex, name: str) -> None:
    if not math.isfinite(value.real) or not math.isfinite(value.imag):
        raise CalibrationValidationError(f"{name} must contain only finite values")


def _binding_mismatch_message(expected: AcquisitionBinding, actual: AcquisitionBinding) -> str:
    for field_name in (
        "source",
        "port",
        "path",
        "device_id",
        "fpga_build_id",
        "stimulus_amplitude_q15",
        "settle_us",
        "integration_count",
    ):
        if getattr(expected, field_name) != getattr(actual, field_name):
            return f"acquisition binding mismatch: {field_name}"
    return "acquisition binding mismatch"


def utc_text(value: datetime) -> str:
    """Stable UTC text shared with the Touchstone trace layer."""
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
