"""Mean-reversion strategy with streaming Welford statistics.

Track the rolling mean and variance of the mid price using Welford's
online algorithm (numerically stable, O(1) per update). When the current
mid deviates from the mean by more than ``z_entry`` standard deviations,
take a position betting on reversion. Cancel and flip when the z-score
crosses back through ``z_exit``.

Welford reference: B. P. Welford (1962), "Note on a method for calculating
corrected sums of squares and products", *Technometrics* 4: 419-420.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from obscura.book.book import OrderBook
from obscura.book.types import BookEvent, Side
from obscura.sim.types import Action, ActionKind


@dataclass
class _Welford:
    """Streaming mean + variance via Welford's online algorithm."""

    n: int = 0
    mean: float = 0.0
    m2: float = 0.0  # sum of squared deviations from running mean

    def update(self, x: float) -> None:
        self.n += 1
        delta = x - self.mean
        self.mean += delta / self.n
        self.m2 += delta * (x - self.mean)

    @property
    def variance(self) -> float:
        return self.m2 / (self.n - 1) if self.n > 1 else 0.0

    @property
    def stddev(self) -> float:
        return math.sqrt(self.variance) if self.variance > 0 else 0.0


@dataclass
class MeanReversion:
    warmup: int = 200      # ticks before we'll trade
    z_entry: float = 2.0
    z_exit: float = 0.3
    shares: int = 100

    _stats: _Welford = field(default_factory=_Welford, init=False)
    _next_id: int = field(default=1, init=False)
    _live_id: int | None = field(default=None, init=False)
    _live_side: Side | None = field(default=None, init=False)

    def on_event(self, event: BookEvent, book: OrderBook) -> list[Action]:
        bb = book.best_bid()
        ba = book.best_ask()
        if bb is None or ba is None:
            return []
        mid = (bb[0] + ba[0]) / 2.0
        self._stats.update(mid)

        if self._stats.n < self.warmup or self._stats.stddev <= 0:
            return []

        z = (mid - self._stats.mean) / self._stats.stddev

        desired_side: Side | None = None
        if self._live_side is not None:
            # In a position: exit (None) when |z| regresses below z_exit, else hold.
            desired_side = None if abs(z) < self.z_exit else self._live_side
        elif z > self.z_entry:
            desired_side = Side.SELL  # mid too high, expect drop
        elif z < -self.z_entry:
            desired_side = Side.BUY   # mid too low, expect bounce

        if desired_side is self._live_side:
            return []

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
