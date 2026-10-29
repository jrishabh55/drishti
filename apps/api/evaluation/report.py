"""A saved run as the core's standalone HTML report page.

The core renderer escapes every value, renders markdown with raw HTML and
images off and declares a no-fetch CSP, so model text cannot inject markup.
Settings are an allowlist of the run's request: no endpoint or credential.
"""
from __future__ import annotations

from apps.api.schemas import RunDetail

_STATE_KEYS = ("market_report", "sentiment_report", "news_report", "fundamentals_report",
               "investment_plan", "trader_investment_plan", "final_trade_decision",
               "investment_debate_state", "risk_debate_state")


def render_html(detail: RunDetail) -> str:
    from tradingagents.report_html import render_report
    from tradingagents.reporting import _report_parts

    state = {k: getattr(detail, k) for k in _STATE_KEYS if getattr(detail, k) is not None}
    state["trade_date"] = detail.analysis_date
    if detail.rating:
        state["final_rating"] = detail.rating
    cfg = detail.config or {}
    settings = {
        "llm_provider": cfg.get("llm_provider"),
        "deep_think_llm": cfg.get("deep_thinker"),
        "quick_think_llm": cfg.get("shallow_thinker"),
        "analysts": cfg.get("analysts") or [],
        "max_debate_rounds": cfg.get("research_depth"),
        "max_risk_discuss_rounds": cfg.get("research_depth"),
        "version": "web",
    }
    return render_report(detail.ticker, state, settings, _report_parts(state))
