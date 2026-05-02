"""Smoke tests for the three baseline strategies."""
from __future__ import annotations

from obscura.book.types import BookEvent, MessageType, Side
from obscura.sim import Latency, run
from obscura.strategies import MeanReversion, OBISignal, PennyMM


def _add(order_id: int, side: Side, shares: int, price: int, ts: int) -> BookEvent:
    return BookEvent(MessageType.ADD, ts, order_id, side, shares, price, "X")


def _execute(order_id: int, shares: int, ts: int) -> BookEvent:
    return BookEvent(MessageType.EXECUTE, ts, order_id, None, shares, 0, "")


def _baseline_book_events() -> list[BookEvent]:
    """Build a simple book + a few trades to drive the strategies."""
    return [
        _add(1, Side.BUY, 500, 10000, ts=1),
        _add(2, Side.BUY, 200, 9990, ts=2),
        _add(3, Side.SELL, 300, 10010, ts=3),
        _add(4, Side.SELL, 400, 10020, ts=4),
        _execute(1, 100, ts=10_000_000),
        _execute(3, 50, ts=20_000_000),
    ]


def test_penny_mm_quotes_inside_spread():
    """PennyMM joins or penny-improves; never quotes outside the spread."""
    events = _baseline_book_events()
    strat = PennyMM(shares=10)
    result = run(iter(events), strat, symbol="X", latency=Latency(network_ns=1_000_000))
    # PennyMM should have placed at least one buy and one sell quote.
    sides_quoted = {o.side for o in result.my_orders.values()}
    assert Side.BUY in sides_quoted
    assert Side.SELL in sides_quoted
    # Tight-spread case (book has bb=10000, ba=10010, 1 tick): join-best.
    # Wide-spread case: penny inside. Either way: bb <= price <= ba on each side.
    for o in result.my_orders.values():
        if o.side is Side.BUY:
            assert 10000 <= o.price < 10010, f"buy at {o.price} outside spread"
        else:
            assert 10000 < o.price <= 10010, f"sell at {o.price} outside spread"


def test_penny_mm_pennies_inside_when_spread_wide():
    """Wide spread (>2 ticks) -> PennyMM penny-improves both sides."""
    events = [
        _add(1, Side.BUY, 500, 10000, ts=1),
        _add(2, Side.SELL, 500, 11000, ts=2),    # spread = 1000 = 10 ticks
        _add(3, Side.BUY, 100, 9900, ts=10_000_000),  # trailing event for action drain
    ]
    strat = PennyMM(shares=10)
    result = run(iter(events), strat, symbol="X", latency=Latency(network_ns=1_000_000))
    buys = [o for o in result.my_orders.values() if o.side is Side.BUY]
    sells = [o for o in result.my_orders.values() if o.side is Side.SELL]
    assert any(o.price == 10100 for o in buys), "expected penny-inside buy at 10100"
    assert any(o.price == 10900 for o in sells), "expected penny-inside sell at 10900"


def test_obi_takes_long_when_bid_heavy():
    """Heavy bid side -> OBI > threshold -> strategy goes long."""
    events = [
        # 1000 on bid, 100 on ask -> OBI = 0.82
        _add(1, Side.BUY, 1000, 10000, ts=1),
        _add(2, Side.SELL, 100, 10010, ts=2),
        # Trailing event so the action queued after event #2 has time to land.
        _add(3, Side.BUY, 50, 9999, ts=1_000_000),
    ]
    strat = OBISignal(depth=5, threshold=0.4, shares=10)
    result = run(iter(events), strat, symbol="X", latency=Latency(network_ns=1))
    long_orders = [o for o in result.my_orders.values() if o.side is Side.BUY]
    assert len(long_orders) >= 1


def test_obi_takes_short_when_ask_heavy():
    events = [
        _add(1, Side.BUY, 100, 10000, ts=1),
        _add(2, Side.SELL, 1000, 10010, ts=2),
        _add(3, Side.SELL, 50, 10011, ts=1_000_000),
    ]
    strat = OBISignal(depth=5, threshold=0.4, shares=10)
    result = run(iter(events), strat, symbol="X", latency=Latency(network_ns=1))
    short_orders = [o for o in result.my_orders.values() if o.side is Side.SELL]
    assert len(short_orders) >= 1


def test_obi_sits_out_when_balanced():
    """Balanced book → no signal → no orders."""
    events = [
        _add(1, Side.BUY, 500, 10000, ts=1),
        _add(2, Side.SELL, 500, 10010, ts=2),
    ]
    strat = OBISignal(depth=5, threshold=0.4, shares=10)
    result = run(iter(events), strat, symbol="X", latency=Latency(network_ns=1))
    assert len(result.my_orders) == 0


def test_mean_rev_does_not_trade_during_warmup():
    """Mean-reversion respects warmup — no orders for the first N ticks."""
    events = [_add(1, Side.BUY, 100, 10000, ts=1), _add(2, Side.SELL, 100, 10010, ts=2)]
    # Add 50 more events, all stable mid → warmup not satisfied.
    for i in range(50):
        events.append(_add(100 + i, Side.BUY, 50, 9990, ts=10_000 * (i + 1)))
        events.append(_add(200 + i, Side.SELL, 50, 10020, ts=10_000 * (i + 1) + 1))

    strat = MeanReversion(warmup=200, z_entry=2.0, z_exit=0.3, shares=10)
    result = run(iter(events), strat, symbol="X", latency=Latency(network_ns=1))
    assert len(result.my_orders) == 0


def test_mean_rev_enters_short_on_high_z():
    """Build a stable mid, then push it high → strategy should short."""
    events: list[BookEvent] = []
    # 250 ticks of stable book to satisfy warmup and build statistics.
    for i in range(250):
        events.append(_add(1000 + 2 * i, Side.BUY, 100, 10000, ts=1000 * (i + 1)))
        events.append(_add(1001 + 2 * i, Side.SELL, 100, 10010, ts=1000 * (i + 1) + 1))
    # Then a sudden mid spike (push up the bid level).
    events.append(_add(99998, Side.BUY, 100, 10500, ts=900_000_000))
    events.append(_add(99999, Side.SELL, 100, 10600, ts=900_000_001))

    strat = MeanReversion(warmup=200, z_entry=2.0, z_exit=0.3, shares=10)
    result = run(iter(events), strat, symbol="X", latency=Latency(network_ns=1))
    # Should have triggered at least one SELL (mid spiked far above mean).
    sells = [o for o in result.my_orders.values() if o.side is Side.SELL]
    assert len(sells) >= 1


def test_all_strategies_complete_on_empty_event_stream():
    """Sanity: empty stream is a no-op for every strategy."""
    for strat in [PennyMM(), OBISignal(), MeanReversion()]:
        result = run(iter([]), strat, symbol="X")
        assert result.fills == []
        assert result.my_orders == {}
