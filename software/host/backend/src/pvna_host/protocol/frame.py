from __future__ import annotations

import binascii
import enum
import struct
from dataclasses import dataclass

MAGIC = b"PV"
VERSION_MAJOR = 0
VERSION_MINOR = 1
HEADER_SIZE = 20
CRC_SIZE = 4
MAX_PAYLOAD = 4096
RESPONSE_REQUIRED = 0x0001
REPLAYED_RESPONSE = 0x0002
ALLOWED_FLAGS = RESPONSE_REQUIRED | REPLAYED_RESPONSE

_HEADER = struct.Struct("<2sBBBBHHHII")
_CRC = struct.Struct("<I")


class ProtocolError(ValueError):
    """A complete frame violates the frozen PVNA-Link V0.1 contract."""


class MessageClass(enum.IntEnum):
    REQUEST = 0x01
    RESPONSE = 0x02
    EVENT = 0x03


class Opcode(enum.IntEnum):
    PING = 0x01
    GET_INFO = 0x02
    GET_STATUS = 0x03
    ENTER_HOLD = 0x04
    EXIT_HOLD = 0x05
    START_POINT = 0x10
    READ_LAST_RESULT = 0x11
    CANCEL = 0x12
    CLEAR_FAULT = 0x13
    POINT_RESULT = 0x80
    POINT_FAILED = 0x81
    DEVICE_FAULT = 0x82


class StatusCode(enum.IntEnum):
    OK = 0x0000
    ACCEPTED = 0x0001
    BAD_VERSION = 0x0101
    BAD_LENGTH = 0x0102
    BAD_FLAGS = 0x0103
    UNKNOWN_OPCODE = 0x0104
    INVALID_PARAM = 0x0105
    INVALID_STATE = 0x0106
    BUSY = 0x0107
    NOT_READY = 0x0108
    RESULT_NOT_FOUND = 0x0109
    TIMEOUT = 0x0201
    CANCELLED = 0x0202
    CLOCK_UNLOCKED = 0x0203
    JESD_NOT_READY = 0x0204
    HARDWARE_FAULT = 0x02FF
    DUPLICATE_MISMATCH = 0x0301
    INTERNAL_ERROR = 0x03FF


def crc32_iso_hdlc(data: bytes) -> int:
    """Return the CRC-32/ISO-HDLC value used by PVNA-Link."""

    return binascii.crc32(data) & 0xFFFFFFFF


@dataclass(frozen=True, slots=True)
class Frame:
    message_class: MessageClass
    opcode: int
    sequence: int
    payload: bytes = b""
    flags: int = 0
    status: int = 0
    version_major: int = VERSION_MAJOR
    version_minor: int = VERSION_MINOR

    def __post_init__(self) -> None:
        if not 0 <= self.opcode <= 0xFF:
            raise ProtocolError("opcode must fit in u8")
        if not 0 <= self.sequence <= 0xFFFFFFFF:
            raise ProtocolError("sequence must fit in u32")
        if len(self.payload) > MAX_PAYLOAD:
            raise ProtocolError("payload exceeds 4096 bytes")
        if self.flags & ~ALLOWED_FLAGS:
            raise ProtocolError("reserved flag bit is set")
        if self.message_class is MessageClass.REQUEST:
            if self.sequence == 0:
                raise ProtocolError("request sequence must be non-zero")
            if not self.flags & RESPONSE_REQUIRED:
                raise ProtocolError("request must set RESPONSE_REQUIRED")
            if self.status != 0:
                raise ProtocolError("request status must be zero")

    def encode(self) -> bytes:
        header = _HEADER.pack(
            MAGIC,
            self.version_major,
            self.version_minor,
            int(self.message_class),
            self.opcode,
            self.flags,
            self.status,
            HEADER_SIZE,
            self.sequence,
            len(self.payload),
        )
        body = header + self.payload
        return body + _CRC.pack(crc32_iso_hdlc(body))

    @classmethod
    def decode(cls, data: bytes) -> Frame:
        if len(data) < HEADER_SIZE + CRC_SIZE:
            raise ProtocolError("frame is incomplete")
        fields = _HEADER.unpack_from(data)
        magic, major, minor, msg_class, opcode, flags, status, header_size, sequence, size = fields
        if magic != MAGIC:
            raise ProtocolError("bad magic")
        if (major, minor) != (VERSION_MAJOR, VERSION_MINOR):
            raise ProtocolError("unsupported protocol version")
        if header_size != HEADER_SIZE:
            raise ProtocolError("bad header size")
        if size > MAX_PAYLOAD:
            raise ProtocolError("payload exceeds 4096 bytes")
        expected_size = HEADER_SIZE + size + CRC_SIZE
        if len(data) != expected_size:
            raise ProtocolError("frame length does not match payload length")
        if flags & ~ALLOWED_FLAGS:
            raise ProtocolError("reserved flag bit is set")
        try:
            message_class = MessageClass(msg_class)
        except ValueError as exc:
            raise ProtocolError("unknown message class") from exc
        expected_crc = _CRC.unpack_from(data, expected_size - CRC_SIZE)[0]
        actual_crc = crc32_iso_hdlc(data[:-CRC_SIZE])
        if actual_crc != expected_crc:
            raise ProtocolError("CRC mismatch")
        return cls(
            message_class=message_class,
            opcode=opcode,
            sequence=sequence,
            payload=data[HEADER_SIZE:-CRC_SIZE],
            flags=flags,
            status=status,
            version_major=major,
            version_minor=minor,
        )


class StreamParser:
    """Incremental byte-stream parser with V0.1 resynchronization semantics."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self.crc_errors = 0
        self.discarded_bytes = 0

    def feed(self, chunk: bytes) -> list[Frame]:
        self._buffer.extend(chunk)
        frames: list[Frame] = []
        while True:
            marker = self._buffer.find(MAGIC)
            if marker < 0:
                keep = 1 if self._buffer.endswith(MAGIC[:1]) else 0
                self.discarded_bytes += len(self._buffer) - keep
                if keep:
                    self._buffer[:] = self._buffer[-1:]
                else:
                    self._buffer.clear()
                break
            if marker:
                del self._buffer[:marker]
                self.discarded_bytes += marker
            if len(self._buffer) < HEADER_SIZE:
                break
            try:
                fields = _HEADER.unpack_from(self._buffer)
                _, major, minor, msg_class, _, flags, _, header_size, _, size = fields
                MessageClass(msg_class)
                valid_header = (
                    (major, minor) == (VERSION_MAJOR, VERSION_MINOR)
                    and header_size == HEADER_SIZE
                    and size <= MAX_PAYLOAD
                    and flags & ~ALLOWED_FLAGS == 0
                )
            except (ValueError, struct.error):
                valid_header = False
                size = 0
            if not valid_header:
                del self._buffer[0]
                self.discarded_bytes += 1
                continue
            frame_size = HEADER_SIZE + size + CRC_SIZE
            if len(self._buffer) < frame_size:
                break
            candidate = bytes(self._buffer[:frame_size])
            expected_crc = _CRC.unpack_from(candidate, frame_size - CRC_SIZE)[0]
            if crc32_iso_hdlc(candidate[:-CRC_SIZE]) != expected_crc:
                self.crc_errors += 1
                del self._buffer[0]
                self.discarded_bytes += 1
                continue
            try:
                frame = Frame.decode(candidate)
            except ProtocolError:
                del self._buffer[0]
                self.discarded_bytes += 1
                continue
            frames.append(frame)
            del self._buffer[:frame_size]
        return frames
