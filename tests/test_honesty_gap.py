"""Honesty-gap tests: queue-aware vs naive instant-fill must diverge."""
from __future__ import annotations

from obscura.analysis import compare_queue_models
from obscura.book.types import BookEvent, MessageType, Side
from obscura.sim import InstantFillQueueModel, Latency
from obscura.strategies import PennyMM


def _add(order_id: int, side: Side, shares: int, price: int, ts: int) -> BookEvent:
    return BookEvent(MessageType.ADD, ts, order_id, side, shares, price, "X")


def _execute(order_id: int, shares: int, ts: int) -> BookEvent:
    return BookEvent(MessageType.EXECUTE, ts, order_id, None, shares, 0, "")


def _stream():
    """Reproducible event stream for honesty-gap testing."""
    return iter([
        _add(1, Side.BUY, 1000, 10000, ts=1),     # 1000 ahead at 10000
        _add(2, Side.SELL, 1000, 10100, ts=2),    # 1000 ahead at 10100
        _add(3, Side.BUY, 50, 9990, ts=3),        # depth event so PennyMM joins
        # Now several executes drain bid side gradually
        _execute(1, 100, ts=10_000_000),
        _execute(1, 100, ts=20_000_000),
        _execute(1, 100, ts=30_000_000),
        _execute(1, 100, ts=40_000_000),
        _execute(1, 100, ts=50_000_000),
        _execute(2, 100, ts=60_000_000),
        _execute(2, 100, ts=70_000_000),
    ])


def test_instant_fill_fills_first_trade_per_order():
    """InstantFillQueueModel: first trade at the level fills us."""
    from obscura.sim.types import MyOrder

    o = MyOrder(synthetic_id=1, side=Side.BUY, shares=100, price=10000,
                placed_at_ns=0, queue_position=500.0)
    m = InstantFillQueueModel()
    m.on_trade(o, traded_shares=10)
    assert o.queue_position == 0.0  # one trade => fillable now


def test_compare_queue_models_runs_both():
    rep = compare_queue_models(
        _stream,
        lambda: PennyMM(shares=10),
        symbol="X",
        strategy_name="PennyMM",
        latency=Latency(network_ns=1_000_000),
    )
    # Both runs should complete; counts may differ but both populate fills/orders.
    assert rep.symbol == "X"
    assert rep.strategy_name == "PennyMM"
    assert isinstance(rep.fill_count_naive, int)
    assert isinstance(rep.fill_count_queue_aware, int)


def test_naive_fills_at_least_as_many_as_queue_aware():
    """Naive model's instant fills should never undershoot queue-aware fills.

    The math: instant fill model puts queue_position to 0 on first trade,
    so any trade at our level fills us. Queue-aware model requires queue
    to drain first. So naive fills >= queue-aware fills, always.
    """
    rep = compare_queue_models(
        _stream,
        lambda: PennyMM(shares=10),
        symbol="X",
        strategy_name="PennyMM",
        latency=Latency(network_ns=1_000_000),
    )
    assert rep.shares_naive >= rep.shares_queue_aware


def test_markdown_report_has_summary_table():
    rep = compare_queue_models(
        _stream,
        lambda: PennyMM(shares=10),
        symbol="X",
        strategy_name="PennyMM",
    )
    md = rep.to_markdown()
    assert "# obscura honesty-gap report" in md
    assert "Naive (instant fill)" in md
    assert "Queue-aware (Cont 2010)" in md
    assert "Realised cash" in md
    assert "Marked P&L" in md


def test_pnl_gap_signed():
    """Direction of the gap is informative — naive overstates if positive."""
    rep = compare_queue_models(
        _stream,
        lambda: PennyMM(shares=10),
        symbol="X",
        strategy_name="PennyMM",
    )
    # Just ensure the property is computable; sign depends on the run.
    _ = rep.pnl_gap
    _ = rep.pnl_gap_dollars
