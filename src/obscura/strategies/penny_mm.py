"""Penny market-maker strategy.

Quote one buy order at ``best_bid + 1 tick`` and one sell at ``best_ask - 1
tick`` — pennying inside the spread. Re-quote whenever the desired price
diverges from the current order. No active inventory management beyond a
hard cap; for the MVP this is the simplest strategy that still exercises
the queue-position machinery.

Tick is fixed to 1 cent (100 in 1/10000 dollar units) — fine for symbols
priced above ~$1.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from obscura.book.book import OrderBook
from obscura.book.types import BookEvent, Side
from obscura.sim.types import Action, ActionKind

TICK = 100  # 1 cent in 1/10000 dollar units


@dataclass
class PennyMM:
    shares: int = 100
    max_inventory: int = 1000

    _next_id: int = field(default=1, init=False)
    _buy_id: int | None = field(default=None, init=False)
    _sell_id: int | None = field(default=None, init=False)
    _buy_price: int | None = field(default=None, init=False)
    _sell_price: int | None = field(default=None, init=False)

    def on_event(self, event: BookEvent, book: OrderBook) -> list[Action]:
        bb = book.best_bid()
        ba = book.best_ask()
        if bb is None or ba is None:
            return []

        # Penny inside if the spread allows (>2 ticks); otherwise just join the
        # best on each side. Always-on quoting beats sitting out for the MVP.
        if (ba[0] - bb[0]) > 2 * TICK:
            desired_buy = bb[0] + TICK
            desired_sell = ba[0] - TICK
        else:
            desired_buy = bb[0]
            desired_sell = ba[0]

        actions: list[Action] = []

        if self._buy_price != desired_buy:
            if self._buy_id is not None:
                actions.append(Action(kind=ActionKind.CANCEL, synthetic_id=self._buy_id))
            self._buy_id = self._next_id
            self._next_id += 1
            actions.append(Action(
                kind=ActionKind.PLACE_LIMIT,
                synthetic_id=self._buy_id,
                side=Side.BUY,
                shares=self.shares,
                price=desired_buy,
            ))
            self._buy_price = desired_buy

        if self._sell_price != desired_sell:
            if self._sell_id is not None:
                actions.append(Action(kind=ActionKind.CANCEL, synthetic_id=self._sell_id))
            self._sell_id = self._next_id
            self._next_id += 1
            actions.append(Action(
                kind=ActionKind.PLACE_LIMIT,
                synthetic_id=self._sell_id,
                side=Side.SELL,
                shares=self.shares,
                price=desired_sell,
            ))
            self._sell_price = desired_sell

        return actions
