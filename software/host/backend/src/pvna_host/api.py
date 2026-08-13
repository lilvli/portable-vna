from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.cors import CORSMiddleware

from pvna_host import __version__
from pvna_host.domain import RunManager, RunStore, SweepPlan
from pvna_host.domain.calibration_manager import CalibrationManager
from pvna_host.domain.device import DeviceError
from pvna_host.domain.manager import RunConflict, RunNotFound

SCHEMA_VERSION = "pvna.api.v1"
ALLOWED_RENDERER_ORIGINS = {
    "null",
    "file://",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
}


class ConnectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["simulated", "serial"]
    port: str | None = None
    baud_rate: int = 115200


class SweepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["simulated", "serial"]
    start_hz: int = Field(gt=0)
    stop_hz: int = Field(gt=0)
    points: int = Field(ge=1, le=100_001)
    spacing: Literal["linear", "log"] = "linear"
    stimulus_amplitude_q15: int = Field(default=8192, ge=0, le=32767)
    settle_us: int = Field(default=1000, ge=0)
    integration_count: int = Field(default=65536, gt=0)
    point_timeout_ms: int = Field(default=2000, gt=0)
    measurement_role: Literal["dut", "open", "short", "load"] | None = None
    simulation_profile: Literal["dut", "open", "short", "load"] = "dut"
    port_path: Literal["PORT1_REFLECTION"] = "PORT1_REFLECTION"
    reference_impedance_ohm: float = Field(default=50.0, gt=0)


class CalibrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    open_run_id: str
    short_run_id: str
    load_run_id: str


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calibration_id: str | None = None


def default_run_root() -> Path:
    configured = os.environ.get("PVNA_RUN_ROOT")
    if configured:
        return Path(configured)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "PortableVNA" / "runs"
    return Path.cwd() / ".runtime" / "runs"


