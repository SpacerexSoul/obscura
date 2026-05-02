"""Order Book Imbalance (OBI) signal strategy.

Compute the imbalance ``(bid_qty - ask_qty) / (bid_qty + ask_qty)`` summed
over the top ``depth`` levels each side. Take a directional position when
``|OBI|`` exceeds ``threshold``: long when bid-heavy, short when ask-heavy.
Cancel and flip when the signal flips.

Places passively at the best price on the chosen side (joins the queue).
For the MVP, position size is fixed; no inventory tracking beyond a single
live order at a time.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from obscura.book.book import OrderBook
from obscura.book.types import BookEvent, Side
from obscura.sim.types import Action, ActionKind


@dataclass
class OBISignal:
    depth: int = 5
    threshold: float = 0.4
    shares: int = 100

    _next_id: int = field(default=1, init=False)
    _live_id: int | None = field(default=None, init=False)
    _live_side: Side | None = field(default=None, init=False)

    def _compute_obi(self, book: OrderBook) -> float | None:
        bids, asks = book.top_of_book(depth=self.depth)
        if not bids or not asks:
            return None
        bq = sum(b[1] for b in bids)
        aq = sum(a[1] for a in asks)
        total = bq + aq
        if total == 0:
            return None
        return (bq - aq) / total

    def on_event(self, event: BookEvent, book: OrderBook) -> list[Action]:
        obi = self._compute_obi(book)
        if obi is None:
            return []
        bb = book.best_bid()
        ba = book.best_ask()
        if bb is None or ba is None:
            return []

        desired_side: Side | None = None
        if obi > self.threshold:
            desired_side = Side.BUY
        elif obi < -self.threshold:
            desired_side = Side.SELL

        if desired_side is self._live_side:
            return []  # signal stable, do nothing

        actions: list[Action] = []

        if self._live_id is not None:
            actions.append(Action(kind=ActionKind.CANCEL, synthetic_id=self._live_id))
            self._live_id = None
            self._live_side = None

        if desired_side is not None:
            self._live_id = self._next_id
            self._next_id += 1
            self._live_side = desired_side
            price = bb[0] if desired_side is Side.BUY else ba[0]
            actions.append(Action(
                kind=ActionKind.PLACE_LIMIT,
                synthetic_id=self._live_id,
                side=desired_side,
                shares=self.shares,
                price=price,
            ))

        return actions
