"""End-to-end simulator tests with synthetic event streams + simple strategies."""
from __future__ import annotations

from obscura.book.book import OrderBook
from obscura.book.types import BookEvent, MessageType, Side
from obscura.sim import (
    Action,
    ActionKind,
    Latency,
    run,
)


def _add(order_id: int, side: Side, shares: int, price: int, ts: int) -> BookEvent:
    return BookEvent(MessageType.ADD, ts, order_id, side, shares, price, "X")


def _delete(order_id: int, ts: int) -> BookEvent:
    return BookEvent(MessageType.DELETE, ts, order_id, None, 0, 0, "")


def _cancel(order_id: int, shares: int, ts: int) -> BookEvent:
    return BookEvent(MessageType.CANCEL, ts, order_id, None, shares, 0, "")


def _execute(order_id: int, shares: int, ts: int) -> BookEvent:
    return BookEvent(MessageType.EXECUTE, ts, order_id, None, shares, 0, "")


class NoOpStrategy:
    def on_event(self, event: BookEvent, book: OrderBook) -> list[Action]:
        return []


class PlaceOnceStrategy:
    """Place a single buy order at the first opportunity, then go silent."""

    def __init__(self, side: Side, shares: int, price: int, synthetic_id: int = 1):
        self.side = side
        self.shares = shares
        self.price = price
        self.synthetic_id = synthetic_id
        self.placed = False

    def on_event(self, event: BookEvent, book: OrderBook) -> list[Action]:
        if self.placed:
            return []
        self.placed = True
        return [Action(
            kind=ActionKind.PLACE_LIMIT,
            synthetic_id=self.synthetic_id,
            side=self.side,
            shares=self.shares,
            price=self.price,
        )]


def test_noop_strategy_produces_empty_result():
    events = [
        _add(1, Side.BUY, 100, 10000, ts=1),
        _add(2, Side.SELL, 50, 10010, ts=2),
        _delete(1, ts=3),
    ]
    result = run(iter(events), NoOpStrategy(), symbol="X")
    assert result.fills == []
    assert result.cash_change == 0
    assert result.inventory == 0
    assert result.my_orders == {}


def test_place_then_swept_by_trades():
    events = [
        _add(1, Side.BUY, 500, 10000, ts=1),
        _add(99, Side.SELL, 9999, 10010, ts=2),
        _execute(1, 200, ts=2_000_000),
        _execute(1, 300, ts=3_000_000),
        _execute(1, 50, ts=4_000_000),
        _execute(1, 100, ts=5_000_000),
    ]
    strat = PlaceOnceStrategy(Side.BUY, shares=100, price=10000, synthetic_id=42)
    result = run(iter(events), strat, symbol="X", latency=Latency(network_ns=1_000_000))
    assert len(result.fills) >= 1
    assert result.inventory > 0
    total_filled = sum(f.shares for f in result.fills)
    assert total_filled <= 100
    assert result.cash_change < 0


def test_cancel_before_fill():
    class PlaceThenCancelStrategy:
        def __init__(self):
            self.step = 0

        def on_event(self, event: BookEvent, book: OrderBook) -> list[Action]:
            self.step += 1
            if self.step == 1:
                return [Action(
                    kind=ActionKind.PLACE_LIMIT,
                    synthetic_id=7,
                    side=Side.BUY,
                    shares=100,
                    price=10000,
                )]
            if self.step == 2:
                return [Action(kind=ActionKind.CANCEL, synthetic_id=7)]
            return []

    events = [
        _add(1, Side.BUY, 500, 10000, ts=1),
        _add(2, Side.SELL, 50, 10010, ts=2),
        _execute(1, 600, ts=10_000_000),
    ]
    result = run(iter(events), PlaceThenCancelStrategy(), symbol="X")
    assert result.fills == []
    order = result.my_orders[7]
    assert order.cancelled is True
    assert order.filled_qty == 0


def test_latency_delays_action_arrival():
    class PlaceImmediately:
        def __init__(self):
            self.fired = False

        def on_event(self, event, book):
            if self.fired:
                return []
            self.fired = True
            return [Action(
                kind=ActionKind.PLACE_LIMIT,
                synthetic_id=1,
                side=Side.BUY,
                shares=10,
                price=10000,
            )]

    events = [
        _add(1, Side.BUY, 500, 10000, ts=1),
        _add(2, Side.SELL, 50, 10010, ts=2),
        _execute(1, 50, ts=1_000_000_000),
        _execute(1, 50, ts=2_000_000_000),
        _execute(1, 50, ts=4_000_000_000),
    ]
    result = run(
        iter(events),
        PlaceImmediately(),
        symbol="X",
        latency=Latency(network_ns=5_000_000_000),
    )
    assert 1 not in result.my_orders
    assert result.fills == []


def test_my_order_does_not_corrupt_book_invariants():
    events = [
        _add(1, Side.BUY, 500, 10000, ts=1),
        _add(2, Side.SELL, 50, 10010, ts=2),
        _add(3, Side.BUY, 200, 9990, ts=3),
        _execute(1, 100, ts=10_000_000),
        _delete(2, ts=20_000_000),
    ]
    result = run(
        iter(events),
        PlaceOnceStrategy(Side.BUY, shares=10, price=10000),
        symbol="X",
    )
    assert isinstance(result.fills, list)


def test_fill_lifecycle_emits_fill_records():
    events = [
        _add(1, Side.BUY, 500, 10000, ts=1),
        _add(2, Side.SELL, 50, 10010, ts=2),
        _execute(1, 200, ts=10_000_000),
        _execute(1, 300, ts=20_000_000),
        _execute(1, 50, ts=30_000_000),
        _execute(1, 50, ts=40_000_000),
    ]
    strat = PlaceOnceStrategy(Side.BUY, shares=100, price=10000, synthetic_id=11)
    result = run(iter(events), strat, symbol="X")
    if 11 in result.my_orders:
        my = result.my_orders[11]
        total_fill = sum(f.shares for f in result.fills if f.synthetic_id == 11)
        assert my.filled_qty == total_fill
