"""Property-based tests on OrderBook invariants.

Hypothesis generates synthetic event streams; the book should never crash,
never go crossed, and per-level totals should always equal the sum of resting
orders at that level.
"""
from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from obscura.book import BookEvent, MessageType, OrderBook, Side


def _add(order_id: int, side: Side, shares: int, price: int) -> BookEvent:
    return BookEvent(MessageType.ADD, 0, order_id, side, shares, price, "X")


def _delete(order_id: int) -> BookEvent:
    return BookEvent(MessageType.DELETE, 0, order_id, None, 0, 0, "")


def _cancel(order_id: int, shares: int) -> BookEvent:
    return BookEvent(MessageType.CANCEL, 0, order_id, None, shares, 0, "")


def _execute(order_id: int, shares: int) -> BookEvent:
    return BookEvent(MessageType.EXECUTE, 0, order_id, None, shares, 0, "")


def test_empty_book_is_consistent():
    book = OrderBook("X")
    book.assert_invariants()
    assert book.best_bid() is None
    assert book.best_ask() is None


def test_simple_add_and_delete():
    book = OrderBook("X")
    book.apply(_add(1, Side.BUY, 100, 10000))
    book.apply(_add(2, Side.SELL, 50, 10010))
    book.assert_invariants()
    assert book.best_bid() == (10000, 100)
    assert book.best_ask() == (10010, 50)

    book.apply(_delete(1))
    book.apply(_delete(2))
    book.assert_invariants()
    assert book.resting_count == 0


def test_partial_cancel_reduces_level():
    book = OrderBook("X")
    book.apply(_add(1, Side.BUY, 100, 10000))
    book.apply(_cancel(1, 30))
    book.assert_invariants()
    assert book.best_bid() == (10000, 70)


def test_execute_drains_order():
    book = OrderBook("X")
    book.apply(_add(1, Side.BUY, 100, 10000))
    book.apply(_execute(1, 100))
    book.assert_invariants()
    assert book.resting_count == 0
    assert book.best_bid() is None


def test_unknown_order_id_is_silently_skipped():
    """Slice spikes can join mid-stream; events for unseen orders must not crash."""
    book = OrderBook("X")
    book.apply(_delete(999))
    book.apply(_cancel(999, 50))
    book.apply(_execute(999, 50))
    book.assert_invariants()
    assert book.resting_count == 0


_PRICE = st.integers(min_value=1, max_value=1_000_000)
_SHARES = st.integers(min_value=1, max_value=10_000)
_ORDER_ID = st.integers(min_value=1, max_value=10_000)
_SIDE = st.sampled_from([Side.BUY, Side.SELL])


@st.composite
def _event_stream(draw):
    n = draw(st.integers(min_value=1, max_value=50))
    events: list[BookEvent] = []
    live: dict[int, tuple[Side, int, int]] = {}
    next_id = 1
    for _ in range(n):
        choice = draw(st.sampled_from(["add", "delete", "cancel"])) if live else "add"
        if choice == "add":
            oid = next_id
            next_id += 1
            side = draw(_SIDE)
            shares = draw(_SHARES)
            # Bias prices to avoid creating crossed books in pathological draws.
            if side is Side.BUY:
                price = draw(st.integers(min_value=1, max_value=10_000))
            else:
                price = draw(st.integers(min_value=10_001, max_value=20_000))
            events.append(_add(oid, side, shares, price))
            live[oid] = (side, shares, price)
        elif choice == "delete":
            oid = draw(st.sampled_from(list(live.keys())))
            events.append(_delete(oid))
            del live[oid]
        else:  # cancel
            oid = draw(st.sampled_from(list(live.keys())))
            _, sh, pr = live[oid]
            cancel_qty = draw(st.integers(min_value=1, max_value=sh))
            events.append(_cancel(oid, cancel_qty))
            new_sh = sh - cancel_qty
            if new_sh <= 0:
                del live[oid]
            else:
                live[oid] = (live[oid][0], new_sh, pr)
    return events


@given(_event_stream())
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_invariants_hold_through_random_streams(events):
    book = OrderBook("X")
    for ev in events:
        book.apply(ev)
        book.assert_invariants()
