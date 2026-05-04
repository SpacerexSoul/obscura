"""Event-driven simulator: market events + my actions → SimResult.

The simulator runs a single-symbol stream of BookEvents. For each market
event:

1. Process any pending Actions whose latency has elapsed by ``event.ts``.
2. Update queue positions for my live orders based on the market event.
3. Apply the market event to the book.
4. Ask the Strategy for new Actions; enqueue them with latency stamps.

After the stream ends, drain any pending Actions (they arrive after market
close — discarded) and return the SimResult.
"""
from __future__ import annotations

import heapq
from collections.abc import Iterable

from obscura.book.book import OrderBook
from obscura.book.types import BookEvent, MessageType, Side
from obscura.sim.latency import Latency
from obscura.sim.queue import ProbabilisticQueueModel, fillable_qty
from obscura.sim.types import Action, ActionKind, Fill, MyOrder, SimResult, Snapshot, Strategy


def _level_qty(book: OrderBook, side: Side, price: int) -> int:
    target = book._bid_qty if side is Side.BUY else book._ask_qty
    return target.get(price, 0)


def _current_mid(book: OrderBook) -> int:
    """Current mid price (1/10000 dollars). Returns 0 if either side empty."""
    bb = book.best_bid()
    ba = book.best_ask()
    if bb is None or ba is None:
        return 0
    return (bb[0] + ba[0]) // 2


def _fill_my_order(
    order: MyOrder,
    fillable: int,
    fill_price: int,
    timestamp_ns: int,
    cause: str,
    result: SimResult,
    mid_at_fill: int,
) -> None:
    if fillable <= 0:
        return
    fill_qty = min(fillable, order.remaining)
    if fill_qty <= 0:
        return
    order.filled_qty += fill_qty
    sign = 1 if order.side is Side.BUY else -1
    result.cash_change -= sign * fill_qty * fill_price
    result.inventory += sign * fill_qty
    result.fills.append(
        Fill(
            synthetic_id=order.synthetic_id,
            side=order.side,
            shares=fill_qty,
            price=fill_price,
            timestamp_ns=timestamp_ns,
            cause=cause,
            mid_at_fill=mid_at_fill,
        )
    )


def _update_my_orders_on_event(
    event: BookEvent,
    book: OrderBook,
    my_orders: dict[int, MyOrder],
    queue_model: ProbabilisticQueueModel,
    result: SimResult,
) -> None:
    """Update queue positions for my orders affected by the market event."""

    if not my_orders:
        return

    if event.msg_type in (MessageType.ADD, MessageType.ADD_MPID):
        return

    existing = book._orders.get(event.order_id)
    if existing is None:
        return

    affected_side = existing.side
    affected_price = existing.price
    level_qty_before = _level_qty(book, affected_side, affected_price)

    if event.msg_type is MessageType.DELETE:
        size = existing.shares
        is_trade = False
    elif event.msg_type is MessageType.CANCEL:
        size = min(event.shares, existing.shares)
        is_trade = False
    elif event.msg_type in (MessageType.EXECUTE, MessageType.EXECUTE_PRICE):
        size = min(event.shares, existing.shares)
        is_trade = True
    elif event.msg_type is MessageType.REPLACE:
        size = existing.shares
        is_trade = False
    else:
        return

    if size <= 0:
        return

    for order in my_orders.values():
        if not order.is_live:
            continue
        if order.side is not affected_side or order.price != affected_price:
            continue
        was_queued = order.queue_position > 0
        if is_trade:
            queue_model.on_trade(order, size)
        else:
            queue_model.on_cancel(order, size, level_qty_before)

        if was_queued and order.queue_position <= 0 and order.mid_at_queue_head == 0:
            order.mid_at_queue_head = _current_mid(book)

        # Only **trades** actually execute against our order. Cancels move us
        # forward in the queue but do not fill us — that takes a real trade.
        if is_trade:
            f = fillable_qty(order)
            if f > 0:
                _fill_my_order(
                    order,
                    f,
                    fill_price=order.price,
                    timestamp_ns=event.timestamp_ns,
                    cause="queue_drained",
                    result=result,
                    mid_at_fill=_current_mid(book),
                )


