"""obscura CLI."""
from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from obscura import __version__
from obscura.book import MessageType, OrderBook, parse_itch_file

NASDAQ_EMI_BASE = "https://emi.nasdaq.com/ITCH/Nasdaq%20ITCH/"


def _cmd_download(args: argparse.Namespace) -> int:
    fname = f"{args.date}.NASDAQ_ITCH50.gz"
    url = NASDAQ_EMI_BASE + fname
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / fname

    if out_path.exists() and not args.force:
        print(f"already exists: {out_path}", file=sys.stderr)
        return 0

    print(f"downloading {url} -> {out_path}", file=sys.stderr)
    try:
        urllib.request.urlretrieve(url, out_path)
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} fetching {url}: {e.reason}", file=sys.stderr)
        return 1
    print(f"done: {out_path.stat().st_size / 1e9:.2f} GB", file=sys.stderr)
    return 0


def _cmd_parse(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if not path.exists():
        print(f"not found: {path}", file=sys.stderr)
        return 1

    types: Counter[str] = Counter()
    n = 0
    for ev in parse_itch_file(path):
        types[ev.msg_type.value] += 1
        n += 1
        if args.limit and n >= args.limit:
            break

    print(f"parsed {n:,} events from {path.name}")
    for t, count in types.most_common():
        print(f"  {t}: {count:,}")
    return 0


def _cmd_book(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if not path.exists():
        print(f"not found: {path}", file=sys.stderr)
        return 1

    book = OrderBook(symbol=args.symbol)
    n_seen = 0
    for ev in parse_itch_file(path):
        n_seen += 1
        if ev.msg_type in (MessageType.ADD, MessageType.ADD_MPID):
            if ev.stock != args.symbol:
                continue
        elif ev.order_id and ev.order_id not in book._orders:
            continue
        book.apply(ev)
        if args.limit and n_seen >= args.limit:
            break

    book.assert_invariants()
    bb = book.best_bid()
    ba = book.best_ask()
    print(f"{args.symbol} book after {n_seen:,} events ({book.resting_count:,} resting orders)")
    if bb:
        print(f"  best bid: {bb[0] / 10000:>10.4f} x {bb[1]:>8}")
    if ba:
        print(f"  best ask: {ba[0] / 10000:>10.4f} x {ba[1]:>8}")
    bids, asks = book.top_of_book(depth=args.depth)
    print(f"  --- top {args.depth} ---")
    for i in range(max(len(bids), len(asks))):
        b = bids[i] if i < len(bids) else None
        a = asks[i] if i < len(asks) else None
        bs = f"{b[0] / 10000:>10.4f} x {b[1]:>8}" if b else " " * 21
        as_ = f"{a[0] / 10000:>10.4f} x {a[1]:>8}" if a else " " * 21
        print(f"    {bs}    {as_}")
    return 0


def _arrays_to_events(filtered, max_events: int):
    """Materialise filtered ItchArrays back into BookEvent objects."""
    from obscura.book.types import BookEvent, MessageType, Side
    type_map = {
        ord("A"): MessageType.ADD, ord("F"): MessageType.ADD_MPID,
        ord("E"): MessageType.EXECUTE, ord("C"): MessageType.EXECUTE_PRICE,
        ord("X"): MessageType.CANCEL, ord("D"): MessageType.DELETE,
        ord("U"): MessageType.REPLACE,
    }
    msg_type = filtered["msg_type"]
    timestamp_ns = filtered["timestamp_ns"]
    order_id = filtered["order_id"]
    side = filtered["side"]
    shares = filtered["shares"]
    price = filtered["price"]
    new_order_id = filtered["new_order_id"]
    new_shares = filtered["new_shares"]
    new_price = filtered["new_price"]
    n = min(len(msg_type), max_events) if max_events else len(msg_type)
    for i in range(n):
        t = type_map.get(int(msg_type[i]))
        if t is None:
            continue
        s = None
        if t in (MessageType.ADD, MessageType.ADD_MPID):
            s = Side.BUY if side[i] == ord("B") else Side.SELL
        yield BookEvent(
            msg_type=t, timestamp_ns=int(timestamp_ns[i]), order_id=int(order_id[i]),
            side=s, shares=int(shares[i]), price=int(price[i]), stock="",
            new_order_id=int(new_order_id[i]), new_shares=int(new_shares[i]),
            new_price=int(new_price[i]),
        )


def _cmd_honesty_gap(args: argparse.Namespace) -> int:
    """Run the same strategy under naive vs queue-aware fills."""
    from obscura.analysis import compare_queue_models
    from obscura.book import filter_to_locate, parse_itch_to_arrays, symbol_to_locate
    from obscura.sim import Latency
    from obscura.strategies import MeanReversion, OBISignal, PennyMM

    path = Path(args.path)
    if not path.exists():
        print(f"not found: {path}", file=sys.stderr)
        return 1

    print(f"parsing {path.name} ...", file=sys.stderr)
    arrays = parse_itch_to_arrays(path, capacity=20_000_000)
    locate = symbol_to_locate(arrays, args.symbol)
    if locate is None:
        avail = ", ".join(list(arrays["locate_to_symbol"].values())[:8])
        print(f"symbol {args.symbol} not in slice. some present: {avail}", file=sys.stderr)
        return 1
    filtered = filter_to_locate(arrays, locate)

    strat_factory = {
        "PennyMM": lambda: PennyMM(shares=args.shares),
        "OBISignal": lambda: OBISignal(depth=5, threshold=0.4, shares=args.shares),
        "MeanReversion": lambda: MeanReversion(warmup=200, z_entry=2.0, z_exit=0.3, shares=args.shares),
    }[args.strategy]

    rep = compare_queue_models(
        events_factory=lambda: _arrays_to_events(filtered, args.limit),
        strategy_factory=strat_factory,
        symbol=args.symbol,
        strategy_name=args.strategy,
        latency=Latency(network_ns=int(args.latency_ms * 1_000_000)),
    )

    md = rep.to_markdown()
    if args.out:
        Path(args.out).write_text(md)
        print(f"wrote {args.out}", file=sys.stderr)
    print(md)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="obscura", description="Pure-Python order-book replay.")
    parser.add_argument("--version", action="version", version=f"obscura {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_dl = sub.add_parser("download", help="Fetch a NASDAQ ITCH 5.0 day from NASDAQ EMI.")
    p_dl.add_argument("--date", required=True, help="MMDDYYYY, e.g. 12302019")
    p_dl.add_argument("--out", default="data/itch", help="Output directory")
    p_dl.add_argument("--force", action="store_true", help="Re-download if already present")
    p_dl.set_defaults(func=_cmd_download)

    p_parse = sub.add_parser("parse", help="Count message types in an ITCH file.")
    p_parse.add_argument("path", help="Path to .NASDAQ_ITCH50.gz file")
    p_parse.add_argument("--limit", type=int, default=0, help="Stop after N events (0 = all)")
    p_parse.set_defaults(func=_cmd_parse)

    p_book = sub.add_parser("book", help="Reconstruct top-of-book for a single symbol.")
    p_book.add_argument("path")
    p_book.add_argument("--symbol", required=True, help="e.g. AAPL")
    p_book.add_argument("--depth", type=int, default=5)
    p_book.add_argument("--limit", type=int, default=0)
    p_book.set_defaults(func=_cmd_book)

    p_hg = sub.add_parser("analyze", help="Honesty-gap report (naive vs queue-aware fills).")
    p_hg_sub = p_hg.add_subparsers(dest="analyze_cmd", required=True)
    p_hg_gap = p_hg_sub.add_parser("honesty-gap")
    p_hg_gap.add_argument("path", help="Path to .NASDAQ_ITCH50.gz file")
    p_hg_gap.add_argument("--symbol", required=True)
    p_hg_gap.add_argument("--strategy", choices=["PennyMM", "OBISignal", "MeanReversion"], default="PennyMM")
    p_hg_gap.add_argument("--shares", type=int, default=10)
    p_hg_gap.add_argument("--limit", type=int, default=200_000)
    p_hg_gap.add_argument("--latency-ms", type=float, default=1.0)
    p_hg_gap.add_argument("--out", help="Optional path to write the markdown report")
    p_hg_gap.set_defaults(func=_cmd_honesty_gap)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
