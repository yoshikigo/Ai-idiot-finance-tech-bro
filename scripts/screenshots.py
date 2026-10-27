"""README 截圖：對一個「示範資料」模式的終端機（FT_FAKE_DATA=1）依序開各分頁並截圖到 docs/screenshots/。

  cd terminal && FT_FAKE_DATA=1 FT_DATA=$(mktemp -d) PYTHONPATH=../common python -m uvicorn app:app --port 8765 &
  curl -XPOST -H "x-ft: 1" localhost:8765/api/events/refresh
  python scripts/screenshots.py            # 全部；或指定檔名，例 09-macro-tw

各功能頁用「簡潔版面」（預設）截；01-overview 與 14-mobile 是「經典版面」（四格終端機）。
00-clean／17-validate／18-track 另外產生（要 AI 引擎或策略研究引擎），這支不動它們。
需要 playwright（pip install playwright && playwright install chromium）。會在示範資料庫建立 demo 帳號與示範持股。
"""
import asyncio
import os
import sys

from playwright.async_api import async_playwright

B = os.environ.get("FT_URL", "http://127.0.0.1:8765")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "screenshots") + "/"
LOGIN = """async () => { const H = {'content-type': 'application/json', 'x-ft': '1'};
  await fetch('/api/auth/register', {method: 'POST', headers: H, body: JSON.stringify({username: 'demo', password: 'demo-password-123'})});
  await fetch('/api/auth/login', {method: 'POST', headers: H, body: JSON.stringify({username: 'demo', password: 'demo-password-123'})}); }"""
SEED = """async () => {
  const H = {'content-type': 'application/json', 'x-ft': '1'};
  if ((await (await fetch('/api/me/holdings')).json()).rows.length) return 'exists';
  for (const x of [['2330.TW', 2000], ['0050.TW', 3000], ['00878.TW', 10000], ['2454.TW', 200], ['NVDA', 3]])
    await fetch('/api/me/holdings', {method: 'POST', headers: H, body: JSON.stringify({symbol: x[0], shares: x[1], cost: 1, opened: '2025-03-10'})});
  const f = {'2330.TW': 0.82, '0050.TW': 0.9, '00878.TW': 1.04, '2454.TW': 0.95, 'NVDA': 0.7};
  for (const r of (await (await fetch('/api/me/holdings')).json()).rows)
    await fetch('/api/me/holdings/' + r.id, {method: 'PUT', headers: H, body: JSON.stringify({symbol: r.symbol, shares: r.shares, cost: +(r.last * f[r.symbol]).toFixed(2), opened: '2025-03-10'})});
  for (const x of [['0050.TW', 5000, '6,16,26'], ['VOO', 3000, '6'], ['BTC-USD', 1000, '16']])
    await fetch('/api/me/dca', {method: 'POST', headers: H, body: JSON.stringify({symbol: x[0], amount: x[1], freq: 'monthly', days: x[2], start: '2023-01-01'})});
  return 'seeded'; }"""
PAGES = [("02-chart-events", "^TWII 走勢", 4000), ("03-etf", "ETF", 3000), ("04-etf-detail", "0056 ETF", 3500),
         ("05-focus", "焦點", 4000), ("06-supply-chain", "供應鏈 CoWoS", 3500), ("07-events", "大事", 3000),
         ("08-macro-us", "總經 NETLIQ", 4000), ("09-macro-tw", "台經 SIGNAL", 4000), ("10-twfx", "台幣 JPY", 4000),
         ("11-toolkit", "技術 2330", 4000), ("15-sectors-us", "產業 美股", 4000), ("16-sectors-tw", "產業 台股", 4000),
         ("12-holdings", "持股", 4500)]


async def cmd(pg, text, wait=2500):
    await pg.fill("#cmd", text)
    await pg.press("#cmd", "Enter")
    await pg.wait_for_timeout(wait)
    # a late search suggestion must not cover the page
    await pg.evaluate("document.getElementById('suggest').classList.add('hidden'); document.activeElement && document.activeElement.blur()")


async def layout(pg, name, wait=3500):
    await pg.evaluate(f"localStorage.setItem('ft.layout', '{name}')")
    await pg.reload()
    await pg.wait_for_timeout(wait)


async def main(only):
    want = lambda n: not only or n in only  # noqa: E731
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={"width": 1600, "height": 960})
        pg = await ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        await pg.goto(B + "/")
        await pg.wait_for_timeout(2000)
        await pg.evaluate(LOGIN)
        print("holdings:", await pg.evaluate(SEED))

        async def shot(page, name):
            await page.screenshot(path=OUT + name + ".png")
            print("shot", name, errs[-1:] if errs else "")

        # ---- 經典版面（四格）
        if want("01-overview"):
            await layout(pg, "classic")
            await pg.evaluate("toggleSide(false)")
            await cmd(pg, "1 台積電", 3500)
            await shot(pg, "01-overview")
        if want("14-mobile"):
            m = await b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True)
            mp = await m.new_page()
            await mp.goto(B + "/")
            await layout(mp, "classic")
            await shot(mp, "14-mobile")
            await m.close()

        # ---- 簡潔版面（預設）：各功能頁，右側 AI 收起讓頁面寬一點
        await layout(pg, "clean")
        await pg.evaluate("toggleSide(false)")
        for name, c, w in PAGES:
            if want(name):
                await cmd(pg, c, w)
                await shot(pg, name)
        if want("13-dca"):
            await cmd(pg, "定期定額 0050, VOO, BTC-USD", 1500)
            btn = pg.locator("text=開始回測")
            if await btn.count():
                await btn.first.click()
            await pg.wait_for_timeout(5000)
            await shot(pg, "13-dca")
        if want("00-clean-mobile"):
            m = await b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True)
            mp = await m.new_page()
            await mp.goto(B + "/")
            await layout(mp, "clean")
            await mp.evaluate("run('2330')")
            await mp.wait_for_timeout(3500)
            await shot(mp, "00-clean-mobile")
            await m.close()
        print("errors:", errs)
        await b.close()

asyncio.run(main(sys.argv[1:]))
