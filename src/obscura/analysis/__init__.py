"""Post-simulation analysis: slippage attribution, PnL decomposition, honesty-gap."""
from obscura.analysis.honesty_gap import HonestyGapReport, compare_queue_models
from obscura.analysis.slippage import (
    FillAttribution,
    SlippageReport,
    attribute_fill,
    attribute_result,
)

__all__ = [
    "FillAttribution",
    "HonestyGapReport",
    "SlippageReport",
    "attribute_fill",
    "attribute_result",
    "compare_queue_models",
]
