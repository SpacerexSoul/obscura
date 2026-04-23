"""Typed records emitted by the ITCH parser.

We deliberately keep these as small dataclasses (frozen, slotted) rather than
NamedTuples — the parser hot path materialises millions per file; slot=True
gives us roughly the memory footprint of a tuple while staying readable.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Side(StrEnum):
    BUY = "B"
    SELL = "S"


class MessageType(StrEnum):
    """ITCH 5.0 message types relevant to book reconstruction.

    Spec: https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/NQTVITCHspecification.pdf
    """

    ADD = "A"           # Add Order (no MPID)
    ADD_MPID = "F"      # Add Order with MPID
    EXECUTE = "E"       # Order Executed
    EXECUTE_PRICE = "C" # Order Executed with Price (off-book print)
    CANCEL = "X"        # Order Cancel (partial)
    DELETE = "D"        # Order Delete (full)
    REPLACE = "U"       # Order Replace
    TRADE = "P"         # Non-cross trade (no resting order)
    CROSS_TRADE = "Q"   # Cross trade
    STOCK_DIRECTORY = "R"
    SYSTEM_EVENT = "S"


@dataclass(frozen=True, slots=True)
class BookEvent:
    """One event yielded by the ITCH parser. Untyped fields are zero when N/A.

    Prices are integer 1/10000 dollars (NASDAQ wire format). Convert to float
    via ``price / 10000`` only at the presentation layer.
    """

    msg_type: MessageType
    timestamp_ns: int
    order_id: int
    side: Side | None
    shares: int
    price: int
    stock: str
    new_order_id: int = 0     # set on REPLACE
    new_shares: int = 0       # set on REPLACE
    new_price: int = 0        # set on REPLACE
    match_number: int = 0     # set on EXECUTE / TRADE
