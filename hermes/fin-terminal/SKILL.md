---
name: fin-terminal
description: 查台股／美股報價、台股 ETF（預估淨值、折溢價、規模、配息、成分股、排行）、境內外基金淨值與報酬、市場總覽、中文財經新聞；也能查現實世界訊號（God's Eye View：台海軍機、紅海／荷莫茲航運、地震、氣旋）、做 TimesFM 走勢預測、回顧過去預測準不準、啟動本機 AI 投資分析（TradingAgents 多代理人、FinGPT 預測與情緒、投資大師基金、Qlib 量化、FinRL 強化學習）。使用者問股價、大盤、ETF、基金、殖利率、配息、折溢價、某檔股票怎麼看、要分析某檔時使用。
---

# FT Terminal（本機金融終端）

所有資料都來自使用者自架的 FT Terminal（`http://127.0.0.1:8130`）：台股即時報價（證交所 MIS）、ETF 淨值（證交所揭露）、ETF 成分股（投信投顧公會）、基金淨值（投信投顧公會、集保），其他市場來自 Yahoo Finance（延遲報價）。

用終端機執行（一律用這支腳本，不要自己上網找價格）：

```
python3 /opt/data/skills/fin-terminal/scripts/ft.py <指令> [參數]
```

## 什麼時候用哪個指令

| 使用者說 | 指令 |
|---|---|
| 「台積電現在多少」「0050、00878 報價」「美股 NVDA」 | `ft.py quote 台積電 0050 NVDA`（可一次多檔，中文名稱或代號都可以） |
| 「大盤今天怎樣」「盤勢」 | `ft.py market` |
| 「我的自選股」 | `ft.py watchlist` |
| 「今天盤後重點」「每日簡報」 | `ft.py brief` |
| 「今年有哪些重大事件」「4 月為什麼大跌」「下次 Fed 開會是哪天」 | `ft.py events`（近一年指數大波動、Fed 決策日、2020 年起的歷史大事與 AI 摘要；`--days 2000` 看更早） |
| 「美國通膨／利率／失業率現在多少」「殖利率曲線還倒掛嗎」 | `ft.py macro`（FRED 官方總經數據） |
| 「NVDA 最近有什麼 8-K」「蘋果最新年報營收」「內部人有沒有在賣」 | `ft.py sec NVDA`（SEC EDGAR 申報與官方財報；內部人只列 Form 4 申報，不解析張數） |
| 「今日焦點」「今天哪些題材強」「法人買賣超」 | `ft.py focus`（供應鏈題材漲跌、強弱勢股、三大法人、新聞） |
| 「CoWoS 供應鏈今天怎樣」「散熱族群」「奇鋐在哪個供應鏈」「AI 伺服器上下游」 | `ft.py chain CoWoS`／`ft.py chain 散熱`／`ft.py chain 奇鋐`；`ft.py chain` 列出各環節今日漲跌 |
| 「鴻海基本面」「2330 的資料」 | `ft.py stock 鴻海`（含公司資料、估值、近期新聞） |
| 「00878 淨值／折溢價／配息／成分股」「0056 殖利率多少」 | `ft.py etf 00878` |
| 「現在哪些 ETF 溢價最高」「債券 ETF 規模排行」「最近誰被大量申購」 | `ft.py etf-rank --sort premium`（`scale` 規模、`premium` 溢價、`discount` 折價、`creation` 淨申購、`redemption` 淨買回；`--category 債券`／`高股息`／`股票型`／`主動式`／`槓桿/反向`／`期貨/商品`） |
| 「安聯台灣科技基金淨值」「統一黑馬今年報酬」 | `ft.py fund 安聯台灣科技` |
| 「有哪些安聯的收益基金」 | `ft.py fund-search 安聯 收益`（`--region 境內`／`境外`／`期信`） |
| 「聯發科最近新聞」 | `ft.py news 聯發科` |
| 「台海現在有沒有異常」「紅海航運正常嗎」「最近有大地震嗎」「這檔股票要注意什麼世界事件」 | `ft.py world --region 台灣` / `ft.py world --region 紅海` / `ft.py world 2603`（God's Eye View 感測資料：地震、軍機 ADS-B、船舶 AIS、氣旋） |
| 「台積電現在該買還是賣」「0050 可以放空嗎」「多空機率」「買賣建議幾趴」 | `ft.py decide 台積電`（Laya 決策模型，約 1 秒：買進／持有／觀望／賣出／放空 百分比；`--days 5` 一週、`20` 一個月、`60` 一季） |
| 「幫我找營收連續成長、投信在買的股票」「今天有哪些高殖利率低本益比的股票」「這個選股方法回測績效如何」 | `ft.py strategy "營收連續成長、投信在買"`（本機 AI 轉成條件＋回測，1–3 分鐘）；內建策略直接打名稱：`ft.py strategy 高殖利率低本益比`；`ft.py strategy` 列出全部 |
| 「之前的 AI 預測準不準」「TradingAgents 命中率多少」「AI 檢討了什麼」 | `ft.py ledger`（5／20／60 個交易日後用實際股價核對、各 agent 命中率、最近檢討與提醒）；`ft.py review` 是舊版簡易回顧 |
| 「0050 未來一個月走勢預測」「台積電接下來 60 天會怎麼走」「這檔基金預測」 | `ft.py forecast 0050 --days 20`（TimesFM 統計預測，幾秒～1 分鐘；基金用 `FUND:<id>`，先 `fund` 查 id） |

