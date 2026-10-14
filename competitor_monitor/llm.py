"""The monitor uses the shared client in common/llm.py, with the monitor's own caps.

The caps come from competitor_monitor/config.py (MAX_LLM_CALLS_PER_RUN, MAX_COST_USD_PER_RUN),
read when the LLM object is created, so a test or a cron job can change them per run.
"""
from common.llm import LLM as _SharedLLM, BudgetExceeded, AgentDisabled  # noqa: F401

from . import config


class LLM(_SharedLLM):
    def __init__(self, stats, mock: dict | None = None):
        super().__init__(
            stats,
            mock=mock if mock is not None else ({"_default": True} if config.MOCK_LLM else {}),
            max_calls=config.MAX_LLM_CALLS_PER_RUN,
            max_cost=config.MAX_COST_USD_PER_RUN,
        )
