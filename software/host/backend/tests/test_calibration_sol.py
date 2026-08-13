from __future__ import annotations

import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pvna_host.calibration import (  # noqa: E402
    AcquisitionBinding,
    CalibrationSingularityError,
    CalibrationValidationError,
    OnePortCalibration,
    RawComplexSample,
    RawSweep,
    SolErrorTerms,
    Standard,
    StandardSweep,
    solve_one_port_sol,
)
from pvna_host.protocol import PointResult  # noqa: E402

FREQUENCIES = (5_000_000, 50_000_000, 100_000_000)
CAPTURE_START = datetime(2026, 8, 12, 1, 0, tzinfo=UTC)
KNOWN_TERMS = (
    SolErrorTerms(0.02 + 0.01j, 0.91 - 0.03j, 0.08 + 0.02j),
    SolErrorTerms(-0.01 + 0.02j, 0.88 + 0.04j, 0.05 - 0.03j),
    SolErrorTerms(0.03 - 0.01j, 0.94 + 0.01j, -0.04 + 0.02j),
)


def binding(**changes: object) -> AcquisitionBinding:
    baseline = AcquisitionBinding(
        source="SIMULATED",
        port=1,
        path="PORT1_DIRECT",
        device_id="sim-vna-001",
        fpga_build_id="sim-build-20260812",
        stimulus_amplitude_q15=8192,
        settle_us=1000,
        integration_count=65536,
    )
    return replace(baseline, **changes)


def forward(gamma: complex, terms: SolErrorTerms) -> complex:
    return terms.directivity + terms.reflection_tracking * gamma / (
        1.0 - terms.source_match * gamma
    )


def standard_sweep(
    standard: Standard,
    *,
    acquisition_binding: AcquisitionBinding | None = None,
    frequencies: tuple[int, ...] = FREQUENCIES,
    capture_offset_minutes: int = 0,
    measured_override: tuple[complex, ...] | None = None,
) -> StandardSweep:
    expected = standard.ideal_reflection
    measured = measured_override or tuple(
        forward(expected, KNOWN_TERMS[index]) for index in range(len(frequencies))
    )
    samples = tuple(
        RawComplexSample(frequency, 1.0 + 0.0j, value)
        for frequency, value in zip(frequencies, measured, strict=True)
    )
    return StandardSweep.ideal(
        standard,
        acquisition_binding or binding(),
        CAPTURE_START + timedelta(minutes=capture_offset_minutes),
        samples,
    )


def solved_calibration() -> OnePortCalibration:
    standards = tuple(
        standard_sweep(standard, capture_offset_minutes=index)
        for index, standard in enumerate(Standard)
    )
    return solve_one_port_sol(
        "cal-sol-001",
        standards,
        valid_until_utc=CAPTURE_START + timedelta(days=1),
    )


def test_sol_recovers_terms_and_recomputes_without_mutating_raw_data() -> None:
    calibration = solved_calibration()
    for actual, expected in zip(calibration.terms, KNOWN_TERMS, strict=True):
        assert actual.directivity == pytest.approx(expected.directivity, abs=1e-12)
        assert actual.reflection_tracking == pytest.approx(expected.reflection_tracking, abs=1e-12)
        assert actual.source_match == pytest.approx(expected.source_match, abs=1e-12)

    expected_s11 = (0.2 - 0.1j, -0.35 + 0.25j, 0.05 + 0.4j)
    raw_samples = tuple(
        RawComplexSample(frequency, 2.0 + 0.0j, 2.0 * forward(gamma, terms))
        for frequency, gamma, terms in zip(FREQUENCIES, expected_s11, KNOWN_TERMS, strict=True)
    )
    raw = RawSweep(binding(), CAPTURE_START + timedelta(hours=1), raw_samples)

    first = calibration.apply(raw)
    second = calibration.apply(raw)

    assert first == second
    assert raw.samples == raw_samples
    assert tuple(point.raw_ratio for point in first.points) == pytest.approx(
        tuple(forward(gamma, terms) for gamma, terms in zip(expected_s11, KNOWN_TERMS, strict=True))
    )
    assert tuple(point.s11 for point in first.points) == pytest.approx(expected_s11, abs=1e-12)


@pytest.mark.parametrize(
    ("changed_binding", "field_name"),
    [
        (binding(source="HARDWARE"), "source"),
        (binding(port=2), "port"),
        (binding(path="PORT1_COUPLED"), "path"),
    ],
)
def test_solver_rejects_source_port_and_path_mismatch(
    changed_binding: AcquisitionBinding, field_name: str
) -> None:
    standards = [standard_sweep(standard) for standard in Standard]
    standards[1] = standard_sweep(Standard.SHORT, acquisition_binding=changed_binding)
    with pytest.raises(CalibrationValidationError, match=field_name):
        solve_one_port_sol("cal-mismatch", standards)


