"""Built-in strategies (classic Taiwan-market ideas, written in the expression language).

They are starting points to edit, not recommendations; every one is backtested on your own data.
"""
PRESETS = [
    {"id": "revenue_momentum", "name": "營收成長動能",
     "description": "近 3 個月平均營收創 12 個月新高、年增 >20%，股價站上季線；挑營收年增最高的 10 檔，月營收公布後換股。",
     "conditions": ["new_high(ma(月營收, 3), 12)", "營收年增率 > 20", "收盤價 > ma(收盤價, 60)"],
     "rank": "營收年增率", "ascending": False, "top_n": 10, "rebalance": "revenue", "stop_loss": 0.1},
    {"id": "value_yield", "name": "高殖利率低本益比",
     "description": "殖利率 >5%、本益比 <15、股價淨值比 <2 的價值股，依殖利率排名前 15 檔，每月換股。",
     "conditions": ["殖利率 > 5", "本益比 < 15", "本益比 > 0", "股價淨值比 < 2"],
     "rank": "殖利率", "ascending": False, "top_n": 15, "rebalance": "M"},
    {"id": "trust_buying", "name": "投信連續買超",
     "description": "投信連 5 日買超且近 10 日外資買超為正，股價在月線上；依近 10 日投信買超張數佔成交量比排名，每週換股。",
     "conditions": ["always(投信買賣超 > 0, 5)", "sum(外資買賣超, 10) > 0", "收盤價 > ma(收盤價, 20)"],
     "rank": "sum(投信買賣超, 10) / sum(成交量, 10)", "ascending": False, "top_n": 10, "rebalance": "W", "stop_loss": 0.08},
    {"id": "breakout", "name": "創新高突破",
     "description": "收盤創一年新高、近 5 日均量大於 60 日均量 1.5 倍；依 60 日漲幅排名前 10，每週換股，10% 停損。",
     "conditions": ["new_high(收盤價, 250)", "ma(成交量, 5) > 1.5 * ma(成交量, 60)"],
     "rank": "pct(收盤價, 60)", "ascending": False, "top_n": 10, "rebalance": "W", "stop_loss": 0.1},
    {"id": "quality", "name": "高 ROE 品質成長",
     "description": "ROE >15%、負債比 <50%、營業利益率 >10%、近四季 EPS 比上季高；依 ROE 排名前 20，財報公布後換股。",
     "conditions": ["ROE > 15", "負債比率 < 50", "營業利益率 > 10", "近四季每股盈餘 > shift(近四季每股盈餘, 1)"],
     "rank": "ROE", "ascending": False, "top_n": 20, "rebalance": "financial"},
    {"id": "small_value", "name": "小型價值",
     "description": "市值最小 30%、股價淨值比 <1.5、營收年增為正；依股價淨值比由低到高前 20 檔，每月換股。",
     "conditions": ["rank(市值) < 0.3", "股價淨值比 < 1.5", "營收年增率 > 0"],
     "rank": "股價淨值比", "ascending": True, "top_n": 20, "rebalance": "M"},
    {"id": "margin_clean", "name": "融資減、法人買（籌碼集中）",
     "description": "近 20 日融資餘額減少 10% 以上、三大法人近 10 日買超、股價在月線上；依法人買超排名前 10，每週換股。",
     "conditions": ["pct(融資餘額, 20) < -10", "sum(三大法人買賣超, 10) > 0", "收盤價 > ma(收盤價, 20)"],
     "rank": "sum(三大法人買賣超, 10)", "ascending": False, "top_n": 10, "rebalance": "W"},
]
BY_ID = {p["id"]: p for p in PRESETS}
