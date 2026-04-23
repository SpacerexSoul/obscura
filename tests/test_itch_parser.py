"""Parser correctness tests, including against the M1 spike slice."""
from __future__ import annotations

import io
import struct
from pathlib import Path

import pytest

from obscura.book import MessageType, Side, parse_itch_file, parse_itch_stream

SLICE_PATH = (
    Path(__file__).parent.parent
    / "data"
    / "itch-samples"
    / "itch_slice_5mb.gz"
)


def _frame(payload: bytes) -> bytes:
    """Wrap payload in NASDAQ EMI 2-byte length prefix."""
    return struct.pack(">H", len(payload)) + payload


def _add_msg(order_id: int, side: bytes, shares: int, stock: bytes, price: int) -> bytes:
    return _frame(
        b"A"
        + b"\x00\x01"               # stock_locate
        + b"\x00\x00"               # tracking
        + b"\x00\x00\x00\x00\x00\x01"  # timestamp_ns = 1
        + struct.pack(">Q", order_id)
        + side
        + struct.pack(">I", shares)
        + stock.ljust(8)
        + struct.pack(">I", price)
    )


def _delete_msg(order_id: int, ts: int = 2) -> bytes:
    return _frame(
        b"D"
        + b"\x00\x01"
        + b"\x00\x00"
        + ts.to_bytes(6, "big")
        + struct.pack(">Q", order_id)
    )


def test_synthetic_add_then_delete():
    stream = io.BytesIO(_add_msg(42, b"B", 100, b"AAPL", 1234500) + _delete_msg(42))
    events = list(parse_itch_stream(stream))
    assert [e.msg_type for e in events] == [MessageType.ADD, MessageType.DELETE]

    add = events[0]
    assert add.order_id == 42
    assert add.side is Side.BUY
    assert add.shares == 100
    assert add.stock == "AAPL"
    assert add.price == 1234500
    assert add.timestamp_ns == 1


def test_truncated_stream_returns_cleanly():
    """Real-world spikes use HTTP range requests; parser must handle EOF mid-message."""
    valid = _add_msg(1, b"S", 10, b"X", 100)
    stream = io.BytesIO(valid + valid[:5])  # second message cut short
    events = list(parse_itch_stream(stream))
    assert len(events) == 1


def test_stream_handles_no_data():
    assert list(parse_itch_stream(io.BytesIO(b""))) == []


@pytest.mark.skipif(not SLICE_PATH.exists(), reason="M1 spike slice not present")
def test_real_slice_parses_minimum_events():
    n = 0
    types_seen: set[MessageType] = set()
    for ev in parse_itch_file(SLICE_PATH):
        n += 1
        types_seen.add(ev.msg_type)
        if n >= 200_000:
            break
    # Minimum sanity: the 5MB slice should contain at least 200k events
    # (the M1 spike ran the whole slice and saw 444k).
    assert n >= 200_000
    # And at least the basic add/delete/cancel mix.
    assert MessageType.ADD in types_seen
    assert MessageType.DELETE in types_seen
