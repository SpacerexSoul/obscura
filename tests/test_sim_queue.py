"""Pinned queue-decay tests.

The Cont (2010) intra-level approximation is the contract obscura is
shipping. These tests exist so the decay rate is **provable** rather than
"trust me bro" — interview-grade rigour.
"""
from __future__ import annotations

import pytest

from obscura.book.types import Side
from obscura.sim.queue import ProbabilisticQueueModel, fillable_qty
from obscura.sim.types import MyOrder


def _new_order(queue_position: float, shares: int = 100) -> MyOrder:
    return MyOrder(
        synthetic_id=1,
        side=Side.BUY,
        shares=shares,
        price=10000,
        placed_at_ns=0,
        queue_position=queue_position,
        initial_level_qty=int(queue_position),
    )


def test_trade_decrements_queue_fifo():
    """A 30-share trade in front of us drops queue_position by exactly 30."""
    o = _new_order(queue_position=500.0)
    m = ProbabilisticQueueModel()
    m.on_trade(o, traded_shares=30)
    assert o.queue_position == 470.0


def test_cancel_decays_queue_by_fraction():
    """Cont approximation: 100 shares cancelled out of 500 → queue * (1 - 0.2)."""
    o = _new_order(queue_position=500.0)
    m = ProbabilisticQueueModel()
    m.on_cancel(o, cancelled_shares=100, level_qty_before=500)
    # 500 * (1 - 100/500) = 400
    assert o.queue_position == pytest.approx(400.0, rel=1e-9)


def test_cancel_zero_level_is_noop():
    o = _new_order(queue_position=500.0)
    m = ProbabilisticQueueModel()
    m.on_cancel(o, cancelled_shares=100, level_qty_before=0)
    assert o.queue_position == 500.0


def test_cancel_full_level_drives_queue_to_zero():
    o = _new_order(queue_position=500.0)
    m = ProbabilisticQueueModel()
    m.on_cancel(o, cancelled_shares=600, level_qty_before=500)
    assert o.queue_position == 0.0


def test_fillable_zero_when_queued():
    o = _new_order(queue_position=10.0)
    assert fillable_qty(o) == 0


def test_fillable_returns_remaining_when_queue_drained():
    o = _new_order(queue_position=0.0, shares=100)
    assert fillable_qty(o) == 100


def test_fillable_returns_remaining_after_partial():
    o = _new_order(queue_position=0.0, shares=100)
    o.filled_qty = 30
    # Even when queue drops further, we never fill more than remaining.
    assert fillable_qty(o) == 70


def test_pinned_decay_full_lifecycle():
    """End-to-end pinned scenario.

    Setup: 100-share BUY at price 10000, joining behind 500 resting shares.

    Sequence of events at our level:
      1. 100-share trade FIFO  → queue_position 500 → 400
      2. 50-share cancel out of 400 → queue_position 400 * (1 - 50/400) = 350
      3. 250-share trade  → queue_position 350 → 100
      4. 100-share trade  → queue_position 100 → 0  (we are at the head)
      5. 50-share trade   → queue_position 0 → -50; we fill 50 of our 100

    The pinned numbers are stable for as long as the model is `ProbabilisticQueueModel`.
    """
    m = ProbabilisticQueueModel()
    o = _new_order(queue_position=500.0, shares=100)

    m.on_trade(o, traded_shares=100)
    assert o.queue_position == 400.0

    m.on_cancel(o, cancelled_shares=50, level_qty_before=400)
    assert o.queue_position == pytest.approx(350.0, rel=1e-9)

    m.on_trade(o, traded_shares=250)
    assert o.queue_position == pytest.approx(100.0, rel=1e-9)

    m.on_trade(o, traded_shares=100)
    assert o.queue_position == pytest.approx(0.0, abs=1e-9)
    assert fillable_qty(o) == 100

    m.on_trade(o, traded_shares=50)
    assert o.queue_position == pytest.approx(-50.0, rel=1e-9)
    # fillable_qty respects the remaining cap.
    assert fillable_qty(o) <= o.remaining
