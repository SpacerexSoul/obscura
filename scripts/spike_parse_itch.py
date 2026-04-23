"""M1 spike: parse a slice of NASDAQ ITCH 5.0 file, reconstruct a book snapshot.

Goal: prove the data path is real. Read the 5MB compressed slice, count message
types, pick one popular symbol, replay its events into an L2 book, dump the
top-of-book snapshot at the moment we run out of bytes.

This is a throwaway script. The real parser will live in `obscura.book`.
"""
from __future__ import annotations

import gzip
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

SLICE_PATH = Path(__file__).parent.parent / "data" / "itch-samples" / "itch_slice_5mb.gz"

# ITCH 5.0 message sizes (bytes including the type byte)
MSG_SIZES = {
    b"S": 12, b"R": 39, b"H": 25, b"Y": 20, b"L": 26, b"V": 35, b"W": 12,
    b"K": 28, b"J": 35, b"h": 21,
    b"A": 36, b"F": 40,
    b"E": 31, b"C": 36, b"X": 23, b"D": 19, b"U": 35,
    b"P": 44, b"Q": 40, b"B": 19,
    b"I": 50, b"N": 20, b"O": 48,
}


def parse_uint48(buf: bytes) -> int:
    """Read 6-byte big-endian unsigned int (used for nanosecond timestamps)."""
    return int.from_bytes(buf, "big")


def parse_stock(buf: bytes) -> str:
    """Read 8-byte ASCII stock symbol, strip trailing spaces."""
    return buf.decode("ascii").rstrip()


