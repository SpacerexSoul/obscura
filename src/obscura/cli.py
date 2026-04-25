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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
