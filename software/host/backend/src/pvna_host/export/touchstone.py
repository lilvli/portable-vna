"""Touchstone 1.0 S1P RI export and an independent strict parser."""

from __future__ import annotations

import math
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from pvna_host.calibration import CalibratedSweep
from pvna_host.calibration.sol import utc_text


class TouchstoneError(ValueError):
    """Invalid S1P input or output request."""


@dataclass(frozen=True, slots=True)
class TouchstoneData:
    reference_ohms: float
    points: tuple[tuple[float, complex], ...]
    comments: tuple[str, ...]


def render_touchstone_s1p(
    sweep: CalibratedSweep,
    *,
    reference_ohms: float = 50.0,
    comments: Iterable[str] = (),
) -> str:
    """Render calibrated S11 as Touchstone 1.0 ``# Hz S RI`` text."""
    _validate_reference(reference_ohms)
    if not sweep.points:
        raise TouchstoneError("cannot export an empty sweep")

    binding = sweep.binding
    trace_comments = [
        "Portable VNA S1P export",
        f"calibration_id: {sweep.calibration_id}",
        f"source: {binding.source}",
        f"port: {binding.port}",
        f"path: {binding.path}",
        f"device_id: {binding.device_id}",
        f"fpga_build_id: {binding.fpga_build_id}",
        f"calibration_valid_from_utc: {utc_text(sweep.calibration_valid_from_utc)}",
        f"dut_captured_at_utc: {utc_text(sweep.captured_at_utc)}",
    ]
    if sweep.calibration_valid_until_utc is not None:
        trace_comments.append(
            "calibration_valid_until_utc: " + utc_text(sweep.calibration_valid_until_utc)
        )
    trace_comments.extend(_validate_comment(comment) for comment in comments)

    lines = [
        *(f"! {comment}" for comment in trace_comments),
        f"# Hz S RI R {_number(reference_ohms)}",
    ]
    previous_frequency = 0
    for point in sweep.points:
        if point.frequency_hz <= previous_frequency:
            raise TouchstoneError("frequencies must be positive and strictly increasing")
        previous_frequency = point.frequency_hz
        _validate_complex(point.s11, "S11")
        lines.append(f"{point.frequency_hz} {_number(point.s11.real)} {_number(point.s11.imag)}")
    return "\n".join(lines) + "\n"


def write_touchstone_s1p(
    path: Path,
    sweep: CalibratedSweep,
    *,
    reference_ohms: float = 50.0,
    comments: Iterable[str] = (),
) -> None:
    """Atomically publish a UTF-8/LF S1P file."""
    if path.suffix.lower() != ".s1p":
        raise TouchstoneError("Touchstone one-port output must use the .s1p suffix")
    text = render_touchstone_s1p(
        sweep,
        reference_ohms=reference_ohms,
        comments=comments,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def parse_touchstone_s1p(text: str) -> TouchstoneData:
    """Parse a single-port RI file without using exporter formatting helpers."""
    comments: list[str] = []
    points: list[tuple[float, complex]] = []
    unit_scale: float | None = None
    reference_ohms: float | None = None

    for line_number, original_line in enumerate(text.splitlines(), start=1):
        stripped = original_line.strip()
        if not stripped:
            continue
        if stripped.startswith("!"):
            comments.append(stripped[1:].strip())
            continue
        data_part, separator, inline_comment = stripped.partition("!")
        if separator:
            comments.append(inline_comment.strip())
        tokens = data_part.split()
        if not tokens:
            continue
        if tokens[0] == "#":
            if unit_scale is not None:
                raise TouchstoneError("multiple option lines are not allowed")
            if len(tokens) != 6:
                raise TouchstoneError("option line must be '# <unit> S RI R <ohms>'")
            units = {"HZ": 1.0, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9}
            try:
                unit_scale = units[tokens[1].upper()]
            except KeyError as exc:
                raise TouchstoneError(f"unsupported frequency unit on line {line_number}") from exc
            if tokens[2].upper() != "S" or tokens[3].upper() != "RI" or tokens[4].upper() != "R":
                raise TouchstoneError("only one-port S-parameter RI data is supported")
            reference_ohms = _parse_float(tokens[5], line_number)
            _validate_reference(reference_ohms)
            continue
        if unit_scale is None:
            raise TouchstoneError("option line must appear before network data")
        if len(tokens) != 3:
            raise TouchstoneError(f"S1P RI data line {line_number} must have three values")
        frequency = _parse_float(tokens[0], line_number) * unit_scale
        real = _parse_float(tokens[1], line_number)
        imaginary = _parse_float(tokens[2], line_number)
        value = complex(real, imaginary)
        if not math.isfinite(frequency) or frequency <= 0:
            raise TouchstoneError(f"frequency on line {line_number} must be finite and positive")
        _validate_complex(value, f"S11 on line {line_number}")
        if points and frequency <= points[-1][0]:
            raise TouchstoneError("frequencies must be strictly increasing")
        points.append((frequency, value))

    if unit_scale is None or reference_ohms is None:
        raise TouchstoneError("missing Touchstone option line")
    if not points:
        raise TouchstoneError("S1P file contains no network data")
    return TouchstoneData(reference_ohms, tuple(points), tuple(comments))


def _parse_float(token: str, line_number: int) -> float:
    try:
        value = float(token)
    except ValueError as exc:
        raise TouchstoneError(f"invalid numeric value on line {line_number}") from exc
    if not math.isfinite(value):
        raise TouchstoneError(f"non-finite numeric value on line {line_number}")
    return value


def _number(value: float) -> str:
    if not math.isfinite(value):
        raise TouchstoneError("Touchstone values must be finite")
    return format(value, ".17g")


def _validate_reference(value: float) -> None:
    if not math.isfinite(value) or value <= 0:
        raise TouchstoneError("reference impedance must be finite and positive")


def _validate_complex(value: complex, name: str) -> None:
    if not math.isfinite(value.real) or not math.isfinite(value.imag):
        raise TouchstoneError(f"{name} must contain only finite values")


def _validate_comment(value: str) -> str:
    if "\r" in value or "\n" in value:
        raise TouchstoneError("comments must be single-line text")
    return value