def create_app(
    *,
    access_token: str | None = None,
    manager: RunManager | None = None,
    instance_id: str | None = None,
) -> FastAPI:
    token = access_token or os.environ.get("PVNA_ACCESS_TOKEN") or secrets.token_urlsafe(32)
    service_instance_id = instance_id or os.environ.get("PVNA_INSTANCE_ID")
    run_manager = manager or RunManager(RunStore(default_run_root()))
    calibration_manager = CalibrationManager(run_manager, run_manager.store.root / ".calibrations")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await run_manager.safe_shutdown()

    app = FastAPI(
        title="Portable VNA Host",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.access_token = token
    app.state.run_manager = run_manager
    app.state.calibration_manager = calibration_manager
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(ALLOWED_RENDERER_ORIGINS),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    def authorize(authorization: Annotated[str | None, Header()] = None) -> None:
        expected = f"Bearer {token}"
        if authorization is None or not secrets.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="invalid local access token")

    auth = [Depends(authorize)]

    @app.get("/api/v1/health", dependencies=auth)
    async def health() -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "status": "ok",
            "service_version": __version__,
            "process_id": os.getpid(),
        }
        if service_instance_id:
            payload["instance_id"] = service_instance_id
        return payload

    @app.get("/api/v1/device/ports", dependencies=auth)
    async def ports() -> dict[str, object]:
        try:
            from serial.tools import list_ports

            serial_ports = [
                {"device": item.device, "description": item.description, "hwid": item.hwid}
                for item in list_ports.comports()
            ]
        except ImportError:
            serial_ports = []
        return {
            "schema_version": SCHEMA_VERSION,
            "ports": serial_ports,
            "note": "listing does not connect to a port",
        }

    @app.post("/api/v1/device/connect", dependencies=auth)
    async def connect(request: ConnectRequest) -> dict[str, object]:
        if request.source == "serial" and not request.port:
            raise HTTPException(status_code=422, detail="serial source requires an explicit port")
        try:
            status = await run_manager.connect(
                request.source, port=request.port, baud_rate=request.baud_rate
            )
        except (DeviceError, RunConflict, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"schema_version": SCHEMA_VERSION, "device": status.to_api_dict()}

    @app.post("/api/v1/device/disconnect", dependencies=auth)
    async def disconnect() -> dict[str, object]:
        try:
            status = await run_manager.disconnect()
        except RunConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"schema_version": SCHEMA_VERSION, "device": status.to_api_dict()}

    @app.get("/api/v1/device/status", dependencies=auth)
    async def device_status() -> dict[str, object]:
        status = await run_manager.get_device_status()
        return {"schema_version": SCHEMA_VERSION, "device": status.to_api_dict()}

    @app.post("/api/v1/device/hold", dependencies=auth)
    async def hold() -> dict[str, object]:
        try:
            status = await run_manager.hold()
        except RunConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"schema_version": SCHEMA_VERSION, "device": status.to_api_dict()}

    @app.post("/api/v1/runs/sweeps", dependencies=auth)
    async def start_sweep(request: SweepRequest) -> JSONResponse:
        try:
            status = await run_manager.get_device_status()
            if status.source is None or status.source.value.lower() != request.source:
                raise RunConflict("run source does not match the explicitly connected device")
            measurement_role = request.measurement_role
            if measurement_role is None:
                # Backward-compatible inference for the original simulated-only SOL UI.
                measurement_role = (
                    request.simulation_profile if request.source == "simulated" else "dut"
                )
            simulation_profile = (
                request.simulation_profile if request.source == "simulated" else "dut"
            )
            record = await run_manager.start_sweep(
                SweepPlan(
                    start_hz=request.start_hz,
                    stop_hz=request.stop_hz,
                    points=request.points,
                    spacing=request.spacing,
                    stimulus_amplitude_q15=request.stimulus_amplitude_q15,
                    settle_us=request.settle_us,
                    integration_count=request.integration_count,
                    point_timeout_ms=request.point_timeout_ms,
                    measurement_role=measurement_role,
                    simulation_profile=simulation_profile,
                    port_path=request.port_path,
                    reference_impedance_ohm=request.reference_impedance_ohm,
                )
            )
        except (RunConflict, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse(
            status_code=202,
            content={"schema_version": SCHEMA_VERSION, "run": record.to_api_dict()},
        )

    @app.get("/api/v1/runs", dependencies=auth)
    async def list_runs() -> dict[str, object]:
        return {"schema_version": SCHEMA_VERSION, "runs": run_manager.list_runs()}

    @app.get("/api/v1/runs/{run_id}", dependencies=auth)
    async def get_run(run_id: str) -> dict[str, object]:
        try:
            record = run_manager.get_run(run_id)
        except RunNotFound as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        return {"schema_version": SCHEMA_VERSION, "run": record.to_api_dict()}

    @app.post("/api/v1/runs/{run_id}/cancel", dependencies=auth)
    async def cancel_run(run_id: str) -> JSONResponse:
        try:
            record = await run_manager.cancel_run(run_id)
        except RunNotFound as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        return JSONResponse(
            status_code=202,
            content={"schema_version": SCHEMA_VERSION, "run": record.to_api_dict()},
        )

    @app.get("/api/v1/runs/{run_id}/points", dependencies=auth)
    async def get_points(run_id: str) -> dict[str, object]:
        try:
            points_data = run_manager.get_points(run_id)
        except RunNotFound as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        return {"schema_version": SCHEMA_VERSION, "points": points_data}

    @app.get("/api/v1/runs/{run_id}/summary", dependencies=auth)
    async def get_summary(run_id: str) -> dict[str, object]:
        try:
            summary = run_manager.get_summary(run_id)
        except RunNotFound as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"schema_version": SCHEMA_VERSION, "summary": summary}

    @app.get("/api/v1/logs", dependencies=auth)
    async def recent_logs() -> dict[str, object]:
        return {"schema_version": SCHEMA_VERSION, "events": await run_manager.events.recent()}

    @app.post("/api/v1/calibrations", dependencies=auth)
    async def create_calibration(request: CalibrationRequest) -> JSONResponse:
        try:
            calibration = calibration_manager.create(
                open_run_id=request.open_run_id,
                short_run_id=request.short_run_id,
                load_run_id=request.load_run_id,
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse(
            status_code=201,
            content={"schema_version": SCHEMA_VERSION, "calibration": calibration},
        )

    @app.get("/api/v1/calibrations", dependencies=auth)
    async def list_calibrations() -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "calibrations": calibration_manager.list(),
        }

    @app.get("/api/v1/runs/{run_id}/trace", dependencies=auth)
    async def get_trace(run_id: str, calibration_id: str | None = None) -> dict[str, object]:
        try:
            trace = calibration_manager.trace(run_id, calibration_id)
        except RunNotFound as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"schema_version": SCHEMA_VERSION, "trace": trace}

    @app.post("/api/v1/runs/{run_id}/exports/s1p", dependencies=auth)
    async def export_s1p(run_id: str, request: ExportRequest) -> dict[str, object]:
        try:
            exported = calibration_manager.export_s1p(run_id, request.calibration_id)
        except RunNotFound as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"schema_version": SCHEMA_VERSION, "export": exported}

    @app.websocket("/api/v1/events")
    async def events(websocket: WebSocket) -> None:
        origin = websocket.headers.get("origin")
        if origin is not None and origin not in ALLOWED_RENDERER_ORIGINS:
            await websocket.close(code=4403)
            return
        supplied = websocket.query_params.get("access_token", "")
        if not secrets.compare_digest(supplied, token):
            await websocket.close(code=4401)
            return
        await websocket.accept()
        try:
            async for event in run_manager.events.subscribe():
                await websocket.send_json(event.to_api_dict())
        except WebSocketDisconnect:
            return

    return app
