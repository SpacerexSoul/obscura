"""Array parser tests + parity vs the iterate-y reference parser."""
from __future__ import annotations

import gzip
import io
import struct
from pathlib import Path

import pytest

from obscura.book import (
    MessageType,
    OrderBook,
    apply_array_batch,
    filter_to_locate,
    parse_itch_file,
    parse_itch_stream,
    parse_itch_to_arrays,
    symbol_to_locate,
)
from obscura.book.array_parser import event_count

SLICE_PATH = (
    Path(__file__).parent.parent
    / "data"
    / "itch-samples"
    / "itch_slice_5mb.gz"
)


def _frame(payload: bytes) -> bytes:
    return struct.pack(">H", len(payload)) + payload


def _add(order_id: int, side: bytes, shares: int, stock: bytes, price: int, locate: int = 1) -> bytes:
    return _frame(
        b"A"
        + struct.pack(">H", locate)
        + b"\x00\x00"
        + b"\x00\x00\x00\x00\x00\x01"
        + struct.pack(">Q", order_id)
        + side
        + struct.pack(">I", shares)
        + stock.ljust(8)
        + struct.pack(">I", price)
    )


def _delete(order_id: int, locate: int = 1) -> bytes:
    return _frame(
        b"D"
        + struct.pack(">H", locate)
        + b"\x00\x00"
        + b"\x00\x00\x00\x00\x00\x02"
        + struct.pack(">Q", order_id)
    )


def test_synthetic_array_parser(tmp_path):
    raw = _add(1, b"B", 100, b"AAPL", 1234500, locate=42) + _delete(1, locate=42)
    p = tmp_path / "tiny.bin"
    p.write_bytes(raw)
    arrays = parse_itch_to_arrays(p, capacity=10)
    assert event_count(arrays) == 2
    assert arrays["msg_type"][0] == ord("A")
    assert arrays["msg_type"][1] == ord("D")
    assert arrays["order_id"][0] == 1
    assert arrays["price"][0] == 1234500
    assert arrays["stock_locate"][0] == 42
    assert arrays["locate_to_symbol"] == {42: "AAPL"}


def test_filter_to_locate(tmp_path):
    raw = (
        _add(1, b"B", 100, b"AAPL", 1234500, locate=42)
        + _add(2, b"S", 50, b"MSFT", 5000000, locate=99)
        + _delete(1, locate=42)
        + _delete(2, locate=99)
    )
    p = tmp_path / "two.bin"
    p.write_bytes(raw)
    arrays = parse_itch_to_arrays(p, capacity=10)
    aapl = filter_to_locate(arrays, 42)
    assert event_count(aapl) == 2
    assert symbol_to_locate(arrays, "AAPL") == 42
    assert symbol_to_locate(arrays, "MSFT") == 99
    assert symbol_to_locate(arrays, "XXXX") is None


def _build_synthetic_gz(tmp_path: Path) -> Path:
    """Build a complete (non-truncated) gzipped ITCH stream for parity tests.

    Uses two symbols (AAPL on locate=42, MSFT on locate=99) and exercises every
    book-relevant message type: A, F, E, C, X, D, U.
    """
    raw = (
        _add(1, b"B", 100, b"AAPL", 1234500, locate=42)
        + _add(2, b"B", 200, b"AAPL", 1234400, locate=42)
        + _add(3, b"S", 50,  b"AAPL", 1234600, locate=42)
        + _add(4, b"B", 500, b"MSFT", 5000000, locate=99)
        + _add(5, b"S", 300, b"MSFT", 5001000, locate=99)
        + _delete(2, locate=42)
        + _frame(  # X: cancel partial 30 of order 1
            b"X" + struct.pack(">H", 42) + b"\x00\x00"
            + b"\x00\x00\x00\x00\x00\x05"
            + struct.pack(">Q", 1)
            + struct.pack(">I", 30)
        )
        + _frame(  # E: execute 50 of order 4
            b"E" + struct.pack(">H", 99) + b"\x00\x00"
            + b"\x00\x00\x00\x00\x00\x06"
            + struct.pack(">Q", 4)
            + struct.pack(">I", 50)
            + struct.pack(">Q", 999)  # match number
        )
        + _frame(  # U: replace order 3 with new id 30, 25 shares at 1234700
            b"U" + struct.pack(">H", 42) + b"\x00\x00"
            + b"\x00\x00\x00\x00\x00\x07"
            + struct.pack(">Q", 3)
            + struct.pack(">Q", 30)
            + struct.pack(">I", 25)
            + struct.pack(">I", 1234700)
        )
    )
    p = tmp_path / "synth.itch.gz"
    with gzip.open(p, "wb") as g:
        g.write(raw)
    return p


