"""OrderBook backed by ItchArrays — fast batch application.

Applies a slice of array events into a single-symbol OrderBook in one call.
Skips the per-event BookEvent dataclass round-trip that the iterate-y API
incurs.
"""
from __future__ import annotations

from obscura.book.array_parser import (
    TYPE_A,
    TYPE_C,
    TYPE_D,
    TYPE_E,
    TYPE_F,
    TYPE_U,
    TYPE_X,
    ItchArrays,
)
from obscura.book.book import OrderBook, _RestingOrder
from obscura.book.types import Side


def apply_array_batch(book: OrderBook, arrays: ItchArrays) -> int:
    """Apply every event in ``arrays`` to ``book`` in array order.

    Caller is responsible for filtering ``arrays`` to a single symbol.
    Returns the number of events actually applied (events for unknown
    order_ids are skipped, matching the iterate-y API).
    """
    msg_type = arrays["msg_type"]
    timestamp_ns = arrays["timestamp_ns"]
    order_id = arrays["order_id"]
    side = arrays["side"]
    shares = arrays["shares"]
    price = arrays["price"]
    new_order_id = arrays["new_order_id"]
    new_shares = arrays["new_shares"]
    new_price = arrays["new_price"]

    orders = book._orders
    bid_qty = book._bid_qty
    ask_qty = book._ask_qty
    n = msg_type.shape[0]
    applied = 0

    for i in range(n):
        t = msg_type[i]
        oid = int(order_id[i])

        if t in (TYPE_A, TYPE_F):
            sd = Side.BUY if side[i] == ord("B") else Side.SELL
            sh = int(shares[i])
            pr = int(price[i])
            orders[oid] = _RestingOrder(sd, sh, pr)
            level = bid_qty if sd is Side.BUY else ask_qty
            level[pr] = level.get(pr, 0) + sh
            applied += 1
        elif t in (TYPE_E, TYPE_C, TYPE_X):
            existing = orders.get(oid)
            if existing is None:
                continue
            sh = int(shares[i])
            level = bid_qty if existing.side is Side.BUY else ask_qty
            taken = min(sh, existing.shares)
            remaining = level.get(existing.price, 0) - taken
            if remaining <= 0:
                level.pop(existing.price, None)
            else:
                level[existing.price] = remaining
            new_sh = existing.shares - sh
            if new_sh <= 0:
                del orders[oid]
            else:
                existing.shares = new_sh
            applied += 1
        elif t == TYPE_D:
            existing = orders.pop(oid, None)
            if existing is None:
                continue
            level = bid_qty if existing.side is Side.BUY else ask_qty
            remaining = level.get(existing.price, 0) - existing.shares
            if remaining <= 0:
                level.pop(existing.price, None)
            else:
                level[existing.price] = remaining
            applied += 1
        elif t == TYPE_U:
            existing = orders.pop(oid, None)
            if existing is None:
                continue
            level = bid_qty if existing.side is Side.BUY else ask_qty
            remaining = level.get(existing.price, 0) - existing.shares
            if remaining <= 0:
                level.pop(existing.price, None)
            else:
                level[existing.price] = remaining
            new_oid = int(new_order_id[i])
            new_sh = int(new_shares[i])
            new_pr = int(new_price[i])
            orders[new_oid] = _RestingOrder(existing.side, new_sh, new_pr)
            level[new_pr] = level.get(new_pr, 0) + new_sh
            applied += 1

    if n > 0:
        book.last_timestamp_ns = int(timestamp_ns[n - 1])
    return applied