def main() -> int:
    if not SLICE_PATH.exists():
        print(f"ERROR: slice file not found at {SLICE_PATH}", file=sys.stderr)
        return 1

    type_counts: Counter[str] = Counter()
    add_orders_per_symbol: Counter[str] = Counter()
    order_book: dict[int, tuple[str, bytes, int, int, str]] = {}  # order_id -> (stock, side, qty, price, ...)

    target_symbol = "AAPL"
    bids: dict[int, int] = defaultdict(int)  # price -> qty
    asks: dict[int, int] = defaultdict(int)
    target_orders: set[int] = set()  # order_ids for AAPL

    bytes_consumed = 0
    msg_count = 0
    truncated = False

    try:
        with gzip.open(SLICE_PATH, "rb") as f:
            while True:
                # ITCH wire framing: 2-byte BE length prefix, then message
                length_bytes = f.read(2)
                if len(length_bytes) < 2:
                    break
                length = struct.unpack(">H", length_bytes)[0]
                msg = f.read(length)
                if len(msg) < length:
                    truncated = True
                    break
                bytes_consumed += 2 + length
                msg_count += 1

                msg_type = msg[0:1]
                type_counts[msg_type.decode("ascii", errors="replace")] += 1

                # We only care about a few types for the spike.
                if msg_type == b"A":
                    # Add Order (no MPID): type(1) | stock_locate(2) | tracking(2) |
                    # ts(6) | order_id(8) | side(1) | shares(4) | stock(8) | price(4)
                    if len(msg) >= 36:
                        order_id = struct.unpack(">Q", msg[11:19])[0]
                        side = msg[19:20]
                        shares = struct.unpack(">I", msg[20:24])[0]
                        stock = parse_stock(msg[24:32])
                        price_raw = struct.unpack(">I", msg[32:36])[0]
                        # Price is in 1/10000 dollars
                        order_book[order_id] = (stock, side, shares, price_raw, "A")
                        add_orders_per_symbol[stock] += 1
                        if stock == target_symbol:
                            target_orders.add(order_id)
                            if side == b"B":
                                bids[price_raw] += shares
                            else:
                                asks[price_raw] += shares
                elif msg_type == b"F":
                    # Add Order with MPID: same as A through price, +4 byte MPID
                    if len(msg) >= 40:
                        order_id = struct.unpack(">Q", msg[11:19])[0]
                        side = msg[19:20]
                        shares = struct.unpack(">I", msg[20:24])[0]
                        stock = parse_stock(msg[24:32])
                        price_raw = struct.unpack(">I", msg[32:36])[0]
                        order_book[order_id] = (stock, side, shares, price_raw, "F")
                        add_orders_per_symbol[stock] += 1
                        if stock == target_symbol:
                            target_orders.add(order_id)
                            if side == b"B":
                                bids[price_raw] += shares
                            else:
                                asks[price_raw] += shares
                elif msg_type == b"E":
                    # Executed: type(1)+sl(2)+tr(2)+ts(6)+oid(8)+exec_shares(4)+match_num(8)
                    if len(msg) >= 31:
                        order_id = struct.unpack(">Q", msg[11:19])[0]
                        exec_shares = struct.unpack(">I", msg[19:23])[0]
                        if order_id in order_book:
                            stock, side, qty, price, src = order_book[order_id]
                            new_qty = qty - exec_shares
                            if order_id in target_orders:
                                if side == b"B":
                                    bids[price] -= exec_shares
                                    if bids[price] <= 0:
                                        del bids[price]
                                else:
                                    asks[price] -= exec_shares
                                    if asks[price] <= 0:
                                        del asks[price]
                            if new_qty <= 0:
                                del order_book[order_id]
                                target_orders.discard(order_id)
                            else:
                                order_book[order_id] = (stock, side, new_qty, price, src)
                elif msg_type == b"X":
                    # Cancel partial: type(1)+sl(2)+tr(2)+ts(6)+oid(8)+cancel_shares(4)
                    if len(msg) >= 23:
                        order_id = struct.unpack(">Q", msg[11:19])[0]
                        cancel_shares = struct.unpack(">I", msg[19:23])[0]
                        if order_id in order_book:
                            stock, side, qty, price, src = order_book[order_id]
                            new_qty = qty - cancel_shares
                            if order_id in target_orders:
                                if side == b"B":
                                    bids[price] -= cancel_shares
                                    if bids[price] <= 0:
                                        del bids[price]
                                else:
                                    asks[price] -= cancel_shares
                                    if asks[price] <= 0:
                                        del asks[price]
                            if new_qty <= 0:
                                del order_book[order_id]
                                target_orders.discard(order_id)
                            else:
                                order_book[order_id] = (stock, side, new_qty, price, src)
                elif msg_type == b"D":
                    # Delete: type(1)+sl(2)+tr(2)+ts(6)+oid(8)
                    if len(msg) >= 19:
                        order_id = struct.unpack(">Q", msg[11:19])[0]
                        if order_id in order_book:
                            stock, side, qty, price, src = order_book[order_id]
                            if order_id in target_orders:
                                if side == b"B":
                                    bids[price] -= qty
                                    if bids[price] <= 0:
                                        del bids[price]
                                else:
                                    asks[price] -= qty
                                    if asks[price] <= 0:
                                        del asks[price]
                                target_orders.discard(order_id)
                            del order_book[order_id]
    except (EOFError, OSError) as e:
        print(f"Stopped at {bytes_consumed:,} bytes (compressed): {e}", file=sys.stderr)
        truncated = True

    # Report
    print("=" * 72)
    print(f"M1 spike — NASDAQ ITCH 5.0 slice ({SLICE_PATH.name})")
    print("=" * 72)
    print(f"Compressed slice size: {SLICE_PATH.stat().st_size:,} bytes")
    print(f"Messages parsed: {msg_count:,}")
    print(f"Truncated at slice EOF: {truncated}")
    print(f"Resident orders at end: {len(order_book):,}")
    print()
    print("Message type counts (top 15):")
    for t, n in type_counts.most_common(15):
        print(f"  {t!r}: {n:,}")
    print()
    print("Add-order counts by symbol (top 15 — proxy for liquidity in slice):")
    for sym, n in add_orders_per_symbol.most_common(15):
        print(f"  {sym}: {n:,}")
    print()
    print(f"--- Top-of-book snapshot for {target_symbol} at slice EOF ---")
    if not bids and not asks:
        print(f"  (no resident {target_symbol} orders — symbol not active in this slice)")
    else:
        top_bids = sorted(bids.items(), key=lambda x: -x[0])[:5]
        top_asks = sorted(asks.items(), key=lambda x: x[0])[:5]
        print(f"  {'Price':>10} {'BidQty':>8}   {'Price':>10} {'AskQty':>8}")
        for i in range(max(len(top_bids), len(top_asks))):
            bid = top_bids[i] if i < len(top_bids) else (None, None)
            ask = top_asks[i] if i < len(top_asks) else (None, None)
            bid_str = f"{bid[0]/10000:>10.4f} {bid[1]:>8}" if bid[0] else " " * 19
            ask_str = f"{ask[0]/10000:>10.4f} {ask[1]:>8}" if ask[0] else " " * 19
            print(f"  {bid_str}   {ask_str}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