def _apply_strategy_action(
    action: Action,
    book: OrderBook,
    my_orders: dict[int, MyOrder],
    timestamp_ns: int,
) -> None:
    if action.kind is ActionKind.PLACE_LIMIT:
        if action.side is None:
            raise ValueError("PLACE_LIMIT requires side")
        level_qty = _level_qty(book, action.side, action.price)
        bb = book.best_bid()
        ba = book.best_ask()
        mid = _current_mid(book)
        opposite = ba[0] if (action.side is Side.BUY and ba is not None) else (
            bb[0] if (action.side is Side.SELL and bb is not None) else 0
        )
        my_orders[action.synthetic_id] = MyOrder(
            synthetic_id=action.synthetic_id,
            side=action.side,
            shares=action.shares,
            price=action.price,
            placed_at_ns=timestamp_ns,
            queue_position=float(level_qty),
            initial_level_qty=level_qty,
            mid_at_placement=mid,
            best_opposite_at_placement=opposite,
        )
    elif action.kind is ActionKind.CANCEL:
        order = my_orders.get(action.synthetic_id)
        if order is not None and order.is_live:
            order.cancelled = True
            order.cancelled_at_ns = timestamp_ns


def _snapshot(book: OrderBook, result: SimResult, ts: int) -> Snapshot:
    bb = book.best_bid()
    ba = book.best_ask()
    bbp, bbq = bb if bb is not None else (0, 0)
    bap, baq = ba if ba is not None else (0, 0)
    states = tuple(
        (o.synthetic_id, o.queue_position, o.filled_qty, o.price)
        for o in result.my_orders.values()
        if not o.cancelled
    )
    return Snapshot(
        timestamp_ns=ts,
        best_bid_price=bbp,
        best_bid_qty=bbq,
        best_ask_price=bap,
        best_ask_qty=baq,
        cash_change=result.cash_change,
        inventory=result.inventory,
        my_order_states=states,
    )


def run(
    events: Iterable[BookEvent],
    strategy: Strategy,
    *,
    symbol: str,
    latency: Latency | None = None,
    queue_model: ProbabilisticQueueModel | None = None,
    snapshot_every: int = 0,
) -> SimResult:
    """Drive ``strategy`` against the ``events`` stream for ``symbol``.

    Caller is responsible for filtering ``events`` to a single symbol. Pass
    ``snapshot_every=N`` to capture a Snapshot every N events for the
    dashboard's playback view; default 0 disables snapshotting.
    """
    latency = latency or Latency()
    queue_model = queue_model or ProbabilisticQueueModel()
    book = OrderBook(symbol=symbol)
    result = SimResult()
    pending: list[tuple[int, int, Action]] = []  # (arrives_at, seq, action)
    seq = 0

    for n_events, event in enumerate(events, start=1):
        while pending and pending[0][0] <= event.timestamp_ns:
            _, _, action = heapq.heappop(pending)
            _apply_strategy_action(action, book, result.my_orders, event.timestamp_ns)

        _update_my_orders_on_event(event, book, result.my_orders, queue_model, result)

        book.apply(event)

        actions = strategy.on_event(event, book)
        for a in actions:
            seq += 1
            arrives_at = event.timestamp_ns + latency()
            heapq.heappush(pending, (arrives_at, seq, a))

        if snapshot_every > 0 and n_events % snapshot_every == 0:
            result.snapshots.append(_snapshot(book, result, event.timestamp_ns))

    pending.clear()
    # Final mid for honest mark-to-market accounting.
    result.final_mid = _current_mid(book)
    if snapshot_every > 0:
        result.snapshots.append(_snapshot(book, result, book.last_timestamp_ns))
    return result
