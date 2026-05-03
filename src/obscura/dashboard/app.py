"""Streamlit dashboard — live order-book replay with queue-position decay.

Run from the repo root:

    pip install -e ".[dashboard]"
    streamlit run src/obscura/dashboard/app.py

Pick a strategy + symbol + sample slice on the sidebar. Click *Run* to start
the simulation; the panes update tick-by-tick as snapshots stream through.
"""
from __future__ import annotations

import time
from pathlib import Path

import polars as pl
import streamlit as st

from obscura.analysis import attribute_result
from obscura.book import (
    filter_to_locate,
    parse_itch_to_arrays,
    symbol_to_locate,
)
from obscura.book.types import BookEvent, MessageType, Side
from obscura.sim import Latency, run
from obscura.strategies import MeanReversion, OBISignal, PennyMM

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SLICE = REPO_ROOT / "data" / "itch-samples" / "itch_slice_5mb.gz"


def _arrays_to_events(arrays, max_events: int):
    """Convert filtered ItchArrays back into BookEvent stream for the sim.

    The sim's event-driven loop expects BookEvents (the dataclass form). The
    array parser's output is column-oriented, so we materialise per-row.
    """
    msg_type = arrays["msg_type"]
    timestamp_ns = arrays["timestamp_ns"]
    order_id = arrays["order_id"]
    side = arrays["side"]
    shares = arrays["shares"]
    price = arrays["price"]
    new_order_id = arrays["new_order_id"]
    new_shares = arrays["new_shares"]
    new_price = arrays["new_price"]
    n = min(len(msg_type), max_events) if max_events else len(msg_type)
    type_map = {ord("A"): MessageType.ADD, ord("F"): MessageType.ADD_MPID, ord("E"): MessageType.EXECUTE,
                ord("C"): MessageType.EXECUTE_PRICE, ord("X"): MessageType.CANCEL, ord("D"): MessageType.DELETE,
                ord("U"): MessageType.REPLACE}
    for i in range(n):
        t = type_map.get(int(msg_type[i]))
        if t is None:
            continue
        s = None
        if t in (MessageType.ADD, MessageType.ADD_MPID):
            s = Side.BUY if side[i] == ord("B") else Side.SELL
        yield BookEvent(
            msg_type=t,
            timestamp_ns=int(timestamp_ns[i]),
            order_id=int(order_id[i]),
            side=s,
            shares=int(shares[i]),
            price=int(price[i]),
            stock="",
            new_order_id=int(new_order_id[i]),
            new_shares=int(new_shares[i]),
            new_price=int(new_price[i]),
        )


def _build_book_df(snapshot, depth=5) -> pl.DataFrame:
    """One-row snapshot of best bid/ask. Multi-level book in M7."""
    return pl.DataFrame({
        "side": ["BID", "ASK"],
        "price": [
            snapshot.best_bid_price / 10000.0 if snapshot.best_bid_price else None,
            snapshot.best_ask_price / 10000.0 if snapshot.best_ask_price else None,
        ],
        "qty": [snapshot.best_bid_qty, snapshot.best_ask_qty],
    })