def test_array_parity_synthetic(tmp_path):
    """Exact parity on a controlled, complete gzipped fixture."""
    path = _build_synthetic_gz(tmp_path)
    arrays = parse_itch_to_arrays(path, capacity=100)

    ref = []
    for ev in parse_itch_file(path):
        ref.append(ev.msg_type.value)

    arr = [chr(t) for t in arrays["msg_type"]]
    assert arr == ref


def test_array_book_matches_reference_synthetic(tmp_path):
    """Both pipelines reconstruct identical books on a controlled stream."""
    path = _build_synthetic_gz(tmp_path)

    # Reference path
    ref_book = OrderBook("AAPL")
    for ev in parse_itch_file(path):
        if ev.msg_type in (MessageType.ADD, MessageType.ADD_MPID):
            if ev.stock != "AAPL":
                continue
        elif ev.order_id and ev.order_id not in ref_book._orders:
            continue
        ref_book.apply(ev)

    # Array path
    arrays = parse_itch_to_arrays(path, capacity=100)
    aapl_locate = symbol_to_locate(arrays, "AAPL")
    assert aapl_locate == 42
    aapl_arrays = filter_to_locate(arrays, aapl_locate)
    array_book = OrderBook("AAPL")
    apply_array_batch(array_book, aapl_arrays)

    ref_book.assert_invariants()
    array_book.assert_invariants()
    assert ref_book.best_bid() == array_book.best_bid()
    assert ref_book.best_ask() == array_book.best_ask()
    assert ref_book.top_of_book(depth=10) == array_book.top_of_book(depth=10)


@pytest.mark.skipif(not SLICE_PATH.exists(), reason="M1 spike slice not present")
def test_array_parser_smoke_against_real_slice():
    """The truncated 5MB slice is a worst-case fixture: gzip raises mid-read.

    We accept up to ~5% loss vs the reference parser on truncated streams;
    a complete day file would not exhibit this. See ``test_array_parity_synthetic``
    for the exact-parity guarantee.
    """
    arrays = parse_itch_to_arrays(SLICE_PATH, capacity=2_000_000)
    n_arr = event_count(arrays)
    n_ref = sum(1 for ev in parse_itch_file(SLICE_PATH) if ev.msg_type in {
        MessageType.ADD, MessageType.ADD_MPID,
        MessageType.EXECUTE, MessageType.EXECUTE_PRICE,
        MessageType.CANCEL, MessageType.DELETE, MessageType.REPLACE,
    })
    # Sanity: at least 90% of events captured even on a truncated stream.
    assert n_arr >= int(0.9 * n_ref), f"array parser yielded {n_arr} vs ref {n_ref}"


def test_polars_snapshot_shape():
    book = OrderBook("X")
    # Build a small book.
    for ev in parse_itch_stream(io.BytesIO(
        _add(1, b"B", 100, b"X", 10000)
        + _add(2, b"B", 200, b"X", 9990)
        + _add(3, b"S", 50, b"X", 10010)
    )):
        book.apply(ev)

    df = book.snapshot_polars(depth=5)
    assert df.shape == (5, 5)
    assert df["level"].to_list() == [1, 2, 3, 4, 5]
    assert df["bid_price"][0] == 1.0
    assert df["bid_qty"][0] == 100
    assert df["ask_price"][0] == 1.001
    assert df["ask_qty"][0] == 50
    # Beyond depth-2, levels should be zero-filled
    assert df["bid_price"][2] == 0.0
    assert df["bid_qty"][2] == 0
