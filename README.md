# FT 金融終端 — 繁中看盤站＋台股 ETF／基金＋本機 Ollama AI，接 Hermes 與 Open WebUI

仿專業金融終端機的操作方式（黑底琥珀字、指令列、功能鍵列、四格視窗、跑馬燈），介面全繁體中文、台股慣用**紅漲綠跌**、成交量以**張**計。右側 AI 側欄整合五個開源專案，所有 LLM 呼叫都走本機 Ollama（預設 `127.0.0.1:11434`，也可指向你自己的 metrics／trace proxy）。同一套資料也透過 **Hermes**（LINE／Signal）和 **Open WebUI** 提供。

> 外觀參考 Bloomberg Terminal，但與 Bloomberg 無任何關聯，也沒有使用其資料或商標。AI 輸出僅供研究，不構成投資建議。

## 這版新增

- **全繁中**：功能鍵、視窗、表頭、財報科目（營業收入、稅後淨利…）、說明；指令可打中文（`台積電`、`鴻海 概況`、`基金 安聯`、`分析 聯發科`、`問 0056 適合存股嗎`）。指令列打中文名稱會即時建議。
- **台股**：證交所 MIS 即時報價（含加權、櫃買指數）、公司基本資料（董事長、產業、成立／上市日、資本額）、Google 中文新聞；英文公司簡介可一鍵用 Ollama 翻譯。
- **台股 ETF**：`ETF` 總覽（全部上市櫃 ETF 的市價、投信**預估淨值**、**折溢價**、前日淨值、規模、**單位數增減＝淨申購／買回**，可依股票型／高股息／債券／主動式／槓反／期貨篩選與排序）；單一 ETF 頁（配息紀錄、近一年殖利率、**前十大成分股**／每季全部持股）。
- **基金**：境內基金、境外基金、期貨信託基金搜尋與淨值走勢、1 週～3 年報酬。官方開放資料只給近期淨值，所以本機每小時檢查、**每天累積**到 SQLite；境外基金另以 ISIN 從 Yahoo/Morningstar 補長期歷史（找得到才補）。
- **ASK** 會自動抓問題裡提到的標的（`0056 和 00878 哪個配息好` → 兩檔 ETF 的淨值、配息、成分股都帶給模型）；問大盤會帶市場總覽。
- **Qlib** 多一個股票池「台股熱門 ETF 輪動（含債券 ETF）」；選股排名附中文名稱。**FinGPT** 對台股改用中文新聞。
- **Hermes skill `fin-terminal`** 與 **Open WebUI Tool**，共用新的 `/api/agent/*`。

## 📈 定期定額（2026.10.19-29 新增）

**我的持股 → 定期定額**（登入後才看得到，只存你的設定）：輸入標的（台股、美股、ETF、基金名稱或 `FUND:` 代號、`BTC-USD`）、每期台幣金額、每月幾號（可多個，例 `6,16,26`）／每週星期幾／每個交易日、開始日（可填過去，系統補算已扣過的每一期）、停扣日、手續費 %（最低 1 元）。
- 每列：期數、累積投入、累積單位、平均成本、市值、損益、報酬、**年化（XIRR）**、下次扣款日；滑過看最近幾次扣款明細；「回測」把同一組設定送到側欄做對比。
- 計算：扣款日遇假日順延到下一個交易日，用當天收盤價成交；台股／美股／ETF 用還原權息價（股利再投入），基金用淨值（配息型不含配息），美元等外幣資產以當天匯率換算台幣。只存設定，不存成交紀錄——價格修正後會自動重算。不含稅，實際成交以券商為準。
- 跟持股一樣：不會進 Obsidian、Hermes、Open WebUI 或 agent API。

**側欄 📈 定期定額**（指令 `定期定額 0050, VOO, BTC-USD`）：任何標的（最多 6 個一起比）過去 1–30 年的定期定額績效——投入、市值、報酬、年化、最大回撤（市值／累積投入從高點的最大跌幅），並和「同一筆總金額在第一期全部買進」比較；圖上畫各標的市值與累積投入。按「🤖 AI 評估這次回測」由本機 Ollama 根據這些數字寫評估（只用回測數字，不讀你的持股）。也可從 Hermes `ft.py dca 0050,VOO --amount 5000 --years 5`、Open WebUI `dca_backtest`、agent `/api/agent/dca?q=0050,VOO&amount=5000&years=5` 使用。

## 💱 台幣匯率（2026.10.19-26 新增）

功能鍵 **台幣**（或指令 `匯率`、`台幣`、`匯率 日圓`、`TWFX JPY`）：新台幣對 32 種貨幣，依「主要／亞洲／歐洲／美洲大洋洲／中東非洲」分組。
- 每列：1 外幣＝台幣（日圓、匈牙利福林以 100，韓元以 1000，印尼盾、越南盾以 10000 為單位）、1 台幣＝外幣、日／1 月／3 月／1 年變動、台銀牌告（現金賣出／即期賣出）、近 1 年 sparkline；滑過看 52 週高低。**紅＝外幣升值、台幣貶值**。
- 點一列：上方畫該貨幣對台幣走勢（1 月～10 年）；頂端有換算器（外幣↔台幣，中價＋台銀牌告價）。
- 資料：Yahoo 交叉匯率（台幣／外幣 ＝ USD/TWD ÷ USD/外幣，完整日線歷史）＋台灣銀行牌告 CSV（10 分鐘快取）。實際換匯以銀行為準。
- **📌 事件**（預設開）：走勢圖標出這個貨幣對台幣的**異常波動日**（單日漲跌 ≥ 近 60 日 3σ，且至少 0.6%／美元、1%／多數貨幣；3 天內算同一波）、**Fed 決策**、**台灣央行理監事會**（新事件類型，也出現在「大事」頁，可篩選）、歷史大事與自訂事件；滑過看說明，點 Fed／央行／歷史標記跳到「大事」頁。央行日期：2025-12-18、2026-06-18、2026-09-17 已查證，其餘依每季第 3 個週四推估，日期不對可在大事頁直接改。
- **🔮 預測**：TimesFM 預測該貨幣兌台幣 1／3／6 個月（20／60／120 個交易日）的中位數與 80% 區間、外幣升值機率（`/api/forecast?s=TWFX:JPY`，TimesFM 引擎直接吃交叉匯率序列）。匯率接近隨機漫步，看區間比看中位數有用。
- ASK 問到匯率、台幣、日圓、升貶值會自動帶入；Hermes `ft.py twfx [USD,JPY]`、Open WebUI `twd_exchange_rates`、agent `/api/agent/twfx?q=USD,JPY`。

## 🌍 公開網址：https://fin.example.com（Cloudflare Tunnel，2026.10.19-23 新增）

```bash
FT_PUBLIC_HOST=fin.example.com ./install.sh terminal --cloudflare
```
1. Cloudflare Zero Trust → Networks → Tunnels → **Create a tunnel**（Cloudflared，名稱例如 `fin-terminal`）→ 複製 `eyJ…` token。
2. 同一個 tunnel 加 **Public Hostname**：`fin.example.com` → Service **HTTP** → `127.0.0.1:8130`。
3. 執行上面指令、貼上 token（存成 podman secret `fin-terminal-tunnel-token`，不寫進檔案）。安裝會：
   - 在 `fin-terminal.env` 加 `FT_PUBLIC_HOSTS=fin.example.com`、`FT_PUBLIC_SIGNUP=off`
   - 沒有帳號時先請你建立一個（`auth.py add`）
   - 裝 Quadlet `fin-terminal-tunnel`（cloudflared，Network=host，唯讀）並啟動，最後從外面打一次確認未登入是 401

**從公開網址進來時**（看 Host 或 Cloudflare 的 `CF-Connecting-IP`）：
- 除了頁面本身和登入，**所有 API 都要先登入**，未登入只看到登入畫面；不能註冊
- `/api/agent/*`（給 Hermes／Open WebUI 的）、`/docs`、`/openapi.json` 一律 404；**設定不能改**（避免改 Ollama 網址打內網）
- 本機 127.0.0.1 與 tailnet（`myhost.<tailnet>.ts.net:8130`）行為完全不變，Hermes／Open WebUI 不受影響
- God's Eye View **不公開**：世界頁的 3D 地球是 tailnet 網址，在外網看不到（資料面板照常）
- 密碼錯 5 次鎖該帳號 5 分鐘；cookie 經 Cloudflare 走 HTTPS 時是 Secure／HttpOnly／SameSite=Strict

