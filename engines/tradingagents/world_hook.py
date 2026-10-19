"""Give TradingAgents the real world: God's Eye View signals as (1) a short block in every agent's
instrument context and (2) a `get_world_events` tool for the News Analyst.

Point-in-time: for a past trade date only snapshots collected up to that date are used.
"""
import datetime as dt
from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from ft_common import world

_ORIG_TOOLS = None
ALIASES = {"台灣": "taiwan", "台海": "taiwan", "taiwan": "taiwan", "日本": "japan", "japan": "japan",
           "南海": "south_china_sea", "荷莫茲": "hormuz", "hormuz": "hormuz", "紅海": "red_sea", "red sea": "red_sea",
           "蘇伊士": "red_sea", "麻六甲": "malacca", "malacca": "malacca", "巴拿馬": "panama", "panama": "panama",
           "墨西哥灣": "gulf_mexico", "gulf": "gulf_mexico", "美國西岸": "us_west", "california": "us_west",
           "global": "global", "全球": "global"}


def as_of_for(trade_date: str):
    return None if not trade_date or trade_date >= dt.date.today().isoformat() else trade_date[:10]


def install(enabled: bool):
    """Add/remove the get_world_events tool on the News Analyst (module-level TOOLS tuple)."""
    global _ORIG_TOOLS
    from tradingagents.agents.analysts import news_analyst

    if _ORIG_TOOLS is None:
        _ORIG_TOOLS = tuple(t for t in news_analyst.TOOLS if t.name != "get_world_events")
    if not enabled:
        news_analyst.TOOLS = _ORIG_TOOLS
        return

    @tool
    def get_world_events(
        symbol: Annotated[str, InjectedState("company_of_interest")],
        trade_date: Annotated[str, InjectedState("trade_date")] = "",
        region: Annotated[str, "Region to inspect: 'auto' (regions relevant to the instrument), or one of "
                               "taiwan, japan, south_china_sea, hormuz, red_sea, malacca, panama, gulf_mexico, "
                               "us_west, global"] = "auto",
    ) -> str:
        """Live physical-world signals from God's Eye View public sensor feeds (USGS earthquakes, military
        ADS-B aircraft, AIS ships in chokepoints, NOAA cyclones, wildfires, rocket launches) for regions that
        matter to markets, with recent baselines and notable events of the last 7 days. Use it to check
        geopolitical / supply-chain / shipping / disaster risk around the instrument. These are sensor
        readings, not news; report what they show and say when a feed is unavailable."""
        key = ALIASES.get(str(region).strip().lower(), str(region).strip().lower())
        regions = world.regions_for(symbol) if key in ("", "auto") else [key]
        return world.digest(regions, as_of=as_of_for(trade_date))

    news_analyst.TOOLS = _ORIG_TOOLS + (get_world_events,)


def graph_class(base, compact_text):
    """TradingAgentsGraph subclass whose instrument context (seen by every agent) ends with the world block."""
    class WorldAwareGraph(base):
        def resolve_instrument_context(self, ticker, asset_type="stock", trade_date=None):
            ctx = super().resolve_instrument_context(ticker, asset_type, trade_date)
            return f"{ctx}\n\n{compact_text}" if compact_text else ctx
    return WorldAwareGraph
