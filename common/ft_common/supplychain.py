"""供應鏈地圖 — a hand-curated map of the AI / semiconductor supply chain (Taiwan-centred, with the
US / Japan / Korea links that move Taiwan stocks), live-priced from the existing quote layer.

Data model
  NODES   : id → {name, mkt, desc, kw (news keywords), cos: [(symbol-or-TW-code, name, role)]}
  EDGES   : (upstream id, downstream id) — "upstream supplies downstream"
  NETWORKS: id → {name, desc, nodes}: views of the same graph (a node can sit in several)

Everything here is structural knowledge (who makes what for whom), not market data; prices,
day changes and news are always fetched live. Edit freely — `validate()` runs in the tests.
"""
from __future__ import annotations

import re
import statistics
import time

from . import market, twmarket

# ------------------------------------------------------------------ nodes
NODES: dict[str, dict] = {
    "semi-equip": {
        "name": "國際半導體設備", "mkt": "US", "kw": ["半導體設備", "微影", "EUV"],
        "desc": "前段製程的曝光、蝕刻、沉積、量測設備。晶圓廠與先進封裝擴產時最先下單的環節，資本支出的領先指標。",
        "cos": [("ASML", "ASML 艾司摩爾", "EUV／DUV 曝光機"), ("AMAT", "應用材料", "沉積、蝕刻、離子佈植"),
                ("LRCX", "科林研發", "蝕刻、沉積"), ("KLAC", "科磊", "製程量測與檢測"),
                ("8035.T", "東京威力科創", "塗佈顯影、蝕刻、沉積"), ("6146.T", "DISCO", "晶圓切割與研磨")]},
    "tw-equip": {
        "name": "台系設備與廠務", "mkt": "TW", "kw": ["廠務", "濕製程", "無塵室", "CoWoS設備"],
        "desc": "台廠在先進封裝濕製程、點膠／貼合、晶圓傳載與無塵室工程的在地供應，隨台積電與封測廠擴產接單。",
        "cos": [("3131", "弘塑", "先進封裝濕製程設備"), ("3583", "辛耘", "濕製程設備、再生晶圓"),
                ("6187", "萬潤", "點膠、貼合等封裝設備"), ("6640", "均華", "晶片挑揀、黏晶設備"),
                ("3680", "家登", "光罩盒、晶圓傳載盒"), ("3413", "京鼎", "半導體設備零組件代工"),
                ("6196", "帆宣", "廠務工程、設備代理"), ("2404", "漢唐", "無塵室與廠務統包工程")]},
    "wafer-material": {
        "name": "矽晶圓與耗材", "mkt": "TW", "kw": ["矽晶圓", "環球晶", "鑽石碟"],
        "desc": "晶圓製造的基礎材料：拋光矽晶圓、磊晶片，以及 CMP 研磨用鑽石碟等耗材。",
        "cos": [("6488", "環球晶", "矽晶圓"), ("5483", "中美晶", "矽晶圓（環球晶母公司）"),
                ("3532", "台勝科", "12 吋矽晶圓"), ("1560", "中砂", "CMP 鑽石碟、再生晶圓")]},
    "ip": {
        "name": "矽智財 IP", "mkt": "TW", "kw": ["矽智財"],
        "desc": "授權給晶片設計公司的記憶體、介面等 IP，依授權金與權利金收費，先進製程投片越多權利金越高。",
        "cos": [("3529", "力旺", "嵌入式非揮發性記憶體 IP"), ("6643", "M31", "基礎元件與高速介面 IP")]},
    "asic": {
        "name": "客製 ASIC 設計服務", "mkt": "TW", "kw": ["ASIC", "設計服務", "客製化晶片"],
        "desc": "替雲端業者、系統廠設計客製 AI 加速器與網通晶片，串接晶圓代工與先進封裝產能（turnkey）。",
        "cos": [("3443", "創意", "台積電體系設計服務"), ("3661", "世芯-KY", "高效能運算 ASIC"),
                ("3035", "智原", "ASIC 設計服務"), ("2454", "聯發科", "雲端 ASIC、SerDes"),
                ("AVGO", "博通", "雲端客製 ASIC 龍頭"), ("MRVL", "邁威爾", "客製 ASIC、資料中心互連")]},
    "foundry": {
        "name": "晶圓代工", "mkt": "TW", "kw": ["晶圓代工", "先進製程", "3奈米", "2奈米"],
        "desc": "把晶片設計做成晶圓。AI 晶片集中在 3／2 奈米等先進製程，成熟製程則供應電源、驅動、周邊晶片。",
        "cos": [("2330", "台積電", "先進製程龍頭"), ("2303", "聯電", "成熟製程"),
                ("5347", "世界", "8 吋成熟製程"), ("6770", "力積電", "記憶體與邏輯代工")]},
    "abf": {
        "name": "ABF 載板", "mkt": "TW", "kw": ["ABF", "載板", "IC 載板"],
        "desc": "高階晶片封裝用的積層載板，AI 晶片面積大、層數多，是先進封裝的關鍵材料與常見瓶頸。",
        "cos": [("3037", "欣興", "ABF 載板龍頭"), ("8046", "南電", "ABF 載板"), ("3189", "景碩", "ABF／BT 載板"),
                ("4062.T", "揖斐電 Ibiden", "高階 ABF 載板")]},
    "cowos": {
        "name": "AI 先進封裝（CoWoS）", "mkt": "TW", "kw": ["CoWoS", "先進封裝", "SoIC", "FOPLP"],
        "desc": "把 GPU／ASIC 晶粒與 HBM 透過矽中介層整合成一顆模組（2.5D／3D 封裝），是 AI 晶片產能的關鍵瓶頸。",
        "cos": [("2330", "台積電", "CoWoS／SoIC 主要產能"), ("3711", "日月光投控", "OSAT、承接先進封裝外溢"),
                ("6239", "力成", "記憶體封測、面板級封裝")]},
    "test": {
        "name": "晶片測試與探針卡", "mkt": "TW", "kw": ["探針卡", "測試座", "晶圓測試", "IC測試"],
        "desc": "晶圓測試、成品測試與測試介面（探針卡、測試座）。AI 晶片測試時間長、良率要求高，測試需求隨之放大。",
        "cos": [("2449", "京元電子", "晶圓與成品測試"), ("6223", "旺矽", "探針卡"), ("6515", "穎崴", "測試座"),
                ("6510", "中華精測", "探針卡、測試板"), ("6857.T", "愛德萬測試", "SoC 測試機")]},
    "hbm": {
        "name": "HBM 高頻寬記憶體", "mkt": "KR", "kw": ["HBM", "高頻寬記憶體", "SK海力士", "三星"],
        "desc": "堆疊 DRAM，與 GPU 一起封在 CoWoS 中介層上。AI 加速器的記憶體頻寬幾乎全靠它，供應集中在三家。",
        "cos": [("000660.KS", "SK 海力士", "HBM 市占龍頭"), ("005930.KS", "三星電子", "HBM、DRAM"),
                ("MU", "美光", "HBM、DRAM")]},
    "tw-memory": {
        "name": "台系記憶體與模組", "mkt": "TW", "kw": ["DRAM", "NAND", "記憶體", "SSD"],
        "desc": "利基型 DRAM、NOR／NAND Flash 與 SSD 控制晶片、模組廠。報價隨 AI 帶動的 DRAM 排擠效應起伏。",
        "cos": [("2408", "南亞科", "DRAM"), ("2344", "華邦電", "利基型 DRAM、Flash"), ("2337", "旺宏", "NOR Flash"),
                ("8299", "群聯", "NAND 控制晶片、企業級 SSD"), ("3260", "威剛", "記憶體模組")]},
    "gpu": {
        "name": "GPU 與 AI 加速器", "mkt": "US", "kw": ["輝達", "NVIDIA", "GPU", "AI 晶片"],
        "desc": "資料中心 AI 訓練與推論的運算核心。設計在美國，晶圓、封裝、測試、伺服器組裝幾乎都在台灣完成。",
        "cos": [("NVDA", "輝達", "GPU、NVLink 機櫃"), ("AMD", "超微", "Instinct GPU"), ("INTC", "英特爾", "Gaudi、伺服器 CPU")]},
    "pcb-ccl": {
        "name": "伺服器 PCB 與銅箔基板", "mkt": "TW", "kw": ["PCB", "CCL", "銅箔基板", "高階板"],
        "desc": "AI 伺服器主板、加速器板與交換器板需要高層數 PCB 與低損耗銅箔基板（CCL），規格每代提升。",
        "cos": [("2383", "台光電", "高速 CCL"), ("6274", "台燿", "高速 CCL"), ("6213", "聯茂", "CCL"),
                ("2368", "金像電", "伺服器／交換器 PCB"), ("3044", "健鼎", "PCB"), ("8358", "金居", "高階銅箔")]},
    "connector": {
        "name": "高速連接器與線材", "mkt": "TW", "kw": ["連接器", "線材", "銅纜"],
        "desc": "CPU／GPU 插座、板對板高速連接器與機櫃內銅纜，機櫃級 AI 系統的內部互連。",
        "cos": [("3533", "嘉澤", "CPU／GPU 插座、連接器"), ("3665", "貿聯-KY", "高速線材組件"),
                ("6290", "良維", "線材與連接器")]},
    "optics": {
        "name": "光通訊與 CPO", "mkt": "TW", "kw": ["光通訊", "矽光子", "CPO", "光收發模組"],
        "desc": "資料中心伺服器與交換器之間的光收發模組、雷射晶片、光纖被動元件，以及共同封裝光學（CPO）。",
        "cos": [("3081", "聯亞", "雷射磊晶片"), ("4979", "華星光", "光收發模組"), ("3163", "波若威", "光纖被動元件"),
                ("3363", "上詮", "光纖連接、CPO 光學"), ("6442", "光聖", "光纖跳線、配線"), ("4977", "眾達-KY", "光收發模組"),
                ("3450", "聯鈞", "光通訊元件"), ("COHR", "Coherent", "光收發模組、雷射")]},
    "thermal": {
        "name": "散熱與液冷", "mkt": "TW", "kw": ["散熱", "水冷", "液冷", "均熱片"],
        "desc": "GPU 功耗一路上升，散熱從氣冷走向水冷板、分歧管、CDU 液冷系統，單機櫃散熱價值大幅提高。",
        "cos": [("3017", "奇鋐", "水冷板、氣冷模組"), ("3324", "雙鴻", "液冷模組"), ("3653", "健策", "均熱片"),
                ("2421", "建準", "風扇"), ("8996", "高力", "熱交換器、CDU")]},
    "power": {
        "name": "伺服器電源", "mkt": "TW", "kw": ["電源供應器", "伺服器電源"],
        "desc": "伺服器與機櫃電源供應器（PSU）、電源架，AI 機櫃功率提高，帶動高瓦數電源需求。",
        "cos": [("2308", "台達電", "伺服器電源、資料中心電力"), ("2301", "光寶科", "伺服器電源"), ("6412", "群電", "電源供應器")]},
    "bbu": {
        "name": "BBU 備援電池", "mkt": "TW", "kw": ["BBU", "備援電池"],
        "desc": "機櫃內建備援電池模組，市電中斷或負載瞬變時供電，AI 機櫃逐步標配。",
        "cos": [("6781", "AES-KY", "BBU 電池模組"), ("3211", "順達", "電池模組")]},
    "chassis": {
        "name": "機殼與滑軌", "mkt": "TW", "kw": ["機殼", "滑軌"],
        "desc": "伺服器機殼、機櫃結構件與伺服器滑軌，機櫃級出貨讓結構件規格與單價同步提高。",
        "cos": [("8210", "勤誠", "伺服器機殼"), ("2059", "川湖", "伺服器滑軌"), ("3013", "晟銘電", "機殼、機櫃"),
                ("6117", "迎廣", "機殼")]},
    "passive": {
        "name": "被動元件", "mkt": "TW", "kw": ["被動元件", "MLCC"],
        "desc": "MLCC、電阻、電感等，AI 伺服器主板用量與規格都比一般伺服器高。",
        "cos": [("2327", "國巨", "MLCC、電阻"), ("2492", "華新科", "MLCC")]},
    "server-odm": {
        "name": "AI 伺服器組裝", "mkt": "TW", "kw": ["AI伺服器", "AI 伺服器", "伺服器代工", "機櫃"],
        "desc": "把 GPU 基板、CPU、記憶體、散熱、電源整合成伺服器與整櫃系統，出貨給雲端業者與品牌商。",
        "cos": [("2317", "鴻海", "GPU 模組與整櫃組裝"), ("2382", "廣達", "雲端伺服器 ODM"),
                ("3231", "緯創", "GPU 基板與伺服器"), ("6669", "緯穎", "雲端伺服器 ODM"),
                ("2356", "英業達", "伺服器 ODM"), ("2376", "技嘉", "伺服器、主機板"), ("2357", "華碩", "伺服器"),
                ("SMCI", "美超微", "AI 伺服器品牌"), ("DELL", "戴爾", "AI 伺服器品牌")]},
    "switch": {
        "name": "資料中心交換器", "mkt": "TW", "kw": ["交換器", "網通", "800G"],
        "desc": "連接 GPU 叢集的 400G／800G 乙太網路與後端網路交換器，台廠多以白牌代工出貨給雲端業者。",
        "cos": [("2345", "智邦", "白牌資料中心交換器"), ("3380", "明泰", "網通設備"), ("ANET", "Arista", "資料中心交換器")]},
    "grid": {
        "name": "重電與電網", "mkt": "TW", "kw": ["重電", "變壓器", "電網", "台電"],
        "desc": "變壓器、配電盤與電網設備。資料中心與晶圓廠用電大增，加上電網韌性投資，帶動重電需求。",
        "cos": [("1519", "華城", "變壓器"), ("1503", "士電", "變壓器、配電"), ("1513", "中興電", "GIS 開關設備"),
                ("1514", "亞力", "配電設備")]},
    "dc-power": {
        "name": "資料中心電力與散熱系統", "mkt": "US", "kw": ["UPS", "資料中心電力", "Vertiv"],
        "desc": "資料中心等級的 UPS、配電、冷卻與機房設施，跟著雲端業者蓋新資料中心的節奏。",
        "cos": [("VRT", "Vertiv", "UPS、機房液冷"), ("ETN", "伊頓", "配電與電力管理")]},
    "csp": {
        "name": "雲端服務商（CSP）", "mkt": "US", "kw": ["雲端業者", "資本支出", "CSP", "微軟", "Google", "亞馬遜", "Meta"],
        "desc": "AI 基礎建設的最終買家。它們的資本支出指引，是整條供應鏈需求最重要的先行訊號。",
        "cos": [("MSFT", "微軟", "Azure"), ("AMZN", "亞馬遜", "AWS、自研 Trainium"), ("GOOGL", "Alphabet", "Google Cloud、自研 TPU"),
                ("META", "Meta", "AI 基礎建設"), ("ORCL", "甲骨文", "OCI 雲端")]},
}

