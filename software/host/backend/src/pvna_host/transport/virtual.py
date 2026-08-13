from __future__ import annotations

import asyncio
import struct
from collections import OrderedDict, defaultdict

from pvna_host.protocol.frame import (
    REPLAYED_RESPONSE,
    Frame,
    MessageClass,
    Opcode,
    StatusCode,
    StreamParser,
)
from pvna_host.protocol.payloads import PointResult, StartPoint

from .base import TransportClosed
from .fake import FakeTransport

_INFO = struct.Struct("<BBBBHHQIIIIQQQII")
_STATUS = struct.Struct("<BBBBIIIIIHHIIIQ")
_IDENTITY = struct.Struct("<II")


class VirtualPvnaDevice:
    """Small PVNA-Link device fixture with duplicate suppression and fault injection."""

    def __init__(
        self,
        transport: FakeTransport,
        *,
        auto_complete: bool = True,
        event_delay_s: float = 0.0,
        device_id: int = (1 << 60) + 17,
        timestamp_ticks: int = (1 << 56) + 33,
        chunk_pattern: tuple[int, ...] | None = None,
    ) -> None:
        self.transport = transport
        self.auto_complete = auto_complete
        self.event_delay_s = event_delay_s
        self.device_id = device_id
        self.timestamp_ticks = timestamp_ticks
        self.chunk_pattern = chunk_pattern
        self.device_state = 1  # HOLD
        self.rf_output_enabled = False
        self.link_flags = 0x1F
        self.active: StartPoint | None = None
        self.last_result: PointResult | None = None
        self.last_error = int(StatusCode.OK)
        self.frames_rx = 0
        self.start_execution_count = 0
        self.cancel_execution_count = 0
        self.enter_hold_execution_count = 0
        self._request_parser = StreamParser()
        self._cache: OrderedDict[int, tuple[bytes, Frame]] = OrderedDict()
        self._drop_responses: defaultdict[int, int] = defaultdict(int)
        self._drop_events: defaultdict[int, int] = defaultdict(int)
        self._corrupt_responses: defaultdict[int, int] = defaultdict(int)
        self._payload_overrides: dict[int, bytes] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        transport.on_write = self._receive

    @property
    def crc_errors(self) -> int:
        return self._request_parser.crc_errors

    def drop_next_response(self, opcode: int, *, count: int = 1) -> None:
        self._drop_responses[int(opcode)] += count

    def drop_next_event(self, opcode: int, *, count: int = 1) -> None:
        self._drop_events[int(opcode)] += count

    def corrupt_next_response(self, opcode: int, *, count: int = 1) -> None:
        self._corrupt_responses[int(opcode)] += count

    def override_response_payload(self, opcode: int, payload: bytes | None) -> None:
        if payload is None:
            self._payload_overrides.pop(int(opcode), None)
        else:
            self._payload_overrides[int(opcode)] = bytes(payload)

    async def emit_noise(self, noise: bytes) -> None:
        await self.transport.inject_rx(noise, chunks=self.chunk_pattern)

    async def _receive(self, data: bytes) -> None:
        for request in self._request_parser.feed(data):
            if request.message_class is not MessageClass.REQUEST:
                continue
            self.frames_rx = (self.frames_rx + 1) & 0xFFFFFFFF
            request_wire = request.encode()
            cached = self._cache.get(request.sequence)
            if cached is not None:
                old_wire, old_response = cached
                if old_wire == request_wire:
                    replay = Frame(
                        message_class=MessageClass.RESPONSE,
                        opcode=old_response.opcode,
                        sequence=old_response.sequence,
                        payload=old_response.payload,
                        flags=old_response.flags | REPLAYED_RESPONSE,
                        status=old_response.status,
                    )
                    await self._emit(replay, event=False)
                else:
                    await self._emit(
                        self._response(request, StatusCode.DUPLICATE_MISMATCH), event=False
                    )
                continue
            response, final_event = self._execute(request)
            self._remember(request_wire, response)
            await self._emit(response, event=False)
            if final_event is not None:
                await self._emit(final_event, event=True)
            elif request.opcode == Opcode.START_POINT and response.status == StatusCode.ACCEPTED:
                task = asyncio.create_task(self._complete_point(request.sequence))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

    def _execute(self, request: Frame) -> tuple[Frame, Frame | None]:
        opcode = request.opcode
        if opcode == Opcode.PING:
            return self._fixed_response(request, b"", expected_length=0), None
        if opcode == Opcode.GET_INFO:
            payload = _INFO.pack(
                0,
                1,
                0,
                1,
                0,
                1,
                self.device_id,
                0x1234ABCD,
                0x8000003D,
                4096,
                1_000_000,
                5_000_000,
                100_000_000,
                100_000_000,
                10,
                1_000_000,
            )
            return self._fixed_response(request, payload, expected_length=0), None
        if opcode == Opcode.GET_STATUS:
            return self._fixed_response(request, self._status_payload(), expected_length=0), None
        if opcode == Opcode.EXIT_HOLD:
            if request.payload:
                return self._response(request, StatusCode.BAD_LENGTH), None
            if self.device_state != 1:
                return self._response(request, StatusCode.INVALID_STATE), None
            self.device_state = 2
            return self._response(request, StatusCode.OK), None
        if opcode == Opcode.ENTER_HOLD:
            if request.payload:
                return self._response(request, StatusCode.BAD_LENGTH), None
            self.enter_hold_execution_count += 1
            self.active = None
            self.rf_output_enabled = False
            self.device_state = 1
            return self._response(request, StatusCode.OK), None
        if opcode == Opcode.START_POINT:
            if len(request.payload) != 32:
                return self._response(request, StatusCode.BAD_LENGTH), None
            try:
                point = StartPoint.decode(request.payload)
                point.encode()
            except ValueError:
                return self._response(request, StatusCode.INVALID_PARAM), None
            if self.device_state not in {2, 4}:
                return self._response(request, StatusCode.INVALID_STATE), None
            self.start_execution_count += 1
            self.active = point
            self.device_state = 3
            self.rf_output_enabled = True
            return self._response(
                request,
                StatusCode.ACCEPTED,
                _IDENTITY.pack(point.measurement_id, point.point_index),
            ), None
        if opcode == Opcode.READ_LAST_RESULT:
            if request.payload:
                return self._response(request, StatusCode.BAD_LENGTH), None
            if self.last_result is None:
                return self._response(request, StatusCode.RESULT_NOT_FOUND), None
            return self._response(request, StatusCode.OK, self.last_result.encode()), None
        if opcode == Opcode.CANCEL:
            if len(request.payload) != _IDENTITY.size:
                return self._response(request, StatusCode.BAD_LENGTH), None
            measurement_id, point_index = _IDENTITY.unpack(request.payload)
            if (
                self.active is None
                or self.active.measurement_id != measurement_id
                or self.active.point_index != point_index
            ):
                return self._response(request, StatusCode.RESULT_NOT_FOUND), None
            self.cancel_execution_count += 1
            self.active = None
            self.rf_output_enabled = False
            self.device_state = 2
            self.last_error = int(StatusCode.CANCELLED)
            return self._response(request, StatusCode.OK, request.payload), None
        if opcode == Opcode.CLEAR_FAULT:
            if request.payload:
                return self._response(request, StatusCode.BAD_LENGTH), None
            if self.device_state != 5:
                return self._response(request, StatusCode.INVALID_STATE), None
            self.device_state = 1
            self.rf_output_enabled = False
            self.last_error = int(StatusCode.OK)
            return self._response(request, StatusCode.OK), None
        return self._response(request, StatusCode.UNKNOWN_OPCODE), None

    async def _complete_point(self, sequence: int) -> None:
        if not self.auto_complete:
            return
        if self.event_delay_s:
            await asyncio.sleep(self.event_delay_s)
        point = self.active
        if point is None:
            return
        result = PointResult(
            measurement_id=point.measurement_id,
            point_index=point.point_index,
            requested_frequency_hz=point.frequency_hz,
            actual_frequency_hz=point.frequency_hz,
            r_i_acc=(1 << 60) + 5,
            r_q_acc=-(1 << 59) + 7,
            a_i_acc=(1 << 58) + 9,
            a_q_acc=-(1 << 57) + 11,
            integration_count=point.integration_count,
            accumulator_right_shift=2,
            result_flags=0,
            fpga_timestamp_ticks=self.timestamp_ticks,
            duration_us=max(1, point.settle_us + 100),
        )
        self.last_result = result
        self.active = None
        self.rf_output_enabled = False
        self.device_state = 4
        event = Frame(
            message_class=MessageClass.EVENT,
            opcode=Opcode.POINT_RESULT,
            sequence=sequence,
            payload=result.encode(),
            status=StatusCode.OK,
        )
        try:
            await self._emit(event, event=True)
        except TransportClosed:
            pass

    def _fixed_response(self, request: Frame, payload: bytes, *, expected_length: int) -> Frame:
        if len(request.payload) != expected_length:
            return self._response(request, StatusCode.BAD_LENGTH)
        return self._response(request, StatusCode.OK, payload)

    def _response(self, request: Frame, status: int, payload: bytes = b"") -> Frame:
        payload = self._payload_overrides.get(int(request.opcode), payload)
        return Frame(
            message_class=MessageClass.RESPONSE,
            opcode=request.opcode,
            sequence=request.sequence,
            payload=payload,
            status=int(status),
        )

    def _status_payload(self) -> bytes:
        active = self.active
        last = self.last_result
        return _STATUS.pack(
            self.device_state,
            int(self.rf_output_enabled),
            int(last is not None),
            0,
            self.link_flags,
            active.measurement_id if active else 0,
            active.point_index if active else 0xFFFFFFFF,
            last.measurement_id if last else 0,
            last.point_index if last else 0xFFFFFFFF,
            self.last_error,
            0,
            self.frames_rx,
            self.crc_errors,
            0,
            (1 << 55) + 123,
        )

    def _remember(self, request_wire: bytes, response: Frame) -> None:
        self._cache[response.sequence] = (request_wire, response)
        self._cache.move_to_end(response.sequence)
        while len(self._cache) > 8:
            self._cache.popitem(last=False)

    async def _emit(self, frame: Frame, *, event: bool) -> None:
        opcode = int(frame.opcode)
        drops = self._drop_events if event else self._drop_responses
        if drops[opcode]:
            drops[opcode] -= 1
            return
        wire = frame.encode()
        if not event and self._corrupt_responses[opcode]:
            self._corrupt_responses[opcode] -= 1
            damaged = bytearray(wire)
            damaged[-1] ^= 0x80
            wire = bytes(damaged)
        await self.transport.inject_rx(wire, chunks=self.chunk_pattern)
