"""Three-component slippage attribution.

For every fill, we decompose the cost (relative to the mid at fill time)
into three additive components:

    total_cost = spread_cost + queue_loss + adverse_selection

where, with ``sign = +1`` for BUY and ``-1`` for SELL:

    spread_cost        = sign * (fill_price - mid_at_placement)
    queue_loss         = -sign * (mid_at_queue_head - mid_at_placement)
    adverse_selection  = -sign * (mid_at_fill - mid_at_queue_head)

These three terms sum to ``sign * (fill_price - mid_at_fill)`` by
construction (verified in tests with ``test_slippage_components_sum_to_total``).

Interpretation (BUY side; flip for SELL):

* **spread_cost** is what we paid relative to the mid at the moment our
  order arrived at the exchange. For a passive limit at the best bid,
  this is approximately ``-half_spread`` — i.e. a *negative* cost (we
  captured the spread).

* **queue_loss** is the cost of waiting in the queue: how much the mid
  drifted between placement and the moment our queue position first
  reached zero. If the market drifts up while we wait, queue_loss is
  positive (we missed the chance to buy lower).

* **adverse_selection** is how much the mid moved between us reaching
  the head of the queue and the actual fill. This isolates the cost
  of being filled at a moment when the market is moving against us.

If ``mid_at_queue_head`` is zero (queue never drained — happens for
swept fills, M5+), the attribution falls back to a 2-component split:
``spread_cost + adverse_selection``, with ``queue_loss = 0``.

Sign convention: positive = cost to us, negative = savings.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from obscura.book.types import Side
from obscura.sim.types import Fill, MyOrder, SimResult


@dataclass(frozen=True, slots=True)
class FillAttribution:
    """Per-fill slippage decomposition. All values in 1/10000 dollars."""

    fill: Fill
    total_cost: int
    spread_cost: int
    queue_loss: int
    adverse_selection: int

    @property
    def shares(self) -> int:
        return self.fill.shares

    @property
    def total_cost_dollars(self) -> float:
        return self.total_cost * self.shares / 10000.0


@dataclass
class SlippageReport:
    """Aggregate slippage across all fills in a SimResult."""

    fills: list[FillAttribution] = field(default_factory=list)
    total_shares: int = 0
    total_cost: int = 0          # signed integer 1/10000 $·shares
    spread_cost: int = 0
    queue_loss: int = 0
    adverse_selection: int = 0

    @property
    def total_cost_dollars(self) -> float:
        return self.total_cost / 10000.0

    @property
    def attribution_pct(self) -> dict[str, float]:
        """Components as percentages of |total|. Only meaningful when total != 0."""
        denom = abs(self.total_cost) or 1
        return {
            "spread_cost": 100.0 * self.spread_cost / denom,
            "queue_loss": 100.0 * self.queue_loss / denom,
            "adverse_selection": 100.0 * self.adverse_selection / denom,
        }


def attribute_fill(fill: Fill, order: MyOrder) -> FillAttribution:
    """Decompose one fill against the placement context stored on its MyOrder."""
    sign = 1 if order.side is Side.BUY else -1

    # Total: cost relative to mid_at_fill (using mid_at_placement if mid_at_fill
    # is missing — should not happen in practice but keeps the math defined).
    mid_at_fill = fill.mid_at_fill if fill.mid_at_fill else order.mid_at_placement
    total_cost = sign * (fill.price - mid_at_fill)

    spread_cost = sign * (fill.price - order.mid_at_placement)

    if order.mid_at_queue_head:
        queue_loss = -sign * (order.mid_at_queue_head - order.mid_at_placement)
        adverse_selection = -sign * (mid_at_fill - order.mid_at_queue_head)
    else:
        # No queue-head observation (e.g. swept fills); merge queue_loss into AS.
        queue_loss = 0
        adverse_selection = -sign * (mid_at_fill - order.mid_at_placement)

    return FillAttribution(
        fill=fill,
        total_cost=total_cost,
        spread_cost=spread_cost,
        queue_loss=queue_loss,
        adverse_selection=adverse_selection,
    )


def attribute_result(result: SimResult) -> SlippageReport:
    """Aggregate slippage across all fills in a completed simulation."""
    report = SlippageReport()
    for fill in result.fills:
        order = result.my_orders.get(fill.synthetic_id)
        if order is None:
            continue
        attr = attribute_fill(fill, order)
        report.fills.append(attr)
        report.total_shares += fill.shares
        report.total_cost += attr.total_cost * fill.shares
        report.spread_cost += attr.spread_cost * fill.shares
        report.queue_loss += attr.queue_loss * fill.shares
        report.adverse_selection += attr.adverse_selection * fill.shares
    return report
