"""Latency models for the network + matching delay between strategy and book.

A single ``Latency`` instance carries two parameters:

- ``network_ns``: round-trip time for our action to reach the exchange.
  Default 1 ms (1_000_000 ns) — typical co-located but cross-rack.
- ``matching_jitter_ns``: extra jitter the matching engine adds. Default 0.

The simulator stamps each Action with ``arrives_at = event.ts + latency()``;
actions are processed in arrival-time order via a min-heap.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Latency:
    network_ns: int = 1_000_000
    matching_jitter_ns: int = 0

    def __call__(self) -> int:
        return self.network_ns + self.matching_jitter_ns
