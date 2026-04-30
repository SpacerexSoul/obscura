"""Public sim types: Strategy protocol, Actions, MyOrder, Fill, SimResult.

The simulator is event-driven: a Strategy receives every market event plus
the current book and emits a list of Actions. Each Action incurs a latency
delay before the exchange "sees" it, after which it interacts with the book.

Prices and quantities are integer-valued in NASDAQ wire units (1/10000
dollars; whole shares). Conversion to floats happens at the presentation
layer only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from obscura.book.book import OrderBook
from obscura.book.types import BookEvent, Side


class ActionKind(StrEnum):
    PLACE_LIMIT = "PLACE_LIMIT"
    CANCEL = "CANCEL"


@dataclass(frozen=True, slots=True)
class Action:
    """Strategy → simulator instruction."""

    kind: ActionKind
    synthetic_id: int
    side: Side | None = None
    shares: int = 0
    price: int = 0


@dataclass(slots=True)
class MyOrder:
    """One of *our* orders, tracked separately from market resting orders."""

    synthetic_id: int
    side: Side
    shares: int
    price: int
    placed_at_ns: int
    queue_position: float       # fractional — Cont-style probabilistic decay
    filled_qty: int = 0
    cancelled: bool = False
    cancelled_at_ns: int = 0
    initial_level_qty: int = 0  # qty at level immediately after we joined

    @property
    def remaining(self) -> int:
        return max(0, self.shares - self.filled_qty)

    @property
    def is_live(self) -> bool:
        return not self.cancelled and self.remaining > 0


@dataclass(frozen=True, slots=True)
class Fill:
    """One execution against one of our orders."""

    synthetic_id: int
    side: Side
    shares: int
    price: int
    timestamp_ns: int
    cause: str  # "queue_drained" | "swept" — informational


@dataclass
class SimResult:
    fills: list[Fill] = field(default_factory=list)
    my_orders: dict[int, MyOrder] = field(default_factory=dict)
    cash_change: int = 0   # signed integer in 1/10000 dollars
    inventory: int = 0     # net shares held (positive = long)

    @property
    def gross_pnl_per_10000(self) -> int:
        """Realised cash change in 1/10000 dollars. Inventory is unmarked."""
        return self.cash_change

    def mark_to_market(self, mid_price: int) -> int:
        return self.cash_change + self.inventory * mid_price


@runtime_checkable
class Strategy(Protocol):
    """Receives every event; returns a list of Actions to enqueue."""

    def on_event(self, event: BookEvent, book: OrderBook) -> list[Action]: ...