# ------------------------------------------------------------------ edges: upstream → downstream
EDGES: list[tuple[str, str]] = [
    ("semi-equip", "foundry"), ("semi-equip", "cowos"), ("semi-equip", "hbm"),
    ("tw-equip", "foundry"), ("tw-equip", "cowos"),
    ("wafer-material", "foundry"), ("wafer-material", "tw-memory"), ("wafer-material", "hbm"),
    ("ip", "asic"),
    ("foundry", "cowos"), ("abf", "cowos"), ("hbm", "cowos"),
    ("cowos", "test"), ("test", "gpu"), ("test", "asic"),
    ("asic", "csp"), ("gpu", "server-odm"),
    ("tw-memory", "server-odm"), ("pcb-ccl", "server-odm"), ("connector", "server-odm"), ("thermal", "server-odm"),
    ("power", "server-odm"), ("bbu", "server-odm"), ("chassis", "server-odm"), ("passive", "server-odm"),
    ("optics", "switch"), ("pcb-ccl", "switch"), ("optics", "server-odm"),
    ("server-odm", "csp"), ("switch", "csp"), ("grid", "dc-power"), ("dc-power", "csp"), ("thermal", "dc-power"),
]

NETWORKS: dict[str, dict] = {
    "ai-datacenter": {"name": "AI 資料中心", "desc": "從設備、晶圓、封裝到伺服器與雲端業者的整條 AI 基礎建設鏈。",
                      "nodes": list(NODES)},
    "semi": {"name": "半導體製造", "desc": "晶片從設備、材料、代工、封裝到測試的製造鏈。",
             "nodes": ["semi-equip", "tw-equip", "wafer-material", "ip", "asic", "foundry", "abf", "cowos", "test", "hbm",
                       "gpu"]},
    "memory": {"name": "記憶體", "desc": "HBM、DRAM、NAND 與它們在 AI 伺服器中的位置。",
               "nodes": ["semi-equip", "wafer-material", "hbm", "tw-memory", "cowos", "test", "gpu", "server-odm", "csp"]},
    "network": {"name": "高速網路", "desc": "光通訊、交換器、PCB 與連接器：GPU 叢集之間的資料怎麼跑。",
                "nodes": ["optics", "pcb-ccl", "connector", "switch", "server-odm", "csp"]},
    "power-cooling": {"name": "電力與散熱", "desc": "電源、備援電池、散熱與電網：AI 機櫃的功耗從哪裡來、熱往哪裡去。",
                      "nodes": ["power", "bbu", "thermal", "passive", "chassis", "grid", "dc-power", "server-odm", "csp"]},
}