def main() -> None:
    st.set_page_config(page_title="obscura", layout="wide", page_icon="📊")
    st.title("obscura — order-book replay")
    st.caption("Pure-Python NASDAQ ITCH 5.0 replay with Cont (2010) queue-position-aware fills.")

    with st.sidebar:
        st.header("Setup")
        slice_path = st.text_input("ITCH slice (.gz)", value=str(DEFAULT_SLICE))
        symbol = st.text_input("Symbol", value="AAPL")
        strategy_name = st.selectbox("Strategy", ["PennyMM", "OBISignal", "MeanReversion"])
        max_events = st.number_input("Max events to replay", min_value=1000, value=100_000, step=10_000)
        snapshot_every = st.number_input("Snapshot every N events", min_value=1, value=200)
        latency_ms = st.slider("Network latency (ms)", min_value=0.0, max_value=10.0, value=1.0, step=0.1)
        playback_delay_ms = st.slider("Playback delay (ms)", min_value=0, max_value=50, value=5)
        st.markdown("---")
        run_btn = st.button("▶ Run replay", type="primary", use_container_width=True)

    tab_book, tab_queue, tab_slip = st.tabs([
        "📒 Book + state", "📈 Queue position", "🎯 Slippage attribution"
    ])

    if not run_btn:
        with tab_book:
            st.info("Set parameters in the sidebar and click **Run replay**.")
            st.markdown(
                "**What you'll see:**\n\n"
                "- Top-of-book updating tick-by-tick.\n"
                "- Queue position of every live order, decaying as the market churns.\n"
                "- Three-component slippage breakdown at the end.\n"
            )
        return

    p = Path(slice_path)
    if not p.exists():
        st.error(f"Slice not found: {p}")
        return

    with st.spinner("Parsing ITCH slice…"):
        arrays = parse_itch_to_arrays(p, capacity=20_000_000)
    locate = symbol_to_locate(arrays, symbol)
    if locate is None:
        st.error(f"Symbol {symbol!r} not seen in this slice. "
                 f"Try one of: {', '.join(list(arrays['locate_to_symbol'].values())[:8])}…")
        return

    filtered = filter_to_locate(arrays, locate)
    n_filtered = filtered["msg_type"].shape[0]
    st.success(f"Parsed: {len(arrays['msg_type']):,} total events, {n_filtered:,} for {symbol}")

    strategy_obj = {
        "PennyMM": PennyMM(shares=10),
        "OBISignal": OBISignal(depth=5, threshold=0.4, shares=10),
        "MeanReversion": MeanReversion(warmup=200, z_entry=2.0, z_exit=0.3, shares=10),
    }[strategy_name]

    with st.spinner(f"Running {strategy_name} on {symbol}…"):
        result = run(
            _arrays_to_events(filtered, max_events=int(max_events)),
            strategy_obj,
            symbol=symbol,
            latency=Latency(network_ns=int(latency_ms * 1_000_000)),
            snapshot_every=int(snapshot_every),
        )

    if not result.snapshots:
        st.warning("No snapshots captured — try a larger Max events or smaller Snapshot every.")
        return

    # Playback
    with tab_book:
        col_book, col_state = st.columns([1, 1])
        book_pl = col_book.empty()
        state_pl = col_state.empty()

    with tab_queue:
        queue_pl = st.empty()
        queue_history: dict[int, list[tuple[int, float]]] = {}

    progress = st.progress(0.0, "Playing back…")
    n = len(result.snapshots)
    for i, snap in enumerate(result.snapshots):
        progress.progress((i + 1) / n, f"Snapshot {i + 1}/{n}")
        book_df = _build_book_df(snap)
        book_pl.dataframe(book_df, use_container_width=True, hide_index=True)
        state_pl.metric("Inventory", snap.inventory, delta=None,
                        help="Net shares held by my orders")

        for sid, qpos, _filled, _price in snap.my_order_states:
            queue_history.setdefault(sid, []).append((snap.timestamp_ns, max(qpos, 0.0)))

        if queue_history:
            rows = []
            for sid, hist in queue_history.items():
                for ts, q in hist:
                    rows.append({"timestamp_ns": ts, "queue_position": q, "order_id": str(sid)})
            df = pl.DataFrame(rows)
            queue_pl.line_chart(df, x="timestamp_ns", y="queue_position", color="order_id")

        if playback_delay_ms > 0:
            time.sleep(playback_delay_ms / 1000.0)

    progress.empty()

    # Slippage
    with tab_slip:
        report = attribute_result(result)
        st.metric("Total fills", len(result.fills))
        st.metric("Total shares filled", report.total_shares)
        st.metric("Realised cash change ($)", f"{result.cash_change / 10000.0:,.2f}")

        if report.total_shares > 0:
            df = pl.DataFrame({
                "component": ["spread_cost", "queue_loss", "adverse_selection"],
                "cost_dollars": [
                    report.spread_cost / 10000.0,
                    report.queue_loss / 10000.0,
                    report.adverse_selection / 10000.0,
                ],
            })
            st.bar_chart(df, x="component", y="cost_dollars")
            st.caption(
                "Cost convention: positive = paid, negative = saved. "
                "spread_cost + queue_loss + adverse_selection = total realised cost (by construction)."
            )
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("No fills in this run — try a longer replay window or a more aggressive strategy.")


if __name__ == "__main__":
    main()
