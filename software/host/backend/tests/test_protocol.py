from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pvna_host.protocol import (  # noqa: E402
    Frame,
    MessageClass,
    Opcode,
    PointResult,
    StartPoint,
    StreamParser,
)
from pvna_host.protocol.frame import RESPONSE_REQUIRED, crc32_iso_hdlc  # noqa: E402


class ProtocolVectorTests(unittest.TestCase):
    REQUEST = bytes.fromhex(
        "50 56 00 01 01 01 01 00 00 00 14 00 01 00 00 00 00 00 00 00 CF 0D A8 F6"
    )
    RESPONSE = bytes.fromhex(
        "50 56 00 01 02 01 00 00 00 00 14 00 01 00 00 00 00 00 00 00 4B 58 6F 42"
    )

    def test_ping_request_matches_frozen_vector(self) -> None:
        frame = Frame(
            message_class=MessageClass.REQUEST,
            opcode=Opcode.PING,
            sequence=1,
            flags=RESPONSE_REQUIRED,
        )
        self.assertEqual(frame.encode(), self.REQUEST)
        self.assertEqual(crc32_iso_hdlc(self.REQUEST[:-4]), 0xF6A80DCF)

    def test_ping_response_matches_frozen_vector(self) -> None:
        frame = Frame(
            message_class=MessageClass.RESPONSE,
            opcode=Opcode.PING,
            sequence=1,
        )
        self.assertEqual(frame.encode(), self.RESPONSE)
        self.assertEqual(crc32_iso_hdlc(self.RESPONSE[:-4]), 0x426F584B)

    def test_stream_parser_handles_split_noise_and_resynchronization(self) -> None:
        damaged = bytearray(self.REQUEST)
        damaged[-1] ^= 0x80
        parser = StreamParser()
        self.assertEqual(parser.feed(b"noise" + bytes(damaged[:11])), [])
        frames = parser.feed(bytes(damaged[11:]) + b"junk" + self.RESPONSE[:7])
        self.assertEqual(frames, [])
        frames = parser.feed(self.RESPONSE[7:])
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0], Frame.decode(self.RESPONSE))
        self.assertEqual(parser.crc_errors, 1)
        self.assertGreaterEqual(parser.discarded_bytes, 9)


class PayloadTests(unittest.TestCase):
    def test_start_point_round_trip_is_exactly_32_bytes(self) -> None:
        value = StartPoint(7, 3, 50_000_000, 8192, 1, 1000, 65536, 2000)
        encoded = value.encode()
        self.assertEqual(len(encoded), 32)
        self.assertEqual(StartPoint.decode(encoded), value)

    def test_point_result_uses_string_ints_at_api_boundary(self) -> None:
        value = PointResult(
            measurement_id=7,
            point_index=3,
            requested_frequency_hz=50_000_000,
            actual_frequency_hz=49_999_999,
            r_i_acc=2**60,
            r_q_acc=-(2**59),
            a_i_acc=2**58,
            a_q_acc=-(2**57),
            integration_count=65536,
            accumulator_right_shift=2,
            result_flags=0,
            fpga_timestamp_ticks=2**55,
            duration_us=1200,
        )
        encoded = value.encode()
        self.assertEqual(len(encoded), 80)
        self.assertEqual(PointResult.decode(encoded), value)
        api = value.to_api_dict()
        self.assertEqual(api["r_i_acc"], str(2**60))
        self.assertEqual(api["fpga_timestamp_ticks"], str(2**55))


if __name__ == "__main__":
    unittest.main()
