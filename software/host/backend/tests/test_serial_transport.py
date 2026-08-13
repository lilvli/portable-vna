from __future__ import annotations

from unittest.mock import Mock

import pytest

from pvna_host.transport import FakeTransport, SerialTransport, TransportClosed


class StubSerial:
    def __init__(self, *, read_data: bytes = b"", **settings: object) -> None:
        self.settings = settings
        self.is_open = True
        self.read_data = bytearray(read_data)
        self.writes: list[bytes] = []

    def open(self) -> None:
        self.is_open = True

    def close(self) -> None:
        self.is_open = False

    def write(self, data: bytes) -> int:
        self.writes.append(bytes(data))
        return len(data)

    def read(self, size: int) -> bytes:
        result = bytes(self.read_data[:size])
        del self.read_data[:size]
        return result


def test_serial_transport_constructor_has_zero_resource_access() -> None:
    factory = Mock(side_effect=AssertionError("factory must not run during construction"))
    transport = SerialTransport("COM42", serial_factory=factory)

    assert not transport.is_open
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_serial_transport_opens_only_when_explicitly_requested_with_8n1() -> None:
    created: list[StubSerial] = []

    def factory(**settings: object) -> StubSerial:
        serial_port = StubSerial(read_data=b"rx", **settings)
        created.append(serial_port)
        return serial_port

    transport = SerialTransport("COM42", baudrate=921_600, serial_factory=factory)
    await transport.open()

    assert transport.is_open
    assert created[0].settings == {
        "port": "COM42",
        "baudrate": 921_600,
        "bytesize": 8,
        "parity": "N",
        "stopbits": 1,
        "timeout": 0.05,
        "write_timeout": 0.5,
        "xonxoff": False,
        "rtscts": False,
        "dsrdtr": False,
    }
    await transport.write(b"tx")
    assert created[0].writes == [b"tx"]
    assert await transport.read() == b"rx"
    await transport.close()
    assert not transport.is_open


def test_serial_transport_rejects_implicit_or_unfrozen_configuration() -> None:
    with pytest.raises(ValueError, match="explicit serial port"):
        SerialTransport("")
    with pytest.raises(ValueError, match="115200 or 921600"):
        SerialTransport("COM42", baudrate=9600)


@pytest.mark.asyncio
async def test_fake_transport_is_a_split_byte_memory_fixture() -> None:
    fake = FakeTransport(read_timeout_s=0.001)
    await fake.open()
    await fake.inject_rx(b"abcdef", chunks=(1, 2))
    assert await fake.read(2) == b"a"
    assert await fake.read(2) == b"bc"
    assert await fake.read(2) == b"d"
    await fake.write(b"host")
    assert fake.writes == [b"host"]
    await fake.close()
    with pytest.raises(TransportClosed):
        await fake.read()
