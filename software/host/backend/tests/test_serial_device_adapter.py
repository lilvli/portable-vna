from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pvna_host.domain import RunManager, RunState, RunStore, SweepPlan
from pvna_host.domain.calibration_manager import CalibrationManager
from pvna_host.domain.device import MeasurementResultUnknown
from pvna_host.domain.serial_device import SerialDeviceAdapter
from pvna_host.protocol import Frame, MessageClass, Opcode, PointResult, StartPoint
from pvna_host.protocol.session import ProtocolSession
from pvna_host.transport import FakeTransport, VirtualPvnaDevice


async def inject_active_result(
    transport: FakeTransport,
    device: VirtualPvnaDevice,
    *,
    result_flags: int,
    r_i_acc: int = 100,
    r_q_acc: int = 10,
    a_i_acc: int = 25,
    a_q_acc: int = -5,
) -> None:
    for _ in range(100):
        if device.active is not None:
            break
        await asyncio.sleep(0.001)
    assert device.active is not None
    start_wire = next(
        wire
        for wire in reversed(transport.writes)
        if Frame.decode(wire).opcode == Opcode.START_POINT
    )
    request_frame = Frame.decode(start_wire)
    request = device.active
    result = PointResult(
        request.measurement_id,
        request.point_index,
        request.frequency_hz,
        request.frequency_hz,
        r_i_acc,
        r_q_acc,
        a_i_acc,
        a_q_acc,
        request.integration_count,
        0,
        result_flags,
        123,
        1100,
    )
    await transport.inject_rx(
        Frame(
            message_class=MessageClass.EVENT,
            opcode=Opcode.POINT_RESULT,
            sequence=request_frame.sequence,
            payload=result.encode(),
        ).encode()
    )


@pytest.mark.asyncio
async def test_serial_domain_adapter_completes_offline_run_with_exact_raw_values(
    tmp_path: Path,
) -> None:
    transport = FakeTransport(read_timeout_s=0.001)
    VirtualPvnaDevice(transport, chunk_pattern=(1, 3, 7))

    def serial_factory(_: str, __: int) -> SerialDeviceAdapter:
        return SerialDeviceAdapter(
            ProtocolSession(
                transport,
                sequence_seed=200,
                response_timeout_s=0.05,
                result_timeout_s=0.05,
            )
        )

    manager = RunManager(RunStore(tmp_path / "runs"), serial_factory=serial_factory)
    connected = await manager.connect("serial", port="FAKE-COM", baud_rate=115_200)
    assert connected.source is not None and connected.source.value == "HARDWARE"
    assert connected.state.value == "HOLD"

    record = await manager.start_sweep(SweepPlan(5_000_000, 5_000_000, 1))
    finished = await manager.wait(record.run_id)

    assert finished.state is RunState.COMPLETED
    assert finished.safe_hold_confirmed
    point = manager.get_points(record.run_id)[0]
    assert point["r_i_acc"] == str((1 << 60) + 5)
    assert point["fpga_timestamp_ticks"] == str((1 << 56) + 33)

    disconnected = await manager.disconnect()
    assert not disconnected.connected
    assert disconnected.state.value == "DISCONNECTED"


@pytest.mark.asyncio
async def test_serial_adapter_prepares_standard_role_without_changing_wire() -> None:
    transport = FakeTransport(read_timeout_s=0.001)
    adapter = SerialDeviceAdapter(ProtocolSession(transport, response_timeout_s=0.05))

    await adapter.prepare_plan(
        SweepPlan(
            5_000_000,
            5_000_000,
            1,
            measurement_role="open",
            simulation_profile="load",
        )
    )

    assert not transport.is_open
    assert transport.writes == []


