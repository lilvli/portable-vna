from __future__ import annotations

import json
import os
from hashlib import sha256
from pathlib import Path
from typing import Any

from pvna_host.protocol import PointResult

from .models import RunRecord, utc_now


class RunStore:
    """Small append-only M1 store; raw points are never overwritten by derived data."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, record: RunRecord) -> None:
        run_dir = self._run_dir(record.run_id)
        run_dir.mkdir(parents=False, exist_ok=False)
        self.update_manifest(record)

    def append_point(self, run_id: str, result: PointResult, source: str) -> None:
        run_dir = self._run_dir(run_id)
        payload = {
            "schema_version": "pvna.point.v1",
            "run_id": run_id,
            "source": source,
            "host_received_at_utc": utc_now(),
            **result.to_api_dict(),
        }
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        with (run_dir / "points.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())

    def update_manifest(self, record: RunRecord) -> None:
        run_dir = self._run_dir(record.run_id)
        target = run_dir / "manifest.json"
        temporary = run_dir / "manifest.json.tmp"
        payload = {
            "schema_version": "pvna.run.v1",
            **record.to_api_dict(),
        }
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)

    def read_points(self, run_id: str) -> list[dict[str, object]]:
        path = self._run_dir(run_id) / "points.jsonl"
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8") as stream:
            return [json.loads(line) for line in stream if line.strip()]

    def validate_complete_run(self, record: RunRecord) -> str:
        path = self._run_dir(record.run_id) / "points.jsonl"
        if not path.exists():
            raise ValueError("points.jsonl is missing")
        raw = path.read_bytes()
        if not raw.endswith(b"\n"):
            raise ValueError("points.jsonl does not end with a complete line")
        points = self.read_points(record.run_id)
        if record.confirmed_points != record.expected_points:
            raise ValueError("confirmed point count does not match the frozen expected point count")
        if len(points) != record.expected_points:
            raise ValueError(
                f"saved point count {len(points)} does not match expected {record.expected_points}"
            )
        frequencies = record.plan.frequency_axis()
        for index, point in enumerate(points):
            if point.get("schema_version") != "pvna.point.v1":
                raise ValueError(f"point {index} has an unsupported schema")
            if point.get("run_id") != record.run_id:
                raise ValueError(f"point {index} run_id mismatch")
            if int(point.get("measurement_id", 0)) != record.measurement_id:
                raise ValueError(f"point {index} measurement_id mismatch")
            if int(point.get("point_index", -1)) != index:
                raise ValueError(f"point {index} index mismatch")
            if int(point.get("requested_frequency_hz", 0)) != frequencies[index]:
                raise ValueError(f"point {index} frequency mismatch")
            if point.get("source") != record.source.value:
                raise ValueError(f"point {index} source mismatch")
            for field in ("r_i_acc", "r_q_acc", "a_i_acc", "a_q_acc"):
                value = point.get(field)
                if not isinstance(value, str) or not value.lstrip("-").isdigit():
                    raise ValueError(f"point {index} {field} is not an exact decimal string")
        return sha256(raw).hexdigest().upper()

    def validate_terminal_publication(self, record: RunRecord) -> None:
        """Require the summary written before the terminal manifest to match exactly."""
        summary = self.read_summary(record.run_id)
        expected = {
            "run_id": record.run_id,
            "source": record.source.value,
            "state": record.state.value,
            "measurement_id": record.measurement_id,
            "confirmed_points": record.confirmed_points,
            "expected_points": record.expected_points,
            "finished_at_utc": record.finished_at_utc,
            "safe_hold_confirmed": record.safe_hold_confirmed,
            "data_validation": record.data_validation,
            "points_sha256": record.points_sha256,
            "error": record.error,
            "plan": record.plan.to_dict(),
        }
        for field, value in expected.items():
            if summary.get(field) != value:
                raise ValueError(f"terminal summary {field} does not match manifest")

    def write_summary(self, record: RunRecord) -> None:
        payload = {
            "schema_version": "pvna.summary.v1",
            "run_id": record.run_id,
            "source": record.source.value,
            "state": record.state.value,
            "measurement_id": record.measurement_id,
            "confirmed_points": record.confirmed_points,
            "expected_points": record.expected_points,
            "created_at_utc": record.created_at_utc,
            "started_at_utc": record.started_at_utc,
            "finished_at_utc": record.finished_at_utc,
            "safe_hold_confirmed": record.safe_hold_confirmed,
            "data_validation": record.data_validation,
            "points_sha256": record.points_sha256,
            "error": record.error,
            "evidence_boundary": (
                "SIMULATED software evidence; not serial, FPGA, JESD, or RF validation"
                if record.source.value == "SIMULATED"
                else "HARDWARE source; scope depends on attached evidence"
            ),
            "plan": record.plan.to_dict(),
        }
        self._atomic_json(self._run_dir(record.run_id) / "summary.json", payload)

    def read_summary(self, run_id: str) -> dict[str, Any]:
        path = self._run_dir(run_id) / "summary.json"
        if not path.exists():
            raise FileNotFoundError("run summary is not available")
        return json.loads(path.read_text(encoding="utf-8"))

    def load_records(self) -> tuple[list[RunRecord], list[dict[str, Any]]]:
        records: list[RunRecord] = []
        errors: list[dict[str, Any]] = []
        for run_dir in sorted(self.root.glob("run_*"), reverse=True):
            if not run_dir.is_dir():
                continue
            manifest_path = run_dir / "manifest.json"
            try:
                if not manifest_path.exists():
                    raise ValueError("manifest.json is missing")
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest.get("schema_version") != "pvna.run.v1":
                    raise ValueError("unsupported run manifest schema")
                records.append(RunRecord.from_manifest(manifest))
            except Exception as exc:
                errors.append(
                    {
                        "run_id": run_dir.name,
                        "state": "UNKNOWN",
                        "error": f"archive unreadable: {exc}",
                        "recovered_after_interruption": True,
                    }
                )
        return records, errors

    def _atomic_json(self, target: Path, payload: dict[str, Any]) -> None:
        temporary = target.with_suffix(target.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)

    def _run_dir(self, run_id: str) -> Path:
        if not run_id.startswith("run_") or any(char in run_id for char in "\\/:"):
            raise ValueError("invalid run id")
        return self.root / run_id