MKT_NAME = {"TW": "台", "US": "美", "JP": "日", "KR": "韓"}

# 美國對照：同一個環節在美股的同類公司（US-listed）。比較時「美國」= 這裡 + 環節本身裡的美股。
US_PEERS: dict[str, list[tuple[str, str, str]]] = {
    "tw-equip": [("AMAT", "應用材料", "沉積、蝕刻"), ("LRCX", "科林研發", "蝕刻、沉積"), ("KLAC", "科磊", "量測檢測"),
                 ("ONTO", "Onto Innovation", "先進封裝量測"), ("ACMR", "盛美半導體", "濕製程清洗設備")],
    "wafer-material": [("ENTG", "Entegris", "製程材料、過濾與傳載耗材")],
    "ip": [("ARM", "安謀 Arm", "CPU 架構授權"), ("SNPS", "新思科技", "EDA、介面 IP"), ("CDNS", "益華電腦", "EDA、IP"),
           ("RMBS", "Rambus", "記憶體介面 IP")],
    "foundry": [("INTC", "英特爾", "Intel Foundry"), ("GFS", "格羅方德", "成熟製程代工")],
    "cowos": [("AMKR", "艾克爾 Amkor", "OSAT、先進封裝"), ("INTC", "英特爾", "Foveros／EMIB 封裝")],
    "test": [("TER", "泰瑞達", "SoC 測試機"), ("FORM", "FormFactor", "探針卡"), ("COHU", "Cohu", "測試分選設備")],
    "tw-memory": [("MU", "美光", "DRAM、NAND"), ("WDC", "威騰電子", "硬碟"), ("SNDK", "SanDisk", "NAND Flash、SSD")],
    "pcb-ccl": [("TTMI", "TTM Technologies", "高階 PCB"), ("SANM", "Sanmina", "PCB、系統組裝")],
    "connector": [("APH", "安費諾", "高速連接器、線纜"), ("TEL", "TE Connectivity", "連接器"), ("CRDO", "Credo", "主動式電纜 AEC、SerDes")],
    "optics": [("LITE", "Lumentum", "雷射、光模組"), ("AAOI", "Applied Optoelectronics", "光收發模組"), ("FN", "Fabrinet", "光模組代工")],
    "thermal": [("VRT", "Vertiv", "機櫃液冷 CDU"), ("MOD", "Modine", "資料中心冷卻")],
    "power": [("MPWR", "Monolithic Power", "伺服器電源管理 IC"), ("VICR", "Vicor", "高壓電源模組")],
    "passive": [("VSH", "Vishay", "被動元件")],
    "server-odm": [("HPE", "慧與 HPE", "AI 伺服器"), ("CLS", "Celestica", "雲端伺服器、交換器 ODM"), ("JBL", "捷普", "伺服器組裝")],
    "switch": [("CSCO", "思科", "網路設備")],
    "grid": [("GEV", "GE Vernova", "電網設備、燃氣渦輪"), ("PWR", "Quanta Services", "電網工程")],
}