def test_solver_rejects_frequency_axis_mismatch() -> None:
    standards = [standard_sweep(standard) for standard in Standard]
    standards[2] = standard_sweep(
        Standard.LOAD,
        frequencies=(5_000_000, 50_000_001, 100_000_000),
    )
    with pytest.raises(CalibrationValidationError, match="frequency axes"):
        solve_one_port_sol("cal-axis-mismatch", standards)


def test_apply_rejects_binding_axis_and_time_mismatch() -> None:
    calibration = solved_calibration()
    valid_axis = tuple(
        RawComplexSample(frequency, 1.0 + 0.0j, 0.1 + 0.0j) for frequency in FREQUENCIES
    )

    with pytest.raises(CalibrationValidationError, match="source"):
        calibration.apply(
            RawSweep(
                binding(source="HARDWARE"),
                CAPTURE_START + timedelta(hours=1),
                valid_axis,
            )
        )

    shifted_axis = tuple(
        RawComplexSample(frequency + 1, 1.0 + 0.0j, 0.1 + 0.0j) for frequency in FREQUENCIES
    )
    with pytest.raises(CalibrationValidationError, match="frequency axis"):
        calibration.apply(RawSweep(binding(), CAPTURE_START + timedelta(hours=1), shifted_axis))

    with pytest.raises(CalibrationValidationError, match="predates"):
        calibration.apply(RawSweep(binding(), CAPTURE_START, valid_axis))
    with pytest.raises(CalibrationValidationError, match="expired"):
        calibration.apply(RawSweep(binding(), CAPTURE_START + timedelta(days=2), valid_axis))


@pytest.mark.parametrize(
    "invalid_sample",
    [
        RawComplexSample(FREQUENCIES[0], 1e-15 + 0.0j, 1.0 + 0.0j),
        RawComplexSample(FREQUENCIES[0], 1.0 + 0.0j, complex(float("nan"), 0.0)),
        RawComplexSample(FREQUENCIES[0], complex(float("inf"), 0.0), 1.0 + 0.0j),
    ],
)
def test_solver_rejects_near_zero_and_non_finite_raw_values(
    invalid_sample: RawComplexSample,
) -> None:
    standards = [standard_sweep(standard) for standard in Standard]
    open_sweep = standards[0]
    standards[0] = StandardSweep.ideal(
        Standard.OPEN,
        open_sweep.binding,
        open_sweep.captured_at_utc,
        (invalid_sample, *open_sweep.samples[1:]),
    )
    with pytest.raises(CalibrationValidationError):
        solve_one_port_sol("cal-invalid-raw", standards)


def test_solver_rejects_singular_and_near_singular_standards() -> None:
    identical = (0.1 + 0.2j,) * len(FREQUENCIES)
    standards = [standard_sweep(standard, measured_override=identical) for standard in Standard]
    with pytest.raises(CalibrationSingularityError, match="singular or near-singular"):
        solve_one_port_sol("cal-singular", standards)

    almost_identical = {
        Standard.OPEN: (0.1 + 0.2j,) * len(FREQUENCIES),
        Standard.SHORT: (0.1 + 0.2j + 1e-15,) * len(FREQUENCIES),
        Standard.LOAD: (0.1 + 0.2j,) * len(FREQUENCIES),
    }
    standards = [
        standard_sweep(standard, measured_override=almost_identical[standard])
        for standard in Standard
    ]
    with pytest.raises(CalibrationSingularityError, match="singular or near-singular"):
        solve_one_port_sol("cal-near-singular", standards)


def test_point_result_adapter_uses_actual_frequency_and_preserves_raw_channels() -> None:
    result = PointResult(
        measurement_id=7,
        point_index=0,
        requested_frequency_hz=50_000_000,
        actual_frequency_hz=49_999_999,
        r_i_acc=4,
        r_q_acc=-2,
        a_i_acc=1,
        a_q_acc=3,
        integration_count=65536,
        accumulator_right_shift=0,
        result_flags=0,
        fpga_timestamp_ticks=123,
        duration_us=1000,
    )
    sample = RawComplexSample.from_point_result(result)
    assert sample.frequency_hz == 49_999_999
    assert sample.reference == 4 - 2j
    assert sample.reflection == 1 + 3j