@pytest.mark.asyncio
async def test_start_ack_unknown_maps_to_result_unknown_with_correlation() -> None:
    transport = FakeTransport(read_timeout_s=0.001)
    device = VirtualPvnaDevice(transport, auto_complete=False)
    adapter = SerialDeviceAdapter(
        ProtocolSession(transport, sequence_seed=300, response_timeout_s=0.005)
    )
    await adapter.connect()
    await adapter.exit_hold()
    device.drop_next_response(Opcode.START_POINT, count=2)
    device.drop_next_response(Opcode.GET_STATUS, count=2)
    request = StartPoint(71, 4, 5_000_000, 8192, 1, 1000, 65536, 100)

    with pytest.raises(MeasurementResultUnknown) as captured:
        await adapter.start_point(request)

    error = captured.value
    assert error.sequence != 0  # type: ignore[attr-defined]
    assert error.measurement_id == 71  # type: ignore[attr-defined]
    assert error.point_index == 4  # type: ignore[attr-defined]
    assert "START_POINT acknowledgement and recovery evidence" in str(error)
    confirmed = await adapter.enter_hold()
    assert confirmed.state.value == "HOLD"


@pytest.mark.asyncio
async def test_start_ack_unknown_keeps_run_unknown_after_safe_hold(tmp_path: Path) -> None:
    transport = FakeTransport(read_timeout_s=0.001)
    device = VirtualPvnaDevice(transport, auto_complete=False)
    manager = RunManager(
        RunStore(tmp_path / "runs"),
        serial_factory=lambda _port, _baud: SerialDeviceAdapter(
            ProtocolSession(transport, sequence_seed=400, response_timeout_s=0.02)
        ),
    )
    await manager.connect("serial", port="FAKE-COM")
    device.drop_next_response(Opcode.START_POINT, count=2)
    receive = transport.on_write
    status_drop_armed = False

    async def drop_recovery_status(wire: bytes) -> None:
        nonlocal status_drop_armed
        request = Frame.decode(wire)
        if request.opcode == Opcode.START_POINT and not status_drop_armed:
            status_drop_armed = True
            device.drop_next_response(Opcode.GET_STATUS, count=2)
        assert receive is not None
        result = receive(wire)
        if asyncio.iscoroutine(result):
            await result

    transport.on_write = drop_recovery_status

    record = await manager.start_sweep(SweepPlan(5_000_000, 5_000_000, 1))
    finished = await manager.wait(record.run_id)

    assert finished.state is RunState.UNKNOWN
    assert finished.safe_hold_confirmed
    assert "sequence=" in (finished.error or "")
    assert "measurement_id=" in (finished.error or "")
    assert "point_index=" in (finished.error or "")
    await manager.disconnect()


@pytest.mark.asyncio
async def test_fatal_result_is_rejected_before_save_or_progress(tmp_path: Path) -> None:
    transport = FakeTransport(read_timeout_s=0.001)
    device = VirtualPvnaDevice(transport, auto_complete=False)
    manager = RunManager(
        RunStore(tmp_path / "runs"),
        serial_factory=lambda _port, _baud: SerialDeviceAdapter(
            ProtocolSession(transport, response_timeout_s=0.05, result_timeout_s=0.2)
        ),
    )
    await manager.connect("serial", port="FAKE-COM")

    record = await manager.start_sweep(SweepPlan(5_000_000, 5_000_000, 1))
    await inject_active_result(transport, device, result_flags=0x0010)
    finished = await manager.wait(record.run_id)

    assert finished.state is RunState.FAILED
    assert finished.confirmed_points == 0
    assert manager.get_points(record.run_id) == []
    assert "invalid measurement flags" in (finished.error or "")
    assert finished.safe_hold_confirmed
    await manager.disconnect()


@pytest.mark.asyncio
async def test_clipping_quality_flags_are_saved_without_becoming_fatal(tmp_path: Path) -> None:
    transport = FakeTransport(read_timeout_s=0.001)
    device = VirtualPvnaDevice(transport, auto_complete=False)
    manager = RunManager(
        RunStore(tmp_path / "runs"),
        serial_factory=lambda _port, _baud: SerialDeviceAdapter(
            ProtocolSession(transport, response_timeout_s=0.05, result_timeout_s=0.2)
        ),
    )
    await manager.connect("serial", port="FAKE-COM")

    record = await manager.start_sweep(SweepPlan(5_000_000, 5_000_000, 1))
    await inject_active_result(transport, device, result_flags=0x0003)
    finished = await manager.wait(record.run_id)

    assert finished.state is RunState.COMPLETED
    assert finished.confirmed_points == 1
    assert manager.get_points(record.run_id)[0]["result_flags"] == 0x0003
    assert finished.safe_hold_confirmed
    await manager.disconnect()


