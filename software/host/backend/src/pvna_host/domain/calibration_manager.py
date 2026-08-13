from __future__ import annotations

import json
import math
import os
import uuid
from dataclasses import asdict
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from pvna_host.calibration import (
    AcquisitionBinding,
    CalibratedPoint,
    CalibratedSweep,
    OnePortCalibration,
    RawComplexSample,
    RawSweep,
    SolErrorTerms,
    Standard,
    StandardSweep,
    solve_one_port_sol,
)
from pvna_host.export import render_touchstone_s1p

from .manager import RunManager
from .models import RunRecord, RunState, utc_now


class CalibrationManager:
    """Persistent SOL sets and repeatable derived traces over immutable raw runs."""

    def __init__(self, run_manager: RunManager, root: Path) -> None:
        self.run_manager = run_manager
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._calibrations: dict[str, OnePortCalibration] = {}
        self._standard_runs: dict[str, dict[str, str]] = {}
        self._load()

    def create(self, *, open_run_id: str, short_run_id: str, load_run_id: str) -> dict[str, Any]:
        run_ids = {
            Standard.OPEN: open_run_id,
            Standard.SHORT: short_run_id,
            Standard.LOAD: load_run_id,
        }
        standards: list[StandardSweep] = []
        for standard, run_id in run_ids.items():
            record = self.run_manager.get_run(run_id)
            if record.state is not RunState.COMPLETED or record.data_validation != "VALID":
                raise ValueError(f"{standard.value} run is not complete and validated")
            if record.plan.measurement_role != standard.value.lower():
                raise ValueError(f"{standard.value} run role is {record.plan.measurement_role!r}")
            raw = self._raw_sweep(record)
            standards.append(
                StandardSweep.ideal(standard, raw.binding, raw.captured_at_utc, raw.samples)
            )
        calibration_id = f"cal_{uuid.uuid4().hex}"
        calibration = solve_one_port_sol(calibration_id, standards)
        self._calibrations[calibration_id] = calibration
        self._standard_runs[calibration_id] = {
            standard.value.lower(): run_id for standard, run_id in run_ids.items()
        }
        self._save(calibration)
        return self.to_api_dict(calibration)

    def list(self) -> list[dict[str, Any]]:
        return [
            self.to_api_dict(item)
            for item in sorted(
                self._calibrations.values(), key=lambda value: value.valid_from_utc, reverse=True
            )
        ]

    def get(self, calibration_id: str) -> OnePortCalibration:
        try:
            return self._calibrations[calibration_id]
        except KeyError as exc:
            raise KeyError("calibration not found") from exc

    def trace(self, run_id: str, calibration_id: str | None = None) -> dict[str, Any]:
        record = self.run_manager.get_run(run_id)
        if record.state is not RunState.COMPLETED or record.data_validation != "VALID":
            raise ValueError("only a complete, validated run can be replayed")
        raw = self._raw_sweep(record)
        calibrated = self.get(calibration_id).apply(raw) if calibration_id else None
        points: list[dict[str, Any]] = []
        for index, sample in enumerate(raw.samples):
            a_over_r = sample.ratio(reference_tolerance=1e-12)
            r_over_a = None if sample.reflection == 0 else sample.reference / sample.reflection
            selected = calibrated.points[index].s11 if calibrated else a_over_r
            magnitude = abs(selected)
            points.append(
                {
                    "point_index": index,
                    "frequency_hz": str(sample.frequency_hz),
                    "r_i_acc": str(round(sample.reference.real)),
                    "r_q_acc": str(round(sample.reference.imag)),
                    "a_i_acc": str(round(sample.reflection.real)),
                    "a_q_acc": str(round(sample.reflection.imag)),
                    "a_over_r_real": a_over_r.real,
                    "a_over_r_imag": a_over_r.imag,
                    "r_over_a_real": r_over_a.real if r_over_a is not None else None,
                    "r_over_a_imag": r_over_a.imag if r_over_a is not None else None,
                    "s11_real": calibrated.points[index].s11.real if calibrated else None,
                    "s11_imag": calibrated.points[index].s11.imag if calibrated else None,
                    "magnitude_db": 20.0 * math.log10(magnitude) if magnitude > 0 else None,
                    "phase_deg": math.degrees(math.atan2(selected.imag, selected.real)),
                }
            )
        return {
            "run_id": run_id,
            "source": record.source.value,
            "calibration_id": calibration_id,
            "data_kind": "CALIBRATED" if calibration_id else "RAW",
            "points": points,
        }

    def export_s1p(self, run_id: str, calibration_id: str | None = None) -> dict[str, str]:
        record = self.run_manager.get_run(run_id)
        if record.state is not RunState.COMPLETED or record.data_validation != "VALID":
            raise ValueError("only a complete, validated run can be exported")
        raw = self._raw_sweep(record)
        if calibration_id:
            sweep = self.get(calibration_id).apply(raw)
            kind = "calibrated"
        else:
            points = tuple(
                CalibratedPoint(
                    sample.frequency_hz,
                    sample.ratio(reference_tolerance=1e-12),
                    sample.ratio(reference_tolerance=1e-12),
                )
                for sample in raw.samples
            )
            sweep = CalibratedSweep(
                calibration_id="RAW_UNCALIBRATED",
                binding=raw.binding,
                calibration_valid_from_utc=raw.captured_at_utc,
                calibration_valid_until_utc=None,
                captured_at_utc=raw.captured_at_utc,
                points=points,
            )
            kind = "raw"
        content = render_touchstone_s1p(
            sweep,
            reference_ohms=record.plan.reference_impedance_ohm,
            comments=(f"run_id: {run_id}", f"data_kind: {kind}"),
        )
        derivation = self._append_export_derivation(
            record=record,
            calibration_id=calibration_id,
            output=content,
            data_kind=kind,
        )
        return {
            "filename": f"{run_id}-{kind}.s1p",
            "content": content,
            "data_kind": kind,
            "derivation_id": derivation["derivation_id"],
            "output_sha256": derivation["output_sha256"],
        }

    def _append_export_derivation(
        self,
        *,
        record: RunRecord,
        calibration_id: str | None,
        output: str,
        data_kind: str,
    ) -> dict[str, Any]:
        calibration_sha256: str | None = None
        if calibration_id:
            calibration_path = self.root / f"{calibration_id}.json"
            calibration_sha256 = sha256(calibration_path.read_bytes()).hexdigest().upper()
        payload: dict[str, Any] = {
            "schema_version": "pvna.derivation.v1",
            "derivation_id": f"drv_{uuid.uuid4().hex}",
            "created_at_utc": utc_now(),
            "operation": "EXPORT_S1P",
            "run_id": record.run_id,
            "run_points_sha256": record.points_sha256,
            "calibration_id": calibration_id,
            "calibration_sha256": calibration_sha256,
            "data_kind": data_kind.upper(),
            "output_sha256": sha256(output.encode("utf-8")).hexdigest().upper(),
        }
        target = self.root / "derivations.jsonl"
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        with target.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
        return payload

    def to_api_dict(self, calibration: OnePortCalibration) -> dict[str, Any]:
        return {
            "calibration_id": calibration.calibration_id,
            "source": calibration.binding.source,
            "port": calibration.binding.port,
            "path": calibration.binding.path,
            "device_id": calibration.binding.device_id,
            "fpga_build_id": calibration.binding.fpga_build_id,
            "frequency_axis_hz": [str(item) for item in calibration.frequency_axis_hz],
            "points": len(calibration.frequency_axis_hz),
            "valid_from_utc": calibration.valid_from_utc.isoformat(),
            "valid_until_utc": (
                calibration.valid_until_utc.isoformat()
                if calibration.valid_until_utc is not None
                else None
            ),
            "standard_runs": self._standard_runs.get(calibration.calibration_id, {}),
        }

    def _raw_sweep(self, record: RunRecord) -> RawSweep:
        if not record.finished_at_utc:
            raise ValueError("run has no completed capture time")
        points = self.run_manager.get_points(record.run_id)
        samples = tuple(
            RawComplexSample(
                frequency_hz=int(point["actual_frequency_hz"]),
                reference=complex(int(point["r_i_acc"]), int(point["r_q_acc"])),
                reflection=complex(int(point["a_i_acc"]), int(point["a_q_acc"])),
            )
            for point in points
        )
        return RawSweep(
            binding=AcquisitionBinding(
                source=record.source.value,
                port=1,
                path=record.plan.port_path,
                device_id=record.device_id,
                fpga_build_id=record.fpga_build_id,
                stimulus_amplitude_q15=record.plan.stimulus_amplitude_q15,
                settle_us=record.plan.settle_us,
                integration_count=record.plan.integration_count,
            ),
            captured_at_utc=datetime.fromisoformat(record.finished_at_utc.replace("Z", "+00:00")),
            samples=samples,
        )

    def _save(self, calibration: OnePortCalibration) -> None:
        payload = {
            "schema_version": "pvna.calibration.v1",
            **self.to_api_dict(calibration),
            "binding": asdict(calibration.binding),
            "terms": [
                {
                    "directivity": [term.directivity.real, term.directivity.imag],
                    "reflection_tracking": [
                        term.reflection_tracking.real,
                        term.reflection_tracking.imag,
                    ],
                    "source_match": [term.source_match.real, term.source_match.imag],
                }
                for term in calibration.terms
            ],
            "standard_capture_times_utc": [
                [standard.value, captured.isoformat()]
                for standard, captured in calibration.standard_capture_times_utc
            ],
            "reference_tolerance": calibration.reference_tolerance,
            "singularity_tolerance": calibration.singularity_tolerance,
        }
        target = self.root / f"{calibration.calibration_id}.json"
        temporary = target.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)

    def _load(self) -> None:
        for path in self.root.glob("cal_*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("schema_version") != "pvna.calibration.v1":
                    continue
                binding = AcquisitionBinding(**data["binding"])
                calibration = OnePortCalibration(
                    calibration_id=data["calibration_id"],
                    binding=binding,
                    frequency_axis_hz=tuple(int(item) for item in data["frequency_axis_hz"]),
                    terms=tuple(
                        SolErrorTerms(
                            complex(*item["directivity"]),
                            complex(*item["reflection_tracking"]),
                            complex(*item["source_match"]),
                        )
                        for item in data["terms"]
                    ),
                    standard_capture_times_utc=tuple(
                        (Standard(item[0]), datetime.fromisoformat(item[1]))
                        for item in data["standard_capture_times_utc"]
                    ),
                    valid_from_utc=datetime.fromisoformat(data["valid_from_utc"]),
                    valid_until_utc=(
                        datetime.fromisoformat(data["valid_until_utc"])
                        if data["valid_until_utc"]
                        else None
                    ),
                    reference_tolerance=float(data["reference_tolerance"]),
                    singularity_tolerance=float(data["singularity_tolerance"]),
                )
                self._calibrations[calibration.calibration_id] = calibration
                self._standard_runs[calibration.calibration_id] = data["standard_runs"]
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
