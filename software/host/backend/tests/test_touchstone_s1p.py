from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pvna_host.calibration import (  # noqa: E402
    AcquisitionBinding,
    CalibratedPoint,
    CalibratedSweep,
)
from pvna_host.export import (  # noqa: E402
    TouchstoneError,
    parse_touchstone_s1p,
    render_touchstone_s1p,
    write_touchstone_s1p,
)

CAPTURED = datetime(2026, 8, 12, 3, 4, 5, tzinfo=UTC)


def calibrated_sweep() -> CalibratedSweep:
    binding = AcquisitionBinding(
        source="SIMULATED",
        port=1,
        path="PORT1_DIRECT",
        device_id="sim-vna-001",
        fpga_build_id="sim-build-20260812",
        stimulus_amplitude_q15=8192,
        settle_us=1000,
        integration_count=65536,
    )
    return CalibratedSweep(
        calibration_id="cal-sol-001",
        binding=binding,
        calibration_valid_from_utc=CAPTURED - timedelta(hours=1),
        calibration_valid_until_utc=CAPTURED + timedelta(days=1),
        captured_at_utc=CAPTURED,
        points=(
            CalibratedPoint(5_000_000, 0.2 - 0.1j, 0.1 - 0.05j),
            CalibratedPoint(50_000_000, -0.3 + 0.2j, -0.25 + 0.125j),
            CalibratedPoint(100_000_000, 0.02 + 0.4j, 0.01 + 0.3333333333333333j),
        ),
    )


def test_ri_export_defaults_to_50_ohms_and_round_trips_independently() -> None:
    sweep = calibrated_sweep()
    text = render_touchstone_s1p(sweep, comments=("operator_note: offline fixture",))

    assert "# Hz S RI R 50\n" in text
    assert "! calibration_id: cal-sol-001\n" in text
    assert "! source: SIMULATED\n" in text
    assert "! path: PORT1_DIRECT\n" in text
    assert "! dut_captured_at_utc: 2026-08-12T03:04:05.000Z\n" in text

    parsed = parse_touchstone_s1p(text)
    assert parsed.reference_ohms == 50.0
    assert tuple(frequency for frequency, _ in parsed.points) == tuple(
        point.frequency_hz for point in sweep.points
    )
    assert tuple(value for _, value in parsed.points) == tuple(point.s11 for point in sweep.points)
    assert "calibration_id: cal-sol-001" in parsed.comments
    assert "operator_note: offline fixture" in parsed.comments


def test_parser_accepts_an_independent_mhz_ri_fixture() -> None:
    fixture = """! independent fixture
# MHz S RI R 75
5 0.25 -0.5
10 -1.0 0.125 ! inline trace
"""
    parsed = parse_touchstone_s1p(fixture)
    assert parsed.reference_ohms == 75.0
    assert parsed.points == (
        (5_000_000.0, 0.25 - 0.5j),
        (10_000_000.0, -1.0 + 0.125j),
    )
    assert parsed.comments == ("independent fixture", "inline trace")


def test_write_publishes_parseable_utf8_lf_s1p(tmp_path: Path) -> None:
    target = tmp_path / "result.s1p"
    write_touchstone_s1p(target, calibrated_sweep())
    payload = target.read_bytes()
    assert not payload.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in payload
    assert parse_touchstone_s1p(payload.decode("utf-8")).reference_ohms == 50.0
    assert not (tmp_path / "result.s1p.tmp").exists()


@pytest.mark.parametrize(
    "fixture",
    [
        "# Hz S MA R 50\n1000000 1 0\n",
        "# Hz S RI R 0\n1000000 1 0\n",
        "# Hz S RI R 50\n1000000 nan 0\n",
        "# Hz S RI R 50\n2000000 1 0\n1000000 1 0\n",
    ],
)
def test_parser_rejects_unsupported_or_invalid_data(fixture: str) -> None:
    with pytest.raises(TouchstoneError):
        parse_touchstone_s1p(fixture)


def test_export_rejects_non_finite_s11_and_multiline_comments() -> None:
    sweep = calibrated_sweep()
    bad_points = (CalibratedPoint(5_000_000, 0.0 + 0.0j, complex(float("inf"), 0.0)),)
    with pytest.raises(TouchstoneError, match="finite"):
        render_touchstone_s1p(
            CalibratedSweep(
                calibration_id=sweep.calibration_id,
                binding=sweep.binding,
                calibration_valid_from_utc=sweep.calibration_valid_from_utc,
                calibration_valid_until_utc=sweep.calibration_valid_until_utc,
                captured_at_utc=sweep.captured_at_utc,
                points=bad_points,
            )
        )
    with pytest.raises(TouchstoneError, match="single-line"):
        render_touchstone_s1p(sweep, comments=("bad\ncomment",))