# ------------------------------------------------------------------ helpers
def _is_tw_code(c: str) -> bool:
    return bool(re.fullmatch(r"\d{4,6}[A-Z]?", c))


def symbol_of(c: str) -> str:
    """'2330' → '2330.TW' / '5347.TWO' (from the securities master); foreign symbols unchanged."""
    if _is_tw_code(c):
        try:
            return twmarket.tw_symbol(c)
        except Exception:  # noqa: BLE001
            return f"{c}.TW"
    return c


def us_group(node_id: str) -> list[dict]:
    """US-listed companies for the 台／美 comparison: the node's own US names + US_PEERS (deduplicated)."""
    seen, out = set(), []
    for c in companies(node_id):
        if c["mkt"] == "US" and c["symbol"] not in seen:
            seen.add(c["symbol"])
            out.append({**c, "peer": False})
    for sym, name, role in US_PEERS.get(node_id, []):
        if sym not in seen:
            seen.add(sym)
            out.append({"symbol": sym, "code": sym, "name": name, "role": role, "mkt": "US", "peer": True})
    return out


def companies(node_id: str) -> list[dict]:
    n = NODES[node_id]
    out = []
    for c, name, role in n["cos"]:
        sym = symbol_of(c)
        out.append({"symbol": sym, "code": c, "name": (twmarket.zh_name(sym) if twmarket.is_tw(sym) else None) or name,
                    "role": role, "mkt": "TW" if twmarket.is_tw(sym) else _mkt_of(sym)})
    return out