**兩步驟驗證（TOTP，2026.10.19-24）**：`--cloudflare` 會設 `FT_PUBLIC_TOTP=required`——從公開網址登入一定要 6 位數驗證碼，沒設 TOTP 的帳號只能從本機／tailnet 登入。設定方式擇一：
- 網頁（本機或 tailnet）：持股 → 帳號 → 兩步驟驗證 → 輸入密碼 → 用 Google Authenticator／1Password／Aegis 掃 QR → 輸入驗證碼啟用 → **存好 8 組救援碼**（只顯示一次）
- 主機：`podman exec -it fin-terminal python /app/terminal/auth.py totp <帳號>`（終端機直接顯示 QR）

每個驗證碼只能用一次（防重放），容許手機時間差 ±30 秒；救援碼可代替驗證碼、各用一次，資料庫只存雜湊。手機掉了又沒救援碼：`auth.py totp-off <帳號>` 後重設。公開網址上不能關閉 TOTP。不想強制：`FT_PUBLIC_TOTP=optional`（有設 TOTP 的帳號照樣要驗證碼）。

建議再加一層 **Cloudflare Access**（Zero Trust → Access → Applications → Self-hosted → `fin.example.com`，Policy 只允許你的 email，一次性驗證碼），這樣連登入頁都要先過 Cloudflare。

關掉公開：`systemctl --user disable --now fin-terminal-tunnel`（或在 Cloudflare 刪掉 Public Hostname）。

## 🇺🇸 SEC EDGAR 與 FRED（2026.10.19-17 新增）

評估過的來源：

| 來源 | 結論 | 理由 |
|---|---|---|
| yfinance／Yahoo Finance | 已在用（yfinance 1.7.0） | 美股報價、K 線、財報、新聞本來就靠它，不用再加 |
| **SEC EDGAR** | ✅ 新增 | 美國官方原始申報、免費；有實際申報日（回測與投資大師不再用「期末 +75 天」估算）、8-K 重大事件、內部人／大股東申報 |
| **FRED** | ✅ 新增 | 聯準會官方總經數據、免費免金鑰；補上原本沒有的利率、通膨、就業、景氣、信用利差 |
| ProsusAI/FinBERT | ❌ 不加 | 只懂英文（台股中文新聞用不上）、只分正／負／中性三類、2019 年的 BERT；要把 PyTorch＋transformers（1–2 GB）裝進 terminal 映像。情緒分析已有 FinGPT／本機 27B 模型（中英文、可解釋理由）；而且模型頁沒有清楚標示授權 |

**SEC EDGAR**（要在 `~/.config/fin-terminal/fin-terminal.env` 設 `FT_SEC_USER_AGENT="FT-Terminal 你的email"`——SEC 規定要附聯絡方式；沒設就自動退回 Yahoo）
- 美股「財報」視窗多了「SEC 官方（含申報日）」：XBRL 原始數字（營收、毛利、營業利益、淨利、稀釋 EPS、營業現金流、資本支出、自由現金流、研發、現金、資產、負債、權益、長期負債），每一期標出 10-K／10-Q 與**第一次申報日**；季度只列單季。
- 美股「概況」視窗下方：SEC 最近申報（10-K、10-Q、8-K 並翻譯項目代碼如「2.02 財報」「5.02 高層異動」「1.05 資安事件」、Form 4 內部人、13D/G 大股東…），點表單開原文。
- 投資大師（ai-hedge-fund）對美股改用 SEC 實際申報日判斷「當時看得到哪些財報」。
- ASK 問美股時自動附上最近 8 筆申報。
- 每秒最多 8 次請求、磁碟快取（財報 12 小時、申報 1 小時）。內部人交易目前只列出 Form 4 申報，沒有解析張數與價格。

**FRED**（指令 `總經`、`總經 DGS10`，代碼 MACRO）
- 21 個序列分 6 組：Fed 利率目標、有效利率、2／10 年美債、10Y−2Y 與 10Y−3M 利差（倒掛）、房貸利率；CPI、核心 CPI、核心 PCE 年增率；失業率、非農月增、初領失業金；實質 GDP、消費者信心、衰退機率；高收益債利差、VIX、美元、M2 年增率；WTI。
- 表格：最新、日期、前期、變動、一年前、近 2 年小走勢圖；點一列看長期走勢（2／5／10／30 年／全部），「📌 Fed」標出升息／降息。
- 「大事」頁的 FOMC 事件改成**實際決策**（例「Fed 降息 1 碼（4.00%–4.25%）」，由 FRED 目標區間前後比對，不靠 AI 猜）。
- ASK 問到利率／通膨／Fed／衰退等會自動帶入總經數字；LINE `ft.py macro`、`ft.py sec NVDA`；Open WebUI `us_macro`、`sec_filings`（工具 v0.9.0）。

## 📒 分析紀錄與檢討（2026.10.19-16 新增）

指令 `紀錄`（或 `紀錄 2330`，代碼 LEDGER）。

- **紀錄**：每次完成的 AI 分析自動記下（每 30 分鐘收一次，或按「↻ 收集＋核對」）：分析時間、哪個 Agent、用哪個模型、哪一檔、結論、當時價格、有沒有帶入過去檢討；點「報告」打開原始報告。舊的分析也會補進來。
- **事後核對（fact check）**：分析日收盤 → 5／20／60 個交易日後收盤自動核對：看多漲了、看空跌了、中性在 ±2／4／7% 內算對；同時算扣掉大盤（台股 0050、其他 S&P 500）的超額報酬。TimesFM 看到期日有沒有落在 80% 區間、中位數差多少。還沒到期的顯示預計日期 ⏳。不含交易成本。
- **成績單**：每個 Agent 的 5／20／60 日命中率、核對筆數、平均超額報酬（樣本少時請保守看）。
- **🤖 AI 檢討**：本機 Ollama 讀這個 Agent 已核對的判斷（日期、標的、結論、理由摘錄、實際漲跌），寫出總結、常見錯誤，以及最多 8 條「給下次分析的提醒」；同時存進 Obsidian `finance/AI 檢討/`。每個 Agent 累積 ≥5 筆新核對且距上次 ≥7 天時會自動檢討（`FT_LEDGER_AUTO_REVIEW=0` 關掉）。
- **回饋給 Agent**：勾「下次帶入檢討」的 Agent（TradingAgents、投資大師、FinGPT）下次分析時，最新的提醒會放進它的提示詞（執行紀錄會寫「帶入過去檢討 N 條」，紀錄表也會標「檢討」）。TimesFM、Laya 不是語言模型，只做核對與檢討，不能帶入。
- 也能從 LINE 問：`ft.py ledger`；Open WebUI 工具 `analysis_track_record`；agent `/api/agent/ledger`。

升級：`./install.sh terminal tradingagents hedgefund`（terminal 含 FinGPT；要讓 TradingAgents／投資大師讀得到檢討，這兩個也要重建）。

## 2026.10.19-15：英文新聞、近年大事、供應鏈美國對照

- **英文新聞**：Yahoo 對指數、匯率、期貨幾乎沒有新聞（^GSPC 會是空的）。現在依序找：Yahoo（該代號）→ 對應 ETF（^GSPC→SPY、^IXIC→QQQ、^SOX→SOXX、^TWII→EWT、黃金→GLD、原油→USO…）→ Google News 英文版。
- **載入近年大事**：「大事」頁按「⤓ 載入近年大事」→ 用近 10 年指數歷史偵測大漲大跌，並載入內建的 2020 年起歷史大事 38 件（疫情股災與熔斷、Fed 無上限 QE、原油負價、輝瑞疫苗、GameStop、Omicron、俄烏戰爭、Fed 升息 3 碼、裴洛西訪台、美國晶片出口管制、ChatGPT、SVB、輝達財報、惠譽降評美國、日銀結束負利率、川普保護費說、日圓套利平倉股災、Fed 降息、美國大選、Fed 鷹派降息、DeepSeek、對等關稅與台股 4/7 暴跌、關稅暫緩、新台幣兩日狂升、美中日內瓦協議、對台關稅 20%、川普威脅 100% 關稅、川習釜山會、2026/6 AI 股重挫、717 股災、中國晶片自主衝擊）。歷史大事會自動算出當天各指數漲跌、抓當時新聞；附近偵測到的大波動會併進去，不重複標。篩選多了「歷史大事」、5 年、10 年。內建清單在 `common/ft_common/events.py` 的 `HISTORY`（日期與數字依當時新聞查證），其餘交給自動偵測＋新聞＋AI 命名；已安裝的會在下次背景偵測或按「⤓ 載入近年大事」時補進新的。背景偵測年數：`FT_EVENTS_YEARS`（預設 2）。
- **供應鏈美國對照**：供應鏈地圖上方「🇺🇸 美國對照」→ 每個環節同時顯示「台股平均」與「美國同類平均」；點環節多一張「美國同類（對照）」表（例：散熱 ↔ Vertiv、Modine；先進封裝 ↔ Amkor、Intel；測試 ↔ Teradyne、FormFactor；光通訊 ↔ Lumentum、Fabrinet；矽智財 ↔ Arm、Synopsys、Cadence；記憶體 ↔ 美光、SanDisk）。環節排行表多了「美國同類」「台−美」欄。沒有美國同類的環節（ABF、BBU、機殼滑軌）就不比。名單在 `supplychain.py` 的 `US_PEERS`。

