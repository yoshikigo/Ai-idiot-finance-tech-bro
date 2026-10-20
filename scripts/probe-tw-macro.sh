#!/usr/bin/env bash
# 探測台灣總經資料來源（主計總處、國發會、央行、政府資料開放平臺）在你這台主機上能不能用、回什麼格式。
# 用法：./scripts/probe-tw-macro.sh > tw-macro-probe.txt   然後把輸出貼給 Claude
set -u
UA="fin-terminal (curl)"
probe() {
  local name=$1 url=$2
  echo "=================== $name"
  echo "URL: $url"
  local out; out=$(mktemp)
  local meta; meta=$(curl -sS -L -m 40 -A "$UA" -o "$out" -w '%{http_code} %{content_type} %{size_download}' "$url" 2>&1) || true
  echo "HTTP: $meta"
  head -c 1500 "$out" | tr -d '\000' ; echo; echo
  rm -f "$out"
}
# 政府資料開放平臺 v2 API：列出資料集的下載網址
probe "data.gov.tw 景氣指標及燈號（國發會）" "https://data.gov.tw/api/v2/rest/dataset/6099"
probe "data.gov.tw 每月國內主要經濟指標（主計總處）" "https://data.gov.tw/api/v2/rest/dataset/13228"
probe "data.gov.tw 年度國內主要金融指標（央行）" "https://data.gov.tw/api/v2/rest/dataset/130490"
# 主計總處 總體統計資料庫 SDMX-JSON
probe "主計總處 CPI 基本分類指數 metadata" "https://nstatdb.dgbas.gov.tw/dgbasAll/webMain.aspx?sdmx/A030101015"
probe "主計總處 GDP 依支出分（季）metadata" "https://nstatdb.dgbas.gov.tw/dgbasAll/webMain.aspx?sdmx/A018102060"
# 央行 開放資料 API
probe "央行 貼放利率（set_id 6022）" "https://cpx.cbc.gov.tw/api/OpenData/DataSet?set_id=6022&index=0"
probe "央行 重貼現率頁面" "https://www.cbc.gov.tw/tw/lp-640-1.html"
probe "央行 M2 年增率頁面" "https://www.cbc.gov.tw/tw/lp-643-1.html"
