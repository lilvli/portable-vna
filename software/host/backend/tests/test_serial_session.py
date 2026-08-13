from __future__ import annotations

import asyncio
import struct

import pytest

from pvna_host.protocol import Frame, MessageClass, Opcode, PointResult, StartPoint
from pvna_host.protocol.session import (
    ConnectionState,
    CorrelationError,
    DeviceState,
    MeasurementUnknown,
    ProtocolSession,
    SafetyStateUnknown,
    SessionProtocolError,
    validate_point_result,
)
from pvna_host.transport import FakeTransport, VirtualPvnaDevice


def point(
    *, measurement_id: int = 7, point_index: int = 3, max_duration_ms: int = 30
) -> StartPoint:
    return StartPoint(
        measurement_id,
        point_index,
        50_000_001,
        8192,
        1,
        1000,
        65536,
        max_duration_ms,
    )


def point_result(
    request: StartPoint,
    *,
    result_flags: int = 0,
    r_i_acc: int = 100,
    r_q_acc: int = 10,
    integration_count: int | None = None,
    duration_us: int = 1100,
) -> PointResult:
    return PointResult(
        measurement_id=request.measurement_id,
        point_index=request.point_index,
        requested_frequency_hz=request.frequency_hz,
        actual_frequency_hz=request.frequency_hz,
        r_i_acc=r_i_acc,
        r_q_acc=r_q_acc,
        a_i_acc=25,
        a_q_acc=-5,
        integration_count=(
            request.integration_count if integration_count is None else integration_count
        ),
        accumulator_right_shift=0,
        result_flags=result_flags,
        fpga_timestamp_ticks=123,
        duration_us=duration_us,
    )


async def opened_pair(
    *,
    seed: int = 100,
    auto_complete: bool = True,
    event_delay_s: float = 0.0,
    chunk_pattern: tuple[int, ...] | None = (1, 3, 2, 7),
    response_timeout_s: float = 0.1,
) -> tuple[ProtocolSession, FakeTransport, VirtualPvnaDevice]:
    transport = FakeTransport(read_timeout_s=0.001)
    device = VirtualPvnaDevice(
        transport,
        auto_complete=auto_complete,
        event_delay_s=event_delay_s,
        chunk_pattern=chunk_pattern,
    )
    session = ProtocolSession(
        transport,
        sequence_seed=seed,
        response_timeout_s=response_timeout_s,
        result_timeout_s=0.02,
    )
    await session.open()
    return session, transport, device


@pytest.mark.asyncio
async def test_preflight_handles_noise_split_and_sticky_frames_with_exact_u64() -> None:
    session, transport, device = await opened_pair(seed=0xFFFFFFFE)
    await device.emit_noise(b"garbagePV\x00not-a-frame")

    preflight = await session.preflight(required_link_flags=0x1F)

    assert preflight.info.device_id == (1 << 60) + 17
    assert preflight.info.to_api_dict()["device_id"] == str((1 << 60) + 17)
    assert preflight.status.uptime_ms == (1 << 55) + 123
    assert preflight.status.to_api_dict()["uptime_ms"] == str((1 << 55) + 123)
    request_sequences = [Frame.decode(wire).sequence for wire in transport.writes]
    assert request_sequences == [0xFFFFFFFE, 0xFFFFFFFF, 1]
    assert all(sequence != 0 for sequence in request_sequences)
    assert session.discarded_bytes >= len(b"garbage")
    await session.abort("offline test complete")


@pytest.mark.asyncio
async def test_crc_bad_response_is_dropped_and_identical_wire_frame_retried() -> None:
    session, transport, device = await opened_pair(seed=11)
    device.corrupt_next_response(Opcode.PING)

    await session.ping()

    assert len(transport.writes) == 2
    assert transport.writes[0] == transport.writes[1]
    assert session.crc_errors == 1
    await session.abort("offline test complete")


@pytest.mark.asyncio
async def test_start_ack_is_separate_from_final_result_and_side_effect_is_deduplicated() -> None:
    session, transport, device = await opened_pair(seed=20, event_delay_s=0.2)
    await session.exit_hold()
    device.drop_next_response(Opcode.START_POINT)

    transaction = await session.start_point(point())

    start_writes = [
        wire for wire in transport.writes if Frame.decode(wire).opcode == Opcode.START_POINT
    ]
    assert transaction.acknowledgement is not None
    assert transaction.acknowledgement.status == 1  # ACCEPTED, not measurement completion
    assert transaction.acknowledgement.flags & 0x0002  # cached replay response
    assert len(start_writes) == 2
    assert start_writes[0] == start_writes[1]
    assert device.start_execution_count == 1

    result = await session.wait_point_result(transaction, timeout_s=0.3)
    assert isinstance(result, PointResult)
    assert result.r_i_acc == (1 << 60) + 5
    assert result.fpga_timestamp_ticks == (1 << 56) + 33
    assert result.to_api_dict()["r_i_acc"] == str((1 << 60) + 5)
    await session.abort("offline test complete")


