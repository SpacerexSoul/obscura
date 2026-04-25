"""Single-symbol L2 order book reconstruction.

Tracks every resting order by ID, then aggregates by price level on demand.
This is the reference implementation; the M3 milestone replaces the dict
guts with Numba-jitted arrays for the hot path.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from obscura.book.types import BookEvent, MessageType, Side


@dataclass
class _RestingOrder:
    side: Side
    shares: int
    price: int


@dataclass
class OrderBook:
    """Reconstruct an L2 book for a single symbol from ITCH events.

    Every public method is O(1) amortised except the level snapshots, which
    are O(k log k) in the number of distinct price levels currently resting.
    """

    symbol: str
    _orders: dict[int, _RestingOrder] = field(default_factory=dict)
    _bid_qty: dict[int, int] = field(default_factory=dict)  # price -> total resting qty
    _ask_qty: dict[int, int] = field(default_factory=dict)
    last_timestamp_ns: int = 0

    @property
    def resting_count(self) -> int:
        return len(self._orders)

    def best_bid(self) -> tuple[int, int] | None:
        if not self._bid_qty:
            return None
        p = max(self._bid_qty)
        return p, self._bid_qty[p]

    def best_ask(self) -> tuple[int, int] | None:
        if not self._ask_qty:
            return None
        p = min(self._ask_qty)
        return p, self._ask_qty[p]

    def top_of_book(self, depth: int = 5) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
        bids = sorted(self._bid_qty.items(), key=lambda kv: -kv[0])[:depth]
        asks = sorted(self._ask_qty.items(), key=lambda kv: kv[0])[:depth]
        return bids, asks

    def apply(self, ev: BookEvent) -> None:
        """Apply one BookEvent. Caller is responsible for symbol filtering.

        Events for orders we never saw added (e.g. an EXECUTE arriving without
        a prior ADD because we joined mid-stream) are silently skipped.
        """
        self.last_timestamp_ns = ev.timestamp_ns
        t = ev.msg_type

        if t in (MessageType.ADD, MessageType.ADD_MPID):
            assert ev.side is not None
            self._orders[ev.order_id] = _RestingOrder(ev.side, ev.shares, ev.price)
            book = self._bid_qty if ev.side is Side.BUY else self._ask_qty
            book[ev.price] = book.get(ev.price, 0) + ev.shares
            return

        if t in (MessageType.EXECUTE, MessageType.EXECUTE_PRICE):
            existing = self._orders.get(ev.order_id)
            if existing is None:
                return
            self._reduce(ev.order_id, ev.shares)
            return

        if t is MessageType.CANCEL:
            existing = self._orders.get(ev.order_id)
            if existing is None:
                return
            self._reduce(ev.order_id, ev.shares)
            return

        if t is MessageType.DELETE:
            existing = self._orders.pop(ev.order_id, None)
            if existing is None:
                return
            book = self._bid_qty if existing.side is Side.BUY else self._ask_qty
            self._decrement_level(book, existing.price, existing.shares)
            return

        if t is MessageType.REPLACE:
            existing = self._orders.pop(ev.order_id, None)
            if existing is None:
                return
            book = self._bid_qty if existing.side is Side.BUY else self._ask_qty
            self._decrement_level(book, existing.price, existing.shares)
            self._orders[ev.new_order_id] = _RestingOrder(
                existing.side, ev.new_shares, ev.new_price
            )
            book[ev.new_price] = book.get(ev.new_price, 0) + ev.new_shares
            return

    def _reduce(self, order_id: int, shares: int) -> None:
        existing = self._orders[order_id]
        new_shares = existing.shares - shares
        book = self._bid_qty if existing.side is Side.BUY else self._ask_qty
        self._decrement_level(book, existing.price, min(shares, existing.shares))
        if new_shares <= 0:
            del self._orders[order_id]
        else:
            existing.shares = new_shares

    @staticmethod
    def _decrement_level(book: dict[int, int], price: int, shares: int) -> None:
        remaining = book.get(price, 0) - shares
        if remaining <= 0:
            book.pop(price, None)
        else:
            book[price] = remaining

    # --- Invariants for property-based testing -------------------------

    def assert_invariants(self) -> None:
        """Cheap consistency checks. O(N) in resting orders + price levels."""
        if self._orders:
            for ob in self._orders.values():
                assert ob.shares > 0, "resting order with non-positive shares"
                assert ob.price > 0, "resting order with non-positive price"

        for price, qty in self._bid_qty.items():
            assert qty > 0, f"bid level {price} has non-positive total qty {qty}"
        for price, qty in self._ask_qty.items():
            assert qty > 0, f"ask level {price} has non-positive total qty {qty}"

        bb = self.best_bid()
        ba = self.best_ask()
        if bb is not None and ba is not None:
            assert bb[0] < ba[0], f"book crossed: best bid {bb[0]} >= best ask {ba[0]}"

        # Aggregate consistency: per-level totals must equal sum of resting orders at that level.
        derived_bid: dict[int, int] = {}
        derived_ask: dict[int, int] = {}
        for ob in self._orders.values():
            target = derived_bid if ob.side is Side.BUY else derived_ask
            target[ob.price] = target.get(ob.price, 0) + ob.shares
        assert derived_bid == self._bid_qty, "bid level totals diverge from resting orders"
        assert derived_ask == self._ask_qty, "ask level totals diverge from resting orders"
