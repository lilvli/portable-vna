from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from pvna_host.api import create_app  # noqa: E402
from pvna_host.domain import RunManager, RunStore  # noqa: E402


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        manager = RunManager(RunStore(Path(self.temporary.name)))
        self.client_context = TestClient(
            create_app(
                access_token="test-token",
                manager=manager,
                instance_id="test-instance",
            )
        )
        self.client = self.client_context.__enter__()
        self.headers = {"Authorization": "Bearer test-token"}

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temporary.cleanup()

    def test_auth_and_explicit_simulated_connect(self) -> None:
        self.assertEqual(self.client.get("/api/v1/health").status_code, 401)
        health = self.client.get("/api/v1/health", headers=self.headers)
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["schema_version"], "pvna.api.v1")
        self.assertEqual(health.json()["process_id"], os.getpid())
        self.assertEqual(health.json()["instance_id"], "test-instance")

        connected = self.client.post(
            "/api/v1/device/connect",
            headers=self.headers,
            json={"source": "simulated"},
        )
        self.assertEqual(connected.status_code, 200)
        self.assertEqual(connected.json()["device"]["source"], "SIMULATED")
        self.assertEqual(connected.json()["device"]["state"], "HOLD")

    def test_cors_allows_packaged_electron_and_fixed_dev_origin_only(self) -> None:
        packaged = self.client.options(
            "/api/v1/health",
            headers={
                "Origin": "null",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization",
            },
        )
        self.assertEqual(packaged.status_code, 200)
        self.assertEqual(packaged.headers["access-control-allow-origin"], "null")

        hostile = self.client.options(
            "/api/v1/health",
            headers={"Origin": "https://example.test", "Access-Control-Request-Method": "GET"},
        )
        self.assertEqual(hostile.status_code, 400)
        self.assertNotIn("access-control-allow-origin", hostile.headers)

    def test_sweep_api_returns_saved_string_ints(self) -> None:
        self.client.post(
            "/api/v1/device/connect", headers=self.headers, json={"source": "simulated"}
        )
        response = self.client.post(
            "/api/v1/runs/sweeps",
            headers=self.headers,
            json={
                "source": "simulated",
                "start_hz": 5_000_000,
                "stop_hz": 7_000_000,
                "points": 3,
                "spacing": "linear",
                "stimulus_amplitude_q15": 8192,
                "settle_us": 1000,
                "integration_count": 65536,
                "point_timeout_ms": 2000,
            },
        )
        self.assertEqual(response.status_code, 202)
        run_id = response.json()["run"]["run_id"]
        snapshot = None
        for _ in range(100):
            snapshot = self.client.get(f"/api/v1/runs/{run_id}", headers=self.headers).json()["run"]
            if snapshot["state"] in {"COMPLETED", "FAILED", "UNKNOWN"}:
                break
            time.sleep(0.01)
        assert snapshot is not None
        self.assertEqual(snapshot["state"], "COMPLETED")
        self.assertEqual(snapshot["confirmed_points"], 3)
        points = self.client.get(f"/api/v1/runs/{run_id}/points", headers=self.headers).json()[
            "points"
        ]
        self.assertEqual(len(points), 3)
        self.assertIsInstance(points[0]["r_i_acc"], str)
        self.assertEqual(points[0]["source"], "SIMULATED")
        runs = self.client.get("/api/v1/runs", headers=self.headers).json()["runs"]
        self.assertEqual(runs[0]["run_id"], run_id)
        summary = self.client.get(f"/api/v1/runs/{run_id}/summary", headers=self.headers).json()[
            "summary"
        ]
        self.assertEqual(summary["data_validation"], "VALID")
        self.assertTrue(summary["safe_hold_confirmed"])

    def test_websocket_reports_device_state_change(self) -> None:
        with self.client.websocket_connect("/api/v1/events?access_token=test-token") as websocket:
            response = self.client.post(
                "/api/v1/device/connect",
                headers=self.headers,
                json={"source": "simulated"},
            )
            self.assertEqual(response.status_code, 200)
            event = websocket.receive_json()
            self.assertEqual(event["schema_version"], "pvna.events.v1")
            self.assertEqual(event["event"], "device.status_changed")
            self.assertEqual(event["data"]["source"], "SIMULATED")

    def test_websocket_rejects_hostile_origin(self) -> None:
        with self.assertRaises(WebSocketDisconnect) as raised:
            with self.client.websocket_connect(
                "/api/v1/events?access_token=test-token",
                headers={"origin": "https://example.test"},
            ):
                pass
        self.assertEqual(raised.exception.code, 4403)

    def test_serial_source_requires_explicit_port_without_opening_a_resource(self) -> None:
        response = self.client.post(
            "/api/v1/device/connect",
            headers=self.headers,
            json={"source": "serial"},
        )
        self.assertEqual(response.status_code, 422)

    def test_sol_trace_and_touchstone_export_round_trip(self) -> None:
        self.client.post(
            "/api/v1/device/connect", headers=self.headers, json={"source": "simulated"}
        )

        def capture(profile: str) -> str:
            started = self.client.post(
                "/api/v1/runs/sweeps",
                headers=self.headers,
                json={
                    "source": "simulated",
                    "start_hz": 5_000_000,
                    "stop_hz": 7_000_000,
                    "points": 3,
                    "simulation_profile": profile,
                },
            )
            self.assertEqual(started.status_code, 202)
            run_id = started.json()["run"]["run_id"]
            for _ in range(100):
                run = self.client.get(f"/api/v1/runs/{run_id}", headers=self.headers).json()["run"]
                if run["state"] in {"COMPLETED", "FAILED", "UNKNOWN"}:
                    break
                time.sleep(0.01)
            self.assertEqual(run["state"], "COMPLETED")
            return run_id

        standards = {name: capture(name) for name in ("open", "short", "load")}
        created = self.client.post(
            "/api/v1/calibrations",
            headers=self.headers,
            json={f"{name}_run_id": run_id for name, run_id in standards.items()},
        )
        self.assertEqual(created.status_code, 201)
        calibration_id = created.json()["calibration"]["calibration_id"]
        dut_run_id = capture("dut")

        trace = self.client.get(
            f"/api/v1/runs/{dut_run_id}/trace",
            headers=self.headers,
            params={"calibration_id": calibration_id},
        )
        self.assertEqual(trace.status_code, 200)
        trace_data = trace.json()["trace"]
        self.assertEqual(trace_data["data_kind"], "CALIBRATED")
        self.assertEqual(len(trace_data["points"]), 3)
        self.assertIsInstance(trace_data["points"][0]["frequency_hz"], str)
        self.assertIsNotNone(trace_data["points"][0]["r_over_a_real"])
        unchanged = self.client.get(f"/api/v1/runs/{dut_run_id}", headers=self.headers).json()[
            "run"
        ]
        self.assertIsNone(unchanged["calibration_id"])

        exported = self.client.post(
            f"/api/v1/runs/{dut_run_id}/exports/s1p",
            headers=self.headers,
            json={"calibration_id": calibration_id},
        )
        self.assertEqual(exported.status_code, 200)
        content = exported.json()["export"]["content"]
        self.assertIn("# Hz S RI R 50", content)
        self.assertIn(f"calibration_id: {calibration_id}", content)
        derivations = (Path(self.temporary.name) / ".calibrations" / "derivations.jsonl").read_text(
            encoding="utf-8"
        )
        derivative = json.loads(derivations)
        self.assertEqual(derivative["operation"], "EXPORT_S1P")
        self.assertEqual(derivative["run_id"], dut_run_id)
        self.assertEqual(derivative["calibration_id"], calibration_id)
        self.assertEqual(len(derivative["output_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