## 📌 重大事件：指數走勢圖上的標記＋「大事」分頁（2026.10.19-14 新增）

- **自動偵測**（近兩年）：加權、S&P 500、Nasdaq、費半任一檔出現「不尋常的大漲大跌」——單日漲跌 ≥ max(門檻, 2.5 × 過去 60 天波動)，門檻 加權 2%、S&P 1.8%、Nasdaq 2.2%、費半 3.5%；或 5 日累計 ≥ 6%／5%／6%／10%。美股收盤後隔天台股反應、週末前後，3 天內的會合併成同一件事。
- **Fed 利率決策日**：2025–2026 年 FOMC 行事曆，未來的會列在「即將到來」。
- **自訂事件**：「大事」頁 ＋ 新增（法說會、選舉、除息…也可以標未來的日子）。
- **自動更新**：服務啟動 2 分鐘後偵測一次，之後每 6 小時用最新收盤重新偵測（新的大波動自動加入，舊的漲跌數字會更新）；事件日過 2 天後才抓新聞、讓 AI 命名（等新聞出齊），每 10 分鐘處理一批。Fed 行事曆內建到 2026-12，之後要更新 `FOMC` 清單。
- **新聞＋AI 摘要**：每件事自動抓「當時」的 Google 新聞標題，背景讓本機 Ollama 只根據那些標題取名（例「關稅引爆全球股災」）並寫 2–3 句摘要；標題看不出原因就會寫「原因不明」。你改過的標題／摘要永遠優先，不會被重新偵測或 AI 覆蓋。關掉 AI：`FT_EVENTS_AI=0`（只抓新聞）。
- **走勢圖**：指數（^ 開頭）預設開「📌 大事」，其他股票按一下就有。只標這個指數自己的大波動＋Fed＋自訂；最大的幾件會寫字，其他只畫箭頭（紅漲綠跌、Fed 紫色方塊、自訂黃點）。滑到標記看日期、各指數漲跌、摘要；點一下在旁邊的視窗打開「大事」頁並捲到那件事。「看走勢」會把圖捲到那天前後。
- **大事頁**（`大事`，代碼 EVENTS）：依月份排列，篩選 指數大波動／Fed 決策／自訂／世界事件（God's Eye View），半年／1 年／2 年；每件可 AI 重寫、編輯、隱藏（自動偵測的不會刪掉，只隱藏）。
- Hermes：`ft.py events`；agent `/api/agent/events`。

## ⛓ 供應鏈地圖・📰 今日焦點・👤 我的持股（2026.10.19-13 新增）

參考 aistockmap 的「供應鏈／每日焦點／我的持股」做成終端機裡的三個視窗（資料與內容是自己整理的，與該網站無關）。

**供應鏈地圖（`供應鏈`、`供應鏈 CoWoS`、`供應鏈 奇鋐`，代碼 SC）**
- 五張圖：AI 資料中心、半導體製造、記憶體、高速網路、電力與散熱。共 25 個環節、約 120 家公司（台股為主，連到美、日、韓的關鍵廠：輝達、博通、SK 海力士、ASML、東京威力、Ibiden…）。
- 由左到右是上游 → 下游；每個環節的色塊是成分股今日平均漲跌（紅漲綠跌跟設定走），每 30 秒更新。
- 點環節：上下游亮起來、其他變淡；下方列出公司、它在這個環節做什麼、即時價格；可以看相關新聞、或「問 AI 這個環節」。
- 個股視窗多一個「⛓ 供應鏈」按鈕，直接跳到它所在的環節。
- 內容在 `common/ft_common/supplychain.py`（環節、公司、上下游關係），想加公司或環節直接改那個檔、`./install.sh terminal`；測試會檢查沒有斷線或循環。

**今日焦點（`焦點`，代碼 FOCUS）**
- 主要指數（加權、櫃買、台積電 ADR、費半、S&P、Nasdaq、日經、韓股、美元／台幣）。
- 題材漲跌排行＝供應鏈環節的成分股等權平均；供應鏈內的台股強勢股／弱勢股。
- 三大法人買賣超（證交所 BFI82U，約 15:00 後公布；之前顯示前一個交易日）。
- 今日新聞（Google 新聞）自動標上相關環節，點一下開供應鏈地圖；「新聞熱門題材」統計今天被提到最多的環節。
- 「我的持股今日」：登入的人看到自己的今日損益。
- 「🤖 AI 今日重點」：按一下讓本機 Ollama 依上面的資料寫一份（每天存一份在 `data/focus/`），**不會用到任何人的持股**。
- Hermes／Open WebUI 也能拿：`ft.py focus`、`ft.py chain CoWoS`；Open WebUI 工具 `todays_focus`、`supply_chain`。

**我的持股（`持股` 或右上 👤，代碼 HOLD）— 要登入，只有自己看得到**
- 註冊／登入後新增持股：代號或名稱、股數（台股以「股」計，1 張 = 1,000 股）、每股成本、日期、備註；可編輯、刪除、匯出 CSV。
- 即時算市值、損益、報酬、今日損益、權重（外幣用 USD／JPY／KRW 匯率換成台幣），以及「供應鏈曝險」：你的部位落在哪些環節、各佔多少。
- 「問 AI 我的持股」把持股表交給本機 Ollama 分析（只在這次對話，不存檔）。
- 安全設計：密碼用 scrypt 雜湊；登入 cookie 是 HttpOnly＋SameSite=Strict，資料庫只存 token 的 SHA-256；所有持股查詢都綁登入者，沒有任何「指定使用者」的 API；修改類請求要帶 `X-FT` 標頭（別的網站無法代你送出）；同一帳號密碼錯 5 次鎖 5 分鐘；改密碼會登出其他裝置；資料庫 `data/users.sqlite` 權限 600。
- 持股**不會**進 Obsidian 筆記、Hermes、Open WebUI、agent API 或 AI 工作紀錄。主機管理者（能進容器的人）技術上還是讀得到資料庫——這是同一台主機上的帳號隔離，不是端對端加密。
- 管理（忘記密碼、關閉註冊）：
  ```bash
  podman exec -it fin-terminal python /app/terminal/auth.py list
  podman exec -it fin-terminal python /app/terminal/auth.py reset-password <帳號>
  podman exec -it fin-terminal python /app/terminal/auth.py add <帳號>      # FT_SIGNUP=off 時用這個建帳號
  ```
  `~/.config/fin-terminal/fin-terminal.env` 設 `FT_SIGNUP=off` 就只能由管理者建帳號（預設 `open`：tailnet 內的人可自行註冊）。

## 🔮 TimesFM 趨勢預測（2026.10.19-4 新增）

