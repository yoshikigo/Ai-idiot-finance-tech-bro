"""
title: FT Terminal 金融終端
author: fin-terminal
version: 0.15.0
description: 台股／美股即時報價、台股 ETF（淨值、折溢價、配息、成分股、排行）、境內外基金淨值、市場總覽、今日焦點、供應鏈地圖（含美國同類對照）、市場重大事件、中文新聞、世界事件、TimesFM 預測、Laya 判斷、策略選股，以及本機 AI 投資分析（TradingAgents、FinGPT、投資大師、Qlib、FinRL）。資料來自自架的 FT Terminal（不含任何人的持股）。
required_open_webui_version: 0.5.0
"""

import asyncio
import time
from typing import Any, Awaitable, Callable, Optional

import aiohttp
from pydantic import BaseModel, Field

Emitter = Optional[Callable[[dict], Awaitable[None]]]


class Tools:
    class Valves(BaseModel):
        FT_URL: str = Field(default="http://127.0.0.1:8130",
                            description="FT Terminal 位址（Open WebUI 是 Network=host，用 127.0.0.1）")
        MAX_WAIT: int = Field(default=900, description="AI 分析最多等幾秒，超過就回傳工作編號，之後用 get_ai_result 查")
        TIMEOUT: int = Field(default=90, description="一般查詢逾時秒數")

    def __init__(self):
        self.valves = self.Valves()

    # ------------------------------------------------------------------ helpers
    async def _get(self, path: str, params: dict, timeout: Optional[int] = None) -> str:
        url = f"{self.valves.FT_URL.rstrip('/')}/api/agent/{path}"
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout or self.valves.TIMEOUT)) as s:
                async with s.get(url, params={k: str(v) for k, v in params.items()}) as r:
                    text = await r.text()
                    if r.status >= 400:
                        return f"FT Terminal 錯誤 {r.status}：{text[:500]}"
                    return text
        except Exception as e:  # noqa: BLE001
            return f"連不到 FT Terminal（{self.valves.FT_URL}）：{e}"

    async def _status(self, emit: Emitter, msg: str, done: bool = False):
        if emit:
            await emit({"type": "status", "data": {"description": msg, "done": done}})

    # ------------------------------------------------------------------ market data
    async def get_quote(self, symbols: str, __event_emitter__: Emitter = None) -> str:
        """
        取得股票、ETF、指數、匯率、商品或基金的最新報價。台股為證交所即時報價（量的單位是張），其他市場為延遲報價。
        :param symbols: 逗號分隔的標的，可用中文名稱或代號，例如 "台積電,0050,00679B,NVDA,^TWII,TWD=X"
        :return: 每檔一行：價格、漲跌、漲跌幅、高低、成交量、時間
        """
        await self._status(__event_emitter__, f"查詢報價：{symbols}")
        out = await self._get("quote", {"q": symbols})
        await self._status(__event_emitter__, "報價完成", True)
        return out

    async def get_security_detail(self, symbol: str, __event_emitter__: Emitter = None) -> str:
        """
        取得單一股票、ETF 或基金的完整資訊：報價、公司資料（台股含董事長、產業、成立與上市日）、估值與獲利指標、ETF 淨值與配息、近期中文新聞。
        :param symbol: 中文名稱或代號，例如 "鴻海"、"2330"、"AAPL"、"00878"
        :return: 多行文字摘要
        """
        await self._status(__event_emitter__, f"讀取 {symbol} 資料")
        out = await self._get("stock", {"q": symbol})
        await self._status(__event_emitter__, "完成", True)
        return out

    async def get_etf(self, code: str, include_holdings: bool = True, __event_emitter__: Emitter = None) -> str:
        """
        台股 ETF 詳細資料：市價、投信預估淨值、折溢價、前日淨值、規模、受益單位數增減（淨申購／買回）、經理公司、追蹤指數、配息頻率與近一年殖利率、前十大成分股。
        :param code: ETF 代號或名稱，例如 "0050"、"00878"、"00679B"、"元大高股息"
        :param include_holdings: 是否查成分股（第一次查詢較慢）
        :return: 多行文字
        """
        await self._status(__event_emitter__, f"查詢 ETF {code}")
        out = await self._get("etf", {"code": code, "holdings": "1" if include_holdings else "0"}, timeout=180)
        await self._status(__event_emitter__, "完成", True)
        return out

    async def rank_etfs(self, sort: str = "scale", category: str = "", limit: int = 15,
                        __event_emitter__: Emitter = None) -> str:
        """
        台股 ETF 排行（全部上市櫃 ETF，含債券與期貨 ETF）。
        :param sort: 排序：scale（規模）、premium（溢價最高）、discount（折價最深）、creation（單位數增加最多＝淨申購）、redemption（單位數減少最多＝淨買回）
        :param category: 篩選類別，可留空：股票型、高股息、債券、主動式、槓桿/反向、期貨/商品
        :param limit: 筆數（最多 50）
        :return: 排名清單
        """
        await self._status(__event_emitter__, "ETF 排行")
        out = await self._get("etf/rank", {"sort": sort, "category": category, "limit": limit})
        await self._status(__event_emitter__, "完成", True)
        return out

    async def search_funds(self, keywords: str, region: str = "", __event_emitter__: Emitter = None) -> str:
        """
        搜尋台灣可買的共同基金（境內基金、境外基金、期貨信託基金），回傳名稱、類型、公司、最新淨值、日變動與基金 id。
        :param keywords: 關鍵字，空白分隔，例如 "安聯 台灣科技"、"貝萊德 世界科技"、"PIMCO 收益"
        :param region: 可留空；或 境內、境外、期信
        :return: 基金清單（含 id，可再用 get_fund 查詳細）
        """
        await self._status(__event_emitter__, f"搜尋基金：{keywords}")
        out = await self._get("fund/search", {"q": keywords, "region": region, "limit": 15})
        await self._status(__event_emitter__, "完成", True)
        return out

    async def get_fund(self, fund: str, __event_emitter__: Emitter = None) -> str:
        """
        基金詳細資料：最新淨值、日變動、1週／1月／3月／6月／今年／1年／3年報酬、淨值歷史區間高低。
        :param fund: 基金 id（例如 "D:12345678"、"O:LU0011963245"）或基金名稱關鍵字
        :return: 多行文字
        """
        await self._status(__event_emitter__, f"查詢基金：{fund}")
        params = {"id": fund} if ":" in fund else {"q": fund}
        out = await self._get("fund", params)
        await self._status(__event_emitter__, "完成", True)
        return out

    async def market_overview(self, __event_emitter__: Emitter = None) -> str:
        """
        市場總覽：加權指數、櫃買指數、美股主要指數與費半、VIX、亞洲股市、美元／台幣、黃金、原油、美債殖利率、比特幣，以及 ETF 溢價警示。
        :return: 多行文字
        """
        await self._status(__event_emitter__, "市場總覽")
        out = await self._get("market", {})
        await self._status(__event_emitter__, "完成", True)
        return out

    async def todays_focus(self, __event_emitter__: Emitter = None) -> str:
        """
        今日焦點：主要指數、供應鏈題材（AI 先進封裝、散熱、光通訊…）今日漲跌排行、供應鏈強勢／弱勢股、三大法人買賣超、今日新聞標題（標上相關題材），以及已產生的 AI 今日重點。
        :return: 多行文字
        """
        await self._status(__event_emitter__, "整理今日焦點")
        out = await self._get("focus", {}, timeout=180)
        await self._status(__event_emitter__, "完成", True)
        return out

    async def supply_chain(self, query: str = "", __event_emitter__: Emitter = None) -> str:
        """
        供應鏈地圖。query 空白：各環節今日平均漲跌；query 是環節（CoWoS、散熱、ABF 載板、光通訊、AI 伺服器…）或個股（台積電、奇鋐、2345）：該環節的說明、上游、下游、成分股與今日報價。
        :param query: 環節名稱或個股，可空白
        :return: 多行文字
        """
        await self._status(__event_emitter__, f"供應鏈 {query or '總覽'}")
        out = await self._get("chain", {"q": query}, timeout=120)
        await self._status(__event_emitter__, "完成", True)
        return out

    async def market_events(self, days: int = 365, __event_emitter__: Emitter = None) -> str:
        """
        市場重大事件：近 N 天指數大漲大跌（加權、S&P 500、Nasdaq、費半，自動偵測並附 AI 依當時新聞寫的摘要）、Fed 利率決策日（含即將到來的）、使用者自訂事件，以及 2020 年起的歷史大事（疫情股災、升降息、SVB、日圓套利平倉、DeepSeek、對等關稅…）。適合回答「某月為什麼大跌」「下次 Fed 開會」「今年有哪些大事」。
        :param days: 往回看幾天（預設 365；要看 2020 年以來用 2500）
        :return: 多行文字，每行一件事件
        """
        await self._status(__event_emitter__, f"重大事件（近 {days} 天）")
        out = await self._get("events", {"days": days}, timeout=60)
        await self._status(__event_emitter__, "完成", True)
        return out

    async def analysis_track_record(self, __event_emitter__: Emitter = None) -> str:
        """
        AI 分析的歷史紀錄與準確度：每個 agent（TradingAgents、投資大師、FinGPT、TimesFM、Laya）過去的判斷，在 5／20／60 個交易日後用實際股價核對的命中率與超額報酬、最近一次 AI 檢討的結論與「給下次分析的提醒」、最近 15 筆分析。
        :return: 多行文字
        """
        await self._status(__event_emitter__, "AI 分析紀錄")
        out = await self._get("ledger", {}, timeout=60)
        await self._status(__event_emitter__, "完成", True)
        return out

    async def us_macro(self, __event_emitter__: Emitter = None) -> str:
        """
        美國總經（FRED 官方數據）：Fed 利率目標、2／10 年美債殖利率與利差（倒掛）、CPI／核心 CPI／核心 PCE 年增率、失業率、非農、初領失業金、GDP、消費者信心、衰退機率、高收益債利差、VIX、美元、M2、油價。
        :return: 多行文字（每項含最新值、日期、前期、一年前）
        """
        await self._status(__event_emitter__, "FRED 總經")
        out = await self._get("macro", {}, timeout=120)
        await self._status(__event_emitter__, "完成", True)
        return out

    async def twd_exchange_rates(self, currencies: str = "", __event_emitter__: Emitter = None) -> str:
        """
        新台幣匯率：1 外幣＝多少台幣（美元、日圓、人民幣、歐元、港幣、英鎊、韓元、新加坡幣、澳幣、泰銖…共 30 多種），含日／1 月／1 年變動。
        :param currencies: 幣別代碼，逗號分隔（例 "USD,JPY"）；空白＝主要幣別
        :return: 多行文字
        """
        await self._status(__event_emitter__, "台幣匯率")
        out = await self._get("twfx", {"q": currencies}, timeout=120)
        await self._status(__event_emitter__, "完成", True)
        return out

    async def technical_analysis(self, symbol: str, __event_emitter__: Emitter = None) -> str:
        """
        技術面：均線、RSI、KD、MACD、布林通道、ATR、一目均衡表、K 線型態（十字線、錘子、吞噬、晨星／夜星）、支撐壓力與規則化訊號。
        :param symbol: 標的：代號或中文名，例 2330、台積電、NVDA
        :return: Markdown 文字
        """
        await self._status(__event_emitter__, "技術面")
        out = await self._get("kit/tech", {"q": symbol}, timeout=180)
        await self._status(__event_emitter__, "完成", True)
        return out

    async def options_chain(self, symbol: str, __event_emitter__: Emitter = None) -> str:
        """
        美股／美國 ETF 選擇權：最近到期鏈（ATM±5 檔）、IV、Delta／Theta（Black-Scholes）、未平倉、Put/Call 比、max pain、隱含波動區間。
        :param symbol: 美股代號，例 NVDA、SPY
        :return: Markdown 文字
        """
        await self._status(__event_emitter__, "美股／美國 ETF 選擇權")
        out = await self._get("kit/options", {"q": symbol}, timeout=180)
        await self._status(__event_emitter__, "完成", True)
        return out

    async def analyst_research(self, symbol: str, __event_emitter__: Emitter = None) -> str:
        """
        研究與籌碼：分析師評等分布、目標價、最近升降評、內部人交易、機構持股；台股另有三大法人、融資券、本益比與殖利率。
        :param symbol: 標的，例 2330、AAPL
        :return: Markdown 文字
        """
        await self._status(__event_emitter__, "研究與籌碼")
        out = await self._get("kit/research", {"q": symbol}, timeout=180)
        await self._status(__event_emitter__, "完成", True)
        return out

    async def earnings_review(self, symbol: str, __event_emitter__: Emitter = None) -> str:
        """
        財報季：下次財報日、近 8 季 EPS 預估 vs 實際、驚喜幅度與財報後隔日／5 日股價反應；台股另有月營收與累計 EPS、毛利率。
        :param symbol: 標的，例 AAPL、2330
        :return: Markdown 文字
        """
        await self._status(__event_emitter__, "財報季")
        out = await self._get("kit/earnings", {"q": symbol}, timeout=180)
        await self._status(__event_emitter__, "完成", True)
        return out

    async def value_investing(self, symbol: str, __event_emitter__: Emitter = None) -> str:
        """
        價值投資檢查：Graham 數、NCAV（net-net）、Piotroski F-score 九項、ROE 與毛利率穩定度、自由現金流殖利率、護城河量化跡象。
        :param symbol: 個股代號，例 KO、2330
        :return: Markdown 文字
        """
        await self._status(__event_emitter__, "價值投資檢查")
        out = await self._get("kit/value", {"q": symbol}, timeout=180)
        await self._status(__event_emitter__, "完成", True)
        return out

    async def market_intel(self, market: str = "all", __event_emitter__: Emitter = None) -> str:
        """
        市場情報：美股 11 個類股 ETF 輪動（1 週／1 月／3 月／1 年）、台股與美股權值股今日強弱、1 個月動能、爆量與站上 200 日線比例（市場廣度）。
        :param market: all、tw 或 us
        :return: Markdown 文字
        """
        await self._status(__event_emitter__, "市場情報")
        out = await self._get("kit/intel", {"market": market}, timeout=180)
        await self._status(__event_emitter__, "完成", True)
        return out

    async def taiwan_macro(self, __event_emitter__: Emitter = None) -> str:
        """
        台灣總經：國發會景氣對策信號（分數與燈號）、領先／同時指標、經濟成長率（實質 GDP 年增）、CPI 與核心 CPI 年增率、失業率、
        央行重貼現率、金融業隔夜拆款利率、M1B／M2 年增率。問台灣景氣、燈號、央行、通膨、資金動能時使用。
        :return: 多行文字
        """
        await self._status(__event_emitter__, "台灣總經")
        out = await self._get("twmacro", {}, timeout=180)
        await self._status(__event_emitter__, "完成", True)
        return out

    async def fed_net_liquidity(self, __event_emitter__: Emitter = None) -> str:
        """
        Fed 淨流動性＝Fed 資產負債表 − 財政部 TGA 帳戶 − 隔夜逆回購（FRED，每週）：最新值、4／13／52 週變化、各組成、近兩年與 S&P 500 的相關係數。
        :return: 一段文字
        """
        await self._status(__event_emitter__, "Fed 淨流動性")
        out = await self._get("liquidity", {}, timeout=120)
        await self._status(__event_emitter__, "完成", True)
        return out

    async def fomc_documents(self, date: str = "", __event_emitter__: Emitter = None) -> str:
        """
        Fed 利率會議（FOMC）官方文件：聲明稿相對上次的逐句措辭變化、本機 AI 解讀、經濟預測摘要（SEP 中位數：GDP、失業率、PCE、聯邦基金利率，含前次比較）、會議紀要摘要。
        :param date: 會議決策日 YYYY-MM-DD；空白＝最近一次
        :return: 多行文字
        """
        await self._status(__event_emitter__, "FOMC 文件")
        out = await self._get("fomc", {"date": date}, timeout=180)
        await self._status(__event_emitter__, "完成", True)
        return out

    async def economic_calendar(self, days: int = 21, __event_emitter__: Emitter = None) -> str:
        """
        接下來的重要日程：Fed 利率決策、台灣央行理監事會、美國 CPI／非農就業／PCE 公布日。
        :param days: 往後幾天（1–120）
        :return: 多行文字
        """
        await self._status(__event_emitter__, "重要日程")
        out = await self._get("calendar", {"days": days}, timeout=60)
        await self._status(__event_emitter__, "完成", True)
        return out

    async def dca_backtest(self, symbols: str, amount: float = 5000, years: float = 5, days: str = "6",
                           __event_emitter__: Emitter = None) -> str:
        """
        定期定額回測：過去每月固定日期買進（台股、美股、ETF、基金 FUND:代號、比特幣 BTC-USD），算投入、市值、報酬、年化（XIRR）、最大回撤，並對比同金額第一期單筆投入。
        :param symbols: 標的，逗號分隔（最多 6 個），例 "0050,VOO,BTC-USD"
        :param amount: 每期金額（台幣）
        :param years: 回測年數
        :param days: 每月扣款日，例 "6" 或 "6,16,26"
        :return: 多行文字
        """
        await self._status(__event_emitter__, "定期定額回測")
        out = await self._get("dca", {"q": symbols, "amount": amount, "years": years, "days": days}, timeout=180)
        await self._status(__event_emitter__, "完成", True)
        return out

    async def sec_filings(self, symbol: str, __event_emitter__: Emitter = None) -> str:
        """
        美股 SEC EDGAR 官方資料：最近的申報（10-K 年報、10-Q 季報、8-K 重大事件與項目說明、Form 4 內部人、13D/G 大股東），以及 XBRL 年度財報關鍵數字與實際申報日。
        :param symbol: 美股代號，例如 NVDA、AAPL、TSM
        :return: 多行文字
        """
        await self._status(__event_emitter__, f"SEC {symbol}")
        out = await self._get("sec", {"q": symbol}, timeout=120)
        await self._status(__event_emitter__, "完成", True)
        return out

    async def get_news(self, query: str, __event_emitter__: Emitter = None) -> str:
        """
        取得某檔股票／ETF 或某個主題的近期中文財經新聞標題（台股用中文新聞）。
        :param query: 標的名稱、代號或主題，例如 "台積電"、"00919"、"聯準會 降息"
        :return: 新聞清單（日期、標題、來源）
        """
        await self._status(__event_emitter__, f"新聞：{query}")
        out = await self._get("news", {"q": query, "n": 12})
        await self._status(__event_emitter__, "完成", True)
        return out

    async def forecast_trend(self, symbol: str, days: int = 30, __event_emitter__: Emitter = None) -> str:
        """
        用 Google TimesFM 時間序列基礎模型預測未來走勢：中位數路徑、80% 區間（P10–P90）、收高於現價的機率，以及回測準確度。只根據過去價格，不含新聞與基本面。
        :param symbol: 股票、ETF、指數、匯率代號或中文名稱，例如 "0050"、"台積電"、"^TWII"；基金用 "FUND:<id>"（id 可先用 search_funds 查）
        :param days: 預測幾個交易日（5–256，預設 30）
        :return: 預測摘要文字
        """
        await self._status(__event_emitter__, f"TimesFM 預測 {symbol} {days} 日")
        out = await self._get("forecast", {"q": symbol, "h": days}, timeout=600)
        await self._status(__event_emitter__, "預測完成", True)
        return out

    async def laya_decision(self, symbol: str, days: int = 20, __event_emitter__: Emitter = None) -> str:
        """
        Laya 決策模型的交易判斷：買進／持有／觀望／賣出／放空 各自的百分比、多空淨值、收高機率、大跌風險與未來區間分布（約 1 秒，不用 LLM）。輸入是技術面、其他 AI 引擎結論、世界事件與英文新聞。Laya 是通用決策模型、未用金融資料訓練，回覆時要說明百分比是模型信心不是統計機率。
        :param symbol: 股票或 ETF 代號或中文名稱，例如 "2330"、"台積電"、"NVDA"
        :param days: 期間：5（一週）、20（一個月）、60（一季）
        :return: 判斷摘要文字
        """
        await self._status(__event_emitter__, f"Laya 判斷 {symbol}")
        out = await self._get("decide", {"q": symbol, "h": days if days in (5, 20, 60) else 20}, timeout=600)
        await self._status(__event_emitter__, "完成", True)
        return out

    async def stock_screener(self, idea: str = "", backtest: bool = True, __event_emitter__: Emitter = None) -> str:
        """
        FinLab 式台股選股＋回測：用官方資料（月營收、財報、本益比殖利率、三大法人、融資融券、股價）依條件選股，回測年化報酬、最大回撤、夏普，並列出今天符合條件的股票。
        :param idea: 內建策略名稱（營收成長動能、高殖利率低本益比、投信連續買超、創新高突破、高 ROE 品質成長、小型價值、融資減、法人買（籌碼集中））或一句中文選股想法；留空列出內建策略
        :param backtest: 是否回測（False 只給今天的選股名單，較快）
        :return: 策略條件、回測摘要、今天的選股名單
        """
        await self._status(__event_emitter__, "策略選股中（AI 轉條件＋回測）")
        out = await self._get("strategy", {"q": idea, "backtest": "1" if backtest else "0"}, timeout=900)
        await self._status(__event_emitter__, "完成", True)
        return out

    async def get_world_events(self, query: str = "", regions: str = "", __event_emitter__: Emitter = None) -> str:
        """
        現實世界即時訊號（God's Eye View 公開感測資料）：USGS 地震、軍機 ADS-B、船舶 AIS（荷莫茲、紅海、麻六甲、巴拿馬、南海、台海）、NOAA 熱帶氣旋、野火、火箭發射，附近期平常值與近 14 天值得注意的事件。用來判斷地緣政治、供應鏈、航運、天災對市場的風險。是感測資料不是新聞。
        :param query: 股票／ETF 代號或名稱（會自動挑相關區域，例如 2603 長榮 → 紅海、荷莫茲、麻六甲…），可留空
        :param regions: 指定區域，逗號分隔：台灣、日本、南海、荷莫茲、紅海、麻六甲、巴拿馬、墨西哥灣、美國西岸、全球；可留空
        :return: 各區域最新訊號與事件
        """
        await self._status(__event_emitter__, "讀取 God's Eye View 世界訊號")
        out = await self._get("world", {"q": query, "region": regions})
        await self._status(__event_emitter__, "完成", True)
        return out

    async def get_forecast_review(self, __event_emitter__: Emitter = None) -> str:
        """
        回顧過去的 AI 預測（TradingAgents、FinGPT、投資大師、TimesFM）後來準不準：各引擎方向命中率、TimesFM 實際落在 80% 區間的比例、最近幾筆的報酬。
        :return: 回顧摘要
        """
        await self._status(__event_emitter__, "計算預測回顧")
        out = await self._get("review", {}, timeout=180)
        await self._status(__event_emitter__, "完成", True)
        return out

    # ------------------------------------------------------------------ AI engines
    async def run_ai_analysis(self, engine: str, symbols: str, options: str = "",
                              __event_emitter__: Emitter = None) -> str:
        """
        啟動本機 AI 投資分析並等待結果（需要數分鐘）。
        engine 可選：
        - tradingagents：多代理人交易公司（技術/情緒/新聞/基本面分析師、多空辯論、風控、最終評等），單一標的，約 10–25 分鐘
        - fingpt：FinGPT 下週走勢預測（options 填 "mode=sentiment" 改做新聞情緒），單一標的，1–3 分鐘
        - hedgefund：巴菲特、蒙格等投資大師人格評估，可多檔
        - finrl：強化學習交易回測，2 檔以上
        - qlib：Alpha158 + LightGBM 量化選股回測（options 填 "universe=tw50" 或 "universe=twetf" 時 symbols 可留空）
        - timesfm：TimesFM 走勢預測完整報告（含回測圖與解讀），單一標的；只要數字用 forecast_trend 比較快
        :param engine: tradingagents、fingpt、hedgefund、finrl、qlib 或 timesfm
        :param symbols: 標的，逗號分隔，可用中文名稱，例如 "台積電" 或 "2330,2317,2454"
        :param options: 額外參數，格式 "key=value,key=value"，例如 "debate_rounds=2"、"mode=sentiment"、"universe=tw50"
        :return: 分析報告（Markdown），若超過等待時間則回傳工作編號與進度
        """
        body: dict[str, Any] = {"engine": engine, "q": symbols, "params": {}}
        for kv in [x for x in options.split(",") if "=" in x]:
            k, v = kv.split("=", 1)
            body["params"][k.strip()] = v.strip()
        base = self.valves.FT_URL.rstrip("/")
        await self._status(__event_emitter__, f"啟動 {engine} 分析 {symbols}")
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as s:
                async with s.post(f"{base}/api/agent/analyze", json=body) as r:
                    if r.status >= 400:
                        return f"無法啟動分析：{(await r.text())[:500]}"
                    job = await r.json()
        except Exception as e:  # noqa: BLE001
            return f"連不到 FT Terminal（{base}）：{e}"
        eng, jid = job["engine"], job["id"]
        deadline = time.time() + self.valves.MAX_WAIT
        last = ""
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
            while time.time() < deadline:
                try:
                    async with s.get(f"{base}/api/engines/{eng}/jobs/{jid}") as r:
                        j = await r.json()
                except Exception:  # noqa: BLE001
                    await asyncio.sleep(5)
                    continue
                if j["status"] in ("done", "error", "cancelled"):
                    break
                msg = f"{eng} {j.get('progress', 0):.0f}%｜{j.get('stage') or '排隊中'}"
                if msg != last:
                    await self._status(__event_emitter__, msg)
                    last = msg
                await asyncio.sleep(5)
        out = await self._get("job", {"engine": eng, "id": jid, "wait": 0})
        await self._status(__event_emitter__, f"{eng} 完成" if out.startswith("DONE") else f"{eng} 仍在執行（{jid}）", True)
        return out

    async def get_ai_result(self, engine: str, job_id: str, __event_emitter__: Emitter = None) -> str:
        """
        查詢先前啟動的 AI 分析結果或進度。
        :param engine: tradingagents、fingpt、hedgefund、finrl、qlib 或 timesfm
        :param job_id: run_ai_analysis 回傳的工作編號
        :return: 報告或進度（PENDING）
        """
        return await self._get("job", {"engine": engine, "id": job_id, "wait": 0})
