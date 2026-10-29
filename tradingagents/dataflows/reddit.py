"""Compatibility alias for the relocated Reddit vendor."""
import sys
from tradingagents.dataflows.vendors import reddit
sys.modules[__name__] = reddit