@pytest.mark.asyncio
async def test_single_fake_serial_captures_roles_and_builds_sol_calibration(
    tmp_path: Path,
) -> None:
    transport = FakeTransport(read_timeout_s=0.001)
    device = VirtualPvnaDevice(transport, auto_complete=False)
    manager = RunManager(
        RunStore(tmp_path / "runs"),
        serial_factory=lambda _port, _baud: SerialDeviceAdapter(
            ProtocolSession(transport, response_timeout_s=0.05, result_timeout_s=0.2)
        ),
    )
    await manager.connect("serial", port="FAKE-COM")
    measured = {
        "open": (800, 50),
        "short": (-700, 20),
        "load": (100, -10),
        "dut": (300, 100),
    }
    runs: dict[str, str] = {}

    for role in ("open", "short", "load", "dut"):
        record = await manager.start_sweep(
            SweepPlan(
                5_000_000,
                5_000_000,
                1,
                measurement_role=role,
                simulation_profile="dut",
            )
        )
        a_i_acc, a_q_acc = measured[role]
        await inject_active_result(
            transport,
            device,
            result_flags=0,
            r_i_acc=1000,
            r_q_acc=0,
            a_i_acc=a_i_acc,
            a_q_acc=a_q_acc,
        )
        finished = await manager.wait(record.run_id)
        assert finished.state is RunState.COMPLETED
        assert finished.data_validation == "VALID"
        assert finished.plan.measurement_role == role
        runs[role] = record.run_id

    calibrations = CalibrationManager(manager, tmp_path / "calibrations")
    created = calibrations.create(
        open_run_id=runs["open"],
        short_run_id=runs["short"],
        load_run_id=runs["load"],
    )
    trace = calibrations.trace(runs["dut"], str(created["calibration_id"]))

    assert created["source"] == "HARDWARE"
    assert created["standard_runs"] == {
        "open": runs["open"],
        "short": runs["short"],
        "load": runs["load"],
    }
    assert trace["data_kind"] == "CALIBRATED"
    assert len(trace["points"]) == 1
    await manager.disconnect()


@pytest.mark.asyncio
async def test_serial_domain_cancel_finishes_cancelled_and_in_hold(tmp_path: Path) -> None:
    transport = FakeTransport(read_timeout_s=0.001)
    VirtualPvnaDevice(transport, auto_complete=False)
    manager = RunManager(
        RunStore(tmp_path / "runs"),
        serial_factory=lambda _port, _baud: SerialDeviceAdapter(
            ProtocolSession(transport, response_timeout_s=0.05, result_timeout_s=0.2)
        ),
    )
    await manager.connect("serial", port="FAKE-COM")
    record = await manager.start_sweep(SweepPlan(5_000_000, 5_000_000, 1))
    for _ in range(100):
        if record.run_id in manager._active_handle:  # white-box synchronization for cancellation
            break
        await asyncio.sleep(0.001)
    await manager.cancel_run(record.run_id)

    finished = await manager.wait(record.run_id)
    assert finished.state is RunState.CANCELLED
    assert finished.safe_hold_confirmed
    assert (await manager.get_device_status()).state.value == "HOLD"
    await manager.disconnect()


@pytest.mark.asyncio
async def test_unprovable_serial_point_is_unknown_even_after_safe_hold(tmp_path: Path) -> None:
    transport = FakeTransport(read_timeout_s=0.001)
    VirtualPvnaDevice(transport, auto_complete=False)
    manager = RunManager(
        RunStore(tmp_path / "runs"),
        serial_factory=lambda _port, _baud: SerialDeviceAdapter(
            ProtocolSession(transport, response_timeout_s=0.02, result_timeout_s=0.005)
        ),
    )
    await manager.connect("serial", port="FAKE-COM")
    record = await manager.start_sweep(SweepPlan(5_000_000, 5_000_000, 1))

    finished = await manager.wait(record.run_id)

    assert finished.state is RunState.UNKNOWN
    assert finished.safe_hold_confirmed
    assert "UNKNOWN_RESULT" in (finished.error or "")
    await manager.disconnect()