## AI 分析（比較久）

| 使用者說 | 指令 |
|---|---|
| 「幫我用 TradingAgents 分析台積電」「台積電該買嗎，深入分析」 | `ft.py analyze tradingagents 台積電` |
| 「FinGPT 預測 NVDA 下週」 | `ft.py analyze fingpt NVDA` |
| 「這幾檔的新聞情緒」 | `ft.py analyze fingpt 2330 --param mode=sentiment` |
| 「用投資大師看 AAPL 和 MSFT」 | `ft.py analyze hedgefund AAPL MSFT` |
| 「強化學習回測 2330 2317 2454」 | `ft.py analyze finrl 2330 2317 2454` |
| 「Qlib 跑台灣 50 選股」 | `ft.py analyze qlib --param universe=tw50`（不用帶標的時，標的位置寫 `0050` 即可，會用 universe 參數） |

- 執行指令時 **timeout 設 600 秒**。腳本預設最多等 540 秒。
- 輸出第一行是 `DONE` 就整理結論回覆；是 `PENDING <engine> <id>` 就先回覆使用者「還在分析中（進度 xx%）」，然後執行 `ft.py job <engine> <id>` 繼續等，最多再等 3 次。
- TradingAgents 一次完整分析大約 10–25 分鐘；FinGPT 1–3 分鐘；投資大師每檔每位大師約 1 分鐘。
- 使用者之後問「剛剛的分析好了嗎」：`ft.py jobs` 找最近的工作，再 `ft.py job <engine> <id> --wait 0`。

## 回覆規則

- 數字只能用腳本輸出裡的，**不要自己編價格、淨值、殖利率**；腳本說查不到就照實說。
- 世界訊號是感測資料（數量、地震規模），不是新聞：只說資料顯示什麼、和平常比偏高或偏低；顯示「無法取得」就照實說，不要推論「很平靜」。
- AI 分析做完會自動存一篇筆記到 Obsidian 的 `finance/AI 預測/`（使用者可以在 Obsidian 看完整報告）。
- TimesFM 預測要說清楚是「只看過去價格的統計預測、附 80% 區間」，不要說成會漲或會跌的保證；有回測就一併說準確度（和「價格不變」基準比）。
- Laya 判斷要說清楚：百分比是通用決策模型對這份資料的信心，不是統計漲跌機率；Laya 沒用金融資料訓練。最高的選項只是「模型最傾向」，請一併提多空淨值和其他引擎（TimesFM、TradingAgents）的看法。
- 策略回測要說清楚：用官方資料、隔日開盤成交、含成本，但只含目前上市櫃公司（存活者偏差、偏樂觀），歷史績效不代表未來；選股名單是「符合條件」不是推薦。
- 台股「量」單位是張；ETF 折溢價超過 ±1% 要提醒；基金報酬是依淨值計算、不含手續費。
- LINE 回覆用純文字、條列，3–12 行為主；完整 AI 報告很長時，摘要結論與理由 3–5 點，並提醒完整報告在 FT Terminal 網頁（tailnet `:8130`）。
- 「我的持股」在 FT Terminal 網頁裡要登入才看得到，這個 skill **讀不到**任何人的持股；使用者問「我的持股今天賺多少」就請他到網頁 `持股`（或右上 👤）查看，不要猜。
- 結尾加一句「僅供參考，非投資建議」。
- 錯誤訊息是「連不到 FT Terminal」：告訴使用者服務可能沒開（`systemctl --user status fin-terminal`）。
