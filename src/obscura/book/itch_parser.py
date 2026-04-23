"""NASDAQ ITCH 5.0 wire-format parser.

Consumes a gzipped ITCH file (as published in the NASDAQ EMI directory) and
yields :class:`BookEvent` records for messages relevant to book reconstruction.
Admin messages (system events, stock trading actions, market participant
positions, etc.) are silently skipped — call sites that need them can add
hooks later.

The wire framing in NASDAQ EMI files is: 2-byte big-endian length, then the
ITCH message body (whose first byte is the message type). The standalone
ITCH 5.0 spec itself has no length prefix; this is a NASDAQ EMI distribution
convention.

Every numeric field is big-endian. Order IDs are 8 bytes. Prices are 4 bytes
in 1/10000 dollars. Quantities are 4 bytes. Timestamps are 6 bytes,
nanoseconds since midnight ET.
"""
from __future__ import annotations

import gzip
import struct
from collections.abc import Iterator
from pathlib import Path
from typing import IO

from obscura.book.types import BookEvent, MessageType, Side

# Pre-compiled struct unpackers — measurably faster than struct.unpack(fmt, ...)
_U16 = struct.Struct(">H")
_U32 = struct.Struct(">I")
_U64 = struct.Struct(">Q")
_ADD_FIELDS = struct.Struct(">QcI8sI")          # order_id, side, shares, stock, price
_EXEC_FIELDS = struct.Struct(">QIQ")            # order_id, exec_shares, match_number
_EXEC_PRICE_TAIL = struct.Struct(">cI")         # printable, exec_price (after EXEC_FIELDS)
_CANCEL_FIELDS = struct.Struct(">QI")           # order_id, cancel_shares
_DELETE_FIELDS = struct.Struct(">Q")            # order_id
_REPLACE_FIELDS = struct.Struct(">QQII")        # old_order_id, new_order_id, shares, price


def _u48(buf: bytes) -> int:
    """Read a 6-byte big-endian unsigned integer (ITCH timestamp format)."""
    return int.from_bytes(buf, "big")


def _stock(buf: bytes) -> str:
    return buf.decode("ascii").rstrip()


def _open(path: str | Path) -> IO[bytes]:
    p = Path(path)
    if p.suffix == ".gz":
        return gzip.open(p, "rb")  # type: ignore[return-value]
    return p.open("rb")


def parse_itch_stream(stream: IO[bytes]) -> Iterator[BookEvent]:
    """Yield :class:`BookEvent` records from an open ITCH 5.0 binary stream.

    Stops cleanly at EOF or on a truncated stream (used for slice-based
    spikes). Use :func:`parse_itch_file` for the path-based common case.
    """
    while True:
        try:
            length_bytes = stream.read(2)
            if len(length_bytes) < 2:
                return
            length = _U16.unpack(length_bytes)[0]
            msg = stream.read(length)
            if len(msg) < length:
                return  # truncated file — graceful end
        except (EOFError, OSError):
            # gzip raises EOFError on missing trailer; range-request slices
            # routinely hit this at end-of-buffer. Treat as clean stream end.
            return

        t = msg[0:1]
        # Skip header: type(1) + stock_locate(2) + tracking(2) + ts(6) = 11 bytes
        ts = _u48(msg[5:11])

        if t == b"A":
            order_id, side, shares, stock_b, price = _ADD_FIELDS.unpack_from(msg, 11)
            yield BookEvent(
                MessageType.ADD, ts, order_id,
                Side.BUY if side == b"B" else Side.SELL,
                shares, price, _stock(stock_b),
            )
        elif t == b"F":
            # Add with MPID — same prefix as A, then 4-byte MPID we ignore
            order_id, side, shares, stock_b, price = _ADD_FIELDS.unpack_from(msg, 11)
            yield BookEvent(
                MessageType.ADD_MPID, ts, order_id,
                Side.BUY if side == b"B" else Side.SELL,
                shares, price, _stock(stock_b),
            )
        elif t == b"E":
            order_id, exec_shares, match_num = _EXEC_FIELDS.unpack_from(msg, 11)
            yield BookEvent(
                MessageType.EXECUTE, ts, order_id, None,
                exec_shares, 0, "", match_number=match_num,
            )
        elif t == b"C":
            order_id, exec_shares, match_num = _EXEC_FIELDS.unpack_from(msg, 11)
            # printable flag + exec_price follow at offset 31
            _printable, exec_price = _EXEC_PRICE_TAIL.unpack_from(msg, 31)
            yield BookEvent(
                MessageType.EXECUTE_PRICE, ts, order_id, None,
                exec_shares, exec_price, "", match_number=match_num,
            )
        elif t == b"X":
            order_id, cancel_shares = _CANCEL_FIELDS.unpack_from(msg, 11)
            yield BookEvent(
                MessageType.CANCEL, ts, order_id, None,
                cancel_shares, 0, "",
            )
        elif t == b"D":
            (order_id,) = _DELETE_FIELDS.unpack_from(msg, 11)
            yield BookEvent(MessageType.DELETE, ts, order_id, None, 0, 0, "")
        elif t == b"U":
            old_oid, new_oid, new_shares, new_price = _REPLACE_FIELDS.unpack_from(msg, 11)
            yield BookEvent(
                MessageType.REPLACE, ts, old_oid, None, 0, 0, "",
                new_order_id=new_oid, new_shares=new_shares, new_price=new_price,
            )
        # Skipped: P, Q (trades — not needed for book), R, H, Y, L, S, etc.


def parse_itch_file(path: str | Path) -> Iterator[BookEvent]:
    """Open a (gzipped) ITCH 5.0 file and yield BookEvents."""
    with _open(path) as f:
        yield from parse_itch_stream(f)
