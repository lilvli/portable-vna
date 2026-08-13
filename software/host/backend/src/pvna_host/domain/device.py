from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from pvna_host.protocol import PointResult, StartPoint

from .models import DeviceStatus, EvidenceSource, SweepPlan


class DeviceError(RuntimeError):
    pass


class MeasurementCancelled(DeviceError):
    pass


class MeasurementResultUnknown(DeviceError):
    """The device safety may be recoverable, but point completion is unprovable."""


@dataclass(frozen=True, slots=True)
class AcceptedPoint:
    """The request was accepted; result remains a separate final transaction."""

    request: StartPoint
    result: asyncio.Task[PointResult]


class DeviceAdapter(Protocol):
    source: EvidenceSource
    device_id: str
    fpga_build_id: str

    async def connect(self) -> DeviceStatus: ...

    async def disconnect(self) -> DeviceStatus: ...

    async def get_status(self) -> DeviceStatus: ...

    async def exit_hold(self) -> DeviceStatus: ...

    async def enter_hold(self) -> DeviceStatus: ...

    async def prepare_plan(self, plan: SweepPlan) -> None: ...

    async def start_point(self, request: StartPoint) -> AcceptedPoint: ...

    async def cancel(self, measurement_id: int, point_index: int) -> DeviceStatus: ...