def _mkt_of(sym: str) -> str:
    return "JP" if sym.endswith(".T") else "KR" if sym.endswith((".KS", ".KQ")) else "US"


def validate() -> list[str]:
    errs = []
    for a, b in EDGES:
        for x in (a, b):
            if x not in NODES:
                errs.append(f"edge {a}->{b}: unknown node {x}")
    for k, net in NETWORKS.items():
        for x in net["nodes"]:
            if x not in NODES:
                errs.append(f"network {k}: unknown node {x}")
    for k in US_PEERS:
        if k not in NODES:
            errs.append(f"US_PEERS: unknown node {k}")
    for k, n in NODES.items():
        if not n.get("cos"):
            errs.append(f"node {k}: no companies")
        for c in n["cos"]:
            if len(c) != 3 or not c[1]:
                errs.append(f"node {k}: bad company {c}")
    # acyclic
    seen, stack = set(), set()

    def visit(v):
        if v in stack:
            errs.append(f"cycle through {v}")
            return
        if v in seen:
            return
        stack.add(v)
        for a, b in EDGES:
            if a == v:
                visit(b)
        stack.discard(v)
        seen.add(v)
    for v in NODES:
        visit(v)
    return errs


def node_index() -> dict[str, list[str]]:
    """symbol → [node ids] (for 'which chains is this stock in')."""
    idx: dict[str, list[str]] = {}
    for k in NODES:
        for c in companies(k):
            idx.setdefault(c["symbol"], []).append(k)
    return idx


