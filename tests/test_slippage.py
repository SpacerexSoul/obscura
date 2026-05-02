"""Slippage attribution tests — pinned and additive."""
from __future__ import annotations

from obscura.analysis import attribute_fill, attribute_result
from obscura.book.types import Side
from obscura.sim.types import Fill, MyOrder, SimResult


def _fill(side: Side, price: int, mid_at_fill: int, sid: int = 1, shares: int = 100) -> Fill:
    return Fill(
        synthetic_id=sid, side=side, shares=shares, price=price,
        timestamp_ns=0, cause="queue_drained", mid_at_fill=mid_at_fill,
    )


def _order(
    side: Side,
    price: int,
    mid_at_placement: int,
    mid_at_queue_head: int = 0,
    sid: int = 1,
) -> MyOrder:
    return MyOrder(
        synthetic_id=sid, side=side, shares=100, price=price,
        placed_at_ns=0, queue_position=0.0,
        mid_at_placement=mid_at_placement,
        best_opposite_at_placement=0,
        mid_at_queue_head=mid_at_queue_head,
    )


def test_passive_buy_no_drift_negative_spread_cost():
    """Buy at 9990, mid was 10000, no drift → spread cost is -10 (saving)."""
    o = _order(Side.BUY, price=9990, mid_at_placement=10000, mid_at_queue_head=10000)
    f = _fill(Side.BUY, price=9990, mid_at_fill=10000)
    a = attribute_fill(f, o)
    assert a.spread_cost == -10        # we paid 9990 vs mid 10000 → savings
    assert a.queue_loss == 0           # no drift
    assert a.adverse_selection == 0    # no drift
    assert a.total_cost == -10         # full saving


def test_components_sum_to_total_buy():
    """spread + queue_loss + adverse_selection == total_cost, always."""
    o = _order(Side.BUY, price=9990, mid_at_placement=10000, mid_at_queue_head=10005)
    f = _fill(Side.BUY, price=9990, mid_at_fill=10010)
    a = attribute_fill(f, o)
    assert a.spread_cost + a.queue_loss + a.adverse_selection == a.total_cost


def test_components_sum_to_total_sell():
    o = _order(Side.SELL, price=10010, mid_at_placement=10000, mid_at_queue_head=9995)
    f = _fill(Side.SELL, price=10010, mid_at_fill=9990)
    a = attribute_fill(f, o)
    assert a.spread_cost + a.queue_loss + a.adverse_selection == a.total_cost


def test_queue_loss_negative_when_market_drifts_with_buyer():
    """For a buyer, mid rising while waiting is *good* (we paid less than current).

    Cost convention: positive = we paid more, negative = saving. So queue_loss
    is negative when mid drifts up while a buyer's order waits in queue.
    Pinned arithmetic:
      total_cost       = sign * (P - M1) = +1 * (10000 - 10010) = -10 (saving)
      spread_cost      = sign * (P - M0) =  0
      queue_loss       = -sign * (M_head - M0) = -1 * (10005 - 10000) = -5
      adverse_selection= -sign * (M1 - M_head) = -1 * (10010 - 10005) = -5
      sum = 0 + (-5) + (-5) = -10
    """
    o = _order(Side.BUY, price=10000, mid_at_placement=10000, mid_at_queue_head=10005)
    f = _fill(Side.BUY, price=10000, mid_at_fill=10010)
    a = attribute_fill(f, o)
    assert a.spread_cost == 0
    assert a.queue_loss == -5
    assert a.adverse_selection == -5
    assert a.total_cost == -10


def test_queue_loss_when_market_drifts_against_buyer():
    """If we placed at price 9990 and mid stayed 10000 the whole time, fill was free."""
    o = _order(Side.BUY, price=9990, mid_at_placement=10000, mid_at_queue_head=10005)
    f = _fill(Side.BUY, price=9990, mid_at_fill=10010)
    a = attribute_fill(f, o)
    # spread_cost = 9990 - 10000 = -10
    # queue_loss = -(10005-10000) = -5
    # adverse_selection = -(10010-10005) = -5
    # total = -10 + -5 + -5 = -20
    assert a.spread_cost == -10
    assert a.queue_loss == -5
    assert a.adverse_selection == -5
    assert a.total_cost == -20  # we filled at 9990 vs current mid 10010 → 20 saving


def test_no_queue_head_falls_back_to_two_components():
    """If mid_at_queue_head is unset (0), queue_loss = 0; AS absorbs the drift."""
    o = _order(Side.BUY, price=10000, mid_at_placement=10000, mid_at_queue_head=0)
    f = _fill(Side.BUY, price=10000, mid_at_fill=10010)
    a = attribute_fill(f, o)
    assert a.queue_loss == 0
    assert a.adverse_selection == -10  # -(10010-10000) for buy = -10
    assert a.spread_cost == 0
    assert a.spread_cost + a.queue_loss + a.adverse_selection == a.total_cost


def test_attribute_result_aggregates_across_fills():
    result = SimResult()
    result.my_orders[1] = _order(Side.BUY, price=9990, mid_at_placement=10000, mid_at_queue_head=10000, sid=1)
    result.my_orders[2] = _order(Side.SELL, price=10010, mid_at_placement=10000, mid_at_queue_head=10000, sid=2)
    result.fills.append(_fill(Side.BUY, price=9990, mid_at_fill=10000, sid=1, shares=10))
    result.fills.append(_fill(Side.SELL, price=10010, mid_at_fill=10000, sid=2, shares=20))
    rep = attribute_result(result)
    # Buy fill: spread_cost = -10/share * 10 shares = -100
    # Sell fill: spread_cost = sign*(P-M0) = -1*(10010-10000) = -10/share * 20 = -200
    assert rep.spread_cost == -300
    assert rep.queue_loss == 0
    assert rep.adverse_selection == 0
    assert rep.total_cost == -300
    assert rep.total_shares == 30


def test_attribution_pct_handles_zero_total():
    """No fills → all percentages are zero, no division-by-zero crash."""
    rep = attribute_result(SimResult())
    pct = rep.attribution_pct
    assert pct["spread_cost"] == 0.0
    assert pct["queue_loss"] == 0.0
    assert pct["adverse_selection"] == 0.0


def test_attribution_pct_sums_to_100_when_fills_exist():
    """Per the contract: components must explain ≥95% of total."""
    result = SimResult()
    result.my_orders[1] = _order(Side.BUY, price=10000, mid_at_placement=10000, mid_at_queue_head=10005)
    result.fills.append(_fill(Side.BUY, price=10000, mid_at_fill=10010, shares=10))
    rep = attribute_result(result)
    pct = rep.attribution_pct
    s = pct["spread_cost"] + pct["queue_loss"] + pct["adverse_selection"]
    # Sum of components / |total| * 100 should equal +/-100 by the additive identity.
    assert abs(abs(s) - 100.0) < 0.5  # well within the 5% MVP DoD threshold
