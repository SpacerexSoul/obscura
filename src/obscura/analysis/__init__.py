"""Post-simulation analysis: slippage attribution, PnL decomposition."""
from obscura.analysis.slippage import (
    FillAttribution,
    SlippageReport,
    attribute_fill,
    attribute_result,
)

__all__ = [
    "FillAttribution",
    "SlippageReport",
    "attribute_fill",
    "attribute_result",
]
