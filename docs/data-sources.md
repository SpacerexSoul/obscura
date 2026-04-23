# Data Sources

> NASDAQ ITCH 5.0 historical files via the public NASDAQ EMI directory.

## Primary feed

**NASDAQ EMI** publishes raw ITCH 5.0 binary files at
`https://emi.nasdaq.com/ITCH/Nasdaq ITCH/`. No account, no API key, no
credit card. These are the canonical NASDAQ market-by-order tick files —
every order arrival, modification, cancellation, and fill at nanosecond
resolution.

This is the gold-standard MBO feed for equities research. Coverage: full-
market NASDAQ daily snapshots, multiple days through 2020 in v5.0 binary,
plus newer subdirectories. **Vintage doesn't matter for an infrastructure
tool** — order book mechanics in 2019 are the same as today.

## Available samples (verified 2026-05-05)

```
/ITCH/Nasdaq ITCH/
  01302019.NASDAQ_ITCH50.gz      4.76 GB
  01302020.NASDAQ_ITCH50.gz      5.60 GB
  03272019.NASDAQ_ITCH50.gz      5.51 GB
  07302019.NASDAQ_ITCH50.gz      3.66 GB
  08302019.NASDAQ_ITCH50.gz      4.08 GB
  10302019.NASDAQ_ITCH50.gz      3.87 GB
  12302019.NASDAQ_ITCH50.gz      3.52 GB
  S071321-v50.txt.gz             6.00 GB    (Jul 13, 2021)
  ...
  /FEB 2022 Files/               (subdirectory)
  /NOII/                         (Net Order Imbalance Indicator)
  S010303-v2.zip                 58.9 MB    (smaller v2 sample)
```

## Operational notes

- **File size**: 3.5–5.6 GB compressed per day. Don't commit raw files —
  they go to `data/itch-samples/` (gitignored).
- **Download workflow**: `obscura download --date 12302019` (or via
  `make download DATE=12302019`). ~10–15 min on a typical home connection.
- **Demo workflow**: download once → preprocess to single-symbol Parquet
  → ship Parquet (~50 MB) inside the repo for fast `make demo` runs.
  Recruiters cloning the repo do not need to wait 15 min.

## Wire format notes (for the `obscura.book` parser)

- Each message is prefixed with a **2-byte big-endian length** in the
  gzipped file. The standalone ITCH 5.0 spec doesn't define this prefix —
  it's a NASDAQ EMI distribution convention.
- Message types relevant to book reconstruction:
  - `A` (Add Order, 36 bytes), `F` (Add Order with MPID, 40 bytes)
  - `E` (Order Executed, 31 bytes), `C` (Order Executed with Price, 36 bytes)
  - `X` (Order Cancel partial, 23 bytes), `D` (Order Delete, 19 bytes)
  - `U` (Order Replace, 35 bytes)
  - `P` (Trade non-cross, 44 bytes), `Q` (Cross Trade, 40 bytes)
- Message types relevant to admin / symbol-mapping:
  - `R` (Stock Directory, 39 bytes) — symbol → stock_locate map
  - `H` (Stock Trading Action, 25 bytes), `Y` (Reg SHO, 20 bytes)
  - `S` (System Event, 12 bytes) — open/close markers
- Prices are unsigned 32-bit ints in 1/10000 dollars. Quantities are
  unsigned 32-bit ints. Order IDs are unsigned 64-bit ints. All big-endian.
- Timestamps are 6-byte big-endian unsigned ints, nanoseconds since
  midnight ET.

## Spec

- NASDAQ. [*TotalView-ITCH 5.0 Specification*](https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/NQTVITCHspecification.pdf).
