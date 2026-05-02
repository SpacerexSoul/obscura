"""Three baseline strategies: PennyMM, OBISignal, MeanReversion."""
from obscura.strategies.mean_rev import MeanReversion
from obscura.strategies.obi import OBISignal
from obscura.strategies.penny_mm import PennyMM

__all__ = ["MeanReversion", "OBISignal", "PennyMM"]