def find(q: str) -> dict | None:
    """Free text → {'node': id, 'symbol': sym|None}: matches node names/keywords, company names or codes."""
    q = (q or "").strip()
    if not q:
        return None
    ql = q.lower()
    if q in NODES:
        return {"node": q, "symbol": None}
    for k, n in NODES.items():
        if ql == n["name"].lower() or ql in n["name"].lower():
            return {"node": k, "symbol": None}
    for k, n in NODES.items():
        for c, name, _ in n["cos"]:
            if ql in (c.lower(), name.lower()) or (len(q) >= 2 and q in name):
                return {"node": k, "symbol": symbol_of(c)}
    for k, n in NODES.items():
        if any(ql == w.lower() or (len(q) >= 2 and ql in w.lower()) for w in n["kw"]):
            return {"node": k, "symbol": None}
    try:
        from . import context
        sym = context.resolve(q)
    except Exception:  # noqa: BLE001
        sym = None
    if sym:
        hits = node_index().get(sym)
        if hits:
            return {"node": hits[0], "symbol": sym}
    return None


# ------------------------------------------------------------------ layout
def layout(net_id: str) -> dict:
    """Columns by longest path (upstream left → downstream right), rows ordered by a barycenter sweep."""
    net = NETWORKS[net_id]
    ids = [n for n in net["nodes"] if n in NODES]
    inside = set(ids)
    edges = [(a, b) for a, b in EDGES if a in inside and b in inside]
    preds = {v: [a for a, b in edges if b == v] for v in ids}
    succs = {v: [b for a, b in edges if a == v] for v in ids}
    height: dict[str, int] = {}

    def ht(v):  # longest path to the end customer → right-aligned columns keep suppliers next to their buyers
        if v not in height:
            height[v] = 0 if not succs[v] else 1 + max(ht(x) for x in succs[v])
        return height[v]
    for v in ids:
        ht(v)
    top = max(height.values()) if height else 0
    depth = {v: top - height[v] for v in ids}
    ncol = top + 1 if ids else 0
    cols = [[v for v in ids if depth[v] == c] for c in range(ncol)]
    # crowded column: slide pure suppliers (nothing upstream of them here) into emptier columns on the left
    target = max(3, -(-len(ids) // max(ncol, 1)) + 1)
    for c in range(ncol - 1, 0, -1):
        movable = [v for v in cols[c] if not preds[v]]
        while len(cols[c]) > target and movable:
            dest = next((k for k in range(c - 1, -1, -1) if len(cols[k]) < target), None)
            if dest is None:
                break
            v = movable.pop()
            cols[c].remove(v)
            cols[dest].append(v)
    pos = {v: i for col in cols for i, v in enumerate(col)}
    for _ in range(4):
        for c in range(1, ncol):
            cols[c].sort(key=lambda v: statistics.mean([pos[p] for p in preds[v]]) if preds[v] else pos[v])
            pos.update({v: i for i, v in enumerate(cols[c])})
        for c in range(ncol - 2, -1, -1):
            cols[c].sort(key=lambda v: statistics.mean([pos[s] for s in succs[v]]) if succs[v] else pos[v])
            pos.update({v: i for i, v in enumerate(cols[c])})
    nodes = []
    for c, col in enumerate(cols):
        for r, v in enumerate(col):
            n = NODES[v]
            nodes.append({"id": v, "name": n["name"], "mkt": n["mkt"], "col": c, "row": r, "n": len(n["cos"])})
    return {"id": net_id, "name": net["name"], "desc": net["desc"], "cols": ncol,
            "rows": max((len(c) for c in cols), default=0), "nodes": nodes, "edges": edges}


def networks() -> list[dict]:
    return [{"id": k, "name": v["name"], "desc": v["desc"], "n": len(v["nodes"])} for k, v in NETWORKS.items()]


def node_detail(node_id: str) -> dict:
    n = NODES[node_id]
    return {"id": node_id, "name": n["name"], "mkt": n["mkt"], "desc": n["desc"], "kw": n["kw"],
            "companies": companies(node_id), "us_peers": us_group(node_id),
            "upstream": [{"id": a, "name": NODES[a]["name"], "mkt": NODES[a]["mkt"]} for a, b in EDGES if b == node_id],
            "downstream": [{"id": b, "name": NODES[b]["name"], "mkt": NODES[b]["mkt"]} for a, b in EDGES if a == node_id],
            "networks": [k for k, v in NETWORKS.items() if node_id in v["nodes"]]}


# ------------------------------------------------------------------ live stats
_stats_cache: dict = {}


def stats(node_ids: list[str] | None = None, ttl: float = 30) -> dict:
    """Live quotes for every company in the nodes + per-node day change (equal-weight mean; TW-only mean too)."""
    node_ids = node_ids or list(NODES)
    key = ",".join(sorted(node_ids))
    hit = _stats_cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    cos = {k: companies(k) for k in node_ids}
    us = {k: us_group(k) for k in node_ids}
    syms = list(dict.fromkeys([c["symbol"] for k in node_ids for c in cos[k]] + [c["symbol"] for k in node_ids for c in us[k]]))
    qs = {q["symbol"]: q for q in market.quotes(syms) if q.get("last") is not None}
    out_nodes = {}
    for k in node_ids:
        rows, pcts, tw = [], [], []
        for c in cos[k]:
            q = qs.get(c["symbol"], {})
            rows.append({**c, "last": q.get("last"), "pct": q.get("pct"), "chg": q.get("chg"), "lots": q.get("lots"),
                         "volume": q.get("volume"), "currency": q.get("currency")})
            if q.get("pct") is not None:
                pcts.append(q["pct"])
                if c["mkt"] == "TW":
                    tw.append(q["pct"])
        up = sum(1 for x in pcts if x > 0)
        us_rows = []
        for c in us[k]:
            q = qs.get(c["symbol"], {})
            us_rows.append({**c, "last": q.get("last"), "pct": q.get("pct"), "chg": q.get("chg"), "volume": q.get("volume")})
        us_p = [r["pct"] for r in us_rows if r["pct"] is not None]
        out_nodes[k] = {"id": k, "name": NODES[k]["name"], "mkt": NODES[k]["mkt"],
                        "pct": round(statistics.mean(pcts), 2) if pcts else None,
                        "tw_pct": round(statistics.mean(tw), 2) if tw else None,
                        "us_pct": round(statistics.mean(us_p), 2) if us_p else None, "us_n": len(us_rows),
                        "us_companies": us_rows,
                        "up": up, "down": sum(1 for x in pcts if x < 0), "n": len(rows), "quoted": len(pcts),
                        "companies": rows}
    res = {"nodes": out_nodes, "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    _stats_cache[key] = (time.time(), res)
    return res


def node_news(node_id: str, limit: int = 15) -> list[dict]:
    n = NODES[node_id]
    q = " OR ".join(f'"{w}"' if " " in w else w for w in n["kw"][:3])
    return twmarket.news_zh(q, limit, days=7)


def tag_text(text: str) -> list[str]:
    """Which nodes a headline is about (company names / codes / keywords)."""
    hits = []
    for k, n in NODES.items():
        names = [name for _, name, _ in n["cos"]] + [c for c, _, _ in n["cos"] if _is_tw_code(c)]
        names = [re.sub(r"-KY$|\s.*$", "", x) for x in names]
        if any(len(x) >= 2 and x in text for x in names) or any(w in text for w in n["kw"] if len(w) >= 2):
            hits.append(k)
    return hits
