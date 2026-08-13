from __future__ import annotations

import asyncio
import enum
import secrets
import struct
from dataclasses import dataclass

from pvna_host.transport.base import ByteTransport, TransportClosed, TransportError

from .frame import (
    REPLAYED_RESPONSE,
    Frame,
    MessageClass,
    Opcode,
    StatusCode,
    StreamParser,
)
from .payloads import PointResult, StartPoint

_INFO = struct.Struct("<BBBBHHQIIIIQQQII")
_STATUS = struct.Struct("<BBBBIIIIIHHIIIQ")
_IDENTITY = struct.Struct("<II")
_POINT_FAILURE = struct.Struct("<IIHHI")


class SessionError(RuntimeError):
    """Base error for a PVNA-Link host transaction."""


class SessionNotOpen(SessionError):
    pass


class SessionProtocolError(SessionError):
    pass


class CorrelationError(SessionProtocolError):
    pass


class ResponseTimeout(SessionError):
    def __init__(self, opcode: int, sequence: int) -> None:
        self.opcode = opcode
        self.sequence = sequence
        super().__init__(f"response timeout for opcode 0x{opcode:02X}, sequence {sequence}")


class CommandRejected(SessionError):
    def __init__(self, response: Frame) -> None:
        self.response = response
        try:
            status_name = StatusCode(response.status).name
        except ValueError:
            status_name = f"0x{response.status:04X}"
        super().__init__(
            f"opcode 0x{response.opcode:02X}, sequence {response.sequence} rejected: {status_name}"
        )


class MeasurementFailed(SessionError):
    def __init__(self, failure: PointFailure) -> None:
        self.failure = failure
        super().__init__(
            f"measurement {failure.measurement_id}/{failure.point_index} failed "
            f"at stage {failure.stage}: 0x{failure.error:04X} (detail {failure.detail})"
        )


class MeasurementUnknown(SessionError):
    """The link cannot prove whether a measurement completed."""

    def __init__(
        self,
        reason: str,
        *,
        sequence: int | None = None,
        measurement_id: int | None = None,
        point_index: int | None = None,
    ) -> None:
        self.reason = reason
        self.sequence = sequence
        self.measurement_id = measurement_id
        self.point_index = point_index
        correlation = []
        if sequence is not None:
            correlation.append(f"sequence={sequence}")
        if measurement_id is not None:
            correlation.append(f"measurement_id={measurement_id}")
        if point_index is not None:
            correlation.append(f"point_index={point_index}")
        prefix = f"{', '.join(correlation)}: " if correlation else ""
        super().__init__(prefix + reason)


class SafetyStateUnknown(SessionError):
    """HOLD and RF-off could not be confirmed before disconnect."""


