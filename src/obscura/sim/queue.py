"""Cont-style probabilistic queue-position model.

The queue model answers: when a market event happens at the price level
where one of our orders is resting, how does our queue position change?

Two flavours of event matter:

- **Trades / executions at our level**: market eats from the front of the
  queue (FIFO). Our queue position decreases by ``shares`` deterministically.
  If our position drops below zero, we start filling.

- **Cancels / deletes at our level**: market participants cancel from
  *some* position in the queue. The Cont (2010) approximation treats cancel
  position as uniform over the resting queue — so a cancel of size ``s`` at
  a level with ``L`` resting shares decreases our position by an expected
  fraction ``s / L``.

Cont, Stoikov, Talreja (2010), "A Stochastic Model for Order Book Dynamics",
Operations Research 58(3): 549-563. We use the intra-level approximation,
not the full diffusion limit.
"""
from __future__ import annotations

from dataclasses import dataclass

from obscura.sim.types import MyOrder


@dataclass
class ProbabilisticQueueModel:
    """Apply queue-position updates to a single MyOrder.

    The model is stateless across orders; each call updates one order based
    on a single market event affecting that order's price level.
    """

    def on_trade(self, order: MyOrder, traded_shares: int) -> None:
        """Market trade ate ``traded_shares`` from the front of the level."""
        order.queue_position -= traded_shares

    def on_cancel(self, order: MyOrder, cancelled_shares: int, level_qty_before: int) -> None:
        """Cont (2010) intra-level approximation: cancel position is uniform."""
        if level_qty_before <= 0:
            return
        fraction = cancelled_shares / level_qty_before
        if fraction >= 1.0:
            order.queue_position = 0.0
        else:
            order.queue_position *= (1.0 - fraction)


def fillable_qty(order: MyOrder) -> int:
    """How many of our shares can fill right now given the queue position."""
    if order.queue_position > 0:
        return 0
    deficit = int(-order.queue_position)
    return min(order.remaining, max(deficit, order.remaining))
