# Changelog

All notable changes to obscura.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0a1]

Initial pre-alpha.

### Added
- ITCH 5.0 wire-format parser (iterate-y reference + column-oriented numpy fast path).
- `OrderBook` with Hypothesis property tests on book invariants.
- Polars snapshot of top-N levels.
- Event-driven simulator with configurable `Latency` and pluggable queue models:
  `ProbabilisticQueueModel` (Cont 2010 intra-level approximation) and
  `InstantFillQueueModel` (naive baseline).
- Three baseline strategies: `PennyMM`, `OBISignal`, `MeanReversion`.
- Three-component slippage attribution (spread / queue-loss / adverse-selection).
- Honesty-gap report comparing naive vs queue-aware fills on the same code path.
- Streamlit dashboard with tick-by-tick replay, queue-position decay live chart,
  and slippage attribution stacked bar.
- CLI: `download`, `parse`, `book`, `analyze honesty-gap`.
- CI on GitHub Actions across Python 3.11 and 3.12.