@pytest.mark.asyncio
async def test_event_before_ack_is_retained_and_correlated() -> None:
    session, _, device = await opened_pair(seed=30, event_delay_s=0)
    await session.exit_hold()
    device.drop_next_response(Opcode.START_POINT)

    transaction = await session.start_point(point())
    result = await session.wait_point_result(transaction)

    assert result.measurement_id == 7
    assert result.point_index == 3
    await session.abort("offline test complete")


@pytest.mark.asyncio
async def test_lost_start_ack_recovers_active_point_without_new_sequence() -> None:
    session, transport, device = await opened_pair(
        seed=35,
        auto_complete=False,
        chunk_pattern=None,
        response_timeout_s=0.01,
    )
    await session.exit_hold()
    device.drop_next_response(Opcode.START_POINT, count=2)

    transaction = await session.start_point(point())

    start_writes = [
        wire for wire in transport.writes if Frame.decode(wire).opcode == Opcode.START_POINT
    ]
    assert transaction.acknowledgement is None
    assert transaction.acceptance_recovered
    assert len(start_writes) == 2
    assert start_writes[0] == start_writes[1]
    assert device.start_execution_count == 1
    await session.cancel(7, 3)
    await session.abort("offline test complete")


@pytest.mark.asyncio
async def test_wrong_response_sequence_fails_the_pending_request() -> None:
    transport = FakeTransport(read_timeout_s=0.001)

    async def wrong_sequence(wire: bytes) -> None:
        request = Frame.decode(wire)
        response = Frame(
            message_class=MessageClass.RESPONSE,
            opcode=request.opcode,
            sequence=request.sequence + 1,
        )
        await transport.inject_rx(response.encode())

    transport.on_write = wrong_sequence
    session = ProtocolSession(transport, sequence_seed=40, response_timeout_s=0.01)
    await session.open()

    with pytest.raises(CorrelationError, match="does not match pending"):
        await session.ping()
    assert session.correlation_errors
    await session.abort("offline test complete")


@pytest.mark.asyncio
async def test_wrong_event_sequence_is_not_accepted_as_a_result() -> None:
    session, transport, device = await opened_pair(seed=50, auto_complete=False)
    await session.exit_hold()
    transaction = await session.start_point(point())
    fake_result = PointResult(
        7,
        3,
        50_000_001,
        50_000_001,
        1,
        2,
        3,
        4,
        65536,
        0,
        0,
        5,
        6,
    )
    wrong_event = Frame(
        message_class=MessageClass.EVENT,
        opcode=Opcode.POINT_RESULT,
        sequence=transaction.sequence + 1,
        payload=fake_result.encode(),
    )
    await transport.inject_rx(wrong_event.encode())

    with pytest.raises(CorrelationError, match="does not match START_POINT"):
        await session.wait_point_result(transaction)
    await session.abort("offline test complete")


@pytest.mark.asyncio
async def test_clipping_flags_are_allowed_and_retained_as_quality_warnings() -> None:
    session, transport, _ = await opened_pair(seed=55, auto_complete=False)
    await session.exit_hold()
    request = point()
    transaction = await session.start_point(request)
    clipped = point_result(request, result_flags=0x0003)
    await transport.inject_rx(
        Frame(
            message_class=MessageClass.EVENT,
            opcode=Opcode.POINT_RESULT,
            sequence=transaction.sequence,
            payload=clipped.encode(),
        ).encode()
    )

    accepted = await session.wait_point_result(transaction)

    assert accepted.result_flags == 0x0003
    await session.abort("offline test complete")


def test_public_result_admission_is_available_without_a_session() -> None:
    request = point()
    clipped = point_result(request, result_flags=0x0001)

    validate_point_result(clipped, request)

    with pytest.raises(SessionProtocolError, match="invalid measurement flags"):
        validate_point_result(point_result(request, result_flags=0x0004), request)


@pytest.mark.asyncio
@pytest.mark.parametrize("fatal_flag", [0x0004, 0x0008, 0x0010])
async def test_fatal_result_flags_are_rejected_before_return(fatal_flag: int) -> None:
    session, transport, _ = await opened_pair(seed=56 + fatal_flag, auto_complete=False)
    await session.exit_hold()
    request = point()
    transaction = await session.start_point(request)
    invalid = point_result(request, result_flags=fatal_flag)
    await transport.inject_rx(
        Frame(
            message_class=MessageClass.EVENT,
            opcode=Opcode.POINT_RESULT,
            sequence=transaction.sequence,
            payload=invalid.encode(),
        ).encode()
    )

    with pytest.raises(SessionProtocolError, match="invalid measurement flags"):
        await session.wait_point_result(transaction)
    await session.abort("offline test complete")


@pytest.mark.asyncio
async def test_unknown_result_flag_is_fail_closed() -> None:
    session, transport, _ = await opened_pair(seed=61, auto_complete=False)
    await session.exit_hold()
    request = point()
    transaction = await session.start_point(request)
    invalid = point_result(request, result_flags=0x0020)
    await transport.inject_rx(
        Frame(
            message_class=MessageClass.EVENT,
            opcode=Opcode.POINT_RESULT,
            sequence=transaction.sequence,
            payload=invalid.encode(),
        ).encode()
    )

    with pytest.raises(SessionProtocolError, match="unknown result_flags bits"):
        await session.wait_point_result(transaction)
    await session.abort("offline test complete")


