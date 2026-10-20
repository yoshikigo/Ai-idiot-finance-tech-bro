"""README 截圖：對一個「示範資料」模式的終端機（FT_FAKE_DATA=1）依序開各分頁並截圖到 docs/screenshots/。

  cd terminal && FT_FAKE_DATA=1 FT_DATA=$(mktemp -d) PYTHONPATH=../common python -m uvicorn app:app --port 8765 &
  curl -XPOST -H "x-ft: 1" localhost:8765/api/events/refresh
  python scripts/screenshots.py            # 全部；或指定檔名，例 09-macro-tw

需要 playwright（pip install playwright && playwright install chromium）。會在示範資料庫建立 demo 帳號與示範持股。
"""
import asyncio, json, sys
from playwright.async_api import async_playwright
import os
B = os.environ.get("FT_URL", "http://127.0.0.1:8765")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "screenshots") + "/"
MAXI = "(() => { const p = document.querySelectorAll('.pane')[0]; if (!p.classList.contains('maximized')) toggleMax(0); })()"

async def cmd(pg, text, wait=2500):
    await pg.fill("#cmd", text); await pg.press("#cmd", "Enter"); await pg.wait_for_timeout(wait)

async def main(only):
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={"width": 1600, "height": 960})
        pg = await ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.goto(B + "/"); await pg.wait_for_timeout(2000)
        await pg.evaluate("""async () => { const H = {'content-type': 'application/json', 'x-ft': '1'};
          await fetch('/api/auth/register', {method: 'POST', headers: H, body: JSON.stringify({username: 'demo', password: 'demo-password-123'})});
          await fetch('/api/auth/login', {method: 'POST', headers: H, body: JSON.stringify({username: 'demo', password: 'demo-password-123'})}); }""")
        await pg.reload(); await pg.wait_for_timeout(3500)
        await pg.evaluate("toggleSide()"); await pg.wait_for_timeout(500)
        async def shot(name):
            if only and name not in only: return
            await pg.screenshot(path=OUT + name + ".png"); print("shot", name, errs[-1:] if errs else "")
        await cmd(pg, "1 台積電", 3000)
        await shot("01-overview")
        pages = [("02-chart-events", "1 ^TWII", 4000), ("03-etf", "1 ETF", 3000), ("04-etf-detail", "1 0056 概況", 3000),
                 ("05-focus", "1 焦點", 3500), ("06-supply-chain", "1 供應鏈 CoWoS", 3500), ("07-events", "1 大事", 3000),
                 ("08-macro-us", "1 總經 NETLIQ", 4000), ("09-macro-tw", "1 台經 SIGNAL", 4000), ("10-twfx", "1 台幣 JPY", 4000),
                 ("11-toolkit", "1 技術 2330", 4000)]
        for name, c, w in pages:
            if only and name not in only: continue
            await cmd(pg, c, 800); await pg.evaluate(MAXI); await pg.wait_for_timeout(w); await shot(name)
        # holdings + DCA (demo account in a throwaway data dir)
        if not only or "12-holdings" in only or "13-dca" in only:
            r = await pg.evaluate("""async () => {
              const H = {'content-type': 'application/json', 'x-ft': '1'};
              for (const x of [['2330.TW', 2000], ['0050.TW', 3000], ['00878.TW', 10000], ['2454.TW', 200], ['NVDA', 3]])
                await fetch('/api/me/holdings', {method: 'POST', headers: H, body: JSON.stringify({symbol: x[0], shares: x[1], cost: 1, opened: '2025-03-10'})});
              const f = {'2330.TW': 0.82, '0050.TW': 0.9, '00878.TW': 1.04, '2454.TW': 0.95, 'NVDA': 0.7};
              for (const r of (await (await fetch('/api/me/holdings')).json()).rows)
                await fetch('/api/me/holdings/' + r.id, {method: 'PUT', headers: H, body: JSON.stringify({symbol: r.symbol, shares: r.shares, cost: +(r.last * f[r.symbol]).toFixed(2), opened: '2025-03-10'})});
              for (const x of [['0050.TW', 5000, '6,16,26'], ['VOO', 3000, '6'], ['BTC-USD', 1000, '16']])
                await fetch('/api/me/dca', {method: 'POST', headers: H, body: JSON.stringify({symbol: x[0], amount: x[1], freq: 'monthly', days: x[2], start: '2023-01-01'})});
              return (await fetch('/api/me/holdings')).status; }""")
            print("holdings status", r)
            await cmd(pg, "1 持股", 800); await pg.evaluate(MAXI); await pg.wait_for_timeout(4500); await shot("12-holdings")
            await pg.evaluate("(() => { const p = document.querySelector('.pane.maximized'); if (p) toggleMax(0); })()")
            await pg.evaluate("toggleSide()")
            await cmd(pg, "定期定額 0050, VOO, BTC-USD", 1500)
            btn = pg.locator("text=開始回測")
            if await btn.count(): await btn.first.click()
            await pg.wait_for_timeout(5000); await shot("13-dca")
        # phone
        if not only or "14-mobile" in only:
            m = await b.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True)
            await m.goto(B + "/"); await m.wait_for_timeout(3500)
            await m.screenshot(path=OUT + "14-mobile.png"); print("shot 14-mobile")
        print("errors:", errs)
        await b.close()
asyncio.run(main(sys.argv[1:]))