class ConnectionState(enum.StrEnum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTED = "CONNECTED"
    UNKNOWN = "UNKNOWN"


class DeviceState(enum.IntEnum):
    BOOT = 0
    HOLD = 1
    IDLE = 2
    BUSY = 3
    RESULT_READY = 4
    FAULT = 5


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    protocol_major: int
    protocol_minor: int
    firmware_major: int
    firmware_minor: int
    firmware_patch: int
    hardware_revision: int
    device_id: int
    fpga_build_id: int
    capabilities: int
    max_payload: int
    max_integration_count: int
    min_frequency_hz: int
    max_frequency_hz: int
    timebase_hz: int
    min_settle_us: int
    max_settle_us: int

    @classmethod
    def decode(cls, payload: bytes) -> DeviceInfo:
        if len(payload) != _INFO.size:
            raise SessionProtocolError("GET_INFO response payload must be exactly 64 bytes")
        info = cls(*_INFO.unpack(payload))
        if (info.protocol_major, info.protocol_minor) != (0, 1):
            raise SessionProtocolError("device reported an incompatible protocol version")
        if info.max_payload > 4096:
            raise SessionProtocolError("device max_payload exceeds the V0.1 limit")
        return info

    def to_api_dict(self) -> dict[str, int | str]:
        """Keep every u64 exact across the JavaScript JSON boundary."""

        return {
            "protocol_major": self.protocol_major,
            "protocol_minor": self.protocol_minor,
            "firmware_major": self.firmware_major,
            "firmware_minor": self.firmware_minor,
            "firmware_patch": self.firmware_patch,
            "hardware_revision": self.hardware_revision,
            "device_id": str(self.device_id),
            "fpga_build_id": self.fpga_build_id,
            "capabilities": self.capabilities,
            "max_payload": self.max_payload,
            "max_integration_count": self.max_integration_count,
            "min_frequency_hz": str(self.min_frequency_hz),
            "max_frequency_hz": str(self.max_frequency_hz),
            "timebase_hz": str(self.timebase_hz),
            "min_settle_us": self.min_settle_us,
            "max_settle_us": self.max_settle_us,
        }


@dataclass(frozen=True, slots=True)
class DeviceStatus:
    device_state: DeviceState
    rf_output_enabled: bool
    last_result_valid: bool
    link_flags: int
    active_measurement_id: int
    active_point_index: int
    last_measurement_id: int
    last_point_index: int
    last_error: int
    frames_rx: int
    crc_errors: int
    uptime_ms: int

    @classmethod
    def decode(cls, payload: bytes) -> DeviceStatus:
        if len(payload) != _STATUS.size:
            raise SessionProtocolError("GET_STATUS response payload must be exactly 48 bytes")
        (
            raw_state,
            rf_output,
            last_valid,
            reserved_0,
            link_flags,
            active_measurement_id,
            active_point_index,
            last_measurement_id,
            last_point_index,
            last_error,
            reserved_1,
            frames_rx,
            crc_errors,
            reserved_2,
            uptime_ms,
        ) = _STATUS.unpack(payload)
        if reserved_0 or reserved_1 or reserved_2:
            raise SessionProtocolError("GET_STATUS reserved field is non-zero")
        if rf_output not in {0, 1} or last_valid not in {0, 1}:
            raise SessionProtocolError("GET_STATUS boolean field is not 0 or 1")
        try:
            state = DeviceState(raw_state)
        except ValueError as exc:
            raise SessionProtocolError("GET_STATUS device state is unknown") from exc
        if state is not DeviceState.BUSY and rf_output:
            raise SessionProtocolError("device reports RF enabled outside BUSY")
        return cls(
            device_state=state,
            rf_output_enabled=bool(rf_output),
            last_result_valid=bool(last_valid),
            link_flags=link_flags,
            active_measurement_id=active_measurement_id,
            active_point_index=active_point_index,
            last_measurement_id=last_measurement_id,
            last_point_index=last_point_index,
            last_error=last_error,
            frames_rx=frames_rx,
            crc_errors=crc_errors,
            uptime_ms=uptime_ms,
        )

    def to_api_dict(self) -> dict[str, bool | int | str]:
        return {
            "device_state": self.device_state.name,
            "rf_output_enabled": self.rf_output_enabled,
            "last_result_valid": self.last_result_valid,
            "link_flags": self.link_flags,
            "active_measurement_id": self.active_measurement_id,
            "active_point_index": self.active_point_index,
            "last_measurement_id": self.last_measurement_id,
            "last_point_index": self.last_point_index,
            "last_error": self.last_error,
            "frames_rx": self.frames_rx,
            "crc_errors": self.crc_errors,
            "uptime_ms": str(self.uptime_ms),
        }


@dataclass(frozen=True, slots=True)
class PointFailure:
    measurement_id: int
    point_index: int
    stage: int
    error: int
    detail: int

    @classmethod
    def decode(cls, payload: bytes) -> PointFailure:
        if len(payload) != _POINT_FAILURE.size:
            raise SessionProtocolError("POINT_FAILED payload must be exactly 16 bytes")
        return cls(*_POINT_FAILURE.unpack(payload))


def validate_point_result(result: PointResult, expected: StartPoint | None = None) -> None:
    """Fail-closed admission gate for every result before confirmation or persistence.

    Clipping bits 0 and 1 are retained as quality warnings.  Saturation,
    clock-loss, JESD errors, unknown flag bits, a zero reference, and frozen
    request mismatches are not admissible as confirmed phase-one points.
    """

    unknown_flags = result.result_flags & ~0x001F
    if unknown_flags:
        raise SessionProtocolError(
            f"POINT_RESULT has unknown result_flags bits 0x{unknown_flags:04X}"
        )
    fatal_flags = result.result_flags & 0x001C
    if fatal_flags:
        raise SessionProtocolError(
            f"POINT_RESULT reports invalid measurement flags 0x{fatal_flags:04X}"
        )
    if result.r_i_acc * result.r_i_acc + result.r_q_acc * result.r_q_acc <= 1:
        raise SessionProtocolError(
            "POINT_RESULT reference R is zero or below one accumulator LSB; A/R is invalid"
        )
    if result.integration_count == 0:
        raise SessionProtocolError("POINT_RESULT integration_count must be non-zero")
    if result.actual_frequency_hz == 0:
        raise SessionProtocolError("POINT_RESULT actual_frequency_hz must be non-zero")
    if expected is None:
        return
    if (result.measurement_id, result.point_index) != (
        expected.measurement_id,
        expected.point_index,
    ):
        raise CorrelationError("point result identity does not match START_POINT")
    if result.requested_frequency_hz != expected.frequency_hz:
        raise CorrelationError("point result requested frequency does not match START_POINT")
    if result.integration_count != expected.integration_count:
        raise CorrelationError("point result integration_count does not match START_POINT")
    if result.duration_us > expected.max_duration_ms * 1000:
        raise CorrelationError("point result duration exceeds START_POINT max_duration_ms")


@dataclass(frozen=True, slots=True)
class Preflight:
    info: DeviceInfo
    status: DeviceStatus


@dataclass(frozen=True, slots=True)
class PointTransaction:
    sequence: int
    request: StartPoint
    acknowledgement: Frame | None
    acceptance_recovered: bool = False
    recovered_result: PointResult | None = None


@dataclass(slots=True)
class _PendingResponse:
    opcode: int
    future: asyncio.Future[Frame]


class ProtocolSession:
    """Correlated PVNA-Link request/response/event session over a byte transport."""

    def __init__(
        self,
        transport: ByteTransport,
        *,
        response_timeout_s: float = 0.1,
        frame_timeout_s: float = 0.2,
        result_timeout_s: float | None = None,
        sequence_seed: int | None = None,
    ) -> None:
        if response_timeout_s <= 0 or frame_timeout_s <= 0:
            raise ValueError("session timeouts must be positive")
        if result_timeout_s is not None and result_timeout_s <= 0:
            raise ValueError("result_timeout_s must be positive")
        if sequence_seed is None:
            sequence_seed = secrets.randbelow(0xFFFFFFFF) + 1
        if not 1 <= sequence_seed <= 0xFFFFFFFF:
            raise ValueError("sequence_seed must be a non-zero u32")
        self.transport = transport
        self.response_timeout_s = response_timeout_s
        self.frame_timeout_s = frame_timeout_s
        self.result_timeout_s = result_timeout_s
        self.connection_state = ConnectionState.DISCONNECTED
        self.unknown_reason: str | None = None
        self.correlation_errors: list[str] = []
        self._next_sequence_value = sequence_seed
        self._parser = StreamParser()
        self._completed_parser_crc_errors = 0
        self._completed_parser_discarded = 0
        self._pending: dict[int, _PendingResponse] = {}
        self._event_queues: dict[int, asyncio.Queue[Frame | SessionError]] = {}
        self._point_ids: dict[int, tuple[int, int]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._command_lock = asyncio.Lock()
        self._closing = False
        self._last_byte_at: float | None = None

    @property
    def is_open(self) -> bool:
        return self.connection_state is ConnectionState.CONNECTED and self.transport.is_open

    @property
    def crc_errors(self) -> int:
        return self._completed_parser_crc_errors + self._parser.crc_errors

    @property
    def discarded_bytes(self) -> int:
        return self._completed_parser_discarded + self._parser.discarded_bytes

    async def open(self) -> None:
        if self.transport.is_open or self.connection_state is ConnectionState.CONNECTED:
            raise SessionError("session is already open")
        await self.transport.open()
        self.connection_state = ConnectionState.CONNECTED
        self.unknown_reason = None
        self._closing = False
        self._reader_task = asyncio.create_task(self._reader_loop())

    async def connect(self, *, required_link_flags: int = 0) -> Preflight:
        await self.open()
        try:
            return await self.preflight(required_link_flags=required_link_flags)
        except Exception:
            await self.abort("connection preflight failed")
            raise

    async def preflight(self, *, required_link_flags: int = 0) -> Preflight:
        await self.ping()
        info = await self.get_info()
        status = await self.get_status()
        if status.link_flags & required_link_flags != required_link_flags:
            raise SessionProtocolError("required hardware link flags are not all asserted")
        return Preflight(info=info, status=status)

    async def ping(self) -> None:
        response = await self._request(Opcode.PING)
        self._require_success(response, StatusCode.OK, payload_length=0)

    async def get_info(self) -> DeviceInfo:
        response = await self._request(Opcode.GET_INFO)
        self._require_success(response, StatusCode.OK, payload_length=_INFO.size)
        return DeviceInfo.decode(response.payload)

    async def get_status(self) -> DeviceStatus:
        response = await self._request(Opcode.GET_STATUS)
        self._require_success(response, StatusCode.OK, payload_length=_STATUS.size)
        return DeviceStatus.decode(response.payload)

    async def exit_hold(self) -> DeviceStatus:
        response = await self._request(Opcode.EXIT_HOLD)
        self._require_success(response, StatusCode.OK, payload_length=0)
        status = await self.get_status()
        if status.device_state is not DeviceState.IDLE or status.rf_output_enabled:
            raise SafetyStateUnknown("EXIT_HOLD response was not confirmed by safe IDLE status")
        return status

    async def enter_hold(self) -> DeviceStatus:
        response = await self._request(Opcode.ENTER_HOLD)
        self._require_success(response, StatusCode.OK, payload_length=0)
        status = await self.get_status()
        if status.device_state is not DeviceState.HOLD or status.rf_output_enabled:
            self._mark_unknown("HOLD/RF-off confirmation failed")
            raise SafetyStateUnknown("HOLD and RF-off were not confirmed")
        return status

    async def start_point(self, request: StartPoint) -> PointTransaction:
        payload = request.encode()
        sequence = self._allocate_sequence()
        event_queue = self._event_queues.setdefault(sequence, asyncio.Queue())
        self._point_ids[sequence] = (request.measurement_id, request.point_index)
        try:
            response = await self._request(
                Opcode.START_POINT, payload, sequence=sequence, retries=1
            )
        except ResponseTimeout as exc:
            try:
                status = await self.get_status()
                if self._status_matches_active(status, request):
                    return PointTransaction(
                        sequence=sequence,
                        request=request,
                        acknowledgement=None,
                        acceptance_recovered=True,
                    )
                if self._status_matches_last(status, request):
                    result = await self.read_last_result(expected=request)
                    return PointTransaction(
                        sequence=sequence,
                        request=request,
                        acknowledgement=None,
                        acceptance_recovered=True,
                        recovered_result=result,
                    )
            except (SessionError, TransportError) as recovery_error:
                self._cleanup_point(sequence)
                raise MeasurementUnknown(
                    "START_POINT acknowledgement and recovery evidence are unavailable",
                    sequence=sequence,
                    measurement_id=request.measurement_id,
                    point_index=request.point_index,
                ) from recovery_error
            self._cleanup_point(sequence)
            raise MeasurementUnknown(
                "START_POINT acknowledgement timed out and device status proves no matching point",
                sequence=sequence,
                measurement_id=request.measurement_id,
                point_index=request.point_index,
            ) from exc
        try:
            self._require_success(response, StatusCode.ACCEPTED, payload_length=_IDENTITY.size)
            if _IDENTITY.unpack(response.payload) != (
                request.measurement_id,
                request.point_index,
            ):
                raise CorrelationError(
                    "START_POINT acknowledgement identity does not match request"
                )
        except Exception:
            self._cleanup_point(sequence)
            raise
        # Keep the queue alive even if an event arrived before the acknowledgement.
        self._event_queues[sequence] = event_queue
        return PointTransaction(sequence=sequence, request=request, acknowledgement=response)

    async def wait_point_result(
        self, transaction: PointTransaction, *, timeout_s: float | None = None
    ) -> PointResult:
        if transaction.recovered_result is not None:
            self._cleanup_point(transaction.sequence)
            validate_point_result(transaction.recovered_result, transaction.request)
            return transaction.recovered_result
        if timeout_s is None:
            timeout_s = self.result_timeout_s
        if timeout_s is None:
            timeout_s = transaction.request.max_duration_ms / 1000
        queue = self._event_queues.setdefault(transaction.sequence, asyncio.Queue())
        try:
            item = await asyncio.wait_for(queue.get(), timeout=timeout_s)
        except TimeoutError:
            try:
                return await self._recover_result(transaction)
            finally:
                self._cleanup_point(transaction.sequence)
        except asyncio.CancelledError:
            self._cleanup_point(transaction.sequence)
            raise
        try:
            if isinstance(item, SessionError):
                raise item
            return self._decode_final_event(item, transaction)
        finally:
            self._cleanup_point(transaction.sequence)

    async def measure_point(
        self, request: StartPoint, *, timeout_s: float | None = None
    ) -> PointResult:
        transaction = await self.start_point(request)
        return await self.wait_point_result(transaction, timeout_s=timeout_s)

    async def read_last_result(self, *, expected: StartPoint | None = None) -> PointResult:
        response = await self._request(Opcode.READ_LAST_RESULT)
        self._require_success(response, StatusCode.OK, payload_length=80)
        result = PointResult.decode(response.payload)
        validate_point_result(result, expected)
        return result

    async def cancel(self, measurement_id: int, point_index: int) -> DeviceStatus:
        payload = _IDENTITY.pack(measurement_id, point_index)
        response = await self._request(Opcode.CANCEL, payload)
        self._require_success(response, StatusCode.OK, payload_length=_IDENTITY.size)
        if response.payload != payload:
            raise CorrelationError("CANCEL acknowledgement identity does not match request")
        status = await self.get_status()
        if (
            status.rf_output_enabled
            or status.device_state is DeviceState.BUSY
            or status.active_measurement_id != 0
        ):
            self._mark_unknown("CANCEL did not confirm RF-off and inactive status")
            raise SafetyStateUnknown("CANCEL safety closure was not confirmed")
        return status

    async def disconnect(self) -> DeviceStatus | None:
        if not self.transport.is_open:
            if self.connection_state is not ConnectionState.DISCONNECTED:
                self._mark_unknown("transport closed before HOLD/RF-off confirmation")
                raise SafetyStateUnknown(self.unknown_reason)
            self.connection_state = ConnectionState.DISCONNECTED
            return None
        try:
            status = await self.enter_hold()
        except Exception as exc:
            await self._close_transport()
            self._mark_unknown("disconnect could not confirm HOLD/RF-off")
            raise SafetyStateUnknown("disconnect could not confirm HOLD/RF-off") from exc
        await self._close_transport()
        self.connection_state = ConnectionState.DISCONNECTED
        self.unknown_reason = None
        return status

    async def close(self) -> DeviceStatus | None:
        return await self.disconnect()

    async def abort(self, reason: str = "transport closed without HOLD confirmation") -> None:
        await self._close_transport()
        self._mark_unknown(reason)

    async def __aenter__(self) -> ProtocolSession:
        await self.open()
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if exc is None:
            await self.disconnect()
        else:
            await self.abort("session exited after an error without HOLD confirmation")

    async def _request(
        self,
        opcode: int,
        payload: bytes = b"",
        *,
        sequence: int | None = None,
        retries: int = 1,
    ) -> Frame:
        if not self.is_open:
            raise SessionNotOpen("PVNA-Link session is not open")
        if retries not in {0, 1}:
            raise ValueError("V0.1 permits at most one identical-frame retransmission")
        if sequence is None:
            sequence = self._allocate_sequence()
        request = Frame(
            message_class=MessageClass.REQUEST,
            opcode=int(opcode),
            sequence=sequence,
            payload=bytes(payload),
            flags=1,
        )
        wire = request.encode()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Frame] = loop.create_future()
        pending = _PendingResponse(opcode=int(opcode), future=future)
        async with self._command_lock:
            self._pending[sequence] = pending
            try:
                for attempt in range(retries + 1):
                    await self.transport.write(wire)
                    try:
                        return await asyncio.wait_for(
                            asyncio.shield(future), timeout=self.response_timeout_s
                        )
                    except TimeoutError:
                        if attempt == retries:
                            raise ResponseTimeout(int(opcode), sequence) from None
            finally:
                self._pending.pop(sequence, None)
        raise AssertionError("unreachable")

    async def _recover_result(self, transaction: PointTransaction) -> PointResult:
        try:
            status = await self.get_status()
            if self._status_matches_last(status, transaction.request):
                return await self.read_last_result(expected=transaction.request)
        except (SessionError, TransportError) as exc:
            raise MeasurementUnknown(
                "result event timed out and recovery evidence is unavailable",
                sequence=transaction.sequence,
                measurement_id=transaction.request.measurement_id,
                point_index=transaction.request.point_index,
            ) from exc
        raise MeasurementUnknown(
            "result event timed out and no matching latched result exists",
            sequence=transaction.sequence,
            measurement_id=transaction.request.measurement_id,
            point_index=transaction.request.point_index,
        )

    def _decode_final_event(self, event: Frame, transaction: PointTransaction) -> PointResult:
        if event.sequence != transaction.sequence:
            raise CorrelationError("point event sequence does not match START_POINT")
        if event.flags:
            raise SessionProtocolError("event frame has non-zero flags")
        if event.opcode == Opcode.POINT_RESULT:
            if event.status != StatusCode.OK:
                raise SessionProtocolError("POINT_RESULT event status must be OK")
            result = PointResult.decode(event.payload)
            validate_point_result(result, transaction.request)
            return result
        if event.opcode == Opcode.POINT_FAILED:
            if event.status == StatusCode.OK:
                raise SessionProtocolError("POINT_FAILED event cannot have OK status")
            failure = PointFailure.decode(event.payload)
            if (failure.measurement_id, failure.point_index) != (
                transaction.request.measurement_id,
                transaction.request.point_index,
            ):
                raise CorrelationError("POINT_FAILED identity does not match START_POINT")
            if failure.error != event.status:
                raise SessionProtocolError("POINT_FAILED payload error does not match event status")
            raise MeasurementFailed(failure)
        if event.opcode == Opcode.DEVICE_FAULT:
            raise MeasurementUnknown(
                "device fault interrupted the active measurement",
                sequence=transaction.sequence,
                measurement_id=transaction.request.measurement_id,
                point_index=transaction.request.point_index,
            )
        raise SessionProtocolError(f"unexpected point event opcode 0x{event.opcode:02X}")

    @staticmethod
    def _status_matches_active(status: DeviceStatus, request: StartPoint) -> bool:
        return (
            status.device_state is DeviceState.BUSY
            and status.active_measurement_id == request.measurement_id
            and status.active_point_index == request.point_index
        )

    @staticmethod
    def _status_matches_last(status: DeviceStatus, request: StartPoint) -> bool:
        return (
            status.last_result_valid
            and status.last_measurement_id == request.measurement_id
            and status.last_point_index == request.point_index
        )

    @staticmethod
    def _require_success(response: Frame, status: int, *, payload_length: int) -> None:
        if response.status != status:
            raise CommandRejected(response)
        if len(response.payload) != payload_length:
            raise SessionProtocolError(
                f"opcode 0x{response.opcode:02X} response payload must be "
                f"exactly {payload_length} bytes"
            )

    async def _reader_loop(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            while True:
                chunk = await self.transport.read(4096)
                now = loop.time()
                if chunk:
                    self._last_byte_at = now
                    for frame in self._parser.feed(chunk):
                        self._dispatch(frame)
                elif (
                    self._last_byte_at is not None
                    and now - self._last_byte_at >= self.frame_timeout_s
                ):
                    self._reset_stream_parser()
                    self._last_byte_at = None
        except asyncio.CancelledError:
            raise
        except (TransportClosed, TransportError) as exc:
            if not self._closing:
                self._fail_pending(exc)
                self._mark_unknown(f"transport disconnected: {exc}")
        except Exception as exc:
            if not self._closing:
                self._fail_pending(exc)
                self._mark_unknown(f"reader failed: {exc}")

    def _dispatch(self, frame: Frame) -> None:
        if frame.message_class is MessageClass.RESPONSE:
            self._dispatch_response(frame)
            return
        if frame.message_class is MessageClass.EVENT:
            self._dispatch_event(frame)

    def _dispatch_response(self, frame: Frame) -> None:
        pending = self._pending.get(frame.sequence)
        if pending is None:
            candidates = [item for item in self._pending.values() if item.opcode == frame.opcode]
            if len(candidates) == 1:
                error = CorrelationError(
                    f"response sequence {frame.sequence} does not match pending request"
                )
                self.correlation_errors.append(str(error))
                if not candidates[0].future.done():
                    candidates[0].future.set_exception(error)
            return
        if frame.opcode != pending.opcode:
            error = CorrelationError("response opcode does not match pending request")
            self.correlation_errors.append(str(error))
            if not pending.future.done():
                pending.future.set_exception(error)
            return
        if frame.flags & ~REPLAYED_RESPONSE:
            error = SessionProtocolError("response frame has invalid flags")
            if not pending.future.done():
                pending.future.set_exception(error)
            return
        if not pending.future.done():
            pending.future.set_result(frame)

    def _dispatch_event(self, frame: Frame) -> None:
        if frame.opcode in {Opcode.POINT_RESULT, Opcode.POINT_FAILED}:
            queue = self._event_queues.get(frame.sequence)
            if queue is None and len(frame.payload) >= _IDENTITY.size:
                identity = _IDENTITY.unpack_from(frame.payload)
                matches = [
                    sequence
                    for sequence, expected in self._point_ids.items()
                    if expected == identity
                ]
                if len(matches) == 1:
                    error = CorrelationError(
                        f"point event sequence {frame.sequence} does not match START_POINT"
                    )
                    self.correlation_errors.append(str(error))
                    self._event_queues[matches[0]].put_nowait(error)
                    return
            self._event_queues.setdefault(frame.sequence, asyncio.Queue()).put_nowait(frame)
            return
        self._event_queues.setdefault(frame.sequence, asyncio.Queue()).put_nowait(frame)

    def _allocate_sequence(self) -> int:
        value = self._next_sequence_value
        self._next_sequence_value = 1 if value == 0xFFFFFFFF else value + 1
        return value

    def _cleanup_point(self, sequence: int) -> None:
        self._event_queues.pop(sequence, None)
        self._point_ids.pop(sequence, None)

    def _reset_stream_parser(self) -> None:
        self._completed_parser_crc_errors += self._parser.crc_errors
        self._completed_parser_discarded += self._parser.discarded_bytes
        self._parser = StreamParser()

    def _fail_pending(self, exc: BaseException) -> None:
        for pending in self._pending.values():
            if not pending.future.done():
                pending.future.set_exception(SessionError(str(exc)))
        for sequence, queue in self._event_queues.items():
            measurement_id, point_index = self._point_ids.get(sequence, (None, None))
            queue.put_nowait(
                MeasurementUnknown(
                    f"transport disconnected: {exc}",
                    sequence=sequence,
                    measurement_id=measurement_id,
                    point_index=point_index,
                )
            )

    def _mark_unknown(self, reason: str) -> None:
        self.connection_state = ConnectionState.UNKNOWN
        self.unknown_reason = reason

    async def _close_transport(self) -> None:
        self._closing = True
        try:
            await self.transport.close()
        finally:
            reader, self._reader_task = self._reader_task, None
            if reader is not None:
                reader.cancel()
                try:
                    await reader
                except asyncio.CancelledError:
                    pass
            self._closing = False