[google-research/timesfm](https://github.com/google-research/timesfm)：Google 的時間序列基礎模型，零樣本（不用訓練）預測股價、ETF、指數、匯率、基金淨值的未來走勢。

- **走勢圖右上「🔮 預測」**：K 線圖直接延伸出中位數虛線＋P10–P90（80%）色帶，可選 10／30／60／120 個交易日；圖例顯示中位數漲跌、80% 區間、收高於現價的機率。基金淨值圖也有「🔮 預測 30 日」（淨值歷史需 ≥ 64 筆）。
- **側欄「TimesFM」分頁**（或指令 `趨勢 0056`）：完整報告＝預測圖、**回測**（用前一段資料預測最近一段，算 MAPE、落在 80% 區間的比例、方向對錯，並和「價格不變」基準比較）、逐日表、Ollama 解讀。
- **模型**：預設 TimesFM 2.5（200M，權重 Apache-2.0）；⚙ 設定可改 TimesFM 3.0（330M，較準，但權重是 `timesfm-non-commercial-license-v1.0`，只限非商業、非正式營運用途）。第一次使用會從 Hugging Face 下載權重到 `~/fin-terminal-data/data`（2.5 約 0.8 GB），之後常駐。
- 跑在 **CPU**（`ft-timesfm`，127.0.0.1:8136），一檔 1–3 秒，不佔 GPU。
- Hermes：「0050 未來一個月走勢預測」→ `ft.py forecast 0050 --days 20`；Open WebUI：`forecast_trend` 工具；agent API：`/api/agent/forecast?q=0050&h=30`。
- 限制：只看過去價格（沒有新聞、財報、事件），區間是統計不確定性。回測如果輸給「價格不變」基準，代表那檔在那段期間預測沒有參考價值。

## 🌐 God's Eye View 現實世界訊號（2026.10.19-5 新增）

[bilawalsidhu/gods-eye-view](https://github.com/bilawalsidhu/gods-eye-view)（MIT）：3D 地球＋公開即時資料（航班、軍機、衛星、地震、颶風、野火、船舶 AIS、火箭發射…）。這版把它當成**感測器**接進來：

- **`gods-eye-view` 容器**（127.0.0.1:4173，tailscale https **:4173**）：就是它的 Vite 伺服器＝網頁＋各資料來源代理＋本機 MCP（`POST /mcp`，只接受本機直連，經 tailscale 進來的 MCP 請求會被它自己擋掉——這是對的）。
- **快照收集**（fin-terminal 內，每 60 分鐘）：依區域查 MCP 工具，存到 `world.sqlite`——台灣／台海、日本、南海、荷莫茲海峽、紅海／曼德海峽、麻六甲、巴拿馬運河、墨西哥灣、美西、全球；項目：M4+/M5+/M6+ 地震、軍機數、船舶數、熱帶氣旋、野火、火箭發射。
- **事件偵測**：台灣／日本 M6+（其他 M6.5+、全球 M7+）、氣旋、野火 ≥5 處、軍機 ≥ 2×近 14 天中位數（需累積 ≥24 筆歷史）、船舶 ≤ 0.5×中位數（咽喉點航運中斷）。
- **TradingAgents**：所有分析師都看得到「與這檔相關區域」的精簡世界訊號（台股 → 台海＋南海；半導體加日本；能源加荷莫茲／紅海／墨西哥灣；航運加運河與海峽…），新聞分析師多一個工具 `get_world_events` 可以查細節。**依分析日期取當時的快照**（回測不會偷看未來）。報告最後附「分析時的世界訊號」。
- **TimesFM**：勾「加入現實世界共變數」時，用 TimesFM 3.0 的 `past_only_covariates` 把每日地震規模、軍機、船舶數當共變數（需 ≥ 64 個交易日的世界歷史，所以**要累積約三個月才會生效**；2.5 不支援，會註明）；報告附回測期間的事件與 Ollama 解讀時的世界訊號。
- **終端機 UI**：功能鍵 **世界 WORLD**（或打 `世界`／`事件`）＝各區最新讀數（偏高偏低標黃）、30 天事件、「開啟上帝視角 ↗」、「🌍 視窗內顯示」；走勢圖 **🌐 事件** 把事件標在 K 線上。**ASK** 問到「戰爭、地震、航運、紅海…」會自動帶入世界摘要。
- **Hermes**：「台海最近有什麼動靜」→ `ft.py world 台灣`；Open WebUI：`get_world_events`；agent API：`/api/agent/world?q=2330` 或 `?region=紅海`；盤後簡報加上近兩天事件。
- **金鑰**放 `~/.config/fin-terminal/gods-eye-view.env`（它自己的 POWER UP 面板經 tailscale 會拒絕存金鑰）：`AISSTREAM_API_KEY`（免費，**沒有就沒有船舶數**）、`CESIUM_ION_TOKEN`（3D 地形）、`FIRMS_MAP_KEY`（野火）。改完 `systemctl --user restart gods-eye-view`。
- ⚠️ 限制：這是**感測器讀數不是新聞**（「軍機 14 架」不代表發生什麼事）；西太平洋颱風不在它的氣旋資料源（NHC，只有大西洋／東太平洋）；OpenSky 匿名額度有限，軍機數偶爾會「目前無法取得」；歷史要自己累積，剛裝好時沒有基準線。
- **只用 tailscale serve**，不要 funnel／Cloudflare 公開：它會帶你的 API 金鑰幫任何來訪者發請求。
- 進階（選用）：Hermes 或 Open WebUI（都是 Network=host）也可以直接把 `http://127.0.0.1:4173/mcp` 加成 MCP 伺服器，拿到它全部 30 個工具；一般用 FT 的 `world` 指令就夠了。

## 📝 AI 預測自動存進 Obsidian（2026.10.19-5 新增）

- 每個 AI 分析（TradingAgents、大師基金、Qlib、FinRL、FinGPT、TimesFM）完成後，自動寫一篇筆記到 vault 的 **`finance/AI 預測/YYYY-MM/<日期> <代號> <名稱> · <引擎> (id).md`**：YAML 屬性（engine、ticker、date、signal、price_at、TimesFM 的 horizon_end／中位數／P10／P90、world_events、tags `finance/ai-forecast`、`engine/…`、`ticker/…`）、結論、完整報告、表格、分析時的世界訊號（摺疊 callout）、回 FT 的連結。
- **`finance/AI 預測回顧.md`**（每天覆寫）：把過去的預測拿來和實際股價對答案——各引擎方向命中率（訊號滿 5 天才計分）、TimesFM 到期價落在 80% 區間的比例。`finance/AI 預測索引.md`（Dataview 表）、**`finance/世界事件/<日期>.md`**（每天一篇）。
- 機制：容器只寫到 `~/fin-terminal-data/data/obsidian-outbox/`，主機上的 `ft-obsidian-apply`（systemd .path 立即觸發＋每 10 分鐘 timer）**確認 vault 有 `.obsidian/` 才搬進去**——vault 硬碟沒掛上時筆記會等著，不會寫進空的掛載點。vault 路徑：`~/.config/fin-terminal/obsidian.env` 的 `FT_VAULT`（預設 `~/Obsidian/vault`），資料夾名 `FT_OBSIDIAN_FOLDER`（預設 `finance`）。Obsidian LiveSync 會照常同步到手機。
- ⚙ 設定可關「自動存 Obsidian」；關掉後每個工作的報告上還有「📝 存到 Obsidian」按鈕。

## 🛑 TradingAgents 在本機模型上的輸出上限（2026.10.19-8）

本機 27B 模型在「多空研究員」這個節點有時寫不停（關掉 thinking 也一樣，曾寫到 8.8k～29k tokens），撞上 proxy 的 15 分鐘逾時，OpenAI SDK 又會把同一個 prompt 重送兩次 → 一個節點卡 45 分鐘。這版：

- **每個節點各自的輸出上限**（`max_tokens`，Ollama 對應 num_predict）：分析師報告 8192、多空研究員 3072、交易員 3072、風控 2048、研究經理／投資組合經理 4096。側欄表單可改「研究員／交易員上限」與「分析師報告上限」，或在 `fin-terminal.env` 設 `FT_TA_MAX_TOKENS_*`。
- 達上限的節點**保留已寫的內容、流程繼續**，執行紀錄會寫「⚠️ 多方研究員 達上限 3072 被截斷」，報告最上方也會提醒；報告最後有「各節點耗時與輸出量」表。
- **不重試**（`FT_TA_RETRIES=0`）、單次請求**逾時 600 秒**（`FT_TA_TIMEOUT`，小於 proxy 的 15 分鐘）：真的卡住時一次失敗，不會再浪費 30 分鐘。
- 預設多空辯論、風控討論都改為 1 回合（輸入也減半）。
- **流程儀表板（-10）**：側欄 TradingAgents 工作頁上方顯示 ① 分析師 → ② 多空辯論 → ③ 交易員 → ④ 風控 → ⑤ 經理，每個代理人 ✓ 完成／◉ 執行中／✂ 達上限／✗ 失敗；執行中的會寫「LLM 生成中 3 分 12 秒」或「工具／資料抓取中 40 秒」（超過 5 分鐘變黃、10 分鐘變紅），以及呼叫次數與輸出 tokens——一眼看出卡在誰、是模型還是資料來源。工作清單的狀態列也會寫目前在哪個代理人。
- 技術細節：新版 langchain-openai 把 `max_tokens` 改送成 `max_completion_tokens`，舊版 Ollama 只認 `max_tokens`，所以兩個欄位都送。

## 📊 策略研究＋資料庫（參考 FinLab 的研究方法，2026.10.19-7 新增）

參考 [FinLab](https://finlab.finance/) 與它的[資料庫目錄](https://finlab.finance/data)的研究流程：**時點正確的資料 → 條件選股 → 排名 → 回測報告 → 今天的選股名單**，再加上「用中文描述想法，AI 幫你寫成策略」。差別是：資料**自己從官方來源抓、存在你的硬碟**（不用 FinLab 帳號／VIP），模型用本機 Ollama。

- **功能鍵「策略研究 LAB」**（或打 `研究`、`選股 營收連續成長、投信在買的股票`）：
  1. **策略**：用中文描述 → 「建立策略」（本機 AI 把想法轉成條件 JSON，系統再逐條驗證，錯了會讓 AI 重寫）；或點 7 個**內建策略**（營收成長動能、高殖利率低本益比、投信連續買超、創新高突破、高 ROE 品質成長、小型價值、融資減法人買）；條件、排名、持股數、換股頻率（每日／週／月／季／**月營收公布後**／**財報公布後**）、停損停利、單檔上限、市場、成交金額門檻、排除金融股都能改。
  2. **績效報告**：年化報酬、最大回撤、夏普、總報酬、勝率、Alpha／Beta、波動、週轉；淨值曲線 vs 0050、回撤圖、每年報酬（含超額）、每月報酬熱力圖。
  3. **選股名單**：最新一個交易日符合全部條件、依排名取前 N 檔（點一下在下一格開走勢圖）。
  4. **交易明細**：每筆進出、天數、報酬、原因（換股／停損）。
  「📝 存成工作」會留紀錄並寫一篇 Obsidian 筆記（`finance/AI 預測/` 裡，標「策略回測」）。
- **條件語法**（安全的運算式，不會執行任意程式）：欄位直接寫中文，例 `營收年增率 > 20`、`new_high(ma(月營收, 3), 12)`（近 3 月均營收創 12 個月新高）、`收盤價 > ma(收盤價, 60)`、`always(投信買賣超 > 0, 5)`、`rank(本益比) < 0.3`、`cross_up(ma(收盤價, 5), ma(收盤價, 20))`。月／季欄位的 `shift`、`rise`、`ma` 以「期」計。函數：ma、ema、sum、std、highest、lowest、shift、diff、pct、rsi、rank、zscore、rise、fall、new_high、new_low、count、always、ever、cross_up／cross_down。
- **功能鍵「資料庫 DATA」**：資料目錄（欄位、筆數、期間、來源）、背景收集進度與錯誤、欄位瀏覽（選欄位＋代號看歷史）。

| 資料 | 頻率 | 官方來源 | 欄位 |
|---|---|---|---|
| 股價與成交 | 日 | Yahoo Finance（還原＋未還原） | 收盤價、開高低、還原收盤價、成交量（張）、成交金額、市值 |
| 本益比／殖利率／淨值比 | 日 | 證交所 BWIBBU_d、櫃買 | 本益比、殖利率、股價淨值比 |
| 三大法人 | 日 | 證交所 T86、櫃買 | 外資／投信／自營商／合計買賣超（張） |
| 融資融券 | 日 | 證交所 MI_MARGN、櫃買 | 融資餘額、融券餘額、券資比 |
| 月營收 | 月 | 公開資訊觀測站 t21sc03（含 KY） | 月營收、月增率、年增率、累計年增率 |
| 季財報 | 季 | 公開資訊觀測站 t163sb04／05 彙總表 | 營收、毛利、營業利益、淨利、EPS、近四季 EPS、毛利率、營益率、淨利率、ROE、負債比、股本 |

- **研究方法（照 FinLab 強調的幾點做）**：①**公布日對齊**——月營收用次月 10 日、財報用法定期限（5/15、8/14、11/14、3/31）當作「可以用的日期」，回測看不到還沒公布的數字；②**還原股價**算報酬；③條件在換股日收盤後判斷、**隔日開盤成交**；④成本含手續費（可設折扣，預設 6 折）、證交稅 0.3%（ETF 0.1%）、滑價 0.1%。
- **資料怎麼來**：`ft-research`（127.0.0.1:8138，和終端機同一個映像）在背景抓：每天 15:00 後更新股價、17:00／21:45 後抓當天估值／法人／融資券；月營收、財報公布後自動補；**歷史回補**：月營收、財報從 2013 年起（約 1 小時），日資料回補 5 年（約 6–8 小時，每個來源間隔 2.5–4.5 秒，避免被證交所封 IP）。全部存在 `~/fin-terminal-data/data/research/`。設定：`fin-terminal.env` 的 `FT_RESEARCH_START`、`FT_RESEARCH_DAILY_YEARS`、`FT_RESEARCH_BACKFILL`。
- 回測只用**資料已涵蓋的期間**：策略用到的欄位哪天開始有資料，起點就自動往後延，報告會註明。
- Hermes：「幫我找營收連續成長、投信在買的股票」→ `ft.py strategy "營收連續成長、投信在買"`；`ft.py strategy 高殖利率低本益比`；`ft.py strategy` 列出內建策略。Open WebUI：`stock_screener`。agent API：`/api/agent/strategy?q=…`。
- ⚠️ 限制：**股票池只含目前仍上市櫃的公司**（已下市的公司沒有來源可抓）→ 存活者偏差，回測會比實際樂觀；Yahoo 的還原股價偶爾有錯；櫃買新版網站的欄位我是依舊版格式＋表頭比對寫的，公開資訊觀測站改版過，**這兩個來源請看「資料庫」頁有沒有錯誤**。選股名單是「符合條件」，不是推薦。

## ⚖ Laya 交易判斷：買進／持有／觀望／賣出／放空 各幾 %（2026.10.19-6 新增）

[receptron/laya](https://github.com/receptron/laya)（MIT）在 Node.js 上用 ONNX Runtime 跑 Convai Innovations 的 **Laya**（[convaiinnovations/laya](https://huggingface.co/convaiinnovations/laya)，ModernBERT-large＋校準過的決策頭，Apache-2.0 權重）。Laya 不產生文字：給它一份「狀態」和幾個有型別的問題，一次前向推論就回傳每個選項的機率。

- **視窗功能「⚖ 判斷」**（每個視窗的功能列、指令 `判斷 台積電`、`2330 判斷`、功能鍵「Laya 判斷」）：大字結論＋五個動作的百分比長條、**多空淨值**（買進 − 賣出 − 放空）、**N 日後收高機率**、**跌逾 X% 風險**、未來位置分布（大跌／下跌／持平／上漲／大漲）。可切一週（5 日）／一個月（20 日）／一季（60 日）。「📝 記錄」會建一筆工作 → Obsidian 筆記 → 之後「AI 預測回顧」會幫它計分；「詳細 ▸」開側欄看完整報告和送進模型的英文狀態。
- **送進模型的狀態**（英文，因為 receptron 發布的 ONNX 只有英文版權重）：報酬（1/5/20/60/120/250 日）、均線位置、RSI、MACD、波動度、回檔、52 週高低、量比、本益比等；**其他 AI 引擎最近 14 天的結論**（TradingAgents、大師基金、FinGPT、TimesFM 預測）；**God's Eye View 近 7 天事件**；英文新聞標題（中文標題會略過）。基準日不是今天時不帶新聞和 TimesFM（避免偷看未來）。
- **跑在 CPU**（`ft-laya`，127.0.0.1:8137，不佔 GPU、不用 Ollama）：第一次啟動從 Hugging Face 下載約 **1.7 GB** 權重到 `~/fin-terminal-data/data/laya`，之後幾秒載入，一次判斷約 0.2–1 秒；記憶體約 2–3 GB。
- Hermes：「台積電現在該買還是賣」→ `ft.py decide 台積電 [--days 5|20|60]`；Open WebUI：`laya_decision`；agent API：`/api/agent/decide?q=2330&h=20`。
- ⚠️ **怎麼看這些百分比**：Laya 是通用的決策模型（訓練任務是客服分流、審核、評分這類），**沒有用金融資料訓練**；作者自己說零樣本在他們的基準上接近亂猜、而且出廠偏過度自信。所以「買進 53%」的意思是「模型讀完這份資料，最傾向買進、信心 53%」，**不是**「有 53% 機率會漲」。它比較像把技術面＋其他引擎＋事件濃縮成一個有分數的投票；要不要信它，用「📝 記錄」累積幾週，看 Obsidian「AI 預測回顧」裡 Laya 的命中率。之後要更準，Laya 可以用自己的資料微調（作者的 typed-decisions 微調版從 0.36 提升到 0.77），這版沒有做。

## 💾 資料位置：~/fin-terminal-data（`FT_DATA_ROOT` 可改到外接硬碟；已安裝的會沿用原位置）

所有容器資料都改成 bind mount 到外接硬碟（不再用 podman volume `fin-terminal-data`）：

| 路徑 | 內容 |
|---|---|
| `~/fin-terminal-data/data/` | 各引擎工作紀錄、設定 `settings.json`、基金淨值庫、證券清單、Qlib 資料、TimesFM／FinRL 模型權重快取、`world.sqlite`、`obsidian-outbox/` |
| `~/fin-terminal-data/gods-eye-view/gev-cache`、`gev-logs` | God's Eye View 的資料快取與日誌 |
| `~/fin-terminal-data/.ft-root` | 哨兵檔：每個單元啟動前檢查它，**硬碟沒掛上就不啟動**（每 10–30 秒重試，掛上後自動起來），不會把資料寫進空的掛載點 目錄 |

`install.sh` 第一次會把舊 volume 的資料**自動搬過來**（目標是空的才搬），確認沒問題後可 `podman volume rm fin-terminal-data`。要放別的地方：`FT_DATA_ROOT=/其他/路徑 ./install.sh`。SQLite 用 rollback journal（不用 WAL），exFAT／NTFS 也能用。

## 資料來源

| 資料 | 來源 | 更新 |
|---|---|---|
| 台股即時報價、加權／櫃買指數 | 證交所 MIS `getStockInfo.jsp` | 約 5 秒 |
| 證券清單（中文名稱、上市／上櫃、產業、ETF） | 證交所 ISIN 查詢 `C_public.jsp` | 每天 |
| ETF 預估淨值、折溢價、單位數 | 證交所 ETF 淨值揭露 `all_etf.txt` | 約 15 秒 |
| ETF 基本資料、成分股 | 投信投顧公會 ETF 專區、基金投資明細（月前十大／季 >1%）；證交所 OpenAPI t187ap47_L | 每月 |
| 境內基金淨值 | 投信投顧公會 `nav.csv` | 每日 |
| 境外基金、期信基金淨值 | 集保結算所開放資料 | 每日 |
| 公司資料 | 證交所 OpenAPI t187ap03_L／櫃買中心 mopsfin_t187ap03_O | 每日 |
| 中文新聞 | Google 新聞 RSS（zh-TW） | 10 分鐘快取 |
| K 線、財報、海外市場、配息紀錄 | Yahoo Finance（yfinance，延遲報價） | — |

投信投顧公會頁面的解析（含 ASP.NET postback 的成分股查詢）移植自 [TWSEMCPServer](https://github.com/twjackysu/TWSEMCPServer)（MIT）。證交所系列網站的憑證會被 Python 嚴格 X.509 檢查擋下，這裡保留驗證、只放寬 strict 旗標；仍失敗時才對這幾個政府網域改用不驗證並記一次警告。

## AI 引擎

| 側欄分頁 | 上游（固定 commit） | 做什麼 | Ollama 用在哪 |
|---|---|---|---|
| 多代理人 | TauricResearch/TradingAgents `e8f5503` | 分析師團隊 → 多空辯論 → 交易員 → 風控 → 經理，5 級評等 | 全部代理人（內建 `ollama` provider，走 `/v1`，需 tool calling） |
| 投資大師 | virattt/ai-hedge-fund v2.5 `78b779c` | 巴菲特／蒙格／葛拉漢／林區／杜肯米勒評估＋經理彙整 | 自寫 `OllamaLLM`；資料用自寫 yfinance DataClient（台股可用） |
| Qlib | microsoft/qlib（pyqlib 0.9.7） | Alpha158 + LightGBM/Ridge → IC → TopK-Dropout 回測（台股成本） | 解讀結果 |
| FinRL | AI4Finance-Foundation/FinRL `8511e7c` | SB3 PPO/A2C/SAC/TD3/DDPG vs 等權買進持有（CPU） | 解讀結果 |
| FinGPT | AI4Finance-Foundation/FinGPT `fdb04c9` | Forecaster 下週預測、v3 新聞情緒 | 推論本身 |

## 安裝／升級

```bash
tar xzf fin-terminal.tar.gz && cd fin-terminal
./install.sh                          # 第一次：建 8 個映像（約 15–25 分鐘）＋ 10 個 Quadlet＋Obsidian 搬運器
./install.sh terminal                 # 從上一版升級：這版只改了 UI＋資料層，重建 terminal 即可（含 FinGPT）
./install.sh qlib                     # 要用新的「台股熱門 ETF」股票池再重建 qlib
./install.sh terminal timesfm         # 加 TimesFM：重建 UI＋新建 ft-timesfm（約 5 分鐘，torch CPU）
./install.sh                          # 從 -4 升級：建 gods-eye-view 和 ft-laya、重建其他映像，搬資料到 ~/fin-terminal-data
./install.sh laya terminal            # 從 -5 升級到 -6：只要建 ft-laya＋重建 terminal
./install.sh terminal                 # 從 -6 升級到 -7：重建 terminal（含 ft-research 策略研究）
./install.sh tradingagents            # -8：TradingAgents 輸出上限／不重試（只要重建這個）
./install.sh terminal                 # -13：供應鏈地圖、今日焦點、我的持股（登入）；-14：重大事件；-15：英文新聞、近年大事、美國對照
./install.sh --no-build --tailscale   # tailnet：https://myhost.<tailnet>.ts.net:8130（終端）與 :4173（God's Eye View）
./tests/smoke.sh                      # 真實資料檢查：中文名稱、MIS、ETF 淨值、成分股、基金庫、中文新聞、agent API、God's Eye View、Obsidian
```

第一次啟動會下載證券清單和三份基金淨值（約 1–2 分鐘），之後每小時檢查一次。設定：`~/.config/fin-terminal/fin-terminal.env`（升級時會自動補上 `GEV_*`、`FT_PUBLIC_URL`、`FT_OBSIDIAN_FOLDER`）、`gods-eye-view.env`（API 金鑰）、`obsidian.env`（vault 路徑）；網頁 **⚙ 設定** 可改 Ollama、各引擎模型、**漲跌顏色**（台灣紅漲綠跌／國際綠漲紅跌）。

## 指令（直接打字）

| 指令 | 功能 |
|---|---|
| `台積電`、`2330`、`2330 走勢`、`NVDA` | K 線＋月線、季線（中文名稱、代號、Yahoo 代號都行） |
| `<標的> 盤中`／`概況`／`新聞`／`財報` | 英文代碼 GIP／DES／N／FA 也可以 |
| `ETF` | ETF 總覽（篩選、排序、折溢價 ±1% 標黃） |
| `0056 概況`、`00679B 概況` | ETF 資料頁（淨值、配息、成分股） |
| `基金 安聯 台灣科技` | 基金搜尋 → 點選看淨值走勢與報酬 |
| `台股`、`熱門ETF`、`全球`、`美股`、`外匯`、`商品`、`利率`、`加密`、`自選` | 監控表 |
| `分析 2330`、`大師 AAPL 2330`、`量化`、`強化學習 2330 2317`、`預測 NVDA` | 打開 AI 分頁並帶入標的 |
| `焦點`、`供應鏈`、`供應鏈 CoWoS`、`持股` | 今日焦點／供應鏈地圖／我的持股（要登入） |
| `問 0056 和 00878 哪個配息好？` | 問 AI（自動帶入提到的標的） |
| `研究`、`選股 營收連續成長、投信在買`、`資料庫` | 策略研究（AI 建策略、內建策略、回測報告、選股名單）、官方資料目錄 |
| `判斷 台積電`、`2330 判斷`、`LY 0050`（側欄） | Laya 交易判斷：買進／持有／觀望／賣出／放空 百分比 |
| `世界`、`事件`、`上帝視角` | 世界事件視窗（God's Eye View 各區讀數＋30 天事件＋內嵌地球） |
| `2 鴻海 概況` | 開頭 1–4 指定視窗；Ctrl+1..4 切換、Ctrl+B 開關側欄 |

## Hermes（LINE／Signal）

```bash
./scripts/install-hermes-skill.sh
```

用 `podman unshare` 把 `hermes/fin-terminal/` 複製到 `~/.hermes/skills/fin-terminal`（擁有者 9999），在 Hermes 容器裡實測一次 `ft.py quote 2330`，內容有變才重啟 Hermes。腳本只用 Python 標準函式庫（Hermes 容器沒有 pip），輸出純文字、超過 4500 字截斷（LINE 安全）。

| 在 LINE／Signal 說 | Hermes 執行 |
|---|---|
| 「台積電、0050 現在多少」 | `ft.py quote 台積電 0050` |
| 「大盤今天怎樣」「盤後簡報」 | `ft.py market`／`ft.py brief` |
| 「00878 折溢價和配息」 | `ft.py etf 00878` |
| 「哪些 ETF 溢價最高」「債券 ETF 誰被大量申購」 | `ft.py etf-rank --sort premium`／`--sort creation --category 債券` |
| 「統一黑馬今年報酬」「安聯的收益基金有哪些」 | `ft.py fund 統一黑馬`／`ft.py fund-search 安聯 收益` |
| 「台海／紅海最近有什麼動靜」「有沒有大地震」 | `ft.py world 台灣`／`ft.py world --region 紅海,荷莫茲` |
| 「幫我找營收連續成長、投信在買的股票」「高殖利率低本益比有哪些」 | `ft.py strategy "營收連續成長、投信在買"`／`ft.py strategy 高殖利率低本益比` |
| 「台積電該買還是賣」「0050 可以放空嗎」 | `ft.py decide 台積電`（`--days 5`／`20`／`60`） |
| 「AI 之前的預測準不準」 | `ft.py review` |
| 「幫我用 TradingAgents 分析 2330」 | `ft.py analyze tradingagents 2330`（等 540 秒，沒完成回 PENDING，再用 `ft.py job` 接著等） |

每日簡報：跟 Hermes 說「每個交易日下午 2 點用 fin-terminal 的 brief 傳給我」，讓它自己建排程。

## Open WebUI

**Workspace → Tools → ＋**，貼上 `openwebui/ft_terminal_tool.py`、儲存。在模型設定（或對話的 ＋ 工具）勾選「FT Terminal 金融終端」，建議模型的 **Function Calling 設為 Native**。Valves：`FT_URL`（預設 `http://127.0.0.1:8130`，Open WebUI 是 Network=host）、`MAX_WAIT`（AI 分析最多等 900 秒）。

工具（v0.9.0）：`us_macro`、`sec_filings`、`analysis_track_record`、`todays_focus`、`supply_chain`、`market_events`、`get_quote`、`get_security_detail`、`get_etf`、`rank_etfs`、`search_funds`、`get_fund`、`market_overview`、`get_news`、`get_world_events`、`get_forecast_review`、`laya_decision`、`stock_screener`、`run_ai_analysis`（右上角會顯示 TradingAgents／Qlib 的進度）、`get_ai_result`。

## Agent API（Hermes、Open WebUI、你自己的腳本都能用）

`curl localhost:8130/api/agent/help` 看全部。全部回繁中純文字（`plain=1` 去 Markdown、`format=json` 拿資料）：
`/quote?q=台積電,0050,AAPL`、`/stock?q=鴻海`、`/etf?code=00878`、`/etf/rank?sort=premium&category=債券`、`/fund/search?q=安聯`、`/fund?q=統一黑馬`、`/market`、`/news?q=聯發科`、`/watchlist`、`/brief`、`/decide?q=2330&h=20`、`/strategy?q=營收成長動能`、`/world?q=2330`、`/world?region=紅海`、`/review`、`POST /analyze {"engine":"tradingagents","q":"2330"}`、`/job?engine=…&id=…&wait=600`、`/jobs`。

## 埠號

| 服務 | 埠 |
|---|---|
| fin-terminal（UI＋agent API） | 127.0.0.1:8130（tailscale https :8130） |
| gods-eye-view（地球＋MCP） | 127.0.0.1:4173（tailscale https :4173；`/mcp` 只接受本機） |
| ft-tradingagents / ft-hedgefund / ft-qlib / ft-finrl / ft-fingpt / ft-timesfm / ft-laya / ft-research | 127.0.0.1:8131 / 8132 / 8133 / 8134 / 8135 / 8136 / 8137 / 8138 |

## 疑難排解

- `smoke.sh` 的「中文名稱解析」失敗：證券清單沒抓到，`journalctl --user -u fin-terminal | grep securities`。
- ETF 淨值揭露只有 0 檔：MIS 暫時擋 IP 或非交易時段格式變動，過幾分鐘再試；收盤後仍會有當日資料。
- 成分股查不到：槓桿／反向 ETF 只持有期貨與現金不列入；期貨 ETF、外幣級別、剛上市 ETF 不在投信投顧公會資料裡。
- 基金只有幾天的歷史：正常，官方資料只給近期；每天累積，境外基金會試著用 Yahoo 補。
- 台股盤中顯示「試算價 *」：那一刻沒有成交，用最佳買賣價中間值。

- 策略研究說某欄位「還沒有資料」：錯誤訊息會寫該資料已成功／失敗幾批、最近的錯誤和收集器正在做什麼（-9 起）。第一次安裝要等背景下載（「資料庫」頁看進度）；某來源一直失敗，「資料庫」頁最下面有錯誤訊息，貼給我看。
- 「⚖ 判斷」顯示「Laya 模型下載中 xx%」：第一次在下載 1.7 GB，會自己重試；卡住看 `journalctl --user -u ft-laya -n 50`（Hugging Face 連不到時會一直重試）。
- 所有單元都沒起來、`journalctl` 顯示 `ExecStartPre … test -f ~/fin-terminal-data/.ft-root` 失敗：外接硬碟沒掛上（或掛在別的路徑）。掛上後會自動啟動。
- 世界視窗「God's Eye View 未連線」：`systemctl --user status gods-eye-view`；第一次啟動 Vite 要預先編譯，約 1 分鐘。
- 世界讀數一直「目前無法取得」：船舶要 `AISSTREAM_API_KEY`；軍機是 OpenSky／adsb.lol 額度問題，過一陣子會恢復。
- Obsidian 沒出現筆記：`ls ~/fin-terminal-data/data/obsidian-outbox`（有檔＝還沒搬）→ `journalctl --user -u ft-obsidian-apply -n 20`（通常是 `FT_VAULT` 路徑不對或 vault 沒掛上）。

## 已驗證／未驗證

- ✅ 台股資料層 9 個離線測試：ISIN 清單（保留股票、ETF，略過權證；Big5 解碼）、MIS（上市／上櫃／指數、無成交試算價、空殼列）、`all_etf.txt`（數字／字串混合、「未結出」）、ETF 分類與排序、投信投顧公會月份選單與表格（合計列、改版偵測）、**成分股 postback 流程**（VIEWSTATE 串接、依類別查、去掉警語與級別比對基金名）、三種基金 CSV（BOM、千分位、民國年、壞資料）、SQLite 累積與報酬計算、Google 新聞 RSS。SITCA fixture 格式沿用 TWSEMCPServer 的測試（那個專案有對真實網站的 e2e 測試）。
- ✅ 用假資料＋假 Ollama 跑過：UI（Chromium 截圖：中文指令、ETF 總覽、ETF 資料頁、基金搜尋→走勢、紅漲綠跌、手機版）、agent API 全部端點、Hermes `ft.py` 全部指令（含 analyze → DONE／PENDING、引擎離線訊息、參數型別轉換）、Open WebUI Tool 10 個方法（含進度事件）、ASK 自動帶入問題中的標的。
- ✅ TimesFM：用 timesfm 3.0.2 的真實程式碼（2.5 與 3.0 兩條路徑）以隨機權重在 CPU 跑過預測、回測、基金淨值、同步 API、側欄報告、走勢圖與基金圖的疊圖、Hermes `forecast`、agent API；lock 檔在乾淨環境可安裝。⚠️ 我這邊連不到 Hugging Face，**真實權重的下載與預測品質要在你那邊看**（smoke.sh 會跑一次 0050）。
- ✅ 2026.10.19-29：定期定額 4 個測試（排程含月底與每週、假日順延、手續費最低 1 元、外幣換算、XIRR、單筆比較、持股計畫的新增／修改／刪除與權限、回測 API、AI 評估只收回測文字、agent）；Chromium 截圖檢查持股頁定期定額與側欄回測圖。⚠️ 真實價格要你那邊確認。
- ✅ 2026.10.19-28：歷史大事 24 → 38 件（補 2021–2026，2025/5 之後的事件日期與數字已對照新聞查證）。
- ✅ 2026.10.19-27：台幣事件與預測 2 個測試（異常波動偵測與 3 日合併、央行理監事會事件、匯率圖不放指數大波動、TimesFM 接受 TWFX:代碼 序列）；Chromium 截圖檢查美元兩年圖的事件標記與 3 個月預測帶。⚠️ 真實 TimesFM 對匯率的預測要你那邊看。
- ✅ 2026.10.19-26：台幣匯率 3 個測試（台銀牌告 CSV 解析含 0＝不提供、交叉匯率與日變動計算、API／走勢／不支援幣別 404／agent 指定幣別）；Chromium 截圖檢查分組表、換算器與走勢圖。⚠️ 真實 Yahoo 交叉匯率與台銀 CSV 要 smoke 確認。
- ✅ 2026.10.19-25：公開版整理——預設值改成通用（Ollama 11434、模型 qwen3:32b、資料 ~/fin-terminal-data、vault ~/Obsidian/vault、公開網址需 `FT_PUBLIC_HOST`），已安裝的機器沿用原本的資料位置與設定檔。
- ✅ 2026.10.19-24：TOTP 2 個測試（RFC 6238 官方向量；設定→錯碼不啟用→啟用→公開網址要驗證碼→同一碼不能重用→下一碼可登入→救援碼只能用一次→公開網址不能關→本機用密碼＋救援碼關閉→資料庫無明文救援碼；公開網址沒 TOTP 的帳號被拒）；Chromium 截圖檢查 QR 設定與公開登入訊息。
- ✅ 2026.10.19-23：公開網址閘門 3 個測試（本機不變；公開網址未登入 401、agent API／docs 404、不能註冊、只帶 CF-Connecting-IP 也算公開；登入後可用但不能改設定、X-Frame-Options DENY）；Chromium 截圖檢查登入閘門（錯密碼訊息、登入後進終端）。⚠️ Cloudflare Tunnel 本身要你那邊建立 token 後確認。
- ✅ 2026.10.19-22：已在你主機證實 FRED 逾時是瀏覽器 UA 造成（Chrome UA 20 秒逾時、httpx 預設 0.4 秒）；總經頁抓不到時改成頂端一行說明，每列只顯示「— 抓取失敗」（滑過看原因）。
- ✅ 2026.10.19-21：FRED 逾時的真正原因——我們共用的 HTTP 標頭自稱 Chrome，FRED 的 CDN 看到「Chrome 標頭＋Python 的 TLS 指紋」就把連線晾著不回（同一台機器用 httpx 預設標頭 0.4 秒就回來）；FRED 改用誠實的標頭。
- ✅ 2026.10.19-20：FRED——fredgraph.csv 只抓 1990 年後（整段日資料會慢到逾時）、逾時 60 秒、被擋時回 HTML 會認出來；失敗後 10 分鐘內不重試（總經頁 21 個序列不會各等 60 秒）；可選 `FT_FRED_API_KEY` 走官方 API 備援；smoke 失敗時顯示每個序列的原因。1 個新測試。⚠️ 真實 FRED 連線要你那邊確認。
- ✅ 2026.10.19-19：修 `/api/history` 500——Yahoo 偶爾回傳只填一半的 K 棒（台股當天那根 Close 是 NaN）；`market.history` 丟掉沒有收盤價的列、開高低用收盤補、成交量補 0，所有服務（終端＋8 個引擎）的預設回應改成 NaN→null，不會再因為一個 NaN 整支 API 500。1 個新測試。
- ✅ 2026.10.19-18：修 Laya `/decide` 500（事實欄位的 NaN 讓 FastAPI 的嚴格 JSON 失敗）——所有引擎工作結果與 Laya／TimesFM 同步 API 都先轉成合法 JSON（NaN/inf→null、numpy→原生型別）；財報與月營收在法定期限前抓到的只是先申報的公司，改為每 12 小時重抓直到期限後 3 天，期限前的空頁面記為「尚未到申報期限」而不是錯誤。2 個新測試。
- ✅ 2026.10.19-17：SEC／FRED 6 個測試（User-Agent 必填、ticker→CIK、XBRL 取第一次申報、季度排除半年累計、申報日 ±5 天比對、8-K 項目翻譯與文件網址、FRED CSV 新舊表頭與缺值、年增率計算、Fed 降息／不變判斷、FOMC 事件改標實際決策）；Chromium 截圖檢查總經頁、SEC 財報與申報。⚠️ 真實的 SEC／FRED 連線要 `./tests/smoke.sh` 確認。
- ✅ 2026.10.19-16：分析紀錄 4 個測試（收集 8 種情況含投資大師多檔、Qlib 只記錄、失敗的工作不收；5／20／60 日與 TimesFM 到期核對、超額報酬；未到期不判；統計、AI 檢討只用核對結果、檢討回饋真的送進 TradingAgents 的請求、可關閉、TimesFM 不送）；Chromium 截圖檢查紀錄頁。
- ✅ 2026.10.19-15：英文新聞備援（指數→ETF→Google 英文）、歷史大事（使用者修改保留、附近偵測併入不重複）、美國對照（同類去重、沒有美國同類就不比）共 4 個新測試；Chromium 截圖檢查美國對照、歷史大事頁、10 年加權圖。⚠️ 歷史大事的日期與描述是依公開紀錄整理的內建清單，數字以畫面上實際算出的指數漲跌為準。
- ✅ 2026.10.19-14：重大事件 5 個測試（美股收盤＋隔天台股＋反彈合併成一件、平靜月份不誤報、使用者修改與隱藏不會被重新偵測蓋掉、新聞與 AI 命名只用當時標題、自訂事件、API 與 X-FT）；Chromium 截圖檢查標記、滑過說明、點標記跳到大事頁。⚠️ 真實指數歷史、Google 新聞的日期區間搜尋（after:/before:）要在你那邊確認。
- ✅ 2026.10.19-13：供應鏈地圖（資料一致性：沒有未知環節、沒有循環、箭頭一律由左往右）、今日焦點（題材排序、三大法人解析）、登入與持股（10 個測試：未登入 401、缺 X-FT 403、A 看不到也改不了 B 的持股、CSV 只含自己的、資料庫裡沒有明文密碼和 token、密碼錯 5 次鎖定、改密碼登出舊 session、刪帳號連持股一起刪）、Hermes／agent `focus`、`chain`。用假資料在 Chromium 截圖檢查過四格、最大化與手機版。⚠️ 三大法人（證交所 BFI82U）、Google 新聞、真實報價要靠 `./tests/smoke.sh` 在你那邊確認。
- ✅ 2026.10.19-12：基金——境內基金用投信公會代號算出 ISIN（例 T3604Y → TW000T3604Y3），向 Yahoo／Morningstar 補完整歷史淨值並存進本機（官方淨值優先）；搜尋結果多了 1月／1年報酬，沒歷史的會在背景補；「基金」指令會回到上次的搜尋、單檔頁有「← 搜尋結果」；只有一筆淨值時不再畫一條平線。
- ✅ 2026.10.19-11：投資大師遇到 ETF（如 0050）改評估前 N 大成分股（預設 5 檔，0050 約涵蓋六成權重），再依權重加權成 ETF 的訊號；債券／期貨 ETF 會直接標示不適用。之前 ETF 沒有自己的財報，五位大師全部「0 filed periods」棄權。
- ✅ 2026.10.19-9：修正「月營收還沒有資料」——①公開資訊觀測站回擋頁／改版頁不再被當成「無資料」永久略過（改記為錯誤並重試，舊紀錄啟動時自動重抓）；②股價下載改在獨立執行緒，月營收、財報不用等 20 分鐘的股價；③月營收／財報抓到就立刻生效（之前要累積 25 批）；④MOPS 抓不到時用證交所／櫃買 OpenAPI 補最新一個月；⑤錯誤訊息附上進度與最近錯誤。
- ✅ 2026.10.19-8：用真的 TradingAgents 程式碼＋假 Ollama 跑過一整個工作，確認每個節點送出的 `max_tokens` 正確（分析師 8192、多空 2048/3072、經理 4096…）、被截斷的節點流程照常完成並記錄、逾時只呼叫一次就失敗（沒有重試）。
- ✅ 2026.10.19-6 Laya：用**真的 `@receptron/laya` 0.2.0 套件**（npm，onnxruntime-node 1.30 CPU）透過 Node sidecar 跑過整條路徑，但模型換成我自己做的、輸入輸出格式相同的小 ONNX（`tests/make_fake_laya.py`），因為這裡連不到 Hugging Face——所以介面、機率格式、工作、筆記、Hermes／agent API 都驗過，**真實權重的下載和判斷內容要在你那邊看**（`./tests/smoke.sh` 會跑一次 2330）。`pytest tests/` 31 個。
- ✅ 2026.10.19-5：God's Eye View 用**真的上游程式碼**（commit 93ea088，Node 24）在本機跑起來，`/mcp` 的 `tools/list`（30 個工具）、外來 Host 回 403、帶 X-Forwarded-For 回 403 都確認過；快照／事件偵測／as-of 防偷看／摘要／TimesFM 3.0 共變數（真 timesfm 程式碼、隨機權重）／TradingAgents（真 TradingAgents 程式碼＋假 Ollama：12 次 LLM 呼叫都收到世界訊號、新聞分析師拿得到 `get_world_events` 工具）／Obsidian 筆記與回顧／主機搬運腳本（vault 沒 `.obsidian` 時不動）都有測試（`pytest tests/` 22 個）；install.sh 用假 podman／systemctl 跑過（資料夾、哨兵檔、單元路徑替換、env 補值、tailscale serve 指令）；UI 截圖（世界視窗、K 線事件標記）。⚠️ 我這邊 God's Eye View 連不到它的外部資料源，**真實讀數要在你那邊看**（`./tests/smoke.sh` 會檢查）；也沒在真的 Podman 上 build。
- ⚠️ 未實測：我的環境連不到證交所、櫃買、投信投顧公會、集保、Google 新聞、Yahoo，**所有真實資料來源都要靠 `./tests/smoke.sh` 在你那邊確認**（欄位名稱是依官方介接格式說明、TWSEMCPServer 2026-10 的實作與 fund-database 2026-10-18 的資料核對的）。也沒在真的 Podman 上 build、沒在你的 Hermes 和 Open WebUI 版本上實際載入。