@pytest.mark.asyncio
@pytest.mark.parametrize(("r_i_acc", "r_q_acc"), [(0, 0), (1, 0), (0, -1)])
async def test_zero_or_sub_lsb_reference_is_rejected_before_ratio_or_save_boundary(
    r_i_acc: int, r_q_acc: int
) -> None:
    session, transport, _ = await opened_pair(seed=62, auto_complete=False)
    await session.exit_hold()
    request = point()
    transaction = await session.start_point(request)
    invalid = point_result(request, r_i_acc=r_i_acc, r_q_acc=r_q_acc)
    await transport.inject_rx(
        Frame(
            message_class=MessageClass.EVENT,
            opcode=Opcode.POINT_RESULT,
            sequence=transaction.sequence,
            payload=invalid.encode(),
        ).encode()
    )

    with pytest.raises(SessionProtocolError, match="zero or below one accumulator LSB"):
        await session.wait_point_result(transaction)
    await session.abort("offline test complete")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"integration_count": 123}, "integration_count does not match"),
        ({"duration_us": 30_001}, "duration exceeds"),
    ],
)
async def test_frozen_start_point_result_constraints_are_enforced(
    overrides: dict[str, int], message: str
) -> None:
    session, transport, _ = await opened_pair(seed=63, auto_complete=False)
    await session.exit_hold()
    request = point(max_duration_ms=30)
    transaction = await session.start_point(request)
    invalid = point_result(request, **overrides)
    await transport.inject_rx(
        Frame(
            message_class=MessageClass.EVENT,
            opcode=Opcode.POINT_RESULT,
            sequence=transaction.sequence,
            payload=invalid.encode(),
        ).encode()
    )

    with pytest.raises(CorrelationError, match=message):
        await session.wait_point_result(transaction)
    await session.abort("offline test complete")


@pytest.mark.asyncio
async def test_partial_payload_is_rejected_without_false_success() -> None:
    session, _, device = await opened_pair(seed=60)
    device.override_response_payload(Opcode.GET_INFO, b"short")

    with pytest.raises(SessionProtocolError, match="exactly 64 bytes"):
        await session.get_info()
    await session.abort("offline test complete")


@pytest.mark.asyncio
async def test_lost_event_recovers_with_get_status_and_read_last_result() -> None:
    session, _, device = await opened_pair(seed=70, event_delay_s=0.001)
    await session.exit_hold()
    device.drop_next_event(Opcode.POINT_RESULT)

    result = await session.measure_point(point(max_duration_ms=10), timeout_s=0.01)

    assert result.measurement_id == 7
    assert result.point_index == 3
    assert device.last_result is not None
    await session.abort("offline test complete")


@pytest.mark.asyncio
async def test_unprovable_result_timeout_is_explicit_unknown() -> None:
    session, _, _ = await opened_pair(seed=80, auto_complete=False)
    await session.exit_hold()
    transaction = await session.start_point(point(max_duration_ms=5))

    with pytest.raises(MeasurementUnknown, match="no matching latched result"):
        await session.wait_point_result(transaction, timeout_s=0.005)
    await session.abort("offline test complete")


@pytest.mark.asyncio
async def test_cancel_and_disconnect_confirm_rf_off_and_hold() -> None:
    session, _, device = await opened_pair(seed=90, auto_complete=False)
    await session.exit_hold()
    await session.start_point(point())

    cancelled = await session.cancel(7, 3)
    assert cancelled.device_state is DeviceState.IDLE
    assert not cancelled.rf_output_enabled
    assert device.cancel_execution_count == 1

    hold = await session.disconnect()
    assert hold is not None
    assert hold.device_state is DeviceState.HOLD
    assert not hold.rf_output_enabled
    assert device.enter_hold_execution_count == 1
    assert session.connection_state is ConnectionState.DISCONNECTED


@pytest.mark.asyncio
async def test_disconnect_without_hold_confirmation_is_unknown() -> None:
    session, transport, _ = await opened_pair(seed=100, auto_complete=False)
    await session.exit_hold()
    await session.start_point(point())

    await transport.close()
    await asyncio.sleep(0)
    with pytest.raises(SafetyStateUnknown):
        await session.disconnect()
    assert session.connection_state is ConnectionState.UNKNOWN


@pytest.mark.asyncio
async def test_status_reserved_fields_are_validated() -> None:
    session, _, device = await opened_pair(seed=110)
    bad_status = bytearray(48)
    struct.pack_into("<BBBB", bad_status, 0, DeviceState.HOLD, 0, 0, 1)
    device.override_response_payload(Opcode.GET_STATUS, bytes(bad_status))

    with pytest.raises(SessionProtocolError, match="reserved field"):
        await session.get_status()
    await session.abort("offline test complete")
